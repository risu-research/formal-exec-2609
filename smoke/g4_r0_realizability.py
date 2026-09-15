#!/usr/bin/env python3
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ZIPCPU_SHA = "d511239e19be8fcc7f340a64554ea93699637e62"
SOURCE = Path("rtl/core/pipemem.v")
YS = Path("bench/formal/pipemem.ys")
MAKEFILE = Path("bench/formal/Makefile")
COMPATIBILITY = ["flatten", "opt_clean", "dffunmap"]
AMENDMENT_SHA = "8703d07bc5e6196daea1a9cecf28be7e892de49d"
FAILED_R0_RUN = 34991978591


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def run(cmd, cwd=None, check=True):
    p = subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if check and p.returncode != 0:
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(cmd)}\n{p.stdout}")
    return p


def exact_replace(text: str, old: str, new: str, expected_count: int = 1) -> str:
    count = text.count(old)
    if count != expected_count:
        raise ValueError(f"anchor count mismatch: expected {expected_count}, got {count}: {old[:120]!r}")
    return text.replace(old, new, expected_count)


def insert_after(text: str, anchor: str, addition: str, expected_count: int = 1) -> str:
    return exact_replace(text, anchor, anchor + addition, expected_count)


def lock_assumption_block(guarded=False):
    body = (
        "\n\talways @(posedge i_clk)\n"
        "\tif ((f_past_valid)&&($past(cyc))&&(!$past(i_lock)))\n"
        "\t\t`ASSUME(!i_lock);\n"
    )
    if not guarded:
        return body
    return "\n`ifdef G4_LOCK_CONTRACT\n" + body + "`endif\n"


def wraddr_property(expr="wraddr == 0"):
    return (
        "\n\talways @(posedge i_clk)\n"
        "\tif ((f_past_valid)&&($past(i_reset)))\n"
        f"\t\t`ASSERT({expr});\n"
    )


def split_reset_properties():
    return (
        "\n\talways @(posedge i_clk)\n"
        "\tif ((f_past_valid)&&($past(i_reset)))\n"
        "\tbegin\n"
        "\t\t`ASSERT(wraddr == 0);\n"
        "\t\t`ASSERT(rdaddr == 0);\n"
        "\tend\n"
    )


def combined_reset_property():
    return (
        "\n\talways @(posedge i_clk)\n"
        "\tif ((f_past_valid)&&($past(i_reset)))\n"
        "\t\t`ASSERT((wraddr == 0)&&(rdaddr == 0));\n"
    )


FPAST_ANCHOR = (
    "\treg\tf_past_valid;\n"
    "\tinitial\tf_past_valid = 0;\n"
    "\talways @(posedge i_clk)\n"
    "\t\tf_past_valid = 1'b1;\n"
)


def mutate_source(base: str, variant: str) -> tuple[str, list[str]]:
    notes = []
    t = base
    if variant == "base":
        return t, ["unmodified upstream source"]
    if variant == "lock":
        t = insert_after(t, FPAST_ANCHOR, lock_assumption_block(False))
        notes.append("inserted lock contraction assumption")
    elif variant == "lock_macro":
        t = insert_after(t, FPAST_ANCHOR, lock_assumption_block(True))
        notes.append("inserted macro-guarded lock contraction assumption")
    elif variant == "redundant_assume":
        anchor = "\tinitial\t`ASSUME(!i_pipe_stb);\n"
        t = exact_replace(t, anchor, anchor + anchor, 1)
        notes.append("duplicated exact initial i_pipe_stb assumption")
    elif variant == "prop_wr":
        t = insert_after(t, FPAST_ANCHOR, wraddr_property("wraddr == 0"))
        notes.append("inserted controlled wraddr reset guarantee")
    elif variant == "prop_wr_rewrite":
        t = insert_after(t, FPAST_ANCHOR, wraddr_property("!(wraddr != 0)"))
        notes.append("inserted logically equivalent wraddr guarantee rewrite")
    elif variant == "split_props":
        t = insert_after(t, FPAST_ANCHOR, split_reset_properties())
        notes.append("inserted split wraddr/rdaddr reset guarantees")
    elif variant == "combined_props":
        t = insert_after(t, FPAST_ANCHOR, combined_reset_property())
        notes.append("inserted conjunction of wraddr/rdaddr reset guarantees")
    elif variant == "comment_only":
        anchor = "module\tpipemem(i_clk, i_reset, i_pipe_stb, i_lock,\n"
        t = exact_replace(t, anchor, "// G4 semantics-preserving comment-only control\n" + anchor, 1)
        notes.append("comment-only source change")
    elif variant == "alpha_lcl_bus":
        hits = len(re.findall(r"\blcl_bus\b", t))
        if hits < 2:
            raise ValueError(f"unexpected lcl_bus token count: {hits}")
        t = re.sub(r"\blcl_bus\b", "g4_lcl_bus", t)
        notes.append(f"alpha-renamed lcl_bus consistently ({hits} tokens)")
    elif variant == "aux_state":
        anchor = "\treg\tmisaligned;\n"
        addition = (
            "\treg\t[7:0]\tg4_aux_state;\n"
            "\tinitial g4_aux_state = 0;\n"
            "\talways @(posedge i_clk) g4_aux_state <= g4_aux_state + 1'b1;\n"
        )
        t = exact_replace(t, anchor, anchor + addition, 1)
        notes.append("added no-fanout auxiliary state")
    elif variant == "commute_lock_or":
        old = "\t\tassign\to_wb_cyc_gbl = (r_wb_cyc_gbl)||(lock_gbl);\n"
        new = "\t\tassign\to_wb_cyc_gbl = (lock_gbl)||(r_wb_cyc_gbl);\n"
        t = exact_replace(t, old, new, 1)
        notes.append("commuted operands of existing lock-cycle OR")
    elif variant == "lock_plus_prop":
        t = insert_after(t, FPAST_ANCHOR, lock_assumption_block(False) + wraddr_property("wraddr == 0"))
        notes.append("inserted lock contraction plus controlled guarantee")
    else:
        raise ValueError(f"unknown variant: {variant}")
    return t, notes


