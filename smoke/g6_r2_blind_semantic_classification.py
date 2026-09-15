#!/usr/bin/env python3
from __future__ import annotations

import argparse, hashlib, json, re, subprocess, tempfile
from collections import Counter
from pathlib import Path
import z3
from z3.z3util import get_vars

CORPUS_SHA256="96217099573c869cfa2232bbb559a4f47d6561590bfe8faa459a6ed6b96a5aed"
CORPUS_AUTHORITY="4c2f1637889f4049ddf69b5e4ca44b4cf5f7270f"
R1_CLOSURE="f5892bf45dc0b2cbacb303b44a318ef05294a1da"
R1_INVENTORY_SHA256="2f5caabda7625524a790be6ec94d07e5fda02a9a5eaca9875fe8add858c88161"
R1_AMENDMENT01="99fd4bfb74e8b2ed5cd0b440e4490944feb99c55"
R2_PROTOCOL="50c7b30fa3a404176108fc5f155c09cc9851578b"
SEMANTIC_SUPPORT="538189291d09284303c27d717ca8b0b82e69b1e1"

SOURCE_SUFFIXES={".v",".sv",".vh",".svh",".vhd",".vhdl"}
DIRECT_BOOL_RE=re.compile(r"^!?[A-Za-z_$][A-Za-z0-9_$.]*$")


def sha256_bytes(b:bytes)->str:return hashlib.sha256(b).hexdigest()
def sha256_file(p:Path)->str:return sha256_bytes(p.read_bytes())
def canon(s:str)->str:return " ".join(s.split())

def require(x,msg):
    if not x: raise RuntimeError(msg)

