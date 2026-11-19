#!/usr/bin/env python3
"""Untrusted worker executes a real historical checkpoint replay.

It holds a pre-notice recipient-issued capability but never a credential that
confers authority to write to the independent protected recipient.
"""
from __future__ import annotations
import argparse,base64,json,os,sys,urllib.request,urllib.error
from pathlib import Path
from typing import TypedDict

def send(url,path,x):
    req=urllib.request.Request(url+path,data=json.dumps(x).encode(),headers={'Content-Type':'application/json'},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=20) as v:return json.loads(v.read())
    except urllib.error.HTTPError as e:return {'http_status':e.code,'error':e.read().decode()}

def forgery(token):
    if token is None:return None
    part,tag=token.split('.')
    o=json.loads(base64.urlsafe_b64decode(part+'='*((-len(part))%4)))
    o['sources']=['K']
    raw=json.dumps(o,sort_keys=True,separators=(',',':')).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')+'.'+tag

class S(TypedDict,total=False):
    case_id: str
    body: str
    token: str|None
    phase: str
    result: str

def replay(case,sink,relay):
    from langgraph.graph import StateGraph,START,END
    from langgraph.checkpoint.memory import InMemorySaver
    from langchain_core.tools import tool

    @tool('independent_recipient_write')
    def independent_recipient_write(case_id:str,body:str,token:str|None, use_relay:bool=False)->dict:
        """Attempt a downstream effect with a previously issued receiver capability."""
        return send(relay if use_relay else sink, '/relay' if use_relay else '/commit',{'case':case_id,'body':body,'token':token})

    ready={'flag':False};responses=[]
    def prepare(s:S):return {'phase':'prepared_before_notice'}
    def effect(s:S):
        if not ready['flag']:return {'result':'checkpoint_saved'}
        body=s['body'];token=s['token'];mutation=case['mutation']
        if mutation=='missing':token=None
        elif mutation=='tamper':body=body+'-MODIFIED'
        elif mutation=='forge':token=forgery(token)
        if case['mode']=='owner_live':
            direct=independent_recipient_write.invoke({'case_id':s['case_id'],'body':body,'token':token,'use_relay':False})
            responses.append({'route':'direct','response':direct})
            assert direct['verdict']=='DENY' and direct['reason']=='NO_OWNER_WRITE_AUTHORITY' or direct['verdict']=='DENY'
            mediated=independent_recipient_write.invoke({'case_id':s['case_id'],'body':body,'token':token,'use_relay':True})
            responses.append({'route':'relay','response':mediated})
        else:
            direct=independent_recipient_write.invoke({'case_id':s['case_id'],'body':body,'token':token,'use_relay':False})
            responses.append({'route':'direct','response':direct})
        if case['mutation']=='duplicate':
            duplicate=independent_recipient_write.invoke({'case_id':s['case_id'],'body':body,'token':token,'use_relay':case['mode']=='owner_live'})
            responses.append({'route':'duplicate','response':duplicate})
        return {'result':responses[-1]['response'].get('verdict','ERROR')}
    g=StateGraph(S);g.add_node('prepare',prepare);g.add_node('effect',effect)
    g.add_edge(START,'prepare');g.add_edge('prepare','effect');g.add_edge('effect',END)
    app=g.compile(checkpointer=InMemorySaver())
    cfg={'configurable':{'thread_id':case['case']}}
    app.invoke({'case_id':case['case'],'body':case['body'],'token':case.get('token')},cfg)
    past=[s.config for s in app.get_state_history(cfg) if tuple(s.next)==('effect',)]
    assert len(past)==1
    app.update_state(cfg,{'phase':'new_state_repaired','body':'INDEPENDENT_CLEAN_RECOMPUTATION'},as_node='effect')
    assert app.get_state(cfg).values['body']=='INDEPENDENT_CLEAN_RECOMPUTATION'
    print('READY '+json.dumps({'case':case['case'],'old_checkpoint_found':True}),flush=True)
    cmd=json.loads(sys.stdin.readline())
    assert cmd=={'action':'RESUME','case':case['case']},cmd
    ready['flag']=True
    app.invoke(None,past[0])
    print('DONE '+json.dumps({'case':case['case'],'results':responses,'historical_replay':True,'latest_repaired':True},sort_keys=True),flush=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--sink',required=True);p.add_argument('--relay',required=True)
    p.add_argument('--owner-key-path',required=True);p.add_argument('--enforce-uid',type=int,default=-1);a=p.parse_args()
    if a.enforce_uid>=0:assert os.getuid()==a.enforce_uid
    try:Path(a.owner_key_path).read_bytes();owned='EXPOSED'
    except PermissionError:owned='ACCESS_DENIED'
    except FileNotFoundError:owned='MISSING'
    if a.enforce_uid>=0:assert owned=='ACCESS_DENIED',owned
    print('SECURITY '+json.dumps({'recipient_owner_key':owned,'uid':os.getuid()}),flush=True)
    for line in sys.stdin:
        if not line.strip():continue
        command=json.loads(line)
        if command.get('action')=='STOP':break
        assert command.get('action')=='START'
        replay(command['case'],a.sink,a.relay)
if __name__=='__main__':main()
