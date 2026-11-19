#!/usr/bin/env python3
"""Clause-grounded synthetic state policy and LangGraph replay probes.
Uses entered-order source pointers. No real case records or AI-provider calls.
"""
import json, sqlite3, tempfile
from collections import Counter
from pathlib import Path
from typing import TypedDict
from importlib.metadata import version
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver

SOURCE_ORDERS = {
  "acosta": {"docket":"1:25-cv-01119","doc":39,"court":"S.D.N.Y.",
      "pdf":"https://cases.justia.com/federal/district-courts/new-york/nysdce/1%3A2025cv01119/636556/39/0.pdf",
      "clauses":["14(c)(ii), p.15","14(d), p.15"],"disposition_window":"10 days"},
  "necec": {"docket":"1:25-cv-05351","doc":58,"court":"S.D.N.Y.",
      "pdf":"https://cases.justia.com/federal/district-courts/new-york/nysdce/1%3A2025cv05351/644926/58/0.pdf",
      "clauses":["IV.1(b), pp.17-18"],"disposition_window":"2 business days"},
  "broadway": {"docket":"1:25-cv-07386","doc":37,"court":"S.D.N.Y.",
      "pdf":"https://cases.justia.com/federal/district-courts/new-york/nysdce/1%3A2025cv07386/648886/37/0.pdf",
      "clauses":["paragraph 16, p.12"]},
  "spheres": {"docket":"2:25-cv-12197","doc":34,"court":"C.D. Cal.",
      "pdf":"https://cases.justia.com/federal/district-courts/california/cacdce/2%3A2025cv12197/1000608/34/0.pdf",
      "clauses":["12.4(b)-(c), p.20","12.4(d)-(e), p.21"]}
}
# A fixture's declarations are simulated contractual assumptions. They DO NOT
# establish any actual provider's compliance.
FULL_SPHERES = dict(no_training=True, access_control=True, deletable=True,
                    enforceable_contract=True, case_only=True, subproviders_controlled=True,
                    safeguards=True)
FULL_BROADWAY = dict(no_training=True, access_control=True, deletable=True)
HALT = {"noticed","challenge_pending","no_challenge","claim_upheld"}

def evaluate(order, action, phase="ordinary", dep="revoked", tool=None,
             assurance=None, deletion_evidence=None):
    if dep == "unknown":
        return "UNRESOLVED"
    affected = dep in {"revoked","mixed"}
    if action in {"replay_effect","read_source"}:
        if not affected:
            return "ALLOW"
        return "HOLD" if phase in HALT else "ALLOW"
    if action == "destroy_derivative":
        if not affected:
            return "BLOCK"
        if order == "necec":
            return "ALLOW" if phase in {"no_challenge","claim_upheld"} else ("UNRESOLVED" if phase=="claim_withdrawn" else "HOLD")
        return "UNRESOLVED"
    if action == "extract_review_db":
        return "ALLOW" if order == "acosta" and affected and phase in HALT else "UNRESOLVED"
    if action == "certify_if_requested":
        return "ALLOW" if order == "necec" and phase in HALT else "UNRESOLVED"
    if action == "ai_submit":
        if order not in {"spheres","broadway"}:
            return "UNRESOLVED"
        if tool == "public":
            return "BLOCK"
        if tool != "closed":
            return "UNRESOLVED"
        if order == "spheres" and phase == "challenge_pending":
            return "BLOCK"
        if assurance is None:
            return "UNRESOLVED"
        required = ["no_training","access_control","deletable"]
        if order == "spheres":
            required += ["enforceable_contract","case_only","subproviders_controlled","safeguards"]
        if any(assurance.get(k) is False for k in required):
            return "BLOCK"
        if any(assurance.get(k) is not True for k in required):
            return "UNRESOLVED"
        return "ALLOW"
    if action == "provider_deletion_verified":
        if order != "spheres":
            return "UNRESOLVED"
        return "ALLOW" if deletion_evidence == "independently_verified" else "UNRESOLVED"
    raise ValueError(action)

