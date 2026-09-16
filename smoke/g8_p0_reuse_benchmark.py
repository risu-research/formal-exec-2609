#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

MANIFEST_SHA = "96217099573c869cfa2232bbb559a4f47d6561590bfe8faa459a6ed6b96a5aed"
R1_SHA = "2f5caabda7625524a790be6ec94d07e5fda02a9a5eaca9875fe8add858c88161"
R2_CANON_SHA = "1d9a39581af5e5fec36e0011a302259c16971f008bc938f08f74bc7b7e6a98df"
BASELINE_P0_P50_MS = 51350.841561
BASELINE_P0_P95_MS = 51477.995299
BASELINE_P1_P95_MS = 11408.357786
BASELINE_P2_P95_MS = 278.285571
BASELINE_CORE_P95_MS = BASELINE_P0_P95_MS + BASELINE_P1_P95_MS + BASELINE_P2_P95_MS
REPS = 5
TMP_RE = re.compile(r"/tmp/tmp[A-Za-z0-9_]+\.smt2")


def sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def nr_pct(xs, p):
    xs = sorted(xs)
    return xs[max(1, math.ceil(len(xs) * p)) - 1]


def stats(xs):
    return {"n": len(xs), "min": min(xs), "p50": nr_pct(xs, .50), "p95": nr_pct(xs, .95), "max": max(xs)}


def parse_maxrss(text: str):
    for ln in text.splitlines():
        if "Maximum resident set size (kbytes):" in ln:
            return int(ln.rsplit(":", 1)[1].strip())
    return None


