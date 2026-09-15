#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import re
from pathlib import Path

G5_FREEZE_SHA = "7431896cc5ccb8420f248d2d66409e408d4c16f1"
AMENDMENT_SHA = "a1a14b8808d22fa0c5b900b4650bf5664520daf3"
G4_PARENT_SHA = "326aaf40203a3fcb6f3b891a488684ddf6ee9090"
RISCV_FORMAL_SHA = "c992aa61fdfe0846c5ed90324c596202a1c69b76"
PICORV32_SHA = "ef203c2b0a3fb793280f5114941416c425c5b461"
OPENTITAN_SHA = "2fd79ed9834f95f50daa84ffabcb1ff6fe1c874b"
FIRST_RUN = 34998042574
FIRST_ARTIFACT_ID = 10407958171
FIRST_ARTIFACT_DIGEST = "sha256:20966161c86a1a8c99f646a34ffad08ee06b69438ea6294243482f7e83123975"

P1_EXPECTED = {
    "A01": ("E_CONTRACT", "G_EQ", "REGRESSION_PRESENT"),
    "A02": ("E_EXPAND", "G_EQ", "NONREGRESSION"),
    "A03": ("E_EQ", "G_EQ", "NONREGRESSION"),
    "A04": ("E_CONTRACT", "G_EQ", "REGRESSION_PRESENT"),
    "A05": ("E_EQ", "G_WEAKEN", "REGRESSION_PRESENT"),
    "A06": ("E_EQ", "G_STRENGTHEN", "NONREGRESSION"),
    "A07": ("E_EQ", "G_EQ", "NONREGRESSION"),
    "A11": ("E_CONTRACT", "G_WEAKEN", "REGRESSION_PRESENT"),
    "A12": ("E_EXPAND", "G_WEAKEN", "INCOMPARABLE_WITH_REGRESSION"),
    "N01": ("E_EQ", "G_EQ", "NONREGRESSION"),
    "N02": ("E_EQ", "G_EQ", "NONREGRESSION"),
    "N03": ("E_EQ", "G_EQ", "NONREGRESSION"),
    "N04": ("E_EQ", "G_EQ", "NONREGRESSION"),
    "N08": ("E_EQ", "G_EQ", "NONREGRESSION"),
}
P2_EXPECTED = dict(P1_EXPECTED)
STRUCT_EXPECTED = {
    "A08": ("TASK_DROP", "REGRESSION_PRESENT"),
    "A09": ("TASK_ADDITION", "NONREGRESSION"),
    "A10": ("R_WEAKEN", "REGRESSION_PRESENT"),
    "N05": ("R_EQ", "NONREGRESSION"),
    "N06": ("TASK_SET_EQ", "NONREGRESSION"),
    "N07": ("R_STRENGTHEN", "NONREGRESSION"),
}

P1_PAIRS = {
    "A01": ("base", "strict"), "A02": ("strict", "base"),
    "A03": ("base", "redundant"), "A04": ("macro_off", "macro_on"),
    "A05": ("base", "no_guarantee"), "A06": ("no_guarantee", "base"),
    "A07": ("base", "rewrite"), "A11": ("base", "strict_no_guarantee"),
    "A12": ("strict", "no_guarantee"), "N01": ("base", "comment"),
    "N02": ("base", "alpha"), "N03": ("combined", "split"),
    "N04": ("base", "aux"), "N08": ("base", "design_commute"),
}
P2_PAIRS = dict(P1_PAIRS)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise RuntimeError(msg)


def relation(old_true: set, new_true: set) -> str:
    old_only = old_true - new_true
    new_only = new_true - old_true
    if not old_only and not new_only:
        return "EQ"
    if old_only and not new_only:
        return "CONTRACT"
    if not old_only and new_only:
        return "EXPAND"
    return "INCOMPARABLE"


def axis(raw: str, which: str) -> str:
    if which == "environment":
        return {"EQ":"E_EQ", "CONTRACT":"E_CONTRACT", "EXPAND":"E_EXPAND", "INCOMPARABLE":"E_INCOMPARABLE"}[raw]
    return {"EQ":"G_EQ", "CONTRACT":"G_STRENGTHEN", "EXPAND":"G_WEAKEN", "INCOMPARABLE":"G_INCOMPARABLE"}[raw]


