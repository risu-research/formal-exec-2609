#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

G6_PRESELECTION_SHA = "4c8218fe664816f01653e483a6f205428eac33c3"
G6_SUPPORT_SHA = "538189291d09284303c27d717ca8b0b82e69b1e1"

ROLE_PATTERNS = {
    "assume_keyword": re.compile(r"(?<![A-Za-z0-9_])assume\s*\(", re.I),
    "assert_keyword": re.compile(r"(?<![A-Za-z0-9_])assert\s*\(", re.I),
    "cover_keyword": re.compile(r"(?<![A-Za-z0-9_])cover\s*\(", re.I),
    "assume_fpv_macro": re.compile(r"\bASSUME(?:_FPV)?\s*\("),
    "assert_fpv_macro": re.compile(r"\bASSERT(?:_FPV)?\s*\("),
    "cover_fpv_macro": re.compile(r"\bCOVER(?:_FPV)?\s*\("),
}
TEMPORAL_PATTERNS = {
    "past": re.compile(r"\$past\s*\("),
    "rose": re.compile(r"\$rose\s*\("),
    "fell": re.compile(r"\$fell\s*\("),
    "stable": re.compile(r"\$stable\s*\("),
    "overlap_implication": re.compile(r"\|->"),
    "nonoverlap_implication": re.compile(r"\|=>"),
    "delay": re.compile(r"##\s*\d+"),
}


def git(repo: Path, args: list[str], check: bool = True, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(["git", "-C", str(repo), *args], text=True, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} rc={p.returncode}\n{p.stderr[-3000:]}")
    return p


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def blob(repo: Path, rev: str, path: str) -> tuple[bytes | None, str | None]:
    spec = f"{rev}:{path}"
    chk = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", spec], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    if chk.returncode != 0:
        return None, None
    p = subprocess.run(["git", "-C", str(repo), "show", spec], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError(f"git show failed {spec}: {p.stderr[-1000:].decode(errors='replace')}")
    return p.stdout, sha(p.stdout)


def source_inventory(data: bytes | None, path: str) -> dict[str, Any]:
    if data is None:
        return {"exists": False}
    text = data.decode("utf-8", errors="replace")
    roles = {k: len(rx.findall(text)) for k, rx in ROLE_PATTERNS.items()}
    temporal = {k: len(rx.findall(text)) for k, rx in TEMPORAL_PATTERNS.items()}
    sections: list[str] = []
    if path.endswith(".sby"):
        sections = [m.group(1).strip() for m in re.finditer(r"^\s*\[([^\]]+)\]\s*$", text, re.M)]
    return {
        "exists": True,
        "bytes": len(data),
        "roles": roles,
        "temporal_tokens": temporal,
        "sby_sections": sections,
        "contains_formal_define": bool(re.search(r"`?FORMAL|FPV_ON", text)),
    }


def path_kind(path: str) -> str:
    p = path.lower()
    if p.endswith(".sby") or p.endswith(".ys") or p.endswith(".hjson") or p.endswith(".core") or p.endswith(".cfg"):
        return "CONFIG_OR_SCRIPT"
    if "/fpv/" in f"/{p}" or "formal" in p:
        return "FORMAL_SOURCE_OR_COLLATERAL"
    if "/rtl/" in f"/{p}" or p.startswith("rtl/"):
        return "RTL"
    return "OTHER_ELIGIBLE"


def actual_paths(repo: Path, parent: str, child: str) -> list[str]:
    p = git(repo, ["diff-tree", "--no-commit-id", "--name-only", "-r", "--no-renames", parent, child])
    return sorted({x for x in p.stdout.splitlines() if x})


def pair_inventory(repo: Path, rec: dict[str, Any]) -> dict[str, Any]:
    parent, child = rec["parent"], rec["child"]
    # Deliberately no subject/body format is requested anywhere in R1.
    for rev in (parent, child):
        if git(repo, ["cat-file", "-e", f"{rev}^{{commit}}"], check=False).returncode != 0:
            return {"status": "PROVENANCE_FAILURE", "reason": f"missing commit {rev}"}
    paths = actual_paths(repo, parent, child)
    manifest_paths = sorted(rec.get("formal_relevant_changed_paths", []))
    if paths != manifest_paths:
        return {"status": "PROVENANCE_FAILURE", "reason": "changed path list differs from frozen R0 row",
                "actual_paths": paths, "manifest_paths": manifest_paths}

    files = []
    for path in paths:
        ob, oh = blob(repo, parent, path)
        nb, nh = blob(repo, child, path)
        files.append({
            "path": path,
            "kind": path_kind(path),
            "old_sha256": oh,
            "new_sha256": nh,
            "old": source_inventory(ob, path),
            "new": source_inventory(nb, path),
        })
    return {"status": "SOURCE_RETRIEVABLE", "files": files}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--zipcpu", type=Path, required=True)
    ap.add_argument("--riscv-formal", type=Path, required=True)
    ap.add_argument("--opentitan", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    m = json.loads(a.manifest.read_text())
    assert m["g6_preselection_authority"] == G6_PRESELECTION_SHA
    repo_map = {
        "ZipCPU/zipcpu": a.zipcpu,
        "SymbioticEDA/riscv-formal": a.riscv_formal,
        "lowRISC/opentitan": a.opentitan,
    }
    rows = []
    for project in m["projects"]:
        name = project["repository_full_name"]
        repo = repo_map[name]
        for rec in project["selected"]:
            inv = pair_inventory(repo, rec)
            rows.append({
                "repository_full_name": name,
                "parent": rec["parent"],
                "child": rec["child"],
                "sampling_digest": rec["sampling_digest"],
                "chronology_stratum": rec["chronology_stratum"],
                "pre_registered_sentinel": rec["pre_registered_sentinel"],
                "inventory": inv,
            })
    a.out.mkdir(parents=True, exist_ok=True)
    out = {
        "schema": "g6-r1-surface-inventory-v1",
        "g6_preselection_authority": G6_PRESELECTION_SHA,
        "g6_semantic_support_authority": G6_SUPPORT_SHA,
        "commit_messages_consumed": False,
        "proof_status_consumed": False,
        "semantic_relations_computed": False,
        "pair_count": len(rows),
        "rows": rows,
    }
    (a.out / "g6-r1-surface-inventory.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    statuses = {}
    kinds = {}
    for r in rows:
        st = r["inventory"]["status"]
        statuses[st] = statuses.get(st, 0) + 1
        for f in r["inventory"].get("files", []): kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
    summary = {"pair_count": len(rows), "statuses": statuses, "changed_file_kinds": kinds}
    (a.out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))

if __name__ == "__main__":
    main()
