#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json
from collections import Counter
from pathlib import Path

R1_SHA256 = "2f5caabda7625524a790be6ec94d07e5fda02a9a5eaca9875fe8add858c88161"
R2_SHA256 = "00cc74ac08a15a785eaf7caa601181c16d8a2ee152b735b3e87ae9a3bb981af1"
PARENT_G6_FINAL = "26fd9ad7f81d6a0813e18c3ea0fcd0e69792c3c4"

def sha256_file(p: Path) -> str:
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1<<20), b""):
            h.update(b)
    return h.hexdigest()

def derive_sem(kind: str, d: dict):
    # B5 derivation: normalized-bundle availability + solver outcomes only.
    # Never read d['relation'] here.
    st=d.get("status")
    pfx="E" if kind=="E" else "G"
    if st=="S2_NO_SOURCE_CHANGE_EQ":
        return pfx+"_EQ"
    if st in ("S2_SCHEMA_EQ_CROSS_SOLVER","S2_GROUNDED_STRICT_FROM_EMPTY"):
        old=(d.get("old_only") or {}).get("z3")
        new=(d.get("new_only") or {}).get("z3")
        if old is None or new is None:
            return None
        if old=="unsat" and new=="unsat": return pfx+"_EQ"
        if old=="sat" and new=="unsat": return "E_CONTRACT" if kind=="E" else "G_WEAKEN"
        if old=="unsat" and new=="sat": return "E_EXPAND" if kind=="E" else "G_STRENGTHEN"
        if old=="sat" and new=="sat": return pfx+"_INCOMPARABLE"
    return None

def derive_tr(st: dict):
    # B5 derivation from normalized task/depth metadata only.
    # Never read st['T'] or st['R'] here.
    if st.get("status")!="S1_EXACT_SBY":
        return None,None
    rem=st.get("task_removed") or []
    add=st.get("task_added") or []
    if rem and add:t="T_INCOMPARABLE"
    elif rem:t="TASK_DROP"
    elif add:t="TASK_ADDITION"
    else:t="TASK_SET_EQ"
    inc=dec=False
    for ch in st.get("depth_changes") or []:
        if ch["new"]>ch["old"]:inc=True
        elif ch["new"]<ch["old"]:dec=True
    if inc and dec:r="R_INCOMPARABLE"
    elif inc:r="R_STRENGTHEN"
    elif dec:r="R_WEAKEN"
    else:r="R_EQ"
    return t,r

def b5_overall(axes: dict):
    adverse=(axes["E"] in ("E_CONTRACT","E_INCOMPARABLE") or axes["G"] in ("G_WEAKEN","G_INCOMPARABLE") or
             axes["T"] in ("TASK_DROP","T_INCOMPARABLE") or axes["R"] in ("R_WEAKEN","R_INCOMPARABLE"))
    improve=(axes["E"]=="E_EXPAND" or axes["G"]=="G_STRENGTHEN" or axes["T"]=="TASK_ADDITION" or axes["R"]=="R_STRENGTHEN")
    if adverse and improve:return "CONFIRMED_INCOMPARABLE_WITH_REGRESSION"
    if adverse:return "CONFIRMED_REGRESSION"
    if all(v is not None for v in axes.values()):return "CONFIRMED_NONREGRESSION"
    return "UNRESOLVED_NO_CONFIRMED_ADVERSE"

def candidate_change(r1row: dict) -> bool:
    for rec in r1row["formal_records"]:
        old=rec["old"].get("source_surface"); new=rec["new"].get("source_surface")
        if old is None and new is None:continue
        oh=old.get("candidate_multiset_sha256") if old else None
        nh=new.get("candidate_multiset_sha256") if new else None
        if oh!=nh:return True
    return False

def b4_signal(r1row: dict, r2row: dict) -> dict:
    cand=candidate_change(r1row)
    st=r2row["structural"]
    structural=bool((st.get("task_removed") or []) or (st.get("task_added") or []) or (st.get("depth_changes") or []) or
                    (st.get("unmatched_depth_coordinates_old") or []) or (st.get("unmatched_depth_coordinates_new") or []))
    return {"signal":bool(cand or structural),"candidate_surface_change":cand,"task_or_regime_syntax_change":structural}

