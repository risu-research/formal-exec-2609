#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

G6_PRESELECTION_SHA = "4c8218fe664816f01653e483a6f205428eac33c3"
EVAL_FREEZE_SHA = "7ec2f62f2c68ac54879f1e25ec014e89a95b6ba8"
G5_PARENT_SHA = "ba7c505dbbe96d16e4f5a35606adea52f2b4aa15"
CUTOFF = datetime(2026, 9, 12, 23, 59, 59, tzinfo=timezone.utc)
MAX_SELECTED = 48

SENTINELS = {
    "ZipCPU/zipcpu": {
        "d511239e19be8fcc7f340a64554ea93699637e62",
        "19112823d4bc5b52b3cfb002cc1d67f9498eb521",
        "f49e57c4bda78cd240cec532fe8b7c7363857966",
        "4e689d3c265e7197cd8aac8234b31202939f9065",
    }
}

ZIP_RE = re.compile(r"(\bassert\b|\bassume\b|\bcover\b|\$past|\$stable|\$rose|\$fell|`?FORMAL)", re.I)
OT_RE = re.compile(r"(\bASSERT(?:_FPV)?\b|\bASSUME(?:_FPV)?\b|\bCOVER(?:_FPV)?\b|\bassert\b|\bassume\b|\bcover\b|FPV_ON|`?FORMAL)", re.I)
HDL_EXTS = (".v", ".sv", ".vh", ".svh")


def run(repo: Path, args: list[str], timeout: int = 900) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], text=True, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} rc={p.returncode}\n{p.stderr[-4000:]}")
    return p.stdout


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def iso_dt(text: str) -> datetime:
    return datetime.fromisoformat(text.strip().replace("Z", "+00:00")).astimezone(timezone.utc)


def child_meta(repo: Path, child: str) -> dict[str, Any] | None:
    parts = run(repo, ["rev-list", "--parents", "-n", "1", child]).strip().split()
    if len(parts) != 2:
        return None
    parent = parts[1]
    ts = run(repo, ["show", "-s", "--format=%cI", child]).strip()
    dt = iso_dt(ts)
    if dt > CUTOFF:
        return None
    return {"child": child, "parent": parent, "committer_timestamp": dt.isoformat().replace("+00:00", "Z")}


def changed_paths(repo: Path, parent: str, child: str) -> list[str]:
    text = run(repo, ["diff-tree", "--no-commit-id", "--name-only", "-r", "--no-renames", parent, child])
    return sorted({x.strip() for x in text.splitlines() if x.strip()})


def changed_lines(repo: Path, parent: str, child: str, paths: list[str]) -> str:
    if not paths:
        return ""
    text = run(repo, ["diff", "--unified=0", "--no-ext-diff", "--no-renames", parent, child, "--", *paths])
    rows = []
    for line in text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+") or line.startswith("-"):
            rows.append(line[1:])
    return "\n".join(rows)


def seed_candidates(repo: Path, project: str) -> set[str]:
    before = "2026-09-13T00:00:00Z"
    base = ["log", "--format=%H", "--no-merges", f"--before={before}"]
    shas: set[str] = set()

    def add_pathspecs(specs: list[str]) -> None:
        out = run(repo, [*base, "--", *specs])
        shas.update(x.strip() for x in out.splitlines() if x.strip())

    def add_pickaxe(regex: str, specs: list[str]) -> None:
        out = run(repo, [*base, "--regexp-ignore-case", f"-G{regex}", "--", *specs], timeout=1800)
        shas.update(x.strip() for x in out.splitlines() if x.strip())

    if project == "ZipCPU/zipcpu":
        add_pathspecs(["bench/formal", "formal", ":(glob)**/*.sby", ":(glob)**/*.ys"])
        add_pickaxe(r"assert|assume|cover|\\$past|\\$stable|\\$rose|\\$fell|FORMAL",
                    [":(glob)rtl/**/*.v", ":(glob)rtl/**/*.sv", ":(glob)rtl/**/*.vh", ":(glob)rtl/**/*.svh"])
    elif project == "SymbioticEDA/riscv-formal":
        add_pathspecs(["checks", "cores", "insns", "cover", "rvfi_macros.vh", "checks.cfg"])
    elif project == "lowRISC/opentitan":
        add_pathspecs([
            ":(glob)hw/**/fpv/**", "hw/formal",
            ":(glob)hw/**/*_assert_fpv.sv", ":(glob)hw/**/*_bind_fpv.sv",
            ":(glob)hw/**/*_fpv.core", ":(glob)hw/**/*formal*.hjson",
        ])
        add_pickaxe(r"ASSERT|ASSUME|COVER|assert|assume|cover|FPV_ON|FORMAL",
                    [":(glob)hw/**/rtl/**/*.v", ":(glob)hw/**/rtl/**/*.sv", ":(glob)hw/**/rtl/**/*.vh", ":(glob)hw/**/rtl/**/*.svh"])
    else:
        raise ValueError(project)
    return shas


