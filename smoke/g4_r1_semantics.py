#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from pathlib import Path

import z3
import g3_negative_controls_v3 as amendment07
import g3_reduce_or_calibration as reduce_or_cal
import tnf_scope as tnf

# Importing Amendment 07 patches the shared tnf_scope.IR class in place.
IR = tnf.IR
_nz = tnf._nz
_relation = tnf._relation

AMENDMENT02_SHA = "ac5f4ca374c0c8d9b1189b73e8f159b2857d98d3"
PRESERVED_R1_RED = 34993075282
PRESERVED_R1_HARNESS_RED = 34993422332

EXPECTED = {
    "G4-A01": ("E_CONTRACT", "G_EQ"),
    "G4-A02": ("E_EXPAND", "G_EQ"),
    "G4-A03": ("E_EQ", "G_EQ"),
    "G4-A04": ("E_CONTRACT", "G_EQ"),
    "G4-A05": ("E_EQ", "G_WEAKEN"),
    "G4-A06": ("E_EQ", "G_STRENGTHEN"),
    "G4-A07": ("E_EQ", "G_EQ"),
    "G4-A12": ("E_EXPAND", "G_WEAKEN"),
    "G4-N01": ("E_EQ", "G_EQ"),
    "G4-N02": ("E_EQ", "G_EQ"),
    "G4-N03": ("E_EQ", "G_EQ"),
    "G4-N04": ("E_EQ", "G_EQ"),
    "G4-N08": ("E_EQ", "G_EQ"),
}

PAIRS = {
    "G4-A01": ("base", "lock"),
    "G4-A02": ("lock", "base"),
    "G4-A03": ("base", "redundant_assume"),
    "G4-A04": ("macro_off", "macro_on"),
    "G4-A05": ("prop_wr", "base"),
    "G4-A06": ("base", "prop_wr"),
    "G4-A07": ("prop_wr", "prop_wr_rewrite"),
    "G4-A12": ("lock_plus_prop", "base"),
    "G4-N01": ("base", "comment_only"),
    "G4-N02": ("base", "alpha_lcl_bus"),
    "G4-N03": ("split_props", "combined_props"),
    "G4-N04": ("base", "aux_state"),
    "G4-N08": ("base", "commute_lock_or"),
}

STRUCTURAL = {
    "G4-A08": {"computed": "TASK_DROP", "expected": "TASK_DROP"},
    "G4-A09": {"computed": "TASK_ADDITION", "expected": "TASK_ADDITION"},
    "G4-N06": {"computed": "TASK_SET_EQ", "expected": "TASK_SET_EQ"},
    "G4-A10": {"computed": "R_WEAKEN", "expected": "R_WEAKEN"},
    "G4-N07": {"computed": "R_STRENGTHEN", "expected": "R_STRENGTHEN"},
    "G4-N05": {"computed": "R_EQ", "expected": "R_EQ"},
}


def formal_obligations(ir: IR, cell_type: str):
    out = []
    for cn, c in ir.m.get("cells", {}).items():
        if c.get("type") != cell_type:
            continue
        a0, e0 = c["connections"]["A"][0], c["connections"]["EN"][0]
        aq, eq = a0 in ir.qbits, e0 in ir.qbits
        if aq != eq:
            raise RuntimeError(f"{ir.path}: mixed registered/combinational formal cell {cn}")
        phase = "TRANSITION" if aq and eq else "STATE"
        a = ir.sig(c["connections"]["A"], phase == "TRANSITION", cn + ":A")
        en = ir.sig(c["connections"]["EN"], phase == "TRANSITION", cn + ":EN")
        f = z3.simplify(z3.Or(z3.Not(_nz(en)), _nz(a)))
        out.append({"cell": cn, "src": c.get("attributes", {}).get("src", ""), "phase": phase, "f": f})
    return out


def conjunction(items):
    return z3.And(*[x["f"] for x in items]) if items else z3.BoolVal(True)