def timed_cmd(cmd):
    tf = tempfile.NamedTemporaryFile(delete=False)
    tf.close()
    t0 = time.perf_counter_ns()
    p = subprocess.run(["/usr/bin/time", "-v", "-o", tf.name, *cmd], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    wall_ms = (time.perf_counter_ns() - t0) / 1e6
    rtext = Path(tf.name).read_text(errors="replace")
    os.unlink(tf.name)
    if p.returncode != 0:
        raise RuntimeError(f"command failed {cmd}\nSTDOUT\n{p.stdout[-2000:]}\nSTDERR\n{p.stderr[-2000:]}")
    return {"wall_ms": wall_ms, "maxrss_kib": parse_maxrss(rtext), "stdout_tail": p.stdout[-1000:], "stderr_tail": p.stderr[-1000:]}


def canonicalize(x):
    if isinstance(x, dict):
        out = {}
        for k, v in x.items():
            if k == "query" and isinstance(v, str) and "queries/" in v:
                out[k] = "queries/" + v.split("queries/", 1)[1]
            elif k == "stderr_tail" and isinstance(v, str):
                out[k] = TMP_RE.sub("/tmp/<ephemeral>.smt2", v)
            else:
                out[k] = canonicalize(v)
        return out
    if isinstance(x, list):
        return [canonicalize(v) for v in x]
    return x


def canon_digest(p: Path) -> str:
    obj = json.loads(p.read_text())
    b = (json.dumps(canonicalize(obj), indent=2, sort_keys=True) + "\n").encode()
    return hashlib.sha256(b).hexdigest()


def query_map(root: Path) -> dict:
    return {str(p.relative_to(root)).replace(os.sep, "/"): sha_file(p) for p in sorted(root.rglob("*.smt2"))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--zipcpu", required=True)
    ap.add_argument("--riscv-formal", required=True)
    ap.add_argument("--opentitan", required=True)
    ap.add_argument("--reference-r2", required=True)
    ap.add_argument("--cvc5", required=True)
    ap.add_argument("--cold-resource", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    work = out / "work"
    work.mkdir()
    refmap = query_map(Path(a.reference_r2) / "queries")
    if len(refmap) != 32:
        raise RuntimeError("reference query count mismatch")

    cold = json.loads(Path(a.cold_resource).read_text())
    hit_rows, p1_rows, p2_rows = [], [], []
    per_rep = []

    for rep in range(REPS):
        rd = work / f"rep{rep}"
        hitd, p1d, p2d = rd / "hit", rd / "p1", rd / "p2"
        rd.mkdir(parents=True)

        h = timed_cmd([
            "python3", "smoke/g8_p0_history_cache.py", "hit",
            "--zipcpu", a.zipcpu, "--riscv-formal", a.riscv_formal, "--opentitan", a.opentitan,
            "--cache", a.cache, "--out", str(hitd)
        ])
        if sha_file(hitd / "g6-r0-corpus-manifest.json") != MANIFEST_SHA:
            raise RuntimeError("cache-hit manifest mismatch")
        hit_rows.append(h)

        p1 = timed_cmd([
            "python3", "smoke/g6_r1_exact_historical_inventory.py",
            "--manifest", str(hitd / "g6-r0-corpus-manifest.json"),
            "--zipcpu", a.zipcpu, "--riscv-formal", a.riscv_formal, "--opentitan", a.opentitan,
            "--out", str(p1d)
        ])
        if sha_file(p1d / "g6-r1-inventory.json") != R1_SHA:
            raise RuntimeError("optimized P1 identity mismatch")
        p1_rows.append(p1)

        p2 = timed_cmd([
            "python3", "smoke/g6_r2_blind_semantic_classification.py",
            "--r1", str(p1d / "g6-r1-inventory.json"), "--cvc5", a.cvc5, "--out", str(p2d)
        ])
        if canon_digest(p2d / "g6-r2-results.json") != R2_CANON_SHA:
            raise RuntimeError("optimized P2 canonical identity mismatch")
        qmap = query_map(p2d / "queries")
        if qmap != refmap:
            raise RuntimeError("optimized query byte map mismatch")
        p2_rows.append(p2)
        per_rep.append({"rep": rep, "P0_CACHE_HIT": h, "P1_EXTRACT_NORMALIZE": p1, "P2_CLASSIFY_QUERY_BUILD": p2})

    stages = {
        "P0_CACHE_HIT": {"wall_ms": stats([x["wall_ms"] for x in hit_rows]), "maxrss_kib": stats([x["maxrss_kib"] for x in hit_rows])},
        "P1_EXTRACT_NORMALIZE": {"wall_ms": stats([x["wall_ms"] for x in p1_rows]), "maxrss_kib": stats([x["maxrss_kib"] for x in p1_rows])},
        "P2_CLASSIFY_QUERY_BUILD": {"wall_ms": stats([x["wall_ms"] for x in p2_rows]), "maxrss_kib": stats([x["maxrss_kib"] for x in p2_rows])},
    }
    opt_core_p95 = sum(v["wall_ms"]["p95"] for v in stages.values())
    for v in stages.values():
        v["p95_share_of_optimized_core"] = v["wall_ms"]["p95"] / opt_core_p95

    hit_p50 = stages["P0_CACHE_HIT"]["wall_ms"]["p50"]
    hit_p95 = stages["P0_CACHE_HIT"]["wall_ms"]["p95"]
    result = {
        "schema": "g8-p0-exact-reuse-result-v1",
        "cold_build": cold,
        "warm_repetitions": REPS,
        "stages": stages,
        "per_rep": per_rep,
        "exactness": {
            "manifest_sha256": MANIFEST_SHA,
            "r1_sha256": R1_SHA,
            "r2_canonical_sha256": R2_CANON_SHA,
            "query_count": len(refmap),
            "query_map_reproduced_each_rep": True,
        },
        "speedup": {
            "P0_vs_original_p50": BASELINE_P0_P50_MS / hit_p50,
            "P0_vs_original_p95": BASELINE_P0_P95_MS / hit_p95,
            "optimized_core_p95_ms": opt_core_p95,
            "original_core_p95_ms": BASELINE_CORE_P95_MS,
            "repeated_core_p95_speedup": BASELINE_CORE_P95_MS / opt_core_p95,
        },
        "interpretation": {
            "cold_build_is_not_accelerated": True,
            "optimization_scope": "amortized repeated analysis on an identical content-addressed history universe",
            "second_optimization_authorized": False,
        },
    }
    shutil.rmtree(work)
    (out / "g8-p0-reuse-result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    summary = {
        "cold_build": cold,
        "stage_summary": stages,
        "speedup": result["speedup"],
        "exactness": result["exactness"],
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