# Explicit independent oracle expectations, fixed before run.
FIXTURES = [
 ("AC1","acosta","replay_effect","noticed","revoked",None,None,None,"HOLD"),
 ("AC2","acosta","replay_effect","noticed","mixed",None,None,None,"HOLD"),
 ("AC3","acosta","replay_effect","noticed","clean",None,None,None,"ALLOW"),
 ("AC4","acosta","extract_review_db","noticed","revoked",None,None,None,"ALLOW"),
 ("AC5","acosta","read_source","ordinary","revoked",None,None,None,"ALLOW"),
 ("AC6","acosta","read_source","challenge_pending","revoked",None,None,None,"HOLD"),
 ("AC7","acosta","replay_effect","noticed","unknown",None,None,None,"UNRESOLVED"),
 ("NE1","necec","replay_effect","noticed","revoked",None,None,None,"HOLD"),
 ("NE2","necec","replay_effect","challenge_pending","clean",None,None,None,"ALLOW"),
 ("NE3","necec","destroy_derivative","challenge_pending","revoked",None,None,None,"HOLD"),
 ("NE4","necec","destroy_derivative","no_challenge","revoked",None,None,None,"ALLOW"),
 ("NE5","necec","destroy_derivative","claim_upheld","revoked",None,None,None,"ALLOW"),
 ("NE6","necec","destroy_derivative","claim_withdrawn","revoked",None,None,None,"UNRESOLVED"),
 ("NE7","necec","certify_if_requested","noticed","revoked",None,None,None,"ALLOW"),
 ("NE8","necec","destroy_derivative","no_challenge","clean",None,None,None,"BLOCK"),
 ("BR1","broadway","ai_submit","ordinary","revoked","public",None,None,"BLOCK"),
 ("BR2","broadway","ai_submit","ordinary","revoked","closed",FULL_BROADWAY,None,"ALLOW"),
 ("BR3","broadway","ai_submit","ordinary","revoked","closed",{"no_training":True,"access_control":True},None,"UNRESOLVED"),
 ("BR4","broadway","ai_submit","ordinary","revoked","closed",{"no_training":False,"access_control":True,"deletable":True},None,"BLOCK"),
 ("SP1","spheres","ai_submit","ordinary","revoked","public",None,None,"BLOCK"),
 ("SP2","spheres","ai_submit","ordinary","revoked","closed",FULL_SPHERES,None,"ALLOW"),
 ("SP3","spheres","ai_submit","ordinary","revoked","closed",{k:v for k,v in FULL_SPHERES.items() if k!="safeguards"},None,"UNRESOLVED"),
 ("SP4","spheres","ai_submit","challenge_pending","revoked","closed",FULL_SPHERES,None,"BLOCK"),
 ("SP5","spheres","provider_deletion_verified","ordinary","revoked",None,None,"provider_claim","UNRESOLVED"),
 ("SP6","spheres","provider_deletion_verified","ordinary","revoked",None,None,"independently_verified","ALLOW"),
]
class S(TypedDict, total=False):
    payload: str
    declared: str
    approval_at_prepare: bool
    result: str

# Config: name, court, guard, declaration, true support, notice phase,
# expected next write, expected exposure
REPLAY_CASES = [
 ("acosta_unchecked","acosta","none","revoked","revoked","noticed",1,True),
 ("acosta_cached","acosta","cached","revoked","revoked","noticed",1,True),
 ("acosta_guarded","acosta","current","revoked","revoked","noticed",0,False),
 ("acosta_clean","acosta","current","clean","clean","noticed",1,False),
 ("necec_guarded","necec","current","revoked","revoked","challenge_pending",0,False),
 ("necec_unknown","necec","current","unknown","revoked","noticed",0,False),
 ("necec_false_complete","necec","current","clean","revoked","noticed",1,True),
 ("necec_withdrawn","necec","current","revoked","revoked","claim_withdrawn",1,False),
]

