#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

CORPUS_SHA256 = "96217099573c869cfa2232bbb559a4f47d6561590bfe8faa459a6ed6b96a5aed"
CORPUS_AUTHORITY = "4c2f1637889f4049ddf69b5e4ca44b4cf5f7270f"
R1_R2_PROTOCOL = "50c7b30fa3a404176108fc5f155c09cc9851578b"
SEMANTIC_SUPPORT = "538189291d09284303c27d717ca8b0b82e69b1e1"
AMENDMENT01 = "99fd4bfb74e8b2ed5cd0b440e4490944feb99c55"

SOURCE_SUFFIXES = {".v", ".sv", ".vh", ".svh", ".vhd", ".vhdl"}
CONFIG_SUFFIXES = {".sby", ".hjson", ".json", ".yaml", ".yml", ".core", ".cfg", ".toml", ".ini"}
FORMAL_WORDS = ("assert", "assume", "restrict", "cover", "formal", "fpv", "sby", "jasper", "prove")
FORMAL_MACRO_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*(?:ASSERT|ASSUME|COVER|RESTRICT)[A-Za-z0-9_]*)\s*\(", re.I)
NATIVE_HEAD_RE = re.compile(r"\b(assert|assume|cover|restrict)\s+(?:property\s*)?\(", re.I)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    return sha256_bytes(p.read_bytes())


def run(cmd, cwd=None, check=True, timeout=180):
    p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError(f"command failed rc={p.returncode}: {' '.join(map(str,cmd))}\n{p.stderr.decode(errors='replace')[-4000:]}")
    return p


def git_text(repo: Path, args, check=True) -> str:
    return run(["git", "-C", str(repo), *args], check=check).stdout.decode("utf-8", errors="replace")


def git_blob(repo: Path, rev: str, path: str):
    p = run(["git", "-C", str(repo), "show", f"{rev}:{path}"], check=False)
    if p.returncode != 0:
        return None
    return p.stdout


def norm_ws(s: str) -> str:
    return " ".join(s.split())


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    return text


def balanced_call(text: str, open_pos: int):
    # Amendment01: Verilog apostrophe is numeric-literal syntax, not a string quote.
    depth = 0
    quote = False
    esc = False
    for i in range(open_pos, len(text)):
        c = text[i]
        if quote:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                quote = False
            continue
        if c == '"':
            quote = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[open_pos:i+1]
    return None


def source_surface(data: bytes):
    text = data.decode("utf-8", errors="replace")
    clean = strip_comments(text)
    candidates = []
    macros = []
    for m in FORMAL_MACRO_RE.finditer(clean):
        open_pos = clean.find("(", m.start())
        call = balanced_call(clean, open_pos)
        if call is None:
            continue
        raw = clean[m.start():open_pos] + call
        canon = norm_ws(raw)
        macros.append(m.group(1))
        candidates.append({"kind":"macro", "head":m.group(1), "canonical":canon, "sha256":sha256_bytes(canon.encode())})
    for m in NATIVE_HEAD_RE.finditer(clean):
        open_pos = clean.find("(", m.start())
        call = balanced_call(clean, open_pos)
        if call is None:
            continue
        raw = clean[m.start():open_pos] + call
        canon = norm_ws(raw)
        candidates.append({"kind":"native", "head":m.group(1).lower(), "canonical":canon, "sha256":sha256_bytes(canon.encode())})
    token_counts = {w: len(re.findall(rf"\b{re.escape(w)}\b", clean, flags=re.I)) for w in FORMAL_WORDS}
    multiset = Counter(x["sha256"] for x in candidates)
    swallowed = []
    for c in candidates:
        s = c["canonical"]
        repeated_macro = c["kind"] == "macro" and s.count("`" + c["head"]) > 1
        if "; always" in s or repeated_macro or len(s) > 1000:
            swallowed.append(c["sha256"])
    return {
        "candidate_count": len(candidates),
        "candidate_multiset": dict(sorted(multiset.items())),
        "candidate_multiset_sha256": sha256_bytes(json.dumps(dict(sorted(multiset.items())), sort_keys=True).encode()),
        "macro_names": sorted(set(macros)),
        "token_counts": token_counts,
        "lexical_sanity_failures": swallowed,
        "candidates": candidates,
    }


def parse_sby(data: bytes):
    text = data.decode("utf-8", errors="replace")
    section = None
    sections = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\[([^]]+)\]$", line)
        if m:
            section = m.group(1).strip().lower()
            sections.setdefault(section, [])
            continue
        if section is not None:
            sections[section].append(norm_ws(line))
    tasks = sections.get("tasks", [])
    options = sections.get("options", [])
    engines = sections.get("engines", [])
    script = sections.get("script", [])
    files = sections.get("files", [])
    depths = []
    modes = []
    for line in options:
        toks = line.split()
        if not toks:
            continue
        if "depth" in toks:
            i = toks.index("depth")
            if i + 1 < len(toks): depths.append(" ".join(toks[:i+2]))
        if "mode" in toks:
            i = toks.index("mode")
            if i + 1 < len(toks): modes.append(" ".join(toks[:i+2]))
    obj = {"tasks":tasks, "options":options, "engines":engines, "script":script, "files":files,
           "depth_lines":depths, "mode_lines":modes}
    obj["normalized_sha256"] = sha256_bytes(json.dumps(obj, sort_keys=True).encode())
    return obj


