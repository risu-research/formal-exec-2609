#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, re, subprocess, tempfile
from pathlib import Path

import z3
from z3.z3util import get_vars
import g4_r1_semantics as r1

PROTOCOL_SHA = "70fb43ac62c2f9f92c8cc0e264f36367835d5b9e"
R1_CLOSURE_SHA = "334b84675b02d628f682a0bb845bbb42838f2f72"

STRICT = [
    ("G4-A01", "environment", "old_only"),
    ("G4-A02", "environment", "new_only"),
    ("G4-A04", "environment", "old_only"),
    ("G4-A05", "guarantee", "new_only"),
    ("G4-A06", "guarantee", "old_only"),
    ("G4-A12", "environment", "new_only"),
    ("G4-A12", "guarantee", "new_only"),
]

EQUALITY = [
    ("G4-A01", "guarantee"),
    ("G4-A02", "guarantee"),
    ("G4-A03", "environment"), ("G4-A03", "guarantee"),
    ("G4-A04", "guarantee"),
    ("G4-A05", "environment"),
    ("G4-A06", "environment"),
    ("G4-A07", "environment"), ("G4-A07", "guarantee"),
    ("G4-N01", "environment"), ("G4-N01", "guarantee"),
    ("G4-N02", "environment"), ("G4-N02", "guarantee"),
    ("G4-N03", "environment"), ("G4-N03", "guarantee"),
    ("G4-N04", "environment"), ("G4-N04", "guarantee"),
    ("G4-N08", "environment"), ("G4-N08", "guarantee"),
]


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def whole_formula(path: Path, axis: str):
    ir = r1.IR(str(path))
    kind = "$assume" if axis == "environment" else "$assert"
    rows = r1.formal_obligations(ir, kind)
    if ir.unsupported:
        raise RuntimeError(f"unsupported cells in {path}: {sorted(ir.unsupported)}")
    return r1.conjunction(rows), {
        "artifact_sha256": ir.sha256,
        "object_count": len(rows),
        "phases": sorted(x["phase"] for x in rows),
    }


def pair_formulas(root: Path, cid: str, axis: str):
    old_name, new_name = r1.PAIRS[cid]
    old_path = r1.variant_json(root, old_name)
    new_path = r1.variant_json(root, new_name)
    vrc, vocab = r1.vocab_audit(Path(__file__).resolve().parent, old_path, new_path)
    if vrc != 0:
        raise RuntimeError(f"{cid}/{axis}: external vocabulary mismatch")
    oldf, oldmeta = whole_formula(old_path, axis)
    newf, newmeta = whole_formula(new_path, axis)
    return oldf, newf, oldmeta, newmeta, vocab, old_name, new_name


def predicate(oldf, newf, direction: str):
    if direction == "old_only":
        return z3.simplify(z3.And(oldf, z3.Not(newf)))
    if direction == "new_only":
        return z3.simplify(z3.And(newf, z3.Not(oldf)))
    if direction == "xor":
        return z3.simplify(z3.Xor(oldf, newf))
    raise ValueError(direction)


def emit_query(pred, path: Path):
    s = z3.Solver()
    s.add(pred)
    text = s.to_smt2()
    if text.count("(check-sat)") != 1:
        raise RuntimeError("query must contain exactly one check-sat")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return sha256_bytes(text.encode())