def exact_eligibility(repo: Path, project: str, meta: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
    paths = changed_paths(repo, meta["parent"], meta["child"])
    reasons: list[str] = []

    if project == "ZipCPU/zipcpu":
        if any(p.startswith("bench/formal/") for p in paths): reasons.append("PATH:bench/formal/")
        if any(p.startswith("formal/") for p in paths): reasons.append("PATH:formal/")
        if any(p.endswith(".sby") for p in paths): reasons.append("SUFFIX:.sby")
        if any(p.endswith(".ys") for p in paths): reasons.append("SUFFIX:.ys")
        rtl = [p for p in paths if p.startswith("rtl/") and p.lower().endswith(HDL_EXTS)]
        if rtl and ZIP_RE.search(changed_lines(repo, meta["parent"], meta["child"], rtl)):
            reasons.append("RTL_CHANGED_LINE_FORMAL_KEYWORD")

    elif project == "SymbioticEDA/riscv-formal":
        prefixes = ["checks/", "cores/", "insns/", "cover/"]
        for pref in prefixes:
            if any(p.startswith(pref) for p in paths): reasons.append(f"PATH:{pref}")
        for exact in ["rvfi_macros.vh", "checks.cfg"]:
            if exact in paths: reasons.append(f"PATH:{exact}")

    elif project == "lowRISC/opentitan":
        if any("/fpv/" in f"/{p}" for p in paths): reasons.append("SEGMENT:/fpv/")
        if any(p.startswith("hw/formal/") for p in paths): reasons.append("PATH:hw/formal/")
        if any(Path(p).name.endswith("_assert_fpv.sv") for p in paths): reasons.append("BASENAME:*_assert_fpv.sv")
        if any(Path(p).name.endswith("_bind_fpv.sv") for p in paths): reasons.append("BASENAME:*_bind_fpv.sv")
        if any(Path(p).name.endswith("_fpv.core") for p in paths): reasons.append("BASENAME:*_fpv.core")
        if any("formal" in p and p.endswith(".hjson") for p in paths): reasons.append("FORMAL_HJSON")
        rtl = [p for p in paths if "/rtl/" in f"/{p}" and p.lower().endswith(HDL_EXTS)]
        if rtl and OT_RE.search(changed_lines(repo, meta["parent"], meta["child"], rtl)):
            reasons.append("RTL_CHANGED_LINE_FORMAL_KEYWORD")
    else:
        raise ValueError(project)

    return bool(reasons), sorted(set(reasons)), paths


def strata(selected: list[dict[str, Any]]) -> None:
    chrono = sorted(selected, key=lambda r: (r["committer_timestamp"], r["child"]))
    n = len(chrono)
    for i, rec in enumerate(chrono):
        bucket = min(2, (i * 3) // max(1, n))
        rec["chronology_stratum"] = ["EARLY", "MIDDLE", "LATE"][bucket]
        rec["chronology_index"] = i


def audit_project(repo: Path, project: str) -> dict[str, Any]:
    seeds = sorted(seed_candidates(repo, project))
    eligible: list[dict[str, Any]] = []
    skipped_ambiguous_parent = 0
    for child in seeds:
        meta = child_meta(repo, child)
        if meta is None:
            skipped_ambiguous_parent += 1
            continue
        ok, reasons, paths = exact_eligibility(repo, project, meta)
        if not ok:
            continue
        rec = dict(meta)
        rec["repository_full_name"] = project
        rec["sampling_digest"] = sha256_text(project + child)
        rec["eligibility_reasons"] = reasons
        rec["formal_relevant_changed_paths"] = paths
        rec["pre_registered_sentinel"] = child in SENTINELS.get(project, set())
        eligible.append(rec)

    eligible.sort(key=lambda r: (r["committer_timestamp"], r["child"]))
    if len(eligible) <= MAX_SELECTED:
        selected = [dict(r) for r in eligible]
    else:
        selected = [dict(r) for r in sorted(eligible, key=lambda r: (r["sampling_digest"], r["child"]))[:MAX_SELECTED]]
    strata(selected)
    selected.sort(key=lambda r: (r["sampling_digest"], r["child"]))

    return {
        "repository_full_name": project,
        "local_head": run(repo, ["rev-parse", "HEAD"]).strip(),
        "seed_candidate_count": len(seeds),
        "eligible_count": len(eligible),
        "selected_count": len(selected),
        "single_parent_or_cutoff_skips_from_seed_set": skipped_ambiguous_parent,
        "eligible": eligible,
        "selected": selected,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zipcpu", type=Path, required=True)
    ap.add_argument("--riscv-formal", type=Path, required=True)
    ap.add_argument("--opentitan", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    projects = [
        (args.zipcpu, "ZipCPU/zipcpu"),
        (args.riscv_formal, "SymbioticEDA/riscv-formal"),
        (args.opentitan, "lowRISC/opentitan"),
    ]
    rows = [audit_project(path, name) for path, name in projects]
    selected_total = sum(r["selected_count"] for r in rows)
    eligible_total = sum(r["eligible_count"] for r in rows)
    sentinel_selected = sum(1 for r in rows for x in r["selected"] if x["pre_registered_sentinel"])

    manifest = {
        "schema": "g6-r0-blind-corpus-seal-v1",
        "g6_preselection_authority": G6_PRESELECTION_SHA,
        "evaluation_freeze": EVAL_FREEZE_SHA,
        "g5_parent": G5_PARENT_SHA,
        "cutoff_inclusive_utc": CUTOFF.isoformat().replace("+00:00", "Z"),
        "max_selected_per_project": MAX_SELECTED,
        "r0_commit_messages_consumed": False,
        "r0_proof_status_consumed": False,
        "r0_semantic_classification_consumed": False,
        "eligible_total": eligible_total,
        "selected_total": selected_total,
        "selected_pre_registered_sentinels": sentinel_selected,
        "projects": rows,
    }
    (args.out / "g6-r0-corpus-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    for row in rows:
        slug = row["repository_full_name"].replace("/", "__")
        with (args.out / f"{slug}__selected.jsonl").open("w") as f:
            for rec in row["selected"]:
                f.write(json.dumps(rec, sort_keys=True) + "\n")
    summary = {
        "eligible_total": eligible_total,
        "selected_total": selected_total,
        "selected_pre_registered_sentinels": sentinel_selected,
        "per_project": {r["repository_full_name"]: {"eligible": r["eligible_count"], "selected": r["selected_count"]} for r in rows},
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