def config_surface(path: str, data: bytes):
    suf = Path(path).suffix.lower()
    if suf == ".sby":
        return {"kind":"sby", "parsed":parse_sby(data)}
    text = data.decode("utf-8", errors="replace")
    rows = []
    for raw in text.splitlines():
        line = norm_ws(raw)
        low = line.lower()
        if line and any(w in low for w in ("formal", "fpv", "jasper", "depth", "engine", "task", "mode", "prove", "fileset")):
            rows.append(line)
    rows = sorted(rows)
    return {"kind":"generic-config", "relevant_lines":rows,
            "relevant_lines_sha256":sha256_bytes(json.dumps(rows, sort_keys=True).encode())}


def blob_record(path: str, data):
    if data is None:
        return {"exists":False}
    rec = {"exists":True, "size":len(data), "sha256":sha256_bytes(data)}
    suf = Path(path).suffix.lower()
    if suf in SOURCE_SUFFIXES:
        rec["source_surface"] = source_surface(data)
    if suf in CONFIG_SUFFIXES:
        rec["config_surface"] = config_surface(path, data)
    return rec


def name_status(repo: Path, parent: str, child: str):
    out = run(["git","-C",str(repo),"diff","--name-status","-z",parent,child]).stdout
    parts = out.split(b"\0")
    rows=[]; i=0
    while i < len(parts) and parts[i]:
        status = parts[i].decode(errors="replace"); i += 1
        if status.startswith("R") or status.startswith("C"):
            oldp = parts[i].decode(errors="replace"); newp = parts[i+1].decode(errors="replace"); i += 2
            rows.append({"status":status,"old_path":oldp,"new_path":newp})
        else:
            path = parts[i].decode(errors="replace"); i += 1
            rows.append({"status":status,"path":path})
    return rows