def replay_one(spec, directory):
    name,order,guard,declared,truth,post_phase,expected_added,expected_exposure=spec
    phase={"value":"ordinary"}
    conn=sqlite3.connect(directory/(name+".sqlite"))
    conn.execute("CREATE TABLE effects (seq INTEGER PRIMARY KEY, body TEXT, phase TEXT)")
    conn.commit()
    decisions=[]
    def prepare(state:S):
        return {"approval_at_prepare":evaluate(order,"replay_effect",phase["value"],state.get("declared","unknown"))=="ALLOW"}
    def sink(state:S):
        status = ("ALLOW" if guard=="none" else
                  ("ALLOW" if state["approval_at_prepare"] else "HOLD") if guard=="cached" else
                  evaluate(order,"replay_effect",phase["value"],state.get("declared","unknown")))
        decisions.append(status)
        if status=="ALLOW":
            conn.execute("INSERT INTO effects(body,phase) VALUES(?,?)",(state["payload"],phase["value"]))
            conn.commit()
            return {"result":"written"}
        return {"result":status}
    graph=StateGraph(S)
    graph.add_node("prepare",prepare);graph.add_node("sink",sink)
    graph.add_edge(START,"prepare");graph.add_edge("prepare","sink");graph.add_edge("sink",END)
    saver=InMemorySaver(); app=graph.compile(checkpointer=saver)
    cfg={"configurable":{"thread_id":name}}
    app.invoke({"payload":"R_MARKER" if truth=="revoked" else "K_MARKER","declared":declared},cfg)
    before=conn.execute("SELECT COUNT(*) FROM effects").fetchone()[0]
    states=list(app.get_state_history(cfg))
    old=[s.config for s in states if tuple(s.next)==("sink",)]
    assert len(old)==1,(name,[(s.next,s.values) for s in states])
    phase["value"]=post_phase
    app.update_state(cfg,{"payload":"K_MARKER","declared":"clean","result":"latest_clean"},as_node="sink")
    assert app.get_state(cfg).values["payload"]=="K_MARKER"
    app.invoke(None,old[0])
    added=conn.execute("SELECT COUNT(*) FROM effects").fetchone()[0]-before
    assert added==expected_added,(name,added,expected_added,decisions)
    exposed=(truth=="revoked" and added>0 and post_phase in HALT)
    assert exposed==expected_exposure,(name,exposed,expected_exposure)
    record={"name":name,"court_order":order,"guard":guard,
            "declared_dependency":declared,"oracle_dependency":truth,
            "replay_phase":post_phase,"replay_decision":decisions[-1],
            "writes_before_replay":before,"new_writes_from_historical_replay":added,
            "unauthorized_write_observed":exposed,"old_checkpoint_used":True,
            "latest_state_cleaned":True}
    conn.close()
    return record

def main():
    ledger=[]
    for caseid,order,action,phase,dep,tool,assurance,deletion_evidence,expected in FIXTURES:
        actual=evaluate(order,action,phase,dep,tool,assurance,deletion_evidence)
        assert actual==expected,(caseid,expected,actual)
        ledger.append({"id":caseid,"order":order,"expected":expected,"actual":actual,"pass":True})
    assert SOURCE_ORDERS["acosta"]["disposition_window"]=="10 days"
    assert SOURCE_ORDERS["necec"]["disposition_window"]=="2 business days"
    with tempfile.TemporaryDirectory() as d:
        replays=[replay_one(x,Path(d)) for x in REPLAY_CASES]
    result={"schema":"court-grounded-checkpoint-replay-v1",
         "status":"PASS","langgraph_version":version("langgraph"),"source_orders":SOURCE_ORDERS,
         "fixture_count":len(ledger),"fixture_passed":len(ledger),
         "deadline_controls_passed":2,"replay_count":len(replays),"replay_passed":len(replays),
         "policy_fixtures":ledger,"replay_results":replays,
         "limitations":["Manually mapped clauses to synthetic operations; no automatic legal interpretation",
                        "No real litigation data","InMemorySaver in this legal-policy run; prior independent R2 verified PostgresSaver",
                        "No external provider contract or deletion verified",
                        "Replaying an older checkpoint after current-state repair is permitted by this test harness",
                        "A false complete dependency label remains unsafe",
                        "No real-world legal deadline calculation or legal certification"]}
    Path("legal_order_result.json").write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
    print("RESULT",json.dumps({"fixture_passed":len(ledger),"replay_passed":len(replays),"version":result["langgraph_version"],
        "outcomes":{r["name"]:[r["new_writes_from_historical_replay"],r["unauthorized_write_observed"]] for r in replays}},sort_keys=True))
if __name__=="__main__":main()