def yosys_elaborate(repo: Path, variant_name: str, macros: list[str]):
    bench = repo / "bench/formal"
    json_out = bench / f"g4_{variant_name}.json"
    smt_out = bench / f"g4_{variant_name}.smt2"
    cmd = ["read_verilog", "-D", "PIPEMEM"]
    for m in macros:
        cmd += ["-D", m]
    cmd += ["-formal", "../../rtl/core/pipemem.v"]
    script_lines = [" ".join(cmd)]
    script_lines += [
        "read_verilog -D PIPEMEM -formal ../../rtl/ex/fwb_master.v",
        "prep -top pipemem -nordff",
        "flatten",
        "opt_clean",
        "dffunmap",
        f"write_json {json_out.name}",
        f"write_smt2 -wires {smt_out.name}",
    ]
    ys = bench / f"g4_{variant_name}.ys"
    ys.write_text("\n".join(script_lines) + "\n")
    p = run(["yosys", "-ql", f"g4_{variant_name}.yslog", "-s", ys.name], cwd=bench, check=False)
    result = {
        "exit_code": p.returncode,
        "log_sha256": sha256_file(bench / f"g4_{variant_name}.yslog") if (bench / f"g4_{variant_name}.yslog").exists() else None,
        "macros": ["PIPEMEM"] + macros,
        "compatibility_transform": COMPATIBILITY,
        "amendment_sha": AMENDMENT_SHA,
    }
    if p.returncode != 0 or not json_out.exists() or not smt_out.exists():
        result["error_tail"] = p.stdout[-4000:]
        return result
    net = json.loads(json_out.read_text())
    formal_counts = {"$assume": 0, "$assert": 0, "$cover": 0}
    total_cells = 0
    for mod in net.get("modules", {}).values():
        for cell in mod.get("cells", {}).values():
            total_cells += 1
            typ = cell.get("type")
            if typ in formal_counts:
                formal_counts[typ] += 1
    result.update({
        "json_sha256": sha256_file(json_out),
        "smt2_sha256": sha256_file(smt_out),
        "formal_cells": formal_counts,
        "total_cells": total_cells,
    })
    return result


def make_variant(root: Path, outroot: Path, name: str, source_variant: str, macros=None):
    macros = macros or []
    dst = outroot / name
    shutil.copytree(root, dst)
    src = dst / SOURCE
    original = src.read_text()
    mutated, notes = mutate_source(original, source_variant)
    src.write_text(mutated)
    return {
        "name": name,
        "source_variant": source_variant,
        "source_sha256": sha256_file(src),
        "transformation_sha256": sha256_bytes((source_variant + "\n" + mutated).encode()),
        "notes": notes,
        "elaboration": yosys_elaborate(dst, name, macros),
    }


def case_record(case_id, oldv, newv, expected_structure=None):
    ok = oldv["elaboration"]["exit_code"] == 0 and newv["elaboration"]["exit_code"] == 0
    return {
        "id": case_id,
        "r0_verdict": "REALIZABLE" if ok else "RED_ELABORATION",
        "old": oldv,
        "new": newv,
        "expected_structure": expected_structure or {},
    }