def z3_result_and_complete_witness(pred, timeout_ms=30000):
    s = z3.Solver(); s.set(timeout=timeout_ms); s.add(pred)
    res = s.check()
    if res == z3.unknown:
        raise RuntimeError("Z3 unknown: " + s.reason_unknown())
    if res == z3.unsat:
        return "unsat", None
    m = s.model()
    vars_ = sorted(get_vars(pred), key=lambda v: v.sexpr())
    witness = {}
    for v in vars_:
        val = m.eval(v, model_completion=True)
        if z3.is_bool(v):
            witness[v.sexpr()] = {"sort": "Bool", "value": bool(z3.is_true(val))}
        elif z3.is_bv(v) and v.size() == 1:
            witness[v.sexpr()] = {"sort": "BV1", "value": int(val.as_long())}
        else:
            raise RuntimeError(f"unsupported witness variable {v.sexpr()} sort={v.sort()}")
    subs = []
    for v in vars_:
        w = witness[v.sexpr()]
        if w["sort"] == "Bool": subs.append((v, z3.BoolVal(w["value"])))
        else: subs.append((v, z3.BitVecVal(w["value"], 1)))
    grounded = z3.simplify(z3.substitute(pred, *subs)) if subs else z3.simplify(pred)
    if not z3.is_true(grounded):
        raise RuntimeError("complete witness does not replay predicate true in Z3")
    return "sat", witness


def bind_query(text: str, witness: dict):
    marker = "(check-sat)"
    if text.count(marker) != 1: raise RuntimeError("bad query marker")
    rows = []
    for sym in sorted(witness):
        w = witness[sym]
        rhs = ("true" if w["value"] else "false") if w["sort"] == "Bool" else f"#b{int(w['value'])}"
        rows.append(f"(assert (= {sym} {rhs}))")
    return text.replace(marker, "\n".join(rows) + "\n" + marker, 1)