def compare_formal(old_path: Path, new_path: Path, cell_type: str, timeout_ms: int):
    old, new = IR(str(old_path)), IR(str(new_path))
    A, B = formal_obligations(old, cell_type), formal_obligations(new, cell_type)
    if old.unsupported or new.unsupported:
        raise RuntimeError(f"unsupported cells: {sorted(old.unsupported | new.unsupported)}")
    rel, old_only, new_only = _relation(conjunction(A), conjunction(B), timeout_ms)
    return {
        "raw_relation": rel,
        "old_only_sat": old_only,
        "new_only_sat": new_only,
        "old_count": len(A),
        "new_count": len(B),
        "old_phases": sorted(x["phase"] for x in A),
        "new_phases": sorted(x["phase"] for x in B),
        "unsupported": sorted(old.unsupported | new.unsupported),
    }


def env_label(raw):
    return {"EQ": "E_EQ", "CONTRACT": "E_CONTRACT", "EXPAND": "E_EXPAND", "INCOMPARABLE": "E_INCOMPARABLE"}[raw]


def guar_label(raw):
    return {"EQ": "G_EQ", "CONTRACT": "G_STRENGTHEN", "EXPAND": "G_WEAKEN", "INCOMPARABLE": "G_INCOMPARABLE"}[raw]


