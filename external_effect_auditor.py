#!/usr/bin/env python3
"""Read-only independent observer for out-of-process effect persistence.

No software callback, guard return value, or synthetic decision acknowledgement
is treated as evidence that a write persisted. Database rows are the evidence.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

def receipt_valid(receipt,payload):
    return (isinstance(receipt,dict)
            and receipt.get("complete") is True
            and receipt.get("payload_digest")==hashlib.sha256(payload.encode()).hexdigest()
            and isinstance(receipt.get("sources"),list)
            and bool(receipt["sources"])
            and all(s in ("R","K") for s in receipt["sources"]))

def expected_commit(spec):
    mode=spec["guard"]
    sch=spec["policy_schedule"]
    if mode=="cached":return True
    if not receipt_valid(spec["receipt"],spec["payload"]):return False
    listed_revoked="R" in spec["receipt"]["sources"]
    if mode=="client_precheck":
        return not (listed_revoked and sch=="before")
    if mode=="sink_atomic":
        return not (listed_revoked and sch in ("before","race"))
    raise AssertionError(mode)

def main():
    a=argparse.ArgumentParser()
    a.add_argument("--db",type=Path,required=True)
    a.add_argument("--oracle",type=Path,required=True)
    a.add_argument("--out",type=Path,required=True)
    a.add_argument("--acks",type=Path,required=True)
    x=a.parse_args()
    manifest=json.loads(x.oracle.read_text())
    cases=manifest["cases"]
    expected_ids={spec["case_id"] for spec in cases}
    acks=json.loads(x.acks.read_text())["acks"]
    assert {z["case_id"] for z in acks}==expected_ids
    assert len(acks)==len(cases)
    assert all(z["replayed_historical_snapshot"] and z["latest_state_repaired"] for z in acks)
    # Make it impossible for this auditor to modify the sink.
    conn=sqlite3.connect(f"file:{x.db.resolve()}?mode=ro",uri=True)
    conn.execute("PRAGMA query_only=ON")
    seen=set()
    by_guard=defaultdict(lambda: {"cases":0,"revoked_at_commit":0,
        "unsafe_writes":0,"clean_cases":0,"clean_writes_preserved":0,
        "detected_unsafe_after_commit":0,"abstentions":0,
        "false_complete_unsafe":0,"race_unsafe":0})
    per_case=[]
    all_writes=conn.execute("SELECT id,case_id,payload,entrypoint,epoch FROM writes ORDER BY id").fetchall()
    writes=defaultdict(list)
    for row in all_writes:
        assert row[1] in expected_ids
        writes[row[1]].append(row)
    policy_rows=conn.execute("SELECT case_id,epoch,revoked FROM policy_events ORDER BY case_id,epoch").fetchall()
    policy=defaultdict(dict)
    for case_id,epoch,revoked in policy_rows:
        assert case_id in expected_ids and epoch not in policy[case_id]
        policy[case_id][epoch]=bool(revoked)
    decisions=defaultdict(list)
    for case_id,api,verdict,epoch in conn.execute(
        "SELECT case_id,api,verdict,epoch FROM decisions ORDER BY id"
    ):
        assert case_id in expected_ids
        decisions[case_id].append({"api":api,"verdict":verdict,"epoch":epoch})
    for spec in cases:
        cid=spec["case_id"]
        assert cid not in seen
        seen.add(cid)
        pp=policy[cid]
        assert pp.get(0) is False
        assert max(pp)==({"before":1,"race":1,"release":2,"none":0}[spec["policy_schedule"]])
        if spec["policy_schedule"] in ("before","race"):
            assert pp.get(1) is True
        if spec["policy_schedule"]=="release":
            assert pp.get(1) is True and pp.get(2) is False
        w=writes[cid]
        assert len(w)<=1,(cid,"duplicate external writes")
        is_written=bool(w)
        # Exact physical result is checked against a separately coded schedule oracle.
        assert is_written==expected_commit(spec),(cid,"unexpected external state",spec,w,decisions[cid])
        actual_epoch=w[0][4] if w else max(pp)
        if w:
            assert w[0][2]==spec["payload"] and w[0][4] in pp
            assert w[0][3]==("/write_atomic" if spec["guard"]=="sink_atomic" else "/write_unguarded")
            assert w[0][4]==max(pp),(cid,"effect did not see the final policy epoch")
        true_revoked="R" in spec["oracle_sources"]
        unauthorized=is_written and true_revoked and pp[actual_epoch]
        clean_work="R" not in spec["oracle_sources"]
        # This audit intentionally diagnoses an unsafe write after commit; detection is not prevention.
        case_decisions=decisions[cid]
        unresolved=any(d["verdict"]=="UNRESOLVED" for d in case_decisions)
        if unresolved:assert not w
        stats=by_guard[spec["guard"]]
        stats["cases"]+=1
        stats["revoked_at_commit"]+=int(true_revoked and bool(pp[max(pp)]))
        stats["unsafe_writes"]+=int(unauthorized)
        stats["clean_cases"]+=int(clean_work)
        stats["clean_writes_preserved"]+=int(clean_work and is_written)
        stats["detected_unsafe_after_commit"]+=int(unauthorized)
        stats["abstentions"]+=int(unresolved)
        stats["false_complete_unsafe"]+=int(unauthorized and spec["scenario"]=="false_complete_clean_label")
        stats["race_unsafe"]+=int(unauthorized and spec["policy_schedule"]=="race")
        per_case.append({"case_id":cid,"scenario":spec["scenario"],"guard":spec["guard"],
                         "order":spec["order"],"seed":spec["seed"],"observed_external_writes":len(w),
                         "true_revoked_source":true_revoked,"revoked_at_commit":bool(pp[actual_epoch]),
                         "unauthorized_persisted_write":bool(unauthorized),
                         "independent_clean_effect_preserved":bool(clean_work and is_written),
                         "observed_sql_epoch":actual_epoch,"receipt_confidence":spec["receipt"] is not None})
    assert len(seen)==len(cases)
    assert len(policy)==len(cases)
    assert sum(len(z) for z in writes.values())==len(all_writes)
    out={"schema":"external-effects-readonly-audit-v1",
         "partition":manifest["plan"]["partition"],
         "cases":len(cases),"observer":"sqlite_read_only_separate_process",
         "physical_persisted_writes":len(all_writes),
         "by_guard":dict(by_guard),
         "rows":per_case,
         "passed":True,
         "limitations":["Synthetic legal policy and provenance receipts",
                        "Single local HTTP service process and SQLite durability, not a vendor",
                        "Server-side atomic policy checking assumes every protected write uses the guarded endpoint",
                        "Post-write observer detects unauthorized effects after they have already persisted",
                        "Falsely complete source receipts can cause unauthorized acceptance",
                        "Fixed templates and repeated seeds are not independent field samples"]}
    x.out.write_text(json.dumps(out,sort_keys=True,indent=2)+"\n")
    d=hashlib.sha256(x.out.read_bytes()).hexdigest()
    print("INDEPENDENT_AUDIT",json.dumps({"partition":out["partition"],"cases":len(cases),
         "persisted":len(all_writes),"by_guard":out["by_guard"],"sha256":d},sort_keys=True,separators=(",",":")))
    conn.close()

if __name__=="__main__":main()
