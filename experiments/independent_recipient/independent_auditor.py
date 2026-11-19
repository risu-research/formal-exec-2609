#!/usr/bin/env python3
"""Offline read-only observer for an independently owned recipient effect database.

Receiver mode, policy acknowledgements, issuer claims, physical effect rows and
predeclared oracle inputs must agree. An independent observer can detect
unauthorized effects but cannot un-commit them.
"""
from __future__ import annotations
import argparse,hashlib,json,sqlite3
from collections import defaultdict,Counter
from pathlib import Path

MODES=('open','static','live','owner_live')
INVALID={'forge_clean_label','tamper_clean_output','wrong_target_clean','missing_token_clean'}
REVOCATION_BLOCK={'revoked_after_notice','mixed_after_notice'}
def sha(x):return hashlib.sha256(x.encode()).hexdigest()

def expected(name,mode):
    if mode=='open':return 'ALLOW'
    if name in INVALID:return 'DENY'
    if mode in ('live','owner_live') and name in REVOCATION_BLOCK:return 'DENY'
    return 'ALLOW'

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--oracle',required=True)
    p.add_argument('--responses',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    oracle=json.loads(Path(a.oracle).read_text());cases=oracle['cases'];plan=oracle['plan']
    responders=json.loads(Path(a.responses).read_text())['responses']
    by_resp={d['case']:d for d in responders};assert len(by_resp)==len(cases)==len(responders)
    sql=sqlite3.connect('file:'+str(Path(a.db).resolve())+'?mode=ro',uri=True)
    sql.execute('PRAGMA query_only=ON')
    assert sql.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    bycase={row[0]:(row[1],bool(row[2]),row[3]) for row in sql.execute('SELECT case_id,mode,r_revoked,epoch FROM cases')}
    events=defaultdict(list)
    for case,epoch,revoked in sql.execute('SELECT case_id,epoch,revoked FROM events ORDER BY id'):
        events[case].append((epoch,bool(revoked)))
    permits=defaultdict(list)
    for permitid,case,sources,digest,audience,epoch in sql.execute('SELECT permit_id,case_id,sources,digest,audience,epoch FROM permits'):
        permits[case].append({'permitid':permitid,'sources':json.loads(sources),'digest':digest,'audience':audience,'epoch':epoch})
    decisions=defaultdict(list)
    for case,route,verdict,reason,epoch in sql.execute('SELECT case_id,route,verdict,reason,epoch FROM decisions ORDER BY id'):
        decisions[case].append((verdict,reason,epoch))
    effects=defaultdict(list)
    for cid,permit_id,body,via,epoch in sql.execute('SELECT case_id,permit_id,body,via,epoch FROM effects ORDER BY id'):
        effects[cid].append((permit_id,body,via,epoch))
    assert len(bycase)==len(cases)==len(permits)
    assert all(len(v)==1 for v in permits.values())
    aggregate={m:dict(cases=0,actual_effects=0,revoked_source_writes_after_owner_notice=0,
                       revoked_source_writes_after_recipient_ack=0,clean_scenario_effects=0,
                       forged_issuer_unsafe_writes=0,undelivered_notice_unsafe_writes=0,
                       direct_denials=0,duplicate_denials=0) for m in MODES}
    errors=[]
    for c in cases:
        cid=c['case'];mode=c['mode'];name=c['scenario'];policy=c['policy'];seed=c['seed']
        assert cid in bycase and len(permits[cid])==1
        assert bycase[cid][0]==mode
        source_claim=permits[cid][0]
        assert source_claim['sources']==c['declared_sources']
        assert source_claim['audience']==c['audience']
        expected_body=sha(f'body:{seed}:{policy}:{name}')
        assert source_claim['digest']==sha(expected_body)
        assert source_claim['epoch']==0
        epochs=events[cid]
        required=[(0,False)]+([(1,True)] if c['recipient_acknowledged'] else [])
        if name=='revoked_after_release': required.append((2,False))
        assert epochs==required,(cid,epochs,required)
        actual_mode,revoked,epoch=bycase[cid]
        assert epoch==epochs[-1][0] and revoked==epochs[-1][1]
        assert len(decisions[cid])==(2 if mode=='owner_live' else 1)+(1 if c['repeat'] else 0)
        anticipated=expected(name,mode)
        response=by_resp[cid]
        assert response['historical_replay'] and response['latest_repaired']
        results=response['results']
        if mode=='owner_live':
            assert len(results)==2+(1 if c['repeat'] else 0)
            direct=results[0]['response'];via=results[1]['response']
            assert direct['verdict']=='DENY' and not direct['committed']
            assert results[0]['route']=='direct' and results[1]['route']=='relay'
            final=via
            aggregate[mode]['direct_denials']+=1
            assert decisions[cid][0][0]=='DENY'
            assert decisions[cid][1][0]==anticipated
        else:
            assert len(results)==1+(1 if c['repeat'] else 0)
            final=results[0]['response']
            assert decisions[cid][0][0]==anticipated
        assert final['verdict']==anticipated and final['committed']==(anticipated=='ALLOW'),(name,mode,final)
        if c['repeat']:
            again=results[-1]['response'];expect='ALLOW' if mode=='open' else 'DENY'
            assert again['verdict']==expect
            assert decisions[cid][-1][0]==expect
            if mode!='open':aggregate[mode]['duplicate_denials']+=1
        count=(1 if anticipated=='ALLOW' else 0)+(1 if c['repeat'] and mode=='open' else 0)
        assert len(effects[cid])==count,(cid,len(effects[cid]),count)
        for issued,body,via,stored_epoch in effects[cid]:
            expected_stored_body=expected_body+'-MODIFIED' if name=='tamper_clean_output' else expected_body
            assert body==expected_stored_body,(cid,'corrupted physical effect')
            assert via==('owner-authenticated' if mode=='owner_live' else 'direct')
            assert stored_epoch==epoch
            if mode!='open':assert issued==source_claim['permitid']
            else:assert issued is None
        st=aggregate[mode];st['cases']+=1;st['actual_effects']+=len(effects[cid])
        is_revoked='R' in c['true_sources'] and c['owner_notice'] and name!='revoked_after_release'
        st['revoked_source_writes_after_owner_notice']+=len(effects[cid]) if is_revoked else 0
        st['revoked_source_writes_after_recipient_ack']+=len(effects[cid]) if is_revoked and c['recipient_acknowledged'] else 0
        st['clean_scenario_effects']+=len(effects[cid]) if name in ('clean_independent','duplicate_clean') else 0
        st['forged_issuer_unsafe_writes']+=len(effects[cid]) if name=='false_clean_issuance' else 0
        st['undelivered_notice_unsafe_writes']+=len(effects[cid]) if name=='revocation_not_delivered' else 0
    assert sum(v['actual_effects'] for v in aggregate.values())==sql.execute('SELECT count(*) FROM effects').fetchone()[0]
    assert all(v['cases']==len(cases)//4 for v in aggregate.values())
    assert all(v['direct_denials']==len(cases)//4 for m,v in aggregate.items() if m=='owner_live')
    result={'schema':'recipient-owned-effect-audit-v1','partition':plan['partition'],'passed':True,
            'total_cases':len(cases),'physical_effects':sum(v['actual_effects'] for v in aggregate.values()),
            'by_mode':aggregate,
            'observed_critical_cases':{
                'recipient_policy_aware_blocks_old_revoked_permits':True,
                'owner_credential_stops_direct_worker_use':True,
                'even_current_policy_fails_when_recipient_did_not_receive_notice':True,
                'even_current_policy_fails_when_signed_issuer_provenance_is_false':True},
            'scope':['Real independently running recipient and relay processes; real LangGraph historical checkpoint replay',
                     'Recipient stores and evaluates its own revocation policy and irreversible synthetic SQL effect',
                     'Source label truth withheld in synthetic oracle for independent verification',
                     'After target acknowledgement, recipient serializes policy changes and writes with BEGIN IMMEDIATE',
                     'Notice not received by recipient and false provenance issuer are explicit failure cases',
                     'Public unprotected alternative destinations cannot be revoked by this architecture',
                     'No live external cloud provider or actual litigation data used']}
    Path(a.out).write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print('INDEPENDENT_RECIPIENT_AUDIT '+json.dumps({'partition':result['partition'],'cases':len(cases),'physical_effects':result['physical_effects'],'by_mode':aggregate},sort_keys=True),flush=True)
    sql.close()
if __name__=='__main__':main()
