#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

EXPECTED_MANIFEST_SHA = "96217099573c869cfa2232bbb559a4f47d6561590bfe8faa459a6ed6b96a5aed"
P0_RUNNER_BLOB = "9bae882c5d334c54f2b7d84e24615d85b480a2c5"
G6_PRESELECTION = "4c8218fe664816f01653e483a6f205428eac33c3"
EVAL_FREEZE = "7ec2f62f2c68ac54879f1e25ec014e89a95b6ba8"
G5_PARENT = "ba7c505dbbe96d16e4f5a35606adea52f2b4aa15"
CUTOFF = "2026-09-12T23:59:59Z"
MAX_SELECTED = 48
REPOS = {
    "ZipCPU/zipcpu": "zipcpu",
    "SymbioticEDA/riscv-formal": "riscv-formal",
    "lowRISC/opentitan": "opentitan",
}
EXPECTED_ORIGINS = {
    "ZipCPU/zipcpu": "github.com/ZipCPU/zipcpu",
    "SymbioticEDA/riscv-formal": "github.com/SymbioticEDA/riscv-formal",
    "lowRISC/opentitan": "github.com/lowRISC/opentitan",
}


def run(cmd):
    p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError(f"command failed {cmd}: {p.stderr[-2000:]}")
    return p.stdout.strip()


def sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def normalize_origin(url: str) -> str:
    u = url.strip()
    if u.endswith(".git"):
        u = u[:-4]
    if u.startswith("https://"):
        u = u[len("https://"):]
    elif u.startswith("http://"):
        u = u[len("http://"):]
    elif u.startswith("git@github.com:"):
        u = "github.com/" + u[len("git@github.com:"):]
    return u.rstrip("/")


def git_head(repo: Path) -> str:
    return run(["git", "-C", str(repo), "rev-parse", "HEAD"])


def git_origin(repo: Path) -> str:
    return normalize_origin(run(["git", "-C", str(repo), "remote", "get-url", "origin"]))


def git_blob(path: Path) -> str:
    return run(["git", "hash-object", str(path)])


def semantic_key(args) -> dict:
    repo_paths = {
        "ZipCPU/zipcpu": Path(args.zipcpu),
        "SymbioticEDA/riscv-formal": Path(args.riscv_formal),
        "lowRISC/opentitan": Path(args.opentitan),
    }
    projects = {}
    for name, path in repo_paths.items():
        origin = git_origin(path)
        if origin != EXPECTED_ORIGINS[name]:
            raise RuntimeError(f"origin mismatch {name}: {origin}")
        projects[name] = {"head": git_head(path), "origin": origin}
    blob = git_blob(Path(args.p0_runner))
    if blob != P0_RUNNER_BLOB:
        raise RuntimeError(f"P0 runner blob mismatch {blob}")
    return {
        "schema": "g8-p0-content-addressed-key-v1",
        "projects": projects,
        "p0_runner_git_blob": blob,
        "g6_preselection_authority": G6_PRESELECTION,
        "evaluation_constitution": EVAL_FREEZE,
        "g5_parent": G5_PARENT,
        "cutoff_inclusive_utc": CUTOFF,
        "max_selected_per_project": MAX_SELECTED,
        "expected_manifest_sha256": EXPECTED_MANIFEST_SHA,
    }


def key_digest(key: dict) -> str:
    b = (json.dumps(key, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return hashlib.sha256(b).hexdigest()


def verify_manifest(manifest: Path, key: dict) -> dict:
    got = sha_file(manifest)
    if got != EXPECTED_MANIFEST_SHA:
        raise RuntimeError(f"manifest hash mismatch {got}")
    obj = json.loads(manifest.read_text())
    if obj.get("cutoff_inclusive_utc") != CUTOFF:
        raise RuntimeError("manifest cutoff mismatch")
    if obj.get("max_selected_per_project") != MAX_SELECTED:
        raise RuntimeError("manifest selection policy mismatch")
    if obj.get("g6_preselection_authority") != G6_PRESELECTION:
        raise RuntimeError("manifest preselection authority mismatch")
    if obj.get("evaluation_freeze") != EVAL_FREEZE:
        raise RuntimeError("manifest evaluation authority mismatch")
    if obj.get("g5_parent") != G5_PARENT:
        raise RuntimeError("manifest G5 parent mismatch")
    expected_heads = {n: v["head"] for n, v in key["projects"].items()}
    seen = {p["repository_full_name"]: p["local_head"] for p in obj["projects"]}
    if seen != expected_heads:
        raise RuntimeError(f"manifest/repository HEAD mismatch: {seen} != {expected_heads}")
    if obj.get("selected_total") != 144:
        raise RuntimeError("manifest denominator mismatch")
    return obj


def build(args):
    key = semantic_key(args)
    manifest = Path(args.manifest)
    verify_manifest(manifest, key)
    out = Path(args.cache)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copyfile(manifest, out / "g6-r0-corpus-manifest.json")
    meta = {
        "schema": "g8-p0-history-cache-v1",
        "semantic_key": key,
        "semantic_key_sha256": key_digest(key),
        "manifest_sha256": EXPECTED_MANIFEST_SHA,
    }
    (out / "cache-key.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": "BUILT", "semantic_key_sha256": meta["semantic_key_sha256"], "manifest_sha256": EXPECTED_MANIFEST_SHA}, sort_keys=True))


def hit(args):
    current = semantic_key(args)
    cache = Path(args.cache)
    meta_path = cache / "cache-key.json"
    manifest = cache / "g6-r0-corpus-manifest.json"
    if not meta_path.exists() or not manifest.exists():
        raise SystemExit("MISS: cache object incomplete")
    meta = json.loads(meta_path.read_text())
    if meta.get("semantic_key") != current or meta.get("semantic_key_sha256") != key_digest(current):
        raise SystemExit("MISS: semantic key mismatch")
    verify_manifest(manifest, current)
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copyfile(manifest, out / "g6-r0-corpus-manifest.json")
    hit_meta = {
        "schema": "g8-p0-history-cache-hit-v1",
        "status": "HIT",
        "semantic_key_sha256": meta["semantic_key_sha256"],
        "manifest_sha256": EXPECTED_MANIFEST_SHA,
    }
    (out / "cache-hit.json").write_text(json.dumps(hit_meta, indent=2, sort_keys=True) + "\n")
    print(json.dumps(hit_meta, sort_keys=True))


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    for name in ("build", "hit"):
        p = sp.add_parser(name)
        p.add_argument("--zipcpu", required=True)
        p.add_argument("--riscv-formal", required=True)
        p.add_argument("--opentitan", required=True)
        p.add_argument("--p0-runner", default="smoke/g6_r0_blind_corpus_seal.py")
        p.add_argument("--cache", required=True)
        if name == "build":
            p.add_argument("--manifest", required=True)
        else:
            p.add_argument("--out", required=True)
    args = ap.parse_args()
    if args.cmd == "build":
        build(args)
    else:
        hit(args)


if __name__ == "__main__":
    main()