def solve_cvc5(binary: str, text: str, timeout=60):
    with tempfile.NamedTemporaryFile("w", suffix=".smt2", delete=False) as f:
        f.write(text); name = f.name
    try:
        p = subprocess.run([binary, name], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        first = (p.stdout.strip().splitlines() or [""])[0].strip()
        if p.returncode != 0: raise RuntimeError(f"cvc5 rc={p.returncode}: {(p.stdout+p.stderr)[-1200:]}")
        if first not in ("sat", "unsat"): raise RuntimeError(f"cvc5 unexpected result {first!r}")
        return {"result": first, "returncode": p.returncode, "stdout_tail": p.stdout[-500:], "stderr_tail": p.stderr[-500:]}
    finally:
        Path(name).unlink(missing_ok=True)


def projected_witness(w):
    buses, scalars = {}, {}
    pat = re.compile(r"^(.*)\[(\d+)\]$")
    for sym, rec in w.items():
        m = pat.match(sym)
        if m and rec["sort"] == "BV1":
            buses.setdefault(m.group(1), {})[int(m.group(2))] = int(rec["value"])
        else:
            scalars[sym] = rec
    packed = {}
    for n, bits in buses.items():
        value = sum((bits[i] & 1) << i for i in bits)
        packed[n] = {"bits": {str(i): bits[i] for i in sorted(bits)}, "value_hex": hex(value), "observed_width": max(bits)+1}
    return {"buses": packed, "scalars": scalars}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants-root", required=True)
    ap.add_argument("--r0-result", required=True)
    ap.add_argument("--cvc5", required=True)
    ap.add_argument("--out", required=True)
    ns = ap.parse_args()
    root = Path(ns.variants_root).resolve(); out = Path(ns.out).resolve(); out.mkdir(parents=True, exist_ok=True)
    r0 = json.loads(Path(ns.r0_result).read_text())
    r0_map = {c["id"]: c for c in r0["cases"]}

    # Re-run the exact Amendment-02 pre-use gate before any R2 query is admitted.
    preflight = r1.reduce_or_preflight(root, out / "preflight")
    write_json(out / "REDUCE_OR_PREFLIGHT.json", preflight)

    strict_rows = []
    for cid, axis, direction in STRICT:
        if r0_map[cid]["r0_verdict"] != "REALIZABLE": raise RuntimeError(f"{cid}: R0 not realizable")
        oldf, newf, om, nm, vocab, old_name, new_name = pair_formulas(root, cid, axis)
        pred = predicate(oldf, newf, direction)
        qpath = out / "queries" / "strict" / f"{cid}__{axis}.smt2"
        qsha = emit_query(pred, qpath)
        zr, witness = z3_result_and_complete_witness(pred)
        if zr != "sat" or witness is None: raise RuntimeError(f"{cid}/{axis}: frozen strict predicate not SAT")
        raw = qpath.read_text()
        unbound = solve_cvc5(ns.cvc5, raw)
        fixed_text = bind_query(raw, witness)
        fixed_path = out / "queries" / "fixed" / f"{cid}__{axis}.smt2"
        fixed_path.parent.mkdir(parents=True, exist_ok=True); fixed_path.write_text(fixed_text)
        fixed = solve_cvc5(ns.cvc5, fixed_text)
        if unbound["result"] != "sat" or fixed["result"] != "sat": raise RuntimeError(f"{cid}/{axis}: cvc5 strict replay failed")
        strict_rows.append({
            "case": cid, "axis": axis, "direction": direction, "old_variant": old_name, "new_variant": new_name,
            "query_relpath": str(qpath.relative_to(out)), "query_sha256": qsha,
            "fixed_query_relpath": str(fixed_path.relative_to(out)), "fixed_query_sha256": sha256_bytes(fixed_text.encode()),
            "z3_result": zr, "complete_witness": witness, "projected_witness": projected_witness(witness), "witness_symbol_count": len(witness),
            "cvc5_unbound": unbound, "cvc5_fixed": fixed, "old_meta": om, "new_meta": nm, "external_vocabulary": vocab, "pass": True,
        })

    eq_rows = []
    for cid, axis in EQUALITY:
        oldf, newf, om, nm, vocab, old_name, new_name = pair_formulas(root, cid, axis)
        pred = predicate(oldf, newf, "xor")
        qpath = out / "queries" / "equality" / f"{cid}__{axis}.smt2"
        qsha = emit_query(pred, qpath)
        zr, witness = z3_result_and_complete_witness(pred)
        if zr != "unsat" or witness is not None: raise RuntimeError(f"{cid}/{axis}: equality XOR SAT in Z3")
        cv = solve_cvc5(ns.cvc5, qpath.read_text())
        if cv["result"] != "unsat": raise RuntimeError(f"{cid}/{axis}: equality XOR not UNSAT in cvc5")
        eq_rows.append({
            "case": cid, "axis": axis, "old_variant": old_name, "new_variant": new_name,
            "query_relpath": str(qpath.relative_to(out)), "query_sha256": qsha,
            "z3_result": zr, "cvc5": cv, "old_meta": om, "new_meta": nm, "external_vocabulary": vocab, "pass": True,
        })

    structural = []
    for cid in ("G4-A08","G4-A09","G4-A10","G4-N05","G4-N06","G4-N07"):
        rec = r0_map[cid]
        structural.append({"case": cid, "r0_verdict": rec["r0_verdict"], "certificate": rec, "pass": rec["r0_verdict"] == "REALIZABLE"})

    result = {
        "schema": "g4-r2-strictness-independent-replay-v1",
        "protocol_sha": PROTOCOL_SHA,
        "r1_closure_sha": R1_CLOSURE_SHA,
        "reduce_or_preflight": {"pass": True, "variant_count": len(preflight["variant_reachable_audits"]), "calibration": "16/16"},
        "strict_obligation_count": len(strict_rows), "equality_obligation_count": len(eq_rows),
        "strict": strict_rows, "equality": eq_rows, "structural": structural,
        "a11": {"status": "PENDING_PRIVATE_PRIOR_AUTHORITY_BINDING"},
        "public_gate_green": len(strict_rows)==7 and len(eq_rows)==19 and all(x["pass"] for x in strict_rows+eq_rows+structural),
    }
    write_json(out / "G4_R2_RESULT.json", result)
    print(json.dumps({"strict":len(strict_rows),"equality":len(eq_rows),"structural":len(structural),"public_gate_green":result["public_gate_green"]}, sort_keys=True))
    if not result["public_gate_green"]: raise SystemExit(2)

if __name__ == "__main__": main()