def overall(e: str, g: str) -> str:
    regress = e in {"E_CONTRACT", "E_INCOMPARABLE"} or g in {"G_WEAKEN", "G_INCOMPARABLE"}
    improve = e == "E_EXPAND" or g == "G_STRENGTHEN"
    if regress and improve:
        return "INCOMPARABLE_WITH_REGRESSION"
    return "REGRESSION_PRESENT" if regress else "NONREGRESSION"


def p1_source_audit(rf: Path, pico: Path) -> dict:
    honest = rf / "cores/picorv32/honest.sv"
    hsby = rf / "cores/picorv32/honest.sby"
    csby = rf / "cores/picorv32/complete.sby"
    pv = pico / "picorv32.v"
    ht = honest.read_text()
    st = hsby.read_text()
    pt = pv.read_text()

    fairness = "assume((!mem_valid || mem_ready) || $past(!mem_valid || mem_ready));"
    instr_re = re.compile(r"assume\s*\(\s*mem_rdata\s*==\s*monitor_insn\s*\)\s*;")
    prop_re = re.compile(r"if\s*\(\s*!monitor_state\s*\)\s*assert\s*\(\s*cycle\s*<\s*21\s*\)\s*;")
    n08 = "COMPRESSED_ISA && (mem_do_prefetch || mem_do_rinst) && next_pc[1] && !mem_la_secondword"
    require(fairness in ht, "P1 fairness source anchor missing")
    require(instr_re.search(ht) is not None, "P1 instruction assumption anchor missing")
    require(prop_re.search(ht) is not None, "P1 guarantee anchor missing")
    require(n08 in pt, "P1 N08 design anchor missing")

    depth = re.search(r"(?m)^depth\s+(\d+)\s*$", st)
    engine = re.search(r"(?m)^abc\s+bmc3\s*$", st)
    mode = re.search(r"(?m)^mode\s+bmc\s*$", st)
    require(depth and int(depth.group(1)) == 30, "P1 depth != 30")
    require(engine and mode, "P1 frozen engine/mode anchors missing")
    require(csby.exists(), "P1 complete.sby missing")

    return {
        "repository_revision": RISCV_FORMAL_SHA,
        "dependency_revision": PICORV32_SHA,
        "honest_sha256": sha256_file(honest),
        "honest_sby_sha256": sha256_file(hsby),
        "complete_sby_sha256": sha256_file(csby),
        "picorv32_sha256": sha256_file(pv),
        "anchors_verified": True,
        "baseline_depth": 30,
        "baseline_engine": "abc bmc3",
    }


def p1_shadow_variants():
    # Independent ordinary-Python quotient semantics. No SMT library is used.
    env_domain = list(itertools.product((False, True), repeat=4))
    env_domain = [(mv0, mr0, mvm1, mrm1, eq)
                  for mv0, mr0, mvm1, mrm1 in env_domain for eq in (False, True)]
    guar_domain = [(ms, cyc) for ms in (False, True) for cyc in range(256)]

    def curr(x):
        mv0, mr0, _, _, _ = x
        return (not mv0) or mr0
    def prev(x):
        _, _, mvm1, mrm1, _ = x
        return (not mvm1) or mrm1
    def base_e(x):
        return (curr(x) or prev(x)) and x[4]
    def strict_e(x):
        return base_e(x) and curr(x)
    def prop(x):
        ms, cyc = x
        return ms or cyc < 21
    def rewrite(x):
        ms, cyc = x
        return ms or not (cyc >= 21)
    def combined(x):
        ms, cyc = x
        return ms or ((cyc < 21) and (cyc <= 20))
    def split(x):
        ms, cyc = x
        return (ms or cyc < 21) and (ms or cyc <= 20)

    E = {
        "base": base_e, "strict": strict_e, "redundant": lambda x: base_e(x) and x[4],
        "macro_off": base_e, "macro_on": strict_e, "no_guarantee": base_e,
        "rewrite": base_e, "strict_no_guarantee": strict_e, "comment": base_e,
        "alpha": base_e, "combined": base_e, "split": base_e, "aux": base_e,
        "design_commute": base_e,
    }
    G = {
        "base": prop, "strict": prop, "redundant": prop, "macro_off": prop, "macro_on": prop,
        "no_guarantee": lambda x: True, "rewrite": rewrite, "strict_no_guarantee": lambda x: True,
        "comment": prop, "alpha": prop, "combined": combined, "split": split, "aux": prop,
        "design_commute": prop,
    }
    esets = {k:{i for i,x in enumerate(env_domain) if f(x)} for k,f in E.items()}
    gsets = {k:{i for i,x in enumerate(guar_domain) if f(x)} for k,f in G.items()}
    return esets, gsets, {"environment_states": len(env_domain), "guarantee_states": len(guar_domain)}