def summarize_pair(repo: Path, pair: dict):
    parent, child = pair["parent"], pair["child"]
    parents = git_text(repo,["rev-list","--parents","-n","1",child]).strip().split()
    provenance_ok = len(parents)==2 and parents[0]==child and parents[1]==parent
    changed = name_status(repo,parent,child) if provenance_ok else []
    changed_paths=set()
    for r in changed:
        changed_paths.update(x for x in (r.get("path"),r.get("old_path"),r.get("new_path")) if x)
    relpaths = list(pair.get("formal_relevant_changed_paths",[]))
    records=[]; retrieval_fail=[]
    for path in relpaths:
        old = git_blob(repo,parent,path)
        new = git_blob(repo,child,path)
        if old is None and new is None:
            retrieval_fail.append(path)
        records.append({"path":path,"changed_path_present":path in changed_paths,
                        "old":blob_record(path,old),"new":blob_record(path,new)})
    state = "SOURCE_RETRIEVABLE" if provenance_ok and not retrieval_fail else ("PROVENANCE_FAILURE" if not provenance_ok else "MISSING_DEPENDENCY")
    agg=[]; lexical_failures=[]
    for r in records:
        for side in ("old","new"):
            ss=r[side].get("source_surface")
            if ss:
                agg.extend([f"{side}:{k}:{v}" for k,v in ss["candidate_multiset"].items()])
                for h in ss.get("lexical_sanity_failures",[]):
                    lexical_failures.append({"path":r["path"],"side":side,"candidate_sha256":h})
    return {
        "repository_full_name":pair["repository_full_name"],"parent":parent,"child":child,
        "sampling_digest":pair["sampling_digest"],"chronology_stratum":pair["chronology_stratum"],
        "pre_registered_sentinel":pair["pre_registered_sentinel"],"provenance_ok":provenance_ok,
        "actual_parent_vector":parents,"machine_state":state,"retrieval_failures":retrieval_fail,
        "changed_path_count":len(changed_paths),"changed_name_status":changed,
        "formal_relevant_path_count":len(relpaths),"formal_records":records,
        "lexical_sanity_failures":lexical_failures,
        "aggregate_candidate_surface_sha256":sha256_bytes("\n".join(sorted(agg)).encode()),
    }


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--manifest",required=True); ap.add_argument("--zipcpu",required=True)
    ap.add_argument("--riscv-formal",required=True); ap.add_argument("--opentitan",required=True); ap.add_argument("--out",required=True)
    a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    mp=Path(a.manifest)
    if sha256_file(mp) != CORPUS_SHA256:
        raise RuntimeError(f"frozen corpus hash mismatch: {sha256_file(mp)}")
    manifest=json.loads(mp.read_text())
    repos={"ZipCPU/zipcpu":Path(a.zipcpu),"SymbioticEDA/riscv-formal":Path(a.riscv_formal),"lowRISC/opentitan":Path(a.opentitan)}
    rows=[]
    for proj in manifest["projects"]:
        repo=repos[proj["repository_full_name"]]
        for pair in proj["selected"]:
            rows.append(summarize_pair(repo,pair))
    rows.sort(key=lambda r:(r["repository_full_name"],r["sampling_digest"]))
    counts=Counter(r["machine_state"] for r in rows)
    lexical_failures=sum(len(r["lexical_sanity_failures"]) for r in rows)
    byproj={}
    for name in repos:
        rr=[r for r in rows if r["repository_full_name"]==name]
        byproj[name]={"total":len(rr),"states":dict(Counter(x["machine_state"] for x in rr)),
                      "sentinels":sum(x["pre_registered_sentinel"] for x in rr),
                      "lexical_sanity_failures":sum(len(x["lexical_sanity_failures"]) for x in rr)}
    result={"schema":"g6-r1-exact-historical-inventory-v1-amendment01","corpus_sha256":CORPUS_SHA256,
            "corpus_authority":CORPUS_AUTHORITY,"r1_r2_protocol":R1_R2_PROTOCOL,"semantic_support":SEMANTIC_SUPPORT,
            "amendment01":AMENDMENT01,"pair_count":len(rows),"state_counts":dict(counts),
            "lexical_sanity_failures":lexical_failures,"by_project":byproj,"rows":rows}
    (out/"g6-r1-inventory.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    summary={"pair_count":len(rows),"state_counts":dict(counts),"lexical_sanity_failures":lexical_failures,"by_project":byproj,
             "inventory_sha256":sha256_file(out/"g6-r1-inventory.json")}
    (out/"summary.json").write_text(json.dumps(summary,indent=2,sort_keys=True)+"\n")
    print(json.dumps(summary,sort_keys=True))
    if len(rows)!=144:
        raise RuntimeError(f"expected 144 frozen rows, got {len(rows)}")
    if any(not r["provenance_ok"] for r in rows):
        raise RuntimeError("R1 provenance failure present")
    if lexical_failures:
        raise RuntimeError(f"R1 lexical sanity failure count={lexical_failures}")

if __name__ == "__main__":
    main()
