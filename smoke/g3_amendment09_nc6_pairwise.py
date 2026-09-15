#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

POS_HEAD = "ec1213c9d954d4f4e40929096a6e16bcd1c75707"
ETHOS_COMMIT = "221641668d75eaffd308e0511d63962cea937110"
AMENDMENT09_FREEZE_SHA = "cfc64a1626ca45193b0d51249d3743b333863e22"
SELECTED = ("semantic_equivalence", "mirror_new", "cell_order")
PROOF_REL = "proofs/semantic_equivalence__mirror_new__cell_order.cpc"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def run_ethos(ethos: Path, proof: Path, cwd: Path) -> dict:
    q = subprocess.run(
        [str(ethos.resolve()), str(proof.resolve())],
        cwd=str(cwd.resolve()),
        capture_output=True,
        text=True,
        timeout=180,
    )
    return {
        "returncode": q.returncode,
        "stdout": q.stdout,
        "stderr": q.stderr,
        "accepted": q.returncode == 0 and q.stdout.strip() == "correct",
    }


def verify_signature_manifest(pos: Path) -> dict:
    manifest = pos / "cpc-signatures.sha256"
    if not manifest.is_file():
        raise RuntimeError("missing cpc-signatures.sha256")
    checked = []
    for raw in manifest.read_text().splitlines():
        if not raw.strip():
            continue
        m = re.fullmatch(r"([0-9a-f]{64})\s+(.+)", raw.strip())
        if not m:
            raise RuntimeError(f"malformed signature manifest line: {raw!r}")
        expected, recorded = m.groups()
        rel = recorded[4:] if recorded.startswith("out/") else recorded
        p = pos / rel
        if not p.is_file():
            raise RuntimeError(f"signature closure missing: {rel}")
        actual = sha(p)
        if actual != expected:
            raise RuntimeError(f"signature hash mismatch: {rel}")
        checked.append({"path": rel, "sha256": actual})
    if not checked:
        raise RuntimeError("empty signature closure")
    return {
        "manifest_sha256": sha(manifest),
        "file_count": len(checked),
        "all_hashes_verified": True,
        "files": checked,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--positive", required=True)
    ap.add_argument("--ethos", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    pos = Path(args.positive)
    ethos = Path(args.ethos)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if (pos / "runner.commit.txt").read_text().strip() != POS_HEAD:
        raise RuntimeError("positive authority head drift")
    if (pos / "ethos.commit.txt").read_text().strip() != ETHOS_COMMIT:
        raise RuntimeError("positive artifact Ethos revision drift")

    rows = list(csv.DictReader((pos / "METAMORPHIC_CERTIFICATES.tsv").open(), delimiter="\t"))
    if len(rows) != 120 or not all(r["status"] == "UNSAT_CERTIFIED_CPC" for r in rows):
        raise RuntimeError("positive certificate matrix drift")
    selected = sorted(rows, key=lambda r: (r["lane"], r["label"], r["family"]))[0]
    if (selected["lane"], selected["label"], selected["family"]) != SELECTED:
        raise RuntimeError("selected proof identity drift")

    sig = verify_signature_manifest(pos)
    src = pos / PROOF_REL
    if not src.is_file():
        raise RuntimeError("selected intact proof missing")
    source_lines = src.read_text().splitlines()
    include_lines = [line for line in source_lines if line.startswith("(include ")]
    if include_lines != [
        '(include "../signatures/Cpc.eo")',
        '(include "../signatures/expert/CpcExpert.eo")',
    ]:
        raise RuntimeError(f"selected proof include drift: {include_lines!r}")
    for line in include_lines:
        rel = re.fullmatch(r'\(include "([^"]+)"\)', line).group(1)
        if not (src.parent / rel).resolve().is_file():
            raise RuntimeError(f"include target missing before checker run: {rel}")

    # Calibration first: the intact real proof MUST be accepted in this exact environment.
    intact = run_ethos(ethos, src, pos)

    corrupted_lines = list(source_lines)
    changed_index = None
    for i in range(len(corrupted_lines) - 1, -1, -1):
        if corrupted_lines[i].startswith("(step ") and " false :rule " in corrupted_lines[i]:
            corrupted_lines[i] = corrupted_lines[i].replace(" false :rule ", " true :rule ", 1)
            changed_index = i
            break
    if changed_index is None:
        raise RuntimeError("no terminal false goal found for governed mutation")

    diff_indices = [i for i, (a, b) in enumerate(zip(source_lines, corrupted_lines)) if a != b]
    if len(source_lines) != len(corrupted_lines) or diff_indices != [changed_index]:
        raise RuntimeError("mutation changed more than one proof line")
    before = source_lines[changed_index]
    after = corrupted_lines[changed_index]
    if before.replace(" false :rule ", " true :rule ", 1) != after:
        raise RuntimeError("mutation is not exactly terminal false-to-true replacement")
    if [line for line in corrupted_lines if line.startswith("(include ")] != include_lines:
        raise RuntimeError("mutation changed proof includes")

    # Critical fix relative to the historical defect: place the corrupted proof beside
    # the intact proof so both resolve the SAME ../signatures closure.
    corrupt = src.parent / "semantic_equivalence__mirror_new__cell_order__amendment09_corrupted.cpc"
    corrupt.write_text("\n".join(corrupted_lines) + "\n")
    corrupted = run_ethos(ethos, corrupt, pos)

    corrupt_combined = (corrupted["stdout"] + "\n" + corrupted["stderr"]).lower()
    infra_markers = [
        "couldn't open file",
        "no such file or directory",
        "failed to open file",
    ]
    infra_failure = any(marker in corrupt_combined for marker in infra_markers)

    same_signature_environment = (
        src.parent.resolve() == corrupt.parent.resolve()
        and [line for line in corrupt.read_text().splitlines() if line.startswith("(include ")] == include_lines
        and sig["all_hashes_verified"]
    )
    exactly_one_governed_mutation = diff_indices == [changed_index]
    pairwise_pass = bool(
        intact["accepted"]
        and not corrupted["accepted"]
        and not infra_failure
        and same_signature_environment
        and exactly_one_governed_mutation
    )

    shutil.copy2(src, out / "selected-intact.cpc")
    shutil.copy2(corrupt, out / "selected-corrupted.cpc")
    (out / "intact.stdout.txt").write_text(intact["stdout"])
    (out / "intact.stderr.txt").write_text(intact["stderr"])
    (out / "corrupted.stdout.txt").write_text(corrupted["stdout"])
    (out / "corrupted.stderr.txt").write_text(corrupted["stderr"])

    result = {
        "schema": "g3-amendment09-pairwise-nc6-v1",
        "authority": "PROSPECTIVE_AMENDMENT09_AUTHORITY",
        "amendment09_freeze_sha": AMENDMENT09_FREEZE_SHA,
        "positive_head": POS_HEAD,
        "ethos_commit": ETHOS_COMMIT,
        "selected": {
            "lane": selected["lane"],
            "label": selected["label"],
            "family": selected["family"],
            "status": selected["status"],
            "proof_relpath": PROOF_REL,
        },
        "signature_closure": sig,
        "source_proof_sha256": sha(src),
        "corrupted_proof_sha256": sha(corrupt),
        "mutation": {
            "changed_line_1based": changed_index + 1,
            "before": before,
            "after": after,
            "exactly_one_governed_mutation": exactly_one_governed_mutation,
            "include_lines_identical": True,
        },
        "intact": {
            "returncode": intact["returncode"],
            "accepted": intact["accepted"],
            "stdout_sha256": hashlib.sha256(intact["stdout"].encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(intact["stderr"].encode()).hexdigest(),
        },
        "corrupted": {
            "returncode": corrupted["returncode"],
            "accepted": corrupted["accepted"],
            "infrastructure_failure_marker": infra_failure,
            "stdout_sha256": hashlib.sha256(corrupted["stdout"].encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(corrupted["stderr"].encode()).hexdigest(),
        },
        "same_signature_environment": same_signature_environment,
        "intact_accepted": intact["accepted"],
        "corrupted_accepted": corrupted["accepted"],
        "pass": pairwise_pass,
        "history": "Run 34974344967 NC6 remains inadmissible for checker sensitivity because its corrupted proof was relocated away from the sealed signature closure.",
    }
    write_json(out / "G3_AMENDMENT09_NC6.json", result)
    print(json.dumps({
        "intact_accepted": result["intact_accepted"],
        "corrupted_accepted": result["corrupted_accepted"],
        "same_signature_environment": result["same_signature_environment"],
        "exactly_one_governed_mutation": result["mutation"]["exactly_one_governed_mutation"],
        "infrastructure_failure_marker": result["corrupted"]["infrastructure_failure_marker"],
        "signature_files": sig["file_count"],
        "pass": result["pass"],
    }, sort_keys=True))
    if not pairwise_pass:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