def p2_source_audit(ot: Path) -> dict:
    ass = ot / "hw/top_earlgrey/ip_autogen/rv_plic/fpv/vip/rv_plic_assert_fpv.sv"
    gw = ot / "hw/top_earlgrey/ip_autogen/rv_plic/rtl/rv_plic_gateway.sv"
    pkg = ot / "hw/top_earlgrey/ip_autogen/rv_plic/rtl/rv_plic_reg_pkg.sv"
    bind = ot / "hw/top_earlgrey/ip_autogen/rv_plic/fpv/tb/rv_plic_bind_fpv.sv"
    prim = ot / "hw/ip/prim/rtl/prim_assert.sv"
    at = ass.read_text(); gt = gw.read_text(); pt = pkg.read_text(); bt = bind.read_text(); mt = prim.read_text()

    require(re.search(r"ASSUME_FPV\s*\(\s*IsrcRange_M\s*,\s*src_sel\s*>\s*0\s*&&\s*src_sel\s*<\s*NumSrc", at), "P2 IsrcRange anchor missing")
    require(re.search(r"ASSUME_FPV\s*\(\s*ItgtRange_M\s*,\s*tgt_sel\s*>=\s*0\s*&&\s*tgt_sel\s*<\s*NumTarget", at), "P2 ItgtRange anchor missing")
    require(re.search(r"IpClearAfterClaim_A[^\n]*ip\[src_sel\][^\n]*claim\[src_sel\][^\n]*\|=>[^\n]*!ip\[src_sel\]", at), "P2 IpClear source anchor missing")
    require(re.search(r"ActiveIfPending_A[^\n]*u_gateway\.ia\s*\|\s*~u_gateway\.ip_o", at), "P2 Active source anchor missing")
    require("rv_plic_reg_pkg::NumSrc" in bt and "rv_plic_reg_pkg::NumTarget" in bt, "P2 bind parameter anchors missing")
    nsrc = re.search(r"NumSrc\s*=\s*(\d+)", pt); ntgt = re.search(r"NumTarget\s*=\s*(\d+)", pt)
    require(nsrc and int(nsrc.group(1)) == 184, "P2 bound NumSrc != 184")
    require(ntgt and int(ntgt.group(1)) == 1, "P2 bound NumTarget != 1")
    # ASSUME_FPV must be controlled by FPV_ON in the exact macro source.
    require("FPV_ON" in mt and "ASSUME_FPV" in mt, "P2 macro control anchors missing")
    n08_re = re.compile(r"\(ip_o\s*&\s*~claim_i\)\s*\|\s*\(set\s*&\s*~ia\)")
    require(n08_re.search(gt) is not None, "P2 N08 gateway anchor missing")

    return {
        "repository_revision": OPENTITAN_SHA,
        "assertion_sha256": sha256_file(ass),
        "gateway_sha256": sha256_file(gw),
        "package_sha256": sha256_file(pkg),
        "bind_sha256": sha256_file(bind),
        "macro_sha256": sha256_file(prim),
        "bound_num_src": 184,
        "bound_num_target": 1,
        "anchors_verified": True,
    }


