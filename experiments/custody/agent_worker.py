#!/usr/bin/env python3
"""Untrusted agent. UID 12002, no vault, ledger, policy admin or signing key access."""
import argparse
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request


def post(url,path,obj,secret=None):
    h={'Content-Type':'application/json'}
    if secret is not None:h['X-Admin-Secret']=secret
    req=urllib.request.Request(url+path,json.dumps(obj).encode(),headers=h,method='POST')
    try:
        with urllib.request.urlopen(req,timeout=15) as resp:return json.loads(resp.read())
    except urllib.error.HTTPError as e:return {'http_status':e.code,'error':e.read().decode()}


def main():
    p=argparse.ArgumentParser();p.add_argument('--endpoint',required=True);p.add_argument('--private',required=True)
    p.add_argument('--agent-uid',type=int,required=True)
    args=p.parse_args()
    assert os.getuid()==args.agent_uid,(os.getuid(),args.agent_uid)
    checks={}
    for label,f in [('vault',Path(args.private)/'vault/R.dat'),('signing_key',Path(args.private)/'signing.key'),
                    ('admin_key',Path(args.private)/'admin.key'),('protected_db',Path(args.private)/'ledger.sqlite')]:
        try:f.read_bytes();checks[label]='EXPOSED'
        except PermissionError:checks[label]='ACCESS_DENIED'
        except FileNotFoundError:checks[label]='MISSING'
    checks['policy_admin_http_denied']=post(args.endpoint,'/policy',{'source':'R','revoked':False}).get('http_status')==400
    checks['test_fault_http_denied']=post(args.endpoint,'/test_inject_evidence_loss',{'seq':1,'target':'issued'}).get('http_status')==400
    checks['untracked_http_endpoint_absent']=post(args.endpoint,'/read_untracked',{'source':'R'}).get('http_status')==400
    assert all(x=='ACCESS_DENIED' for k,x in checks.items() if k in ('vault','signing_key','admin_key','protected_db')),checks
    assert all(checks[k] is True for k in ('policy_admin_http_denied','test_fault_http_denied','untracked_http_endpoint_absent')),checks
    print('SECURITY_CHECK '+json.dumps(checks,sort_keys=True),flush=True)

    from langchain_core.tools import tool
    from langgraph.graph import StateGraph,START,END
    from langgraph.checkpoint.memory import InMemorySaver
    @tool('trusted_derivation')
    def trusted_derivation(flow: str,sources:list[str])->dict:
        """Ask the trusted broker to derive a digest; cannot retrieve protected source bytes."""
        return post(args.endpoint,'/derive',{'flow':flow,'sources':sources})

    while True:
        raw=sys.stdin.readline()
        if not raw:break
        order=json.loads(raw)
        if order.get('command')=='STOP':break
        assert order.get('command')=='START'
        flow=order['flow'];sources=order['sources'];action=order['action']
        armed={'yes':False}
        replies=[]
        def derive_node(s):
            resp=trusted_derivation.invoke({'flow':s['flow'],'sources':s['sources']})
            return {'issued':resp}
        def effect_node(s):
            if not armed['yes']:return {'status':'checkpoint_before_restriction'}
            resp=s['issued'];token=resp.get('token');output=resp.get('output');target_flow=s['flow']
            if action=='missing_token':token=None
            elif action=='forge_clean':
                import base64
                part,mac=token.split('.')
                payload=json.loads(base64.urlsafe_b64decode(part+'='*((-len(part))%4)))
                payload['sources']=['K']
                part=base64.urlsafe_b64encode(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).decode().rstrip('=')
                token=part+'.'+mac
            elif action=='tamper_output':output+='-TAMPER'
            elif action=='cross_flow':target_flow='wrong-flow-'+s['flow']
            actual=post(args.endpoint,'/commit',{'flow':target_flow,'token':token,'output':output,'declared_sources':['K']})
            replies.append(actual)
            if action=='duplicate':
                again=post(args.endpoint,'/commit',{'flow':target_flow,'token':token,'output':output})
                replies.append(again)
            return {'status':actual.get('verdict','ERROR')}
        class State(dict):pass
        # TypedDict gives LangGraph explicit declared state keys.
        from typing import TypedDict
        class S(TypedDict,total=False):
            flow:str;sources:list[str];issued:dict;status:str
        graph=StateGraph(S);graph.add_node('derive',derive_node);graph.add_node('effect',effect_node)
        graph.add_edge(START,'derive');graph.add_edge('derive','effect');graph.add_edge('effect',END)
        app=graph.compile(checkpointer=InMemorySaver())
        cfg={'configurable':{'thread_id':safekey(flow,action)}}
        app.invoke({'flow':flow,'sources':sources},cfg)
        hist=[s.config for s in app.get_state_history(cfg) if tuple(s.next)==('effect',)]
        assert len(hist)==1
        checkpoint=app.get_state(hist[0]).values
        issued=checkpoint['issued'];issued_ok=issued.get('verdict')=='ALLOW'
        if issued_ok:
            app.update_state(cfg,{'status':'latest_repaired','sources':['K']},as_node='effect')
            assert app.get_state(cfg).values['status']=='latest_repaired'
        print('READY '+json.dumps({'flow':flow,'issued_ok':issued_ok,'issued_verdict':issued.get('verdict'),
          'seq':issued.get('seq'),'checkpoint_replay_possible':issued_ok},sort_keys=True),flush=True)
        nxt=json.loads(sys.stdin.readline())
        assert nxt.get('command')=='GO' and nxt.get('flow')==flow
        if issued_ok:
            armed['yes']=True
            app.invoke(None,hist[0])
        print('DONE '+json.dumps({'flow':flow,'issued_ok':issued_ok,'results':replies,
            'historical_replay':issued_ok,'latest_state_repaired':issued_ok},sort_keys=True),flush=True)


def safekey(flow,action):return flow+'-'+action
if __name__=='__main__':main()
