#!/usr/bin/env python3
"""Replay real LangGraph checkpoints into a distinct HTTP/SQLite effect service.

The service sees only opaque case keys, payloads and deliberately injected source
receipts. It never receives the oracle truth used by the post-run observer.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import random
import urllib.request
from pathlib import Path
from typing import TypedDict

ORDERS=("acosta_notice","necec_challenge")
GUARDS=("cached","client_precheck","sink_atomic")
# label, oracle sources, asserted receipt sources, receipt status, policy timing
SCENARIOS=(
 ("revoked_before_check",("R",),("R",),"valid","before"),
 ("independent_clean",("K",),("K",),"valid","before"),
 ("mixed_provenance",("R","K"),("R","K"),"valid","before"),
 ("revoke_after_client_check",("R",),("R",),"valid","race"),
 ("clean_during_revocation",("K",),("K",),"valid","race"),
 ("mixed_during_revocation",("R","K"),("R","K"),"valid","race"),
 ("missing_revoked_receipt",("R",),None,"missing","before"),
 ("missing_clean_receipt",("K",),None,"missing","before"),
 ("stale_revoked_digest",("R",),("R",),"wrong_digest","before"),
 ("stale_clean_digest",("K",),("K",),"wrong_digest","before"),
 ("false_complete_clean_label",("R",),("K",),"valid","before"),
 ("false_complete_revoked_label",("K",),("R",),"valid","before"),
 ("mixed_with_omitted_revoked",("R","K"),("K",),"valid","before"),
 ("revocation_withdrawn",("R",),("R",),"valid","release"),
 ("prior_valid_authorization",("R",),("R",),"valid","none"),
 ("incomplete_mixed_receipt",("R","K"),("R","K"),"incomplete","before"),
)
DEV_SEEDS=(901,902,903,904)
EVAL_SEEDS=tuple(range(7001,7013))

def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()

def send(url, action, payload):
    data=json.dumps(payload,sort_keys=True).encode()
    req=urllib.request.Request(url+action,data=data,headers={"Content-Type":"application/json"},method="POST")
    with urllib.request.urlopen(req,timeout=20) as x:
        reply=json.loads(x.read())
    if "error" in reply:raise RuntimeError(reply)
    return reply

class State(TypedDict,total=False):
    case_id: str
    payload: str
    cached_ok: bool
    status: str

def make(order,seed,tpl,guard):
    label,truth,receipt_src,fault,timing=tpl
    # No semantic labels or oracle truth in the policy-facing identifier or payload.
    caseid=sha(f"opaque:{order}:{seed}:{label}:{guard}")[:32]
    payload=sha(f"payload:{order}:{seed}:{label}")[:48]
    receipt=None
    if receipt_src is not None:
        receipt={"sources":list(receipt_src),"complete":fault!="incomplete",
                 "payload_digest":sha(payload if fault!="wrong_digest" else payload+"-old")}
    return {"case_id":caseid,"order":order,"seed":seed,"scenario":label,"guard":guard,
            "oracle_sources":list(truth),"policy_schedule":timing,
            "payload":payload,"receipt":receipt}

def execute(spec,endpoint):
    from langgraph.graph import StateGraph, START, END
    from langgraph.checkpoint.memory import InMemorySaver
    cid=spec["case_id"]
    payload=spec["payload"]
    send(endpoint,"/setup",{"case_id":cid,"receipt":spec["receipt"]})
    flags={"active":False}
    replies=[]
    def prepare(state: State):
        return {"cached_ok":True}
    def effect(state: State):
        if not flags["active"]:
            return {"status":"prepared_without_external_effect"}
        mode=spec["guard"]
        timing=spec["policy_schedule"]
        if mode=="cached":
            if timing=="race":
                send(endpoint,"/set_policy",{"case_id":cid,"revoked":True})
            if state["cached_ok"]:
                reply=send(endpoint,"/write_unguarded",{"case_id":cid,"payload":state["payload"]})
            else:
                reply={"verdict":"BLOCK","committed":False}
        elif mode=="client_precheck":
            auth=send(endpoint,"/authorize",{"case_id":cid,"payload":state["payload"]})
            if timing=="race":
                send(endpoint,"/set_policy",{"case_id":cid,"revoked":True})
            if auth["verdict"]=="ALLOW":
                reply=send(endpoint,"/write_unguarded",{"case_id":cid,"payload":state["payload"]})
            else:
                reply={"verdict":auth["verdict"],"committed":False}
        elif mode=="sink_atomic":
            if timing=="race":
                send(endpoint,"/set_policy",{"case_id":cid,"revoked":True})
            reply=send(endpoint,"/write_atomic",{"case_id":cid,"payload":state["payload"]})
        else:raise ValueError(mode)
        replies.append(reply)
        return {"status":"effect_ack"}
    g=StateGraph(State)
    g.add_node("prepare",prepare);g.add_node("effect",effect)
    g.add_edge(START,"prepare");g.add_edge("prepare","effect");g.add_edge("effect",END)
    app=g.compile(checkpointer=InMemorySaver())
    cfg={"configurable":{"thread_id":cid}}
    app.invoke({"case_id":cid,"payload":payload},cfg)
    snap=[s.config for s in app.get_state_history(cfg) if tuple(s.next)==("effect",)]
    assert len(snap)==1
    flags["active"]=True
    app.update_state(cfg,{"payload":"latest_recomputed_clean_payload","status":"latest_repaired"},as_node="effect")
    assert app.get_state(cfg).values["payload"]=="latest_recomputed_clean_payload"
    assert app.get_state(snap[0]).values["payload"]==payload
    timing=spec["policy_schedule"]
    if timing in ("before","release"):
        send(endpoint,"/set_policy",{"case_id":cid,"revoked":True})
        if timing=="release":
            send(endpoint,"/set_policy",{"case_id":cid,"revoked":False})
    app.invoke(None,snap[0])
    assert len(replies)==1
    return {"case_id":cid,"guard":spec["guard"],"ack":replies[0],
            "replayed_historical_snapshot":True,"latest_state_repaired":True}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--partition",choices=("plan","dev","holdout"),required=True)
    ap.add_argument("--endpoint",default="http://127.0.0.1:8765")
    args=ap.parse_args()
    assert len(SCENARIOS)==16
    assert len({x[0] for x in SCENARIOS})==len(SCENARIOS)
    seeds=DEV_SEEDS if args.partition=="dev" else EVAL_SEEDS
    plan={"schema":"external-effect-evaluation-v1","partition":args.partition,
          "templates":len(SCENARIOS),"guards":list(GUARDS),"orders":list(ORDERS),
          "dev_seeds":list(DEV_SEEDS),"holdout_seeds":list(EVAL_SEEDS)}
    if args.partition=="plan":
        Path("external_effect_plan.json").write_text(json.dumps(plan,indent=2,sort_keys=True)+"\n")
        print("PLAN",json.dumps(plan,sort_keys=True))
        return
    manifest=[make(o,s,t,g) for s in seeds for o in ORDERS for t in SCENARIOS for g in GUARDS]
    assert len({x["case_id"] for x in manifest})==len(manifest)
    # Freeze oracle before any replay is executed. Never forward oracle_sources.
    manifest_path=Path(f"external_effect_{args.partition}_oracle.json")
    manifest_path.write_text(json.dumps({"plan":plan,"cases":manifest},indent=2,sort_keys=True)+"\n")
    # Always use a fixed seed matrix. Permutation only changes execution order.
    indices=list(range(len(manifest)))
    rng=random.Random(5031 if args.partition=="dev" else 81053)
    rng.shuffle(indices)
    acks=[execute(manifest[i],args.endpoint) for i in indices]
    Path(f"external_effect_{args.partition}_acks.json").write_text(
        json.dumps({"acks":acks},indent=2,sort_keys=True)+"\n"
    )
    assert all(x["replayed_historical_snapshot"] and x["latest_state_repaired"] for x in acks)
    print("REPLAY_COMPLETE",json.dumps({"partition":args.partition,"scenarios":len(manifest),
                                  "source":"real_langgraph_checkpoints",
                                  "effect_sink":"out_of_process_http_sqlite",
                                  "ack_count":len(acks)},sort_keys=True))

if __name__=="__main__":main()