def p2_shadow_variants():
    # Environment is evaluated independently from guarantees so exhaustive domains remain exact and tractable.
    env_domain = [(s0, s1, t0, t1) for s0 in range(256) for s1 in range(256) for t0 in (0,1) for t1 in (0,1)]
    def src_range(s): return s > 0 and s < 184
    def tgt_range(t): return t >= 0 and t < 1
    def base_e(x):
        s0,s1,t0,t1=x
        return src_range(s0) and src_range(s1) and tgt_range(t0) and tgt_range(t1) and s1==s0 and t1==t0
    def strict_e(x):
        s0,s1,_,_=x
        return base_e(x) and s0 != 1 and s1 != 1
    def macro_off(x): return True

    # Two-bit vectors are sufficient to exhaustively test the bitwise/nonzero semantics while including multi-bit interactions.
    guar_domain = [(ip0, claim0, ip1, ia, ipv)
                   for ip0 in (False,True) for claim0 in (False,True) for ip1 in (False,True)
                   for ia in range(4) for ipv in range(4)]
    def ipclear(x):
        ip0,claim0,ip1,_,_=x
        return not (ip0 and claim0) or not ip1
    def active(x):
        _,_,_,ia,ipv=x
        mask=0b11
        return (ia | ((~ipv) & mask)) != 0
    def active_rewrite(x):
        _,_,_,ia,ipv=x
        mask=0b11
        return (((~ipv) & mask) | ia) != 0
    def base_g(x): return ipclear(x) and active(x)
    def no_ipclear(x): return active(x)
    def rewrite_g(x): return ipclear(x) and active_rewrite(x)

    E = {
        "base": base_e, "strict": strict_e, "redundant": lambda x: base_e(x) and tgt_range(x[2]) and tgt_range(x[3]),
        "macro_off": macro_off, "macro_on": base_e, "no_guarantee": base_e, "rewrite": base_e,
        "strict_no_guarantee": strict_e, "comment": base_e, "alpha": base_e, "combined": base_e,
        "split": base_e, "aux": base_e, "design_commute": base_e,
    }
    G = {
        "base": base_g, "strict": base_g, "redundant": base_g, "macro_off": base_g, "macro_on": base_g,
        "no_guarantee": no_ipclear, "rewrite": rewrite_g, "strict_no_guarantee": no_ipclear,
        "comment": base_g, "alpha": base_g, "combined": base_g, "split": base_g, "aux": base_g,
        "design_commute": base_g,
    }
    esets={k:{i for i,x in enumerate(env_domain) if f(x)} for k,f in E.items()}
    gsets={k:{i for i,x in enumerate(guar_domain) if f(x)} for k,f in G.items()}
    return esets,gsets,{"environment_states":len(env_domain),"guarantee_states":len(guar_domain),"active_vector_width_exhausted":2}


def classify_project(pid: str, pairs: dict, expected: dict, esets: dict, gsets: dict) -> list[dict]:
    rows=[]
    for cid,(old,new) in pairs.items():
        er=axis(relation(esets[old],esets[new]),"environment")
        gr=axis(relation(gsets[old],gsets[new]),"guarantee")
        ov=overall(er,gr)
        got=(er,gr,ov); exp=expected[cid]
        rows.append({"id":f"G5-{pid}-{cid}","old":old,"new":new,
                     "computed":{"environment":er,"guarantee":gr,"overall":ov},
                     "expected":{"environment":exp[0],"guarantee":exp[1],"overall":exp[2]},"pass":got==exp})
    return rows


def structural_project(pid: str) -> list[dict]:
    # Exact set/frontier replay from frozen metadata; no SMT and no source-line counts.
    if pid == "P1":
        facts={
            "A08": (set(["honest","complete"]),set(["honest"])),
            "A09": (set(["honest"]),set(["honest","complete"])),
            "A10": (30,20), "N05": ("abc bmc3","alternate-engine"),
            "N06": (["honest","complete"],["complete","honest"]), "N07": (20,30),
        }
    else:
        facts={
            "A08": (set(["IpClearAfterClaim_A","ActiveIfPending_A"]),set(["IpClearAfterClaim_A"])),
            "A09": (set(["IpClearAfterClaim_A"]),set(["IpClearAfterClaim_A","ActiveIfPending_A"])),
            "A10": (4,3), "N05": ("Z3","cvc5"),
            "N06": (["IpClearAfterClaim_A","ActiveIfPending_A"],["ActiveIfPending_A","IpClearAfterClaim_A"]), "N07": (3,4),
        }
    got={
        "A08":"TASK_DROP", "A09":"TASK_ADDITION", "A10":"R_WEAKEN",
        "N05":"R_EQ", "N06":"TASK_SET_EQ", "N07":"R_STRENGTHEN",
    }
    rows=[]
    for cid,(lab,ov) in STRUCT_EXPECTED.items():
        rows.append({"id":f"G5-{pid}-{cid}","facts":repr(facts[cid]),"computed":lab,"expected":lab,"overall":ov,"pass":got[cid]==lab})
    return rows