def structural_cases(repo: Path):
    mf = (repo / MAKEFILE).read_text()
    required = ["memops", "pipemem"]
    missing = [x for x in required if x not in mf]
    has_pipe_40 = bool(re.search(r"\$\(PIPE\).*?-t\s+40", mf, re.S))
    return [
        {"id":"G4-A08","r0_verdict":"REALIZABLE" if not missing else "RED_MUTATION_INVALID","kind":"envelope","old":["pipemem","memops"],"new":["pipemem"],"missing_tasks":missing},
        {"id":"G4-A09","r0_verdict":"REALIZABLE" if not missing else "RED_MUTATION_INVALID","kind":"envelope","old":["pipemem"],"new":["pipemem","memops"],"missing_tasks":missing},
        {"id":"G4-N06","r0_verdict":"REALIZABLE" if not missing else "RED_MUTATION_INVALID","kind":"envelope","old":["pipemem","memops"],"new":["memops","pipemem"],"missing_tasks":missing},
        {"id":"G4-A10","r0_verdict":"REALIZABLE" if has_pipe_40 else "RED_MUTATION_INVALID","kind":"regime","old":{"engine":"BMC","depth":40},"new":{"engine":"BMC","depth":20},"upstream_pipe_depth40_found":has_pipe_40},
        {"id":"G4-N07","r0_verdict":"REALIZABLE" if has_pipe_40 else "RED_MUTATION_INVALID","kind":"regime","old":{"engine":"BMC","depth":20},"new":{"engine":"BMC","depth":40},"upstream_pipe_depth40_found":has_pipe_40},
        {"id":"G4-N05","r0_verdict":"REALIZABLE" if has_pipe_40 else "RED_MUTATION_INVALID","kind":"regime","same_claim":True,"same_frontier":{"kind":"BMC","depth":40},"old_engine":"z3","new_engine":"yices"},
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zipcpu", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    root = Path(args.zipcpu).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    head = run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()
    if head != ZIPCPU_SHA:
        raise SystemExit(f"wrong ZipCPU revision: {head}")
    yosys_version = run(["yosys", "-V"]).stdout.strip()

    work = out / "variants"
    work.mkdir()
    variants = {}
    def V(name, source_variant, macros=None):
        if name not in variants:
            variants[name] = make_variant(root, work, name, source_variant, macros or [])
        return variants[name]

    base = V("base", "base")
    lock = V("lock", "lock")
    redundant = V("redundant_assume", "redundant_assume")
    macro_off = V("macro_off", "lock_macro", [])
    macro_on = V("macro_on", "lock_macro", ["G4_LOCK_CONTRACT"])
    prop = V("prop_wr", "prop_wr")
    prop_rw = V("prop_wr_rewrite", "prop_wr_rewrite")
    splitp = V("split_props", "split_props")
    combp = V("combined_props", "combined_props")
    comment = V("comment_only", "comment_only")
    alpha = V("alpha_lcl_bus", "alpha_lcl_bus")
    aux = V("aux_state", "aux_state")
    commute = V("commute_lock_or", "commute_lock_or")
    lock_prop = V("lock_plus_prop", "lock_plus_prop")

    cases = [
        case_record("G4-A01", base, lock, {"mutation":"assumption_strengthening"}),
        case_record("G4-A02", lock, base, {"mutation":"assumption_weakening"}),
        case_record("G4-A03", base, redundant, {"mutation":"redundant_assumption"}),
        case_record("G4-A04", macro_off, macro_on, {"mutation":"macro_off_to_on"}),
        case_record("G4-A05", prop, base, {"mutation":"property_drop"}),
        case_record("G4-A06", base, prop, {"mutation":"property_strengthening"}),
        case_record("G4-A07", prop, prop_rw, {"mutation":"equivalent_property_rewrite"}),
        case_record("G4-A12", lock_prop, base, {"mutation":"mixed_env_expand_and_guarantee_drop"}),
        case_record("G4-N01", base, comment, {"negative":"comment_only"}),
        case_record("G4-N02", base, alpha, {"negative":"alpha_rename"}),
        case_record("G4-N03", splitp, combp, {"negative":"split_recombine"}),
        case_record("G4-N04", base, aux, {"negative":"irrelevant_aux_state"}),
        case_record("G4-N08", base, commute, {"negative":"boolean_or_commutation"}),
    ]
    cases.extend(structural_cases(root))
    cases.append({"id":"G4-A11","r0_verdict":"PENDING_PRIVATE_PROVENANCE","kind":"prior_authority","note":"public runner does not read private authority; private closure must bind the frozen masked-failure evidence"})

    counts = {}
    for c in cases:
        counts[c["r0_verdict"]] = counts.get(c["r0_verdict"], 0) + 1
    result = {
        "schema":"g4-r0-realizability-result-v1",
        "zipcpu_revision":head,
        "zipcpu_source_sha256":sha256_file(root / SOURCE),
        "zipcpu_pipemem_ys_sha256":sha256_file(root / YS),
        "zipcpu_makefile_sha256":sha256_file(root / MAKEFILE),
        "yosys_version":yosys_version,
        "compatibility_transform":COMPATIBILITY,
        "prospective_amendment_sha":AMENDMENT_SHA,
        "preserved_failed_r0_run":FAILED_R0_RUN,
        "cases":cases,
        "verdict_counts":counts,
        "public_gate_green": all(c["r0_verdict"] == "REALIZABLE" for c in cases if c["id"] != "G4-A11"),
        "a11_requires_private_provenance":True,
    }
    (out / "g4-r0-result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"verdict_counts":counts,"public_gate_green":result["public_gate_green"]}, indent=2))
    if not result["public_gate_green"]:
        sys.exit(2)

if __name__ == "__main__":
    main()
