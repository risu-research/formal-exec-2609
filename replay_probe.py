#!/usr/bin/env python3
"""A synthetic checkpoint-to-effect replay test using real LangGraph checkpoints."""
import json
import sqlite3
import tempfile
from pathlib import Path
from importlib.metadata import version
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver

R="SOURCE_R_SYNTHETIC"
K="SOURCE_K_SYNTHETIC"

class Authority:
    def __init__(self):
        self.states={R:"allowed",K:"allowed"}
        self.version=1
    def set(self,source,state):
        assert source in self.states and state in ("allowed","sequestered","deleted")
        self.states[source]=state
        self.version+=1
    def decide(self,deps):
        if not isinstance(deps,list) or not deps or any(x not in self.states for x in deps):
            return "unresolved"
        if any(self.states[x]!="allowed" for x in deps):
            return "blocked"
        return "allowed"

class State(TypedDict,total=False):
    payload: str
    declared_deps: list[str] | None
    approval_at_prepare: bool
    policy_version_at_prepare: int
    result: str

CASES=(
 ("no_guard","none",[R,K],[R,K],True,False),
 ("entry_only","entry",[R,K],[R,K],True,False),
 ("pre_effect","sink",[R,K],[R,K],False,False),
 ("clean_independent","sink",[K],[K],False,False),
 ("unknown_dependency","sink",None,[R,K],False,False),
 ("false_complete_metadata","sink",[K],[R,K],True,False),
 ("release_after_sequester","sink",[R,K],[R,K],False,True),
)

def test_case(case,base):
    name,mode,declared,truth,expect_unsafe,release=case
    auth=Authority()
    db=sqlite3.connect(base/(name+".sqlite"))
    db.execute("CREATE TABLE effects(seq INTEGER PRIMARY KEY,payload TEXT,policy_version INTEGER)")
    db.commit()
    attempts=[]
    payload="R_and_K" if R in truth else "K_only"
    def prepare(s:State):
        return {"approval_at_prepare":auth.decide(s.get("declared_deps"))=="allowed",
                "policy_version_at_prepare":auth.version}
    def emit(s:State):
        if mode=="none":decision="allowed"
        elif mode=="entry":decision="allowed" if s["approval_at_prepare"] else "blocked"
        else:decision=auth.decide(s.get("declared_deps"))
        attempts.append({"decision":decision,"version":auth.version})
        if decision=="allowed":
            db.execute("INSERT INTO effects(payload,policy_version) VALUES(?,?)",(s["payload"],auth.version))
            db.commit()
            return {"result":"written"}
        return {"result":decision}
    graph=StateGraph(State)
    graph.add_node("prepare",prepare)
    graph.add_node("emit",emit)
    graph.add_edge(START,"prepare")
    graph.add_edge("prepare","emit")
    graph.add_edge("emit",END)
    saver=InMemorySaver()
    app=graph.compile(checkpointer=saver)
    cfg={"configurable":{"thread_id":name}}
    app.invoke({"payload":payload,"declared_deps":declared},cfg)
    snapshots=list(app.get_state_history(cfg))
    paused=[s for s in snapshots if tuple(s.next)==("emit",)]
    assert len(paused)==1, (name,[(s.next,s.values) for s in snapshots])
    old=paused[0].config
    assert db.execute("SELECT COUNT(*) FROM effects").fetchone()[0]==(0 if name=="unknown_dependency" else 1)
    auth.set(R,"sequestered")
    app.update_state(cfg,{"payload":"K_only","declared_deps":[K],"result":"repaired_latest"},as_node="emit")
    assert app.get_state(cfg).values["payload"]=="K_only"
    assert any(tuple(s.next)==("emit",) and s.values.get("payload")==payload for s in app.get_state_history(cfg))
    before=db.execute("SELECT COUNT(*) FROM effects").fetchone()[0]
    app.invoke(None,old)
    after=db.execute("SELECT COUNT(*) FROM effects").fetchone()[0]
    first_writes=after-before
    first_decision=attempts[-1]["decision"]
    if release:
        assert first_writes==0 and first_decision=="blocked"
        auth.set(R,"allowed")
        app.invoke(None,old)
    final=db.execute("SELECT COUNT(*) FROM effects").fetchone()[0]
    unsafe=R in truth and first_writes>0
    assert unsafe==expect_unsafe,(name,"unexpected safety result",unsafe,attempts)
    expected_first={"no_guard":"allowed","entry_only":"allowed","pre_effect":"blocked",
                    "clean_independent":"allowed","unknown_dependency":"unresolved",
                    "false_complete_metadata":"allowed","release_after_sequester":"blocked"}[name]
    assert first_decision==expected_first,(name,first_decision,expected_first)
    if release:
        assert final-after==1 and attempts[-1]["decision"]=="allowed"
    else:
        assert final-after==0
    if name=="clean_independent":assert first_writes==1
    if name in ("pre_effect","unknown_dependency"):assert first_writes==0
    rows=db.execute("SELECT seq,payload,policy_version FROM effects ORDER BY seq").fetchall()
    db.close()
    return {"name":name,"guard_mode":mode,"declared_deps":declared,
            "oracle_truth_deps":truth,"baseline_effects":before,
            "first_replay_decision":first_decision,"first_replay_new_effects":first_writes,
            "unauthorized_effect_observed":unsafe,"total_effects_after_replays":final,
            "attempts":attempts,"rows":rows,
            "old_checkpoint_retained":True,"latest_view_repaired":True,
            "checkpoint_count_before":len(snapshots),
            "checkpoint_count_after_repair":len(list(app.get_state_history(cfg)))}

def main():
    a=Authority()
    assert a.decide([K])=="allowed" and a.decide(None)=="unresolved"
    a.set(R,"sequestered")
    assert a.decide([R])=="blocked" and a.decide([K])=="allowed"
    a.set(R,"allowed")
    assert a.decide([R])=="allowed"
    with tempfile.TemporaryDirectory() as d:
        results=[test_case(c,Path(d)) for c in CASES]
    out={"schema":"checkpoint-effect-v1","langgraph_version":version("langgraph"),
         "case_count":len(results),"all_assertions_passed":True,"cases":results,
         "limitations":["Constructed synthetic markers","InMemorySaver only",
                        "SQLite effect sink is local","Incorrectly complete dependency metadata remains unsafe"]}
    Path("result.json").write_text(json.dumps(out,indent=2,sort_keys=True)+"\n")
    print("RESULT",json.dumps({"passed":len(results),"version":out["langgraph_version"],
           "outcomes":{r["name"]:[r["first_replay_decision"],r["unauthorized_effect_observed"]]
                       for r in results}}))
if __name__=="__main__":main()
