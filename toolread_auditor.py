#!/usr/bin/env python3
"""Separate read-only audit of actual source reads, nondeterministic transformations,
policy epochs and durable post-clawback writes. Never imports runner or server.
"""
import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

SOURCE_VALUES={
 'R':'SYNTHETIC_REVOKED_DISCOVERY_RECORD_V1',
 'K':'SYNTHETIC_INDEPENDENT_LEGAL_RECORD_V1',
}
EXPECTED={
 'revoked_logged': ('ALLOW','BLOCK','BLOCK'),
 'clean_logged': ('ALLOW','ALLOW','ALLOW'),
 'mixed_logged': ('ALLOW','BLOCK','BLOCK'),
 'revoked_declared_clean': ('ALLOW','ALLOW','BLOCK'),
 'clean_declared_revoked': ('ALLOW','BLOCK','ALLOW'),
 'mixed_omitted_claim': ('ALLOW','ALLOW','BLOCK'),
 'missing_claim_revoked': ('ALLOW','UNRESOLVED','BLOCK'),
 'missing_claim_clean': ('ALLOW','UNRESOLVED','ALLOW'),
 'untraced_revoked': ('ALLOW','BLOCK','UNRESOLVED'),
 'untraced_clean': ('ALLOW','ALLOW','UNRESOLVED'),
 'partial_bypass_revoked': ('ALLOW','ALLOW','ALLOW'),
 'tampered_output_clean': ('ALLOW','ALLOW','UNRESOLVED'),
 'stale_transform_id_clean': ('ALLOW','ALLOW','UNRESOLVED'),
 'released_revoked': ('ALLOW','ALLOW','ALLOW'),
 'nondet_clean_repeat': ('ALLOW','ALLOW','ALLOW'),
}
GUARDS=('cached','declared','tool_trace')

def digest(s):return hashlib.sha256(s.encode('utf-8')).hexdigest()

def transform(values,nonce):
    return digest(json.dumps(values,sort_keys=True,separators=(',',':'))+'::'+nonce)