def solve_cvc5(binary,text,timeout=60):
    with tempfile.NamedTemporaryFile("w",suffix=".smt2",delete=False) as f:
        f.write(text); name=f.name
    try:
        p=subprocess.run([binary,name],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
        first=(p.stdout.strip().splitlines() or [""])[0].strip()
        require(p.returncode==0,f"cvc5 rc={p.returncode}: {(p.stdout+p.stderr)[-1200:]}")
        require(first in ("sat","unsat"),f"cvc5 unexpected {first!r}")
        return {"result":first,"stdout_tail":p.stdout[-400:],"stderr_tail":p.stderr[-400:]}
    finally: Path(name).unlink(missing_ok=True)

def emit_query(pred,path:Path):
    s=z3.Solver(); s.add(pred); text=s.to_smt2()
    path.parent.mkdir(parents=True,exist_ok=True); path.write_text(text)
    return text,sha256_bytes(text.encode())

def z3_result(pred):
    s=z3.Solver(); s.set(timeout=30000); s.add(pred); r=s.check()
    require(r!=z3.unknown,"Z3 unknown: "+s.reason_unknown())
    if r==z3.unsat:return "unsat",None
    m=s.model(); witness={}
    for v in sorted(get_vars(pred),key=lambda x:x.sexpr()):
        require(z3.is_bool(v),f"non-Bool abstraction var {v}")
        witness[v.sexpr()]=bool(z3.is_true(m.eval(v,model_completion=True)))
    return "sat",witness

def bind_bool_query(text,witness):
    marker="(check-sat)"; require(text.count(marker)==1,"bad query marker")
    rows=[f"(assert (= {sym} {'true' if witness[sym] else 'false'}))" for sym in sorted(witness)]
    return text.replace(marker,"\n".join(rows)+"\n"+marker,1)

def dual_check(pred,path:Path,cvc5:str,permit_sat_witness:bool):
    text,qsha=emit_query(pred,path); zr,w=z3_result(pred); cr=solve_cvc5(cvc5,text)
    require(cr["result"]==zr,f"solver disagreement {path}: {zr}/{cr['result']}")
    fixed=None
    if zr=="sat" and permit_sat_witness:
        ft=bind_bool_query(text,w); fp=path.with_name(path.stem+"__fixed.smt2"); fp.write_text(ft)
        fr=solve_cvc5(cvc5,ft); require(fr["result"]=="sat",f"fixed witness rejected {path}")
        fixed={"query":str(fp),"query_sha256":sha256_bytes(ft.encode()),"cvc5":fr,"witness":w}
    return {"query":str(path),"query_sha256":qsha,"z3":zr,"cvc5":cr,"fixed_replay":fixed}

# ---------- Boolean schema abstraction ----------
def strip_outer(s):
    s=s.strip()
    while s.startswith("(") and s.endswith(")"):
        d=0; ok=True; quote=False; esc=False
        for i,c in enumerate(s):
            if quote:
                if esc:esc=False
                elif c=="\\":esc=True
                elif c=='"':quote=False
                continue
            if c=='"':quote=True;continue
            if c=='(':d+=1
            elif c==')':
                d-=1
                if d==0 and i!=len(s)-1:ok=False;break
        if ok and d==0:s=s[1:-1].strip()
        else:break
    return s

def split_top(s,op):
    d=0; quote=False; esc=False; out=[]; last=0; i=0
    while i<len(s):
        c=s[i]
        if quote:
            if esc:esc=False
            elif c=="\\":esc=True
            elif c=='"':quote=False
            i+=1;continue
        if c=='"':quote=True;i+=1;continue
        if c=='(':d+=1;i+=1;continue
        if c==')':d-=1;i+=1;continue
        if d==0 and s.startswith(op,i):
            out.append(s[last:i]); last=i+len(op); i=last; continue
        i+=1
    if out:
        out.append(s[last:]); return out
    return None

def atom_symbol(atom,atoms):
    a=canon(strip_outer(atom)); key=sha256_bytes(a.encode())[:20]
    atoms[key]=a; return z3.Bool("a_"+key)

def parse_bool_schema(s,atoms):
    s=strip_outer(s)
    parts=split_top(s,"||")
    if parts:return z3.Or(*[parse_bool_schema(x,atoms) for x in parts])
    parts=split_top(s,"&&")
    if parts:return z3.And(*[parse_bool_schema(x,atoms) for x in parts])
    if s.startswith("!") and not s.startswith("!="):
        return z3.Not(parse_bool_schema(s[1:].strip(),atoms))
    return atom_symbol(s,atoms)

def expr_from_native(c):
    if c.get("kind")!="native" or c.get("head") not in ("assume","assert"):return None
    s=c["canonical"]
    m=re.match(r"^(assume|assert)\s*\((.*)\)$",s,re.S)
    return m.group(2).strip() if m else None

def direct_literal(expr):
    e=strip_outer(expr)
    return e if DIRECT_BOOL_RE.fullmatch(e) else None

def literal_pred(expr):
    e=strip_outer(expr); neg=e.startswith("!"); name=e[1:] if neg else e
    require(re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$.]*",name) is not None,"not direct bool")
    v=z3.Bool("g_"+sha256_bytes(name.encode())[:20])
    return z3.Not(v) if neg else v, {v.sexpr():name}

def classify_axis(native_old,native_new,macro_old,macro_new,axis,outdir,cvc5):
    # Any assertion/assumption macro on the governed axis makes generic source expansion unresolved.
    if macro_old or macro_new:
        return {"relation":None,"status":"UNRESOLVED_MACRO_SEMANTICS","macro_counts":[len(macro_old),len(macro_new)]}
    old_expr=[expr_from_native(c) for c in native_old]; new_expr=[expr_from_native(c) for c in native_new]
    old_expr=[x for x in old_expr if x is not None]; new_expr=[x for x in new_expr if x is not None]
    if not old_expr and not new_expr:
        return {"relation":None,"status":"NO_SOURCE_OBLIGATION_EVIDENCE"}
    atoms={}
    oldf=z3.And(*[parse_bool_schema(x,atoms) for x in old_expr]) if old_expr else z3.BoolVal(True)
    newf=z3.And(*[parse_bool_schema(x,atoms) for x in new_expr]) if new_expr else z3.BoolVal(True)
    old_only=z3.And(oldf,z3.Not(newf)); new_only=z3.And(newf,z3.Not(oldf))
    a=dual_check(old_only,outdir/f"{axis}__old_only.smt2",cvc5,False)
    b=dual_check(new_only,outdir/f"{axis}__new_only.smt2",cvc5,False)
    osat=a["z3"]=="sat"; nsat=b["z3"]=="sat"
    # UNSAT in the less-constrained propositional schema is sound for concrete formulas.
    if not osat and not nsat:
        relation=("E_EQ" if axis=="environment" else "G_EQ")
        return {"relation":relation,"status":"S2_SCHEMA_EQ_CROSS_SOLVER","old_only":a,"new_only":b,"atom_map":atoms}
    # Strict one-sided-empty case: a direct Boolean literal conjunct provides a concrete separating witness.
    if not old_expr and new_expr:
        dl=next((direct_literal(x) for x in new_expr if direct_literal(x)),None)
        if dl:
            lp,names=literal_pred(dl); witness_pred=z3.Not(lp)
            w=dual_check(witness_pred,outdir/f"{axis}__grounded_separator.smt2",cvc5,True)
            relation=("E_CONTRACT" if axis=="environment" else "G_STRENGTHEN")
            return {"relation":relation,"status":"S2_GROUNDED_STRICT_FROM_EMPTY","separator_literal":dl,"separator_names":names,"separator_replay":w,"old_only":a,"new_only":b,"atom_map":atoms}
    if old_expr and not new_expr:
        dl=next((direct_literal(x) for x in old_expr if direct_literal(x)),None)
        if dl:
            lp,names=literal_pred(dl); witness_pred=z3.Not(lp)
            w=dual_check(witness_pred,outdir/f"{axis}__grounded_separator.smt2",cvc5,True)
            relation=("E_EXPAND" if axis=="environment" else "G_WEAKEN")
            return {"relation":relation,"status":"S2_GROUNDED_STRICT_TO_EMPTY","separator_literal":dl,"separator_names":names,"separator_replay":w,"old_only":a,"new_only":b,"atom_map":atoms}
    return {"relation":None,"status":"ABSTRACT_DIFFERENCE_UNGROUNDED","old_only":a,"new_only":b,"atom_map":atoms}

# ---------- exact SBY structural surface ----------
def sby_parsed(rec,side):return rec[side].get("config_surface",{}).get("parsed")
def task_names(p):
    if p is None:return set()
    ts=p.get("tasks",[])
    return {x.split()[0] for x in ts if x.split()} if ts else {"__default__"}
def depth_map(p):
    if p is None:return {}
    d={}
    for line in p.get("depth_lines",[]):
        m=re.fullmatch(r"(?:(\S+):\s*)?depth\s+(\d+)",line)
        if m:d[m.group(1) or "*"]=int(m.group(2))
    return d

def structural_axes(row):
    old_tasks=set(); new_tasks=set(); od={}; nd={}; old_depth_keys=set();new_depth_keys=set(); sby_count=0
    for rec in row["formal_records"]:
        if not rec["path"].endswith(".sby"):continue
        sby_count+=1; o=sby_parsed(rec,"old"); n=sby_parsed(rec,"new"); path=rec["path"]
        if o is not None:
            old_tasks |= {(path,t) for t in task_names(o)}
            for k,v in depth_map(o).items():od[(path,k)]=v
        if n is not None:
            new_tasks |= {(path,t) for t in task_names(n)}
            for k,v in depth_map(n).items():nd[(path,k)]=v
    if not sby_count:
        return {"T":None,"R":None,"status":"NO_SBY_STRUCTURAL_EVIDENCE"}
    rem=old_tasks-new_tasks; add=new_tasks-old_tasks
    if rem and add:t="T_INCOMPARABLE"
    elif rem:t="TASK_DROP"
    elif add:t="TASK_ADDITION"
    else:t="TASK_SET_EQ"
    inc=dec=False; changes=[]
    for k in sorted(set(od)&set(nd)):
        if od[k]!=nd[k]:
            changes.append({"coordinate":list(k),"old":od[k],"new":nd[k]});inc |= nd[k]>od[k];dec |= nd[k]<od[k]
    if inc and dec:r="R_INCOMPARABLE"
    elif inc:r="R_STRENGTHEN"
    elif dec:r="R_WEAKEN"
    else:r="R_EQ"
    return {"T":t,"R":r,"status":"S1_EXACT_SBY","task_removed":[list(x) for x in sorted(rem)],"task_added":[list(x) for x in sorted(add)],"depth_changes":changes,
            "unmatched_depth_coordinates_old":[list(x) for x in sorted(set(od)-set(nd))],"unmatched_depth_coordinates_new":[list(x) for x in sorted(set(nd)-set(od))]}

def collect_candidates(row):
    out={"old":{"environment":[],"guarantee":[],"cover":[],"macro_environment":[],"macro_guarantee":[]},"new":{"environment":[],"guarantee":[],"cover":[],"macro_environment":[],"macro_guarantee":[]}}
    source_record_count=0
    for rec in row["formal_records"]:
        is_source=Path(rec["path"]).suffix.lower() in SOURCE_SUFFIXES
        if is_source:source_record_count+=1
        for side in ("old","new"):
            ss=rec[side].get("source_surface")
            if not ss:continue
            for c in ss.get("candidates",[]):
                h=c.get("head","").lower()
                if c.get("kind")=="native":
                    if h=="assume":out[side]["environment"].append(c)
                    elif h=="assert":out[side]["guarantee"].append(c)
                    elif h=="cover":out[side]["cover"].append(c)
                else:
                    hu=c.get("head","").upper()
                    if "ASSUME" in hu:out[side]["macro_environment"].append(c)
                    if "ASSERT" in hu:out[side]["macro_guarantee"].append(c)
                    if "COVER" in hu:out[side]["cover"].append(c)
    return out,source_record_count

def relation_flags(rels):
    adverse={"E_CONTRACT","E_INCOMPARABLE","G_WEAKEN","G_INCOMPARABLE","TASK_DROP","T_INCOMPARABLE","R_WEAKEN","R_INCOMPARABLE"}
    improve={"E_EXPAND","G_STRENGTHEN","TASK_ADDITION","R_STRENGTHEN"}
    return any(x in adverse for x in rels if x),any(x in improve for x in rels if x)

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--r1",required=True);ap.add_argument("--cvc5",required=True);ap.add_argument("--out",required=True)
    a=ap.parse_args(); out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    r1p=Path(a.r1); require(sha256_file(r1p)==R1_INVENTORY_SHA256,f"R1 inventory hash mismatch {sha256_file(r1p)}")
    r1=json.loads(r1p.read_text());require(r1.get("lexical_sanity_failures")==0,"R1 lexical gate not green")
    rows=[]
    for src in r1["rows"]:
        pid=src["sampling_digest"][:16]; qdir=out/"queries"/pid
        cand,source_record_count=collect_candidates(src)
        env=classify_axis(cand["old"]["environment"],cand["new"]["environment"],cand["old"]["macro_environment"],cand["new"]["macro_environment"],"environment",qdir,a.cvc5)
        guar=classify_axis(cand["old"]["guarantee"],cand["new"]["guarantee"],cand["old"]["macro_guarantee"],cand["new"]["macro_guarantee"],"guarantee",qdir,a.cvc5)
        st=structural_axes(src)
        # If the pair has no changed source-language file, source obligation formulas are unchanged by construction in this governed source slice.
        if source_record_count==0:
            if env["relation"] is None:env={"relation":"E_EQ","status":"S2_NO_SOURCE_CHANGE_EQ"}
            if guar["relation"] is None:guar={"relation":"G_EQ","status":"S2_NO_SOURCE_CHANGE_EQ"}
        rels=[env.get("relation"),guar.get("relation"),st.get("T"),st.get("R")]
        adverse,improve=relation_flags(rels)
        if adverse and improve:overall="CONFIRMED_INCOMPARABLE_WITH_REGRESSION"
        elif adverse:overall="CONFIRMED_REGRESSION"
        else:
            all_resolved=env.get("relation") is not None and guar.get("relation") is not None and st.get("T") is not None and st.get("R") is not None
            overall="CONFIRMED_NONREGRESSION" if all_resolved else "UNRESOLVED_NO_CONFIRMED_ADVERSE"
        if env.get("relation") or guar.get("relation"):
            primary="CLASSIFIED_SUPPORTED" if overall!="UNRESOLVED_NO_CONFIRMED_ADVERSE" else "CLASSIFIED_SUPPORTED_PARTIAL"
        elif st.get("T") or st.get("R"):primary="STRUCTURAL_ONLY"
        else:primary="OTHER_UNRESOLVED"
        rows.append({"repository_full_name":src["repository_full_name"],"parent":src["parent"],"child":src["child"],"sampling_digest":src["sampling_digest"],
                     "chronology_stratum":src["chronology_stratum"],"pre_registered_sentinel":src["pre_registered_sentinel"],"machine_state":primary,
                     "environment":env,"guarantee":guar,"structural":st,"overall":overall,
                     "source_record_count":source_record_count,"cover_candidate_counts":[len(cand['old']['cover']),len(cand['new']['cover'])],
                     "s3":{"status":"NOT_CLAIMED_GENERIC_HISTORY","reason":"No prospectively demonstrated project-general historical post-elaboration adapter beyond the frozen finite G4/G5 substrates."}})
    require(len(rows)==144,"wrong R2 denominator")
    blind=[r for r in rows if not r["pre_registered_sentinel"]];require(len(blind)==143,"wrong blind denominator")
    def summarize(rr):
        c=Counter(x["overall"] for x in rr); states=Counter(x["machine_state"] for x in rr)
        adverse=sum(c[k] for k in ("CONFIRMED_REGRESSION","CONFIRMED_INCOMPARABLE_WITH_REGRESSION")); unresolved=c["UNRESOLVED_NO_CONFIRMED_ADVERSE"]
        return {"n":len(rr),"overall":dict(c),"machine_states":dict(states),"confirmed_adverse":adverse,"unresolved":unresolved,
                "adverse_identification_interval":[adverse/len(rr) if rr else None,(adverse+unresolved)/len(rr) if rr else None]}
    byproj={p:summarize([r for r in blind if r["repository_full_name"]==p]) for p in sorted({r["repository_full_name"] for r in blind})}
    bystr={s:summarize([r for r in blind if r["chronology_stratum"]==s]) for s in ("EARLY","MIDDLE","LATE")}
    result={"schema":"g6-r2-blind-semantic-classification-v1","corpus_sha256":CORPUS_SHA256,"corpus_authority":CORPUS_AUTHORITY,
            "r1_closure":R1_CLOSURE,"r1_inventory_sha256":R1_INVENTORY_SHA256,"r1_amendment01":R1_AMENDMENT01,"r2_protocol":R2_PROTOCOL,"semantic_support":SEMANTIC_SUPPORT,
            "solver_versions":{"z3":z3.get_version_string(),"cvc5":"1.3.4 expected/pinned by workflow"},"selected_total":144,"blind_non_sentinel_total":143,
            "blind_summary":summarize(blind),"by_project":byproj,"by_chronology":bystr,"sentinel_summary":summarize([r for r in rows if r["pre_registered_sentinel"]]),"rows":rows}
    rp=out/"g6-r2-results.json";rp.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    (out/"summary.json").write_text(json.dumps({"results_sha256":sha256_file(rp),"blind_summary":result["blind_summary"],"by_project":byproj,"by_chronology":bystr,"sentinel_summary":result["sentinel_summary"]},indent=2,sort_keys=True)+"\n")
    print(json.dumps(json.loads((out/"summary.json").read_text()),sort_keys=True))

if __name__=="__main__":main()