def sem_adverse(full: dict) -> bool:
    return full["E"] in ("E_CONTRACT","E_INCOMPARABLE") or full["G"] in ("G_WEAKEN","G_INCOMPARABLE")

def struct_adverse(full: dict) -> bool:
    return full["T"] in ("TASK_DROP","T_INCOMPARABLE") or full["R"] in ("R_WEAKEN","R_INCOMPARABLE")

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--r1",required=True);ap.add_argument("--r2",required=True);ap.add_argument("--controls",required=True);ap.add_argument("--out",required=True)
    a=ap.parse_args();r1p=Path(a.r1);r2p=Path(a.r2);cp=Path(a.controls);out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    if sha256_file(r1p)!=R1_SHA256:raise SystemExit("R1 hash mismatch")
    if sha256_file(r2p)!=R2_SHA256:raise SystemExit("R2 hash mismatch")
    r1=json.loads(r1p.read_text());r2=json.loads(r2p.read_text());controls=json.loads(cp.read_text())
    if r1["pair_count"]!=144 or r2["selected_total"]!=144 or r2["blind_non_sentinel_total"]!=143:raise SystemExit("frozen denominator mismatch")
    if controls["parent_g6_final"]!=PARENT_G6_FINAL or len(controls["controls"])!=8:raise SystemExit("control fixture mismatch")
    idx={(x["repository_full_name"],x["parent"],x["child"]):x for x in r1["rows"]}
    blind=[x for x in r2["rows"] if not x["pre_registered_sentinel"]]
    if len(blind)!=143:raise SystemExit("wrong blind denominator")

    rows=[];axis_defined=Counter();axis_available=Counter();axis_match=Counter();mismatches=[]
    b1_change=0;b4=Counter();overall_match=0;full_outcomes=Counter();b5_outcomes=Counter()
    adverse_sem=adverse_struct=adverse_total=replay_semantic_axes=0
    for x in blind:
        key=(x["repository_full_name"],x["parent"],x["child"]);rr=idx[key]
        full={"E":x["environment"].get("relation"),"G":x["guarantee"].get("relation"),"T":x["structural"].get("T"),"R":x["structural"].get("R")}
        b5={"E":derive_sem("E",x["environment"]),"G":derive_sem("G",x["guarantee"])};b5["T"],b5["R"]=derive_tr(x["structural"])
        for ax in "EGTR":
            if full[ax] is not None:
                axis_defined[ax]+=1
                if b5[ax] is not None:axis_available[ax]+=1
                if b5[ax]==full[ax]:axis_match[ax]+=1
                else:mismatches.append({"repository":x["repository_full_name"],"parent":x["parent"],"child":x["child"],"axis":ax,"full":full[ax],"b5":b5[ax]})
        bo=b5_overall(b5);full_outcomes[x["overall"]]+=1;b5_outcomes[bo]+=1;overall_match+=int(bo==x["overall"])
        b1_change+=int(rr["changed_path_count"]>0);b4x=b4_signal(rr,x)
        b4["signal"]+=int(b4x["signal"]);b4["candidate_surface_change"]+=int(b4x["candidate_surface_change"]);b4["task_or_regime_syntax_change"]+=int(b4x["task_or_regime_syntax_change"])
        if x["overall"] in ("CONFIRMED_REGRESSION","CONFIRMED_INCOMPARABLE_WITH_REGRESSION"):
            adverse_total+=1;b4["adverse_signal"]+=int(b4x["signal"]);adverse_sem+=int(sem_adverse(full));adverse_struct+=int(struct_adverse(full))
        if x["environment"].get("status") in ("S2_SCHEMA_EQ_CROSS_SOLVER","S2_GROUNDED_STRICT_FROM_EMPTY"):replay_semantic_axes+=1
        if x["guarantee"].get("status") in ("S2_SCHEMA_EQ_CROSS_SOLVER","S2_GROUNDED_STRICT_FROM_EMPTY"):replay_semantic_axes+=1
        rows.append({"repository_full_name":x["repository_full_name"],"parent":x["parent"],"child":x["child"],"full_overall":x["overall"],"full_axes":full,"b5_axes":b5,"b5_overall":bo,"b1":{"change_signal":rr["changed_path_count"]>0,"changed_path_count":rr["changed_path_count"]},"b4":b4x})

    ctl={}
    for c in controls["controls"]:
        cid=c["id"]
        if cid.startswith("CTRL-B1B4"):
            ctl[cid]={"pass":c["baseline_signal"]=="SOURCE_OR_FORMAL_CHANGE" and c["full_relation"]==["E_EQ","G_EQ"],"meaning":"explicit change signal coexists with governed semantic equality"}
        elif cid.startswith("CTRL-B2"):
            ctl[cid]={"pass":c["old_total"]==c["new_total"] and c["full_relation"]==["E_CONTRACT","G_WEAKEN"],"meaning":"equal aggregate count coexists with adverse E/G change"}
        elif cid=="CTRL-B0-S1":
            ctl[cid]={"pass":c["old_status"]=="GREEN" and c["new_status"]=="GREEN" and c["full_relation"]==["E_EXPAND"],"meaning":"same PASS/FAIL status coexists with certified semantic difference"}

    if adverse_total!=9:raise SystemExit(f"unexpected G6 adverse count {adverse_total}")
    expected=Counter({"UNRESOLVED_NO_CONFIRMED_ADVERSE":130,"CONFIRMED_REGRESSION":8,"CONFIRMED_NONREGRESSION":4,"CONFIRMED_INCOMPARABLE_WITH_REGRESSION":1})
    if full_outcomes!=expected:raise SystemExit(f"unexpected frozen outcome partition {full_outcomes}")

    capability={
      "B0":{"C0_CHANGE_SIGNAL":"STATUS_ONLY","C1_DIRECTION":False,"C2_STRICTNESS_OR_EQUALITY_CERTIFICATE":False,"C3_REALIZED_SOURCE_BINDING":False,"C4_CROSS_VERSION_REPLAY":False,"C5_TASK_REGIME_COVERAGE":False,"C6_UNSUPPORTED_DISCIPLINE":"NOT_APPLICABLE"},
      "B1":{"C0_CHANGE_SIGNAL":True,"C1_DIRECTION":False,"C2_STRICTNESS_OR_EQUALITY_CERTIFICATE":False,"C3_REALIZED_SOURCE_BINDING":False,"C4_CROSS_VERSION_REPLAY":False,"C5_TASK_REGIME_COVERAGE":False,"C6_UNSUPPORTED_DISCIPLINE":"NOT_A_SEMANTIC_CLASSIFIER"},
      "B2":{"C0_CHANGE_SIGNAL":True,"C1_DIRECTION":"STRUCTURAL_ONLY","C2_STRICTNESS_OR_EQUALITY_CERTIFICATE":False,"C3_REALIZED_SOURCE_BINDING":"ELABORATED_STRUCTURE_ONLY_WHERE_AVAILABLE","C4_CROSS_VERSION_REPLAY":False,"C5_TASK_REGIME_COVERAGE":"PARTIAL_TASK_SET","C6_UNSUPPORTED_DISCIPLINE":"BASELINE_SPECIFIC"},
      "B3":{"C0_CHANGE_SIGNAL":"LOCAL_DIAGNOSTIC","C1_DIRECTION":False,"C2_STRICTNESS_OR_EQUALITY_CERTIFICATE":"LOCAL_ONLY","C3_REALIZED_SOURCE_BINDING":"WHERE_GOVERNED","C4_CROSS_VERSION_REPLAY":False,"C5_TASK_REGIME_COVERAGE":False,"C6_UNSUPPORTED_DISCIPLINE":"BASELINE_SPECIFIC"},
      "B4":{"C0_CHANGE_SIGNAL":True,"C1_DIRECTION":False,"C2_STRICTNESS_OR_EQUALITY_CERTIFICATE":False,"C3_REALIZED_SOURCE_BINDING":False,"C4_CROSS_VERSION_REPLAY":False,"C5_TASK_REGIME_COVERAGE":False,"C6_UNSUPPORTED_DISCIPLINE":"NOT_A_SEMANTIC_CLASSIFIER"},
      "B5":{"C0_CHANGE_SIGNAL":True,"C1_DIRECTION":True,"C2_STRICTNESS_OR_EQUALITY_CERTIFICATE":True,"C3_REALIZED_SOURCE_BINDING":False,"C4_CROSS_VERSION_REPLAY":"NORMALIZED_BUNDLE_ONLY","C5_TASK_REGIME_COVERAGE":True,"C6_UNSUPPORTED_DISCIPLINE":True},
      "FULL":{"C0_CHANGE_SIGNAL":True,"C1_DIRECTION":True,"C2_STRICTNESS_OR_EQUALITY_CERTIFICATE":True,"C3_REALIZED_SOURCE_BINDING":"GOVERNED_SUPPORTED_SLICE","C4_CROSS_VERSION_REPLAY":True,"C5_TASK_REGIME_COVERAGE":True,"C6_UNSUPPORTED_DISCIPLINE":True}
    }

    result={"schema":"g7-baseline-frontier-result-v1","input_sha256":{"r1":R1_SHA256,"r2":R2_SHA256,"controls":sha256_file(cp)},"blind_denominator":143,
      "B1":{"change_signal":b1_change,"direction_claimed":False},
      "B4":{"signal":b4["signal"],"candidate_surface_change":b4["candidate_surface_change"],"task_or_regime_syntax_change":b4["task_or_regime_syntax_change"],"adverse_signal":b4["adverse_signal"],"adverse_total":adverse_total,"direction_claimed":False},
      "B5":{"defined_axes":dict(axis_defined),"available_axes":dict(axis_available),"exact_matches":dict(axis_match),"mismatches":mismatches,"total_defined_axes":sum(axis_defined.values()),"total_exact_matches":sum(axis_match.values()),"overall_exact_match":overall_match,"full_outcomes":dict(full_outcomes),"b5_outcomes":dict(b5_outcomes),"realized_source_binding":False},
      "controlled_non_subsumption":ctl,"B3":{"status":"B3_NOT_CLOSED","reason":"No source-token proxy is promoted to governed local vacuity/cover evidence."},
      "ablations":{"A1_NO_SEMANTIC_EG":{"remaining_adverse_certifications":adverse_struct,"removed":adverse_total-adverse_struct},"A2_NO_STRUCTURAL_TR":{"remaining_adverse_certifications":adverse_sem,"removed":adverse_total-adverse_sem},"A3_B5_NO_REALIZATION_PROVENANCE":{"overall_relation_matches":overall_match,"denominator":143,"realized_source_binding":False},"A4_NO_INDEPENDENT_REPLAY":{"classification_labels_recomputed":False,"independent_replay_backed_semantic_axes_degraded":replay_semantic_axes,"interpretation":"certificate/TCB ablation, not accuracy ablation"}},
      "capability_frontier":capability,"rows":rows}
    (out/"g7-baseline-frontier.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    summary={"blind_denominator":143,"B1_change_signal":b1_change,"B4_signal":b4["signal"],"B4_adverse_signal":b4["adverse_signal"],"B5_defined_axes":sum(axis_defined.values()),"B5_exact_matches":sum(axis_match.values()),"B5_overall_exact_match":overall_match,"B5_mismatches":len(mismatches),"A1_structural_only":adverse_struct,"A2_semantic_only":adverse_sem,"B3":"B3_NOT_CLOSED","control_pass":sum(int(v["pass"]) for v in ctl.values()),"control_total":len(ctl)}
    (out/"summary.json").write_text(json.dumps(summary,indent=2,sort_keys=True)+"\n");print(json.dumps(summary,sort_keys=True))

if __name__=="__main__":main()