def main():
    a=argparse.ArgumentParser();a.add_argument('--db',type=Path,required=True)
    a.add_argument('--oracle',type=Path,required=True);a.add_argument('--acks',type=Path,required=True)
    a.add_argument('--out',type=Path,required=True);x=a.parse_args()
    plan=json.loads(x.oracle.read_text());cases=plan['specs'];replies=json.loads(x.acks.read_text())['replies']
    ids={s['flow_id'] for s in cases};assert len(ids)==len(cases)==len(replies)
    assert {r['flow_id'] for r in replies}==ids
    assert all(z['historical_snapshot_replayed'] and z['latest_state_repaired'] for z in replies)
    ack={z['flow_id']:z for z in replies}

    con=sqlite3.connect('file:'+str(x.db.resolve())+'?mode=ro',uri=True)
    con.execute('PRAGMA query_only=ON')
    assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    flows={f:(e,bool(r)) for f,e,r in con.execute('SELECT flow_id,epoch,revoked FROM flow')}
    reads=defaultdict(list)
    for ident,f,source,content_hash in con.execute(
        'SELECT id,flow_id,source,content_digest FROM source_reads ORDER BY id'):
        assert f in ids and source in SOURCE_VALUES
        assert content_hash==digest(SOURCE_VALUES[source]);reads[f].append((ident,source))
    txs=defaultdict(list)
    nonces=[]
    for ident,f,output_hash,n_read,nonce in con.execute(
        'SELECT id,flow_id,output_digest,observed_read_count,nonce FROM transforms ORDER BY id'):
        assert f in ids and len(nonce)==32 and all(c in '0123456789abcdef' for c in nonce)
        txs[f].append((ident,output_hash,n_read,nonce));nonces.append(nonce)
    assert len(nonces)==len(set(nonces))==len(cases)
    policy=defaultdict(dict)
    for f,epoch,revoked in con.execute('SELECT flow_id,epoch,revoked FROM policy_history'):
        assert f in ids and epoch not in policy[f];policy[f][epoch]=bool(revoked)
    decisions=defaultdict(list)
    for f,mode,verdict,epoch in con.execute('SELECT flow_id,mode,verdict,epoch FROM decisions ORDER BY id'):
        assert f in ids; decisions[f].append((mode,verdict,epoch))
    writes=defaultdict(list)
    for i,f,out,mode,epoch in con.execute('SELECT id,flow_id,output,mode,epoch FROM effects ORDER BY id'):
        assert f in ids; writes[f].append((i,out,mode,epoch))
    assert len(flows)==len(txs)==len(decisions)==len(policy)==len(cases)
    metrics={g:{'cases':0,'affected':0,'revocation_violation':0,
                'clean':0,'clean_work_preserved':0,'unresolved':0,
                'unbound_output_write':0,'untraced_bypass_violation':0,
                'released_source_effect':0} for g in GUARDS}
    rows=[]; by_case={}
    for case in cases:
        flow=case['flow_id'];name=case['scenario'];guard=case['guard']
        assert name in EXPECTED and guard in GUARDS
        assert case['order'] in ('acosta_notice','necec_challenge')
        assert len(txs[flow])==len(decisions[flow])==1
        assert set(case['untracked']).issubset(set(case['sources']))
        registered=txs[flow][0]; tid, registered_hash, nread,nonce=registered
        observed=[v for _,v in reads[flow]]
        expected_observed=[v for v in case['sources'] if v not in case['untracked']]
        assert observed==expected_observed,(name,'tool events',observed,expected_observed)
        assert nread==len(observed)
        actual_out=transform([SOURCE_VALUES[s] for s in case['sources']],nonce)
        assert registered_hash==digest(actual_out),(name,'transformation consistency')
        expected_decision=EXPECTED[name][GUARDS.index(guard)]
        dm,dv,de=decisions[flow][0]
        assert dm==guard and dv==expected_decision
        expected_policy={0:False,1:True} if case['policy']=='notice' else {0:False,1:True,2:False}
        assert policy[flow]==expected_policy
        assert flows[flow]==(max(expected_policy),expected_policy[max(expected_policy)])
        assert de==max(expected_policy)
        saved=writes[flow];assert len(saved)<=1
        assert bool(saved)==(dv=='ALLOW')
        if saved:
            _,body,mode,epoch=saved[0]
            assert mode==guard and epoch==de
            assert body==(actual_out+':tampered' if case['fault']=='tamper' else actual_out)
        assert ack[flow]['client_ack']['verdict']==dv
        assert bool(saved)==ack[flow]['client_ack']['committed']
        assert ack[flow]['checkpoint_source_reads']==len(case['sources'])
        assert ack[flow]['checkpoint_logged_events']==len(expected_observed)
        active=bool(expected_policy[max(expected_policy)])
        true_revoked='R' in case['sources'] and active
        clean='R' not in case['sources']
        stored_good=bool(saved) and saved[0][1]==actual_out
        unauth=bool(saved) and true_revoked
        group=metrics[guard]
        group['cases']+=1;group['affected']+=int(true_revoked)
        group['revocation_violation']+=int(unauth)
        group['clean']+=int(clean)
        group['clean_work_preserved']+=int(clean and stored_good)
        group['unresolved']+=int(dv=='UNRESOLVED')
        group['unbound_output_write']+=int(bool(saved) and not stored_good)
        group['untraced_bypass_violation']+=int(unauth and bool(case['untracked']))
        group['released_source_effect']+=int(bool(saved) and 'R' in case['sources'] and not active)
        rows.append({'id':flow,'order':case['order'],'scenario':name,'seed':case['seed'],
                     'guard':guard,'decision':dv,'source_ids_oracle':case['sources'],
                     'observed_read_events':len(observed),'logged_sources':observed,
                     'registered_transform':tid,'registered_output_sha256':registered_hash,
                     'nonce':nonce,'actual_stored_writes':len(saved),
                     'revocation_violation':unauth,'genuinely_clean_work_preserved':bool(clean and stored_good),
                     'tampered_output_persisted':bool(saved and not stored_good),
                     'read_bypass':bool(case['untracked'])})
        by_case[(case['seed'],case['order'],guard,name)]=actual_out
    nondet_pairs=0
    for seed in plan['plan']['dev_seeds'] if plan['plan'].get('partition')=='dev' else plan['plan']['heldout_seeds']:
        for order in ('acosta_notice','necec_challenge'):
            for guard in GUARDS:
                aa=by_case.get((seed,order,guard,'clean_logged'))
                bb=by_case.get((seed,order,guard,'nondet_clean_repeat'))
                assert aa is not None and bb is not None and aa!=bb
                nondet_pairs+=1
    assert len(rows)==len(cases)
    result={'schema':'independent-toolread-nondeterminism-audit-v1',
            'partition':plan['plan']['partition'],'cases':len(cases),
            'source_event_count':sum(len(a) for a in reads.values()),
            'unique_transform_nonces':len(nonces),'nondeterministic_clean_pairs':nondet_pairs,
            'physical_writes':sum(len(v) for v in writes.values()),
            'guards':metrics,'rows':rows,'passed':True,
            'qualifications':[
              'Synthetic source records are actually retrieved over HTTP via a LangChain tool.',
              'Tool-read gateway events are service-generated; unchecked direct reads can bypass it.',
              'Registration binds the current transform output digest to observed reads but cannot prove every input passed through the gateway.',
              'A fresh random nonce makes transformations nondeterministic, not an LLM inference.',
              'Only a localhost HTTP+SQLite service and LangGraph InMemorySaver are tested.',
              'The physical observer detects committed results but does not undo disclosures.',
              'The fixed seeded scenarios are not independent production failure-rate samples.'
            ]}
    x.out.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print('INDEPENDENT_AUDIT',json.dumps({'partition':result['partition'],'cases':len(cases),
             'writes':result['physical_writes'],'nondet_pairs':nondet_pairs,'guards':metrics},sort_keys=True))
    con.close()
if __name__=='__main__':main()
