#!/usr/bin/env python3
"""Reproducible fault-injection study of checkpoint provenance at effect time.

All source labels and payloads are synthetic. Oracle truth is used only by the
experiment auditor. Guard implementations consume checkpoint declarations or
independently persisted read receipts, never oracle truth.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import random
import sqlite3
import tempfile
from pathlib import Path
from typing import TypedDict

POLICY_ORDERS = {"acosta": "notice_received", "necec": "challenge_pending"}
GUARDS = ("cached", "embedded", "receipt")
SOURCES = {"R", "K"}
# name, true dependence, checkpoint declaration, read-receipt dependence,
# receipt integrity status. All nonempty source sets are ordered in source order.
CASES = (
    ("intact_revoked", ("R",), ("R",), ("R",), "valid"),
    ("intact_clean", ("K",), ("K",), ("K",), "valid"),
    ("intact_mixed", ("R", "K"), ("R", "K"), ("R", "K"), "valid"),
    ("missing_declared_revoked", ("R",), None, ("R",), "valid"),
    ("missing_declared_clean", ("K",), None, ("K",), "valid"),
    ("false_clean_declared", ("R",), ("K",), ("R",), "valid"),
    ("false_revoked_declared", ("K",), ("R",), ("K",), "valid"),
    ("omitted_mixed_source", ("R", "K"), ("K",), ("R", "K"), "valid"),
    ("absent_receipt_revoked", ("R",), ("R",), None, "missing"),
    ("absent_receipt_clean", ("K",), ("K",), None, "missing"),
    ("incomplete_receipt_revoked", ("R",), ("R",), ("R",), "incomplete"),
    ("incomplete_receipt_clean", ("K",), ("K",), ("K",), "incomplete"),
    ("stale_receipt_revoked", ("R",), ("R",), ("R",), "wrong_digest"),
    ("stale_receipt_clean", ("K",), ("K",), ("K",), "wrong_digest"),
    ("false_complete_receipt_revoked", ("R",), ("K",), ("K",), "false_complete"),
    ("false_complete_receipt_clean", ("K",), ("K",), ("R",), "false_complete"),
    ("indistinguishable_revoked", ("R",), ("K",), ("K",), "false_complete"),
    ("indistinguishable_clean", ("K",), ("K",), ("K",), "valid"),
)
# Expected decisions fixed without looking at software output.
# An unavailable/incomplete receipt is unresolved, not a claim of safety.
EXPECTED = {
    "intact_revoked":                ("ALLOW", "BLOCK", "BLOCK"),
    "intact_clean":                  ("ALLOW", "ALLOW", "ALLOW"),
    "intact_mixed":                  ("ALLOW", "BLOCK", "BLOCK"),
    "missing_declared_revoked":      ("ALLOW", "UNRESOLVED", "BLOCK"),
    "missing_declared_clean":        ("ALLOW", "UNRESOLVED", "ALLOW"),
    "false_clean_declared":          ("ALLOW", "ALLOW", "BLOCK"),
    "false_revoked_declared":        ("ALLOW", "BLOCK", "ALLOW"),
    "omitted_mixed_source":          ("ALLOW", "ALLOW", "BLOCK"),
    "absent_receipt_revoked":        ("ALLOW", "BLOCK", "UNRESOLVED"),
    "absent_receipt_clean":          ("ALLOW", "ALLOW", "UNRESOLVED"),
    "incomplete_receipt_revoked":    ("ALLOW", "BLOCK", "UNRESOLVED"),
    "incomplete_receipt_clean":      ("ALLOW", "ALLOW", "UNRESOLVED"),
    "stale_receipt_revoked":         ("ALLOW", "BLOCK", "UNRESOLVED"),
    "stale_receipt_clean":           ("ALLOW", "ALLOW", "UNRESOLVED"),
    "false_complete_receipt_revoked":("ALLOW", "ALLOW", "ALLOW"),
    "false_complete_receipt_clean":  ("ALLOW", "ALLOW", "BLOCK"),
    "indistinguishable_revoked":    ("ALLOW", "ALLOW", "ALLOW"),
    "indistinguishable_clean":      ("ALLOW", "ALLOW", "ALLOW"),
}
DEV_SEEDS = tuple(range(11, 15))
HELDOUT_SEEDS = tuple(range(4101, 4121))


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def payload_for(order: str, seed: int, template: str) -> str:
    # The indistinguishability pair has literally identical visible payloads.
    if template.startswith("indistinguishable_"):
        return f"review-note/{order}/{seed}/shared-output"
    rng = random.Random(sha(f"{order}|{seed}|{template}"))
    return f"review-note/{order}/{seed}/{rng.getrandbits(96):024x}"


def make_spec(order: str, seed: int, template: tuple) -> dict:
    label, truth, declaration, receipt_sources, receipt_fault = template
    message = payload_for(order, seed, label)
    receipt = None
    if receipt_sources is not None:
        receipt = {
            "sources": list(receipt_sources),
            "complete": receipt_fault != "incomplete",
            "payload_digest": sha(message if receipt_fault != "wrong_digest" else message + "-outdated"),
        }
    return {"id": f"{order}-{seed}-{label}", "order": order, "seed": seed,
            "template": label, "oracle_truth": list(truth),
            "declared": list(declaration) if declaration is not None else None,
            "receipt": receipt, "payload": message, "phase": POLICY_ORDERS[order]}


def check_structural_spec():
    assert len(CASES) == len(EXPECTED) == 18
    # The two controls share the same policy-facing artifact key.
    def visible_artifact(spec, guard):
        return (f"collision/{spec['order']}/{spec['seed']}/{guard}"
                if spec["template"].startswith("indistinguishable_")
                else spec["id"] + "/" + guard)
    assert set(x[0] for x in CASES) == set(EXPECTED)
    assert all(len(EXPECTED[x[0]]) == len(GUARDS) for x in CASES)
    for order in POLICY_ORDERS:
        for seed in (DEV_SEEDS[0], HELDOUT_SEEDS[0]):
            pair = [make_spec(order, seed, x) for x in CASES if x[0].startswith("indistinguishable_")]
            assert len(pair) == 2
            assert pair[0]["payload"] == pair[1]["payload"]
            assert pair[0]["declared"] == pair[1]["declared"]
            assert pair[0]["receipt"] == pair[1]["receipt"]
            assert pair[0]["phase"] == pair[1]["phase"]
            assert pair[0]["oracle_truth"] != pair[1]["oracle_truth"]
            for guard in GUARDS:
                assert visible_artifact(pair[0],guard)==visible_artifact(pair[1],guard)
    return True


class WorkflowState(TypedDict, total=False):
    artifact_id: str
    payload: str
    declared: list[str] | None
    approval_at_prepare: bool
    last_result: str


def resolve_sources(sources: object) -> str:
    if not isinstance(sources, list) or not sources or any(x not in SOURCES for x in sources):
        return "UNRESOLVED"
    return "BLOCK" if "R" in sources else "ALLOW"


def evaluate_case(spec: dict, guard: str, conn: sqlite3.Connection) -> dict:
    # Import only for live experiments, allowing standard-library-only plan checks.
    from langgraph.graph import StateGraph, START, END
    from langgraph.checkpoint.memory import InMemorySaver

    # Collision controls share the exact identifier and all observable fields.
    # They differ solely in the auditor's withheld oracle lineage.
    artifact_id = (
        f"collision/{spec['order']}/{spec['seed']}/{guard}"
        if spec["template"].startswith("indistinguishable_")
        else spec["id"] + "/" + guard
    )
    payload = spec["payload"]
    encoded_receipt = json.dumps(spec["receipt"], sort_keys=True)
    previous_receipt = conn.execute(
        "SELECT receipt_json FROM source_receipts WHERE artifact_id=?", (artifact_id,)
    ).fetchone()
    if previous_receipt:
        assert previous_receipt[0] == encoded_receipt, ("collision receipt differs", artifact_id)
    else:
        conn.execute("INSERT INTO source_receipts(artifact_id,receipt_json) VALUES(?,?)",
                     (artifact_id, encoded_receipt))
        conn.commit()
    phase = {"active": False}
    actions: list[str] = []

    def prepare(s: WorkflowState):
        return {"approval_at_prepare": True}  # before legal notice

    def emit(s: WorkflowState):
        if guard == "cached":
            verdict = "ALLOW" if s.get("approval_at_prepare") else "BLOCK"
        elif guard == "embedded":
            verdict = resolve_sources(s.get("declared")) if phase["active"] else "ALLOW"
        elif guard == "receipt":
            row = conn.execute("SELECT receipt_json FROM source_receipts WHERE artifact_id=?",
                               (s["artifact_id"],)).fetchone()
            receipt = json.loads(row[0]) if row else None
            if receipt is None or receipt.get("complete") is not True:
                verdict = "UNRESOLVED"
            elif receipt.get("payload_digest") != sha(s["payload"]):
                verdict = "UNRESOLVED"
            else:
                verdict = resolve_sources(receipt.get("sources")) if phase["active"] else "ALLOW"
        else:
            raise ValueError(guard)
        actions.append(verdict)
        if verdict == "ALLOW":
            conn.execute("INSERT INTO effect_writes(artifact_id,phase,payload) VALUES(?,?,?)",
                         (artifact_id, "post_notice" if phase["active"] else "pre_notice", s["payload"]))
            conn.commit()
        return {"last_result": verdict}

    graph = StateGraph(WorkflowState)
    graph.add_node("prepare", prepare)
    graph.add_node("emit", emit)
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "emit")
    graph.add_edge("emit", END)
    saver = InMemorySaver()
    app = graph.compile(checkpointer=saver)
    config = {"configurable": {"thread_id": artifact_id}}
    app.invoke({"artifact_id": artifact_id, "payload": payload, "declared": spec["declared"]}, config)
    snapshots = list(app.get_state_history(config))
    old = [x.config for x in snapshots if tuple(x.next) == ("emit",)]
    assert len(old) == 1, (artifact_id, [(x.next, x.values) for x in snapshots])
    phase["active"] = True
    app.update_state(config, {"payload": "clean-recomputed-state", "declared": ["K"],
                              "last_result": "latest_repaired"}, as_node="emit")
    assert app.get_state(config).values["payload"] == "clean-recomputed-state"
    before = conn.execute("SELECT COUNT(*) FROM effect_writes WHERE artifact_id=?", (artifact_id,)).fetchone()[0]
    app.invoke(None, old[0])
    after = conn.execute("SELECT COUNT(*) FROM effect_writes WHERE artifact_id=?", (artifact_id,)).fetchone()[0]
    new_writes = after - before
    decision = actions[-1]
    assert new_writes == (1 if decision == "ALLOW" else 0), (artifact_id, decision, new_writes)
    expected = EXPECTED[spec["template"]][GUARDS.index(guard)]
    assert decision == expected, (artifact_id, "decision", expected, decision)
    truly_affected = "R" in spec["oracle_truth"]
    return {"id": artifact_id, "order": spec["order"], "seed": spec["seed"],
            "template": spec["template"], "guard": guard,
            "oracle_affected": truly_affected, "decision": decision,
            "new_writes": new_writes, "unauthorized_effect": bool(truly_affected and new_writes),
            "clean_effect_preserved": bool(not truly_affected and new_writes),
            "unknown_decision": decision == "UNRESOLVED",
            "old_checkpoint_used": True, "latest_state_repaired": True}


def summarise(records: list[dict]) -> dict:
    by_guard: dict[str, dict] = {}
    for g in GUARDS:
        rr = [r for r in records if r["guard"] == g]
        a = [r for r in rr if r["oracle_affected"]]
        c = [r for r in rr if not r["oracle_affected"]]
        by_guard[g] = {
            "total": len(rr), "affected": len(a), "clean": len(c),
            "unauthorized_effects": sum(r["unauthorized_effect"] for r in a),
            "clean_effects_preserved": sum(r["clean_effect_preserved"] for r in c),
            "unresolved_affected": sum(r["unknown_decision"] for r in a),
            "unresolved_clean": sum(r["unknown_decision"] for r in c),
        }
    by_template = {}
    for t in EXPECTED:
        by_template[t] = {g: {
            "count": sum(r["template"] == t and r["guard"] == g for r in records),
            "unauthorized_effects": sum(r["template"] == t and r["guard"] == g and r["unauthorized_effect"] for r in records),
            "clean_preserved": sum(r["template"] == t and r["guard"] == g and r["clean_effect_preserved"] for r in records),
            "unresolved": sum(r["template"] == t and r["guard"] == g and r["unknown_decision"] for r in records),
        } for g in GUARDS}
    return {"by_guard": by_guard, "by_template": by_template}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--partition", required=True, choices=("plan", "dev", "holdout"))
    args = ap.parse_args()
    check_structural_spec()
    if args.partition == "plan":
        print("PLAN", json.dumps({"templates": len(CASES), "guards": GUARDS,
                                 "orders": POLICY_ORDERS, "dev_seeds": DEV_SEEDS,
                                 "holdout_seeds": HELDOUT_SEEDS,
                                 "indistinguishable_pair_verified": True}, sort_keys=True))
        return
    from importlib.metadata import version
    seeds = DEV_SEEDS if args.partition == "dev" else HELDOUT_SEEDS
    rows = []
    with tempfile.TemporaryDirectory() as d:
        conn = sqlite3.connect(Path(d) / "independent_effects.sqlite")
        conn.execute("CREATE TABLE source_receipts (artifact_id TEXT PRIMARY KEY,receipt_json TEXT)")
        conn.execute("CREATE TABLE effect_writes (write_id INTEGER PRIMARY KEY,artifact_id TEXT,phase TEXT,payload TEXT)")
        for seed in seeds:
            for order in POLICY_ORDERS:
                for template in CASES:
                    spec = make_spec(order, seed, template)
                    for g in GUARDS:
                        rows.append(evaluate_case(spec, g, conn))
        total_sql_effects = conn.execute("SELECT COUNT(*) FROM effect_writes").fetchone()[0]
        conn.close()
    assert len(rows) == len(seeds) * len(POLICY_ORDERS) * len(CASES) * len(GUARDS)
    by_id = {(r["order"], r["seed"], r["template"], r["guard"]):r for r in rows}
    for order in POLICY_ORDERS:
        for seed in seeds:
            for g in GUARDS:
                p = by_id[(order,seed,"indistinguishable_revoked",g)]
                q = by_id[(order,seed,"indistinguishable_clean",g)]
                assert p["decision"] == q["decision"]
                assert p["oracle_affected"] and not q["oracle_affected"]
    result = {"schema": "provenance-fault-evaluation-v1", "partition": args.partition,
              "source_policy": "Acosta notice / NECEC challenge encoded as current hold on revoked-derived work",
              "language_runtime": "Python 3.12 / real LangGraph / independent SQLite effect ledger",
              "langgraph_version": version("langgraph"),
              "guard_names": GUARDS, "seeds": list(seeds), "cases_per_order": len(CASES),
              "orders": list(POLICY_ORDERS), "records": rows,
              "summary": summarise(rows), "sql_effects_total_including_pre_notice": total_sql_effects,
              "passed": True, "asserted_expected_outcomes": len(rows),
              "indistinguishable_pair_checks": len(seeds)*len(POLICY_ORDERS)*len(GUARDS),
              "limitations": [
                "Constructed synthetic source dependencies and output text",
                "Fixture-weighted outcomes are stress-test rates, not production error prevalence",
                "SQLite source receipts simulate a separate instrumentation channel",
                "The read-receipt source list can be falsely complete; no mechanism can recover omitted lineage from identical available evidence",
                "No LLM transformations, provider deletion, or real court cases are executed",
                "Only InMemorySaver in this run; an earlier distinct experiment tested PostgresSaver",
                "No legal advice or external compliance certification",
              ]}
    fname = f"provenance_{args.partition}_result.json"
    Path(fname).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    digest = hashlib.sha256(Path(fname).read_bytes()).hexdigest()
    print("SUMMARY_JSON", json.dumps({"partition": args.partition, "passed":True,
          "langgraph_version":result["langgraph_version"], "rows":len(rows),
          "checks":result["asserted_expected_outcomes"],
          "collision_checks": result["indistinguishable_pair_checks"],
          "by_guard":result["summary"]["by_guard"],
          "result_sha256":digest}, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