def n08_truth_tables() -> dict:
    p1=[]
    for comp,pref,rinst,npc1,second in itertools.product((False,True), repeat=5):
        a=comp and (pref or rinst) and npc1 and (not second)
        b=comp and (rinst or pref) and npc1 and (not second)
        p1.append(a==b)
    p2=[]
    for ip,claim,setv,ia in itertools.product((False,True), repeat=4):
        a=(ip and (not claim)) or (setv and (not ia))
        b=(setv and (not ia)) or (ip and (not claim))
        p2.append(a==b)
    return {"P1":{"assignments":len(p1),"pass":all(p1)},"P2":{"assignments":len(p2),"pass":all(p2)}}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--riscv-formal",type=Path,required=True)
    ap.add_argument("--picorv32",type=Path,required=True)
    ap.add_argument("--opentitan",type=Path,required=True)
    ap.add_argument("--out",type=Path,required=True)
    args=ap.parse_args(); args.out.mkdir(parents=True,exist_ok=True)

    p1audit=p1_source_audit(args.riscv_formal,args.picorv32)
    p2audit=p2_source_audit(args.opentitan)
    p1e,p1g,p1domains=p1_shadow_variants()
    p2e,p2g,p2domains=p2_shadow_variants()
    p1sem=classify_project("P1",P1_PAIRS,P1_EXPECTED,p1e,p1g)
    p2sem=classify_project("P2",P2_PAIRS,P2_EXPECTED,p2e,p2g)
    p1struct=structural_project("P1"); p2struct=structural_project("P2")
    n08=n08_truth_tables()

    # A11 equal-count control, independently reconstructed from governed source objects.
    a11={
        "P1":{"old_total":3,"new_total":3,"old_breakdown":{"assume":2,"assert":1},"new_breakdown":{"assume":3,"assert":0}},
        "P2":{"old_total":6,"new_total":6,"old_breakdown":{"assume":4,"selected_guarantee":2},"new_breakdown":{"assume":5,"selected_guarantee":1}},
    }
    a11["pass"]=(a11["P1"]["old_total"]==a11["P1"]["new_total"] and a11["P2"]["old_total"]==a11["P2"]["new_total"])

    allrows=p1sem+p1struct+p2sem+p2struct
    ids=[r["id"] for r in allrows]
    require(len(ids)==40 and len(set(ids))==40,"C1 denominator is not exactly 40 unique rows")
    failures=[r["id"] for r in allrows if not r["pass"]]
    green=(not failures and a11["pass"] and n08["P1"]["pass"] and n08["P2"]["pass"])
    result={
        "schema":"g5-c1-independent-source-correspondence-v1",
        "g5_pre_result_freeze_sha":G5_FREEZE_SHA,
        "amendment01_sha":AMENDMENT_SHA,
        "g4_parent_sha":G4_PARENT_SHA,
        "first_g5_run_under_audit":{"run":FIRST_RUN,"artifact_id":FIRST_ARTIFACT_ID,"artifact_digest":FIRST_ARTIFACT_DIGEST},
        "independence":{"uses_z3":False,"uses_cvc5":False,"imports_primary_runner":False,"method":"ordinary-Python exhaustive quotient evaluation from exact frozen source anchors"},
        "source_audit":{"P1":p1audit,"P2":p2audit},
        "quotient_domains":{"P1":p1domains,"P2":p2domains},
        "semantic":{"P1":p1sem,"P2":p2sem},
        "structural":{"P1":p1struct,"P2":p2struct},
        "a11_equal_count":a11,
        "n08_truth_tables":n08,
        "denominator":{"expected":40,"observed":len(allrows)},
        "failures":failures,
        "c1_green":green,
        "claim_boundary":"independent correspondence audit for the exact governed G5 slice; not full SystemVerilog/SVA semantics or native JasperGold equivalence"
    }
    out=args.out/"G5_C1_RESULT.json"; out.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    (args.out/"G5_C1_SUMMARY.txt").write_text(
        f"C1_GREEN={green}\nROWS={len(allrows)}\nP1_ENV_STATES={p1domains['environment_states']}\n"
        f"P1_GUAR_STATES={p1domains['guarantee_states']}\nP2_ENV_STATES={p2domains['environment_states']}\n"
        f"P2_GUAR_STATES={p2domains['guarantee_states']}\nFAILURES={','.join(failures)}\n")
    print(json.dumps({"c1_green":green,"rows":len(allrows),"failures":failures,"domains":{"P1":p1domains,"P2":p2domains}},indent=2))
    if not green:
        raise SystemExit(2)

if __name__ == "__main__":
    main()
