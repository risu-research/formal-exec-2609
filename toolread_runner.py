#!/usr/bin/env python3
"""Real LangChain-tool source reads and nondeterministic LangGraph transforms.

Every tool call returns actual HTTP source data. The source service, not checkpoint
metadata, records the observed source-read events. The withheld oracle manifest is
used later only by an independent audit process.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import random
import secrets
import urllib.request
from pathlib import Path
from typing import TypedDict

ORDERS=('acosta_notice','necec_challenge')
GUARDS=('cached','declared','tool_trace')
# name, actual contributing sources, uninstrumented source subset, claimed sources,
# transform-binding fault, policy scenario.
SCENARIOS=(
 ('revoked_logged',('R',),(),('R',),'none','notice'),
 ('clean_logged',('K',),(),('K',),'none','notice'),
 ('mixed_logged',('R','K'),(),('R','K'),'none','notice'),
 ('revoked_declared_clean',('R',),(),('K',),'none','notice'),
 ('clean_declared_revoked',('K',),(),('R',),'none','notice'),
 ('mixed_omitted_claim',('R','K'),(),('K',),'none','notice'),
 ('missing_claim_revoked',('R',),(),None,'none','notice'),
 ('missing_claim_clean',('K',),(),None,'none','notice'),
 ('untraced_revoked',('R',),('R',),('R',),'none','notice'),
 ('untraced_clean',('K',),('K',),('K',),'none','notice'),
 ('partial_bypass_revoked',('K','R'),('R',),('K',),'none','notice'),
 ('tampered_output_clean',('K',),(),('K',),'tamper','notice'),
 ('stale_transform_id_clean',('K',),(),('K',),'stale_id','notice'),
 ('released_revoked',('R',),(),('R',),'none','release'),
 ('nondet_clean_repeat',('K',),(),('K',),'none','notice'),
)
DEV_SEEDS=(71,72)
HELDOUT_SEEDS=(6101,6102,6103,6104,6105,6106)
SOURCES={'R':'SYNTHETIC_REVOKED_DISCOVERY_RECORD_V1',
         'K':'SYNTHETIC_INDEPENDENT_LEGAL_RECORD_V1'}

def digest(s):return hashlib.sha256(s.encode('utf-8')).hexdigest()

def encode_transform(values,nonce):
    return digest(json.dumps(values,sort_keys=True,separators=(',',':'))+'::'+nonce)

def post(endpoint,op,data):
    req=urllib.request.Request(endpoint+op,data=json.dumps(data,sort_keys=True).encode(),
            headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(req,timeout=20) as r:
        reply=json.loads(r.read())
    assert 'error' not in reply,(op,reply)
    return reply

class State(TypedDict,total=False):
    flow_id: str
    read_sources: list[str]
    untracked: list[str]
    declared: list[str] | None
    read_values: list[str]
    read_event_ids: list[int]
    transform_id: int
    output: str
    observed_output: str
    cached_authorized: bool
    execution_status: str

def run_one(spec,endpoint):
    from langchain_core.tools import tool
    from langgraph.graph import StateGraph,START,END
    from langgraph.checkpoint.memory import InMemorySaver

    @tool('read_court_record')
    def read_court_record(flow_id:str, source_id:str, untracked:bool=False)->dict:
        """Read a source record from an external HTTP tool; the service records tracked accesses."""
        return post(endpoint,'/read_untracked' if untracked else '/read',
                    {'flow_id':flow_id,'source_id':source_id})

    flow=spec['flow_id']
    post(endpoint,'/setup',{'flow_id':flow})
    armed={'value':False}
    effect_replies=[]

    def read_node(s:State):
        vals=[]; ids=[]
        for source in s['read_sources']:
            response=read_court_record.invoke({'flow_id':s['flow_id'],'source_id':source,
                                                'untracked':source in s['untracked']})
            vals.append(response['value'])
            if response['source_event_id'] is not None:
                ids.append(response['source_event_id'])
        return {'read_values':vals,'read_event_ids':ids,'cached_authorized':True}

    def transform_node(s:State):
        nonce=secrets.token_hex(16)
        actual=encode_transform(s['read_values'],nonce)
        register=post(endpoint,'/transform',{'flow_id':s['flow_id'],'output':actual,'nonce':nonce})
        output=actual+':tampered' if spec['fault']=='tamper' else actual
        tid=register['transform_id']+100000000 if spec['fault']=='stale_id' else register['transform_id']
        return {'output':output,'observed_output':actual,'transform_id':tid}

    def effect_node(s:State):
        if not armed['value']:
            return {'execution_status':'checkpoint_saved_pre_notice'}
        reply=post(endpoint,'/effect',{'flow_id':s['flow_id'],'mode':spec['guard'],
                'output':s['output'],'transform_id':s['transform_id'],
                'declared_sources':s['declared'],'cached_ok':s.get('cached_authorized')})
        effect_replies.append(reply)
        return {'execution_status':reply['verdict']}

    g=StateGraph(State)
    g.add_node('read',read_node);g.add_node('transform',transform_node);g.add_node('effect',effect_node)
    g.add_edge(START,'read');g.add_edge('read','transform');g.add_edge('transform','effect');g.add_edge('effect',END)
    app=g.compile(checkpointer=InMemorySaver())
    cfg={'configurable':{'thread_id':flow}}
    app.invoke({'flow_id':flow,'read_sources':spec['sources'], 'untracked':spec['untracked'],
                'declared':spec['declared']},cfg)
    history=list(app.get_state_history(cfg))
    old=[x.config for x in history if tuple(x.next)==('effect',)]
    assert len(old)==1,(flow,[(x.next,x.values.keys()) for x in history])
    saved=app.get_state(old[0]).values
    assert saved['output'] and saved['transform_id']
    assert len(saved['read_values'])==len(spec['sources'])
    assert len(saved['read_event_ids'])==len(spec['sources'])-len(spec['untracked'])
    armed['value']=True
    app.update_state(cfg,{'output':'LATEST_STATE_CLEAN','declared':['K'],
                         'execution_status':'latest_repaired'},as_node='effect')
    assert app.get_state(cfg).values['output']=='LATEST_STATE_CLEAN'
    if spec['policy']=='notice':post(endpoint,'/policy',{'flow_id':flow,'revoked':True})
    elif spec['policy']=='release':
        post(endpoint,'/policy',{'flow_id':flow,'revoked':True})
        post(endpoint,'/policy',{'flow_id':flow,'revoked':False})
    else:raise ValueError(spec['policy'])
    app.invoke(None,old[0])
    assert len(effect_replies)==1
    return {'flow_id':flow,'guard':spec['guard'],'client_ack':effect_replies[0],
            'checkpoint_source_reads':len(saved['read_values']),
            'checkpoint_logged_events':len(saved['read_event_ids']),
            'historical_snapshot_replayed':True,'latest_state_repaired':True}

def make(order,seed,scenario,mode):
    label,truth,untracked,declared,fault,policy=scenario
    opaque=digest(f'flow:{order}:{seed}:{label}:{mode}')[:40]
    return {'flow_id':opaque,'order':order,'seed':seed,'scenario':label,
            'sources':list(truth),'untracked':list(untracked),
            'declared':list(declared) if declared is not None else None,
            'fault':fault,'policy':policy,'guard':mode}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--partition',choices=('plan','dev','holdout'),required=True)
    ap.add_argument('--endpoint',default='http://127.0.0.1:8778')
    a=ap.parse_args()
    assert len(SCENARIOS)==15
    assert set(DEV_SEEDS).isdisjoint(HELDOUT_SEEDS)
    plan={'schema':'observed-tool-read-nondeterminism-v1','partition':a.partition,'scenarios':[s[0] for s in SCENARIOS],
          'policies':list(ORDERS),'guards':list(GUARDS),'dev_seeds':list(DEV_SEEDS),
          'heldout_seeds':list(HELDOUT_SEEDS)}
    if a.partition=='plan':
        Path('tool_read_plan.json').write_text(json.dumps(plan,indent=2,sort_keys=True)+'\n')
        print('PLAN',json.dumps(plan,sort_keys=True))
        return
    seeds=DEV_SEEDS if a.partition=='dev' else HELDOUT_SEEDS
    specs=[make(o,seed,s,g) for seed in seeds for o in ORDERS for s in SCENARIOS for g in GUARDS]
    assert len({x['flow_id'] for x in specs})==len(specs)
    Path(f'tool_read_{a.partition}_oracle.json').write_text(json.dumps({'plan':plan,'specs':specs},indent=2,sort_keys=True)+'\n')
    order=list(range(len(specs)));random.Random(123 if a.partition=='dev' else 947).shuffle(order)
    replies=[run_one(specs[i],a.endpoint) for i in order]
    Path(f'tool_read_{a.partition}_acks.json').write_text(json.dumps({'replies':replies},indent=2,sort_keys=True)+'\n')
    print('REPLAY_COMPLETE',json.dumps({'partition':a.partition,'count':len(replies),
           'real_tool':'langchain_core.tools.tool.invoke over HTTP','transform':'secrets.token_hex(16)',
           'checkpoint':'LangGraph InMemorySaver','sink':'out_of_process_SQLite'}))

if __name__=='__main__':main()