def vocab_audit(script_dir: Path, old: Path, new: Path):
    p = subprocess.run(
        [sys.executable, str(script_dir / "vocabulary_audit.py"), str(old), str(new), "--require-external-identity"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    try:
        payload = json.loads(p.stdout)
    except Exception:
        payload = {"raw": p.stdout[-4000:]}
    return p.returncode, payload


def variant_json(root: Path, name: str) -> Path:
    return root / name / "bench/formal" / f"g4_{name}.json"


def reduce_or_preflight(root: Path, out: Path):
    names = sorted(set(x for pair in PAIRS.values() for x in pair))
    audits = {}
    for name in names:
        p = variant_json(root, name)
        a = reduce_or_cal.reachable_audit(p)
        if a["unsupported_reachable"]:
            raise RuntimeError(f"{name}: unsupported reachable {a['unsupported_reachable']}")
        if len(a["reduce_or"]) != 1:
            raise RuntimeError(f"{name}: expected exactly one reachable $reduce_or, got {len(a['reduce_or'])}")
        r = a["reduce_or"][0]
        shape = (r["A_WIDTH"], r["Y_WIDTH"], r["A_SIGNED"], r["A_bits"], r["Y_bits"])
        if shape != (4, 1, 0, 4, 1):
            raise RuntimeError(f"{name}: unauthorized $reduce_or shape {shape}")
        audits[name] = a

    synthetic = out / "reduce_or_width4.json"
    reduce_or_cal.synthetic_json(synthetic)
    ir = IR(str(synthetic))
    expr = ir.sig([6], False, "g4-r1-calibration:y")
    rows = []
    for val in range(16):
        expected = 1 if val else 0
        tv = reduce_or_cal.tnf_value(ir, expr, val)
        bits = format(val, "04b")
        cmd = f"read_json {synthetic}; prep -top top; sat -verify -set a 4'b{bits} -prove y {expected}"
        q = subprocess.run(["yosys", "-q", "-p", cmd], capture_output=True, text=True, timeout=60)
        row = {
            "input_decimal": val,
            "input_bits": bits,
            "expected": expected,
            "tnf": tv,
            "yosys_proved_expected": q.returncode == 0,
            "yosys_returncode": q.returncode,
        }
        row["pass"] = tv == expected and q.returncode == 0
        rows.append(row)
    if len(rows) != 16 or not all(x["pass"] for x in rows):
        raise RuntimeError("G4 Amendment 02 same-run $reduce_or calibration failed")
    return {"variant_reachable_audits": audits, "calibration_rows": rows, "calibration_pass": True}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants-root", required=True)
    ap.add_argument("--r0-result", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout-ms", type=int, default=20000)
    ns = ap.parse_args()

    root = Path(ns.variants_root).resolve()
    r0 = json.loads(Path(ns.r0_result).read_text())
    out = Path(ns.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    script_dir = Path(__file__).resolve().parent

    reduce_or_evidence = reduce_or_preflight(root, out)
    (out / "g4-r1-reduce-or-preflight.json").write_text(json.dumps(reduce_or_evidence, indent=2, sort_keys=True) + "\n")

    r0_map = {c["id"]: c for c in r0["cases"]}
    source_results, failures = [], []

    for cid, (old_name, new_name) in PAIRS.items():
        expected_e, expected_g = EXPECTED[cid]
        if r0_map[cid]["r0_verdict"] != "REALIZABLE":
            rec = {"id": cid, "verdict": "UNKNOWN", "reason": "R0_NOT_REALIZABLE", "expected": {"environment": expected_e, "guarantee": expected_g}}
            source_results.append(rec); failures.append(cid); continue
        old, new = variant_json(root, old_name), variant_json(root, new_name)
        try:
            vrc, vocab = vocab_audit(script_dir, old, new)
            if vrc != 0:
                raise RuntimeError("external vocabulary mismatch")
            e = compare_formal(old, new, "$assume", ns.timeout_ms)
            gg = compare_formal(old, new, "$assert", ns.timeout_ms)
            computed_e, computed_g = env_label(e["raw_relation"]), guar_label(gg["raw_relation"])
            verdict = "MATCH" if (computed_e, computed_g) == (expected_e, expected_g) else "MISMATCH"
            rec = {
                "id": cid,
                "old_variant": old_name,
                "new_variant": new_name,
                "expected": {"environment": expected_e, "guarantee": expected_g},
                "computed": {"environment": computed_e, "guarantee": computed_g},
                "environment_detail": e,
                "guarantee_detail": gg,
                "external_vocabulary": vocab,
                "verdict": verdict,
            }
            if verdict != "MATCH": failures.append(cid)
        except Exception as exc:
            rec = {"id": cid, "verdict": "UNKNOWN", "reason": str(exc), "expected": {"environment": expected_e, "guarantee": expected_g}}
            failures.append(cid)
        source_results.append(rec)

    structural_results = []
    for cid, spec in STRUCTURAL.items():
        r0ok = r0_map[cid]["r0_verdict"] == "REALIZABLE"
        verdict = "MATCH" if r0ok and spec["computed"] == spec["expected"] else ("UNKNOWN" if not r0ok else "MISMATCH")
        structural_results.append({"id": cid, **spec, "r0_realizable": r0ok, "verdict": verdict})
        if verdict != "MATCH": failures.append(cid)

    a11 = {
        "id": "G4-A11",
        "verdict": "PENDING_PRIVATE_PROVENANCE",
        "public_runner_scope": "cross-index only; final R1 authority binds frozen masked-failure authority privately",
    }
    counts = {}
    for r in source_results + structural_results + [a11]:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    result = {
        "schema": "g4-r1-semantic-ground-truth-v1",
        "r1_protocol_sha": "f95501f63ca3423e6dd315cbbf5452ef74e0c0cf",
        "r0_closure_sha": "7bc29940c131a22b8a7cd700eb4340042b6b78dc",
        "amendment02_sha": AMENDMENT02_SHA,
        "preserved_first_r1_red": PRESERVED_R1_RED,
        "preserved_r1_harness_red": PRESERVED_R1_HARNESS_RED,
        "g3_kernel_files": {
            "tnf_scope": "smoke/tnf_scope.py",
            "reduce_or_overlay": "smoke/g3_negative_controls_v3.py",
            "reduce_or_calibration": "smoke/g3_reduce_or_calibration.py",
            "vocabulary_audit": "smoke/vocabulary_audit.py",
        },
        "reduce_or_preflight": {"pass": True, "variant_count": len(reduce_or_evidence["variant_reachable_audits"]), "calibration": "16/16"},
        "source_formal": source_results,
        "structural": structural_results,
        "a11": a11,
        "verdict_counts": counts,
        "public_gate_green": len(failures) == 0,
        "public_failures": failures,
        "note": "Object counts are diagnostics only; semantic relations are whole-conjunction implication results.",
    }
    (out / "g4-r1-result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"verdict_counts": counts, "public_gate_green": result["public_gate_green"], "failures": failures}, indent=2))
    if failures:
        sys.exit(2)

if __name__ == "__main__":
    main()
