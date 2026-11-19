#!/usr/bin/env python3
"""Read-only SQL + oracle auditor for process-isolated trusted custody study."""
import argparse
from collections import Counter,defaultdict
import hashlib
import itertools
import json
from pathlib import Path
import sqlite3

SOURCE_VALUES={}

def digest(x):return hashlib.sha256(x).hexdigest()

def recover(output,nonce,candidates,limit=2):
    """Exhaustive conditional replay; NOT a valid replacement for signed access evidence."""
    if not isinstance(nonce,str) or len(nonce)!=32:
        return 'UNRESOLVED_NONCE',[]
    if not isinstance(output,str) or len(output)!=64:
        return 'UNRESOLVED_OUTPUT',[]
    hits=[]
    names=sorted(candidates)
    for depth in range(1,limit+1):
        for seq in itertools.product(names,repeat=depth):
            pred=digest(b'|'.join(candidates[k] for k in seq)+b'::'+nonce.encode())
            if pred==output:hits.append(list(seq))
    if not hits:return 'UNRESOLVED_NO_MATCH',[]
    if len(hits)>1:return 'UNRESOLVED_AMBIGUOUS',hits
    return 'UNIQUE',hits

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',type=Path,required=True)
    ap.add_argument('--oracle',type=Path,required=True);ap.add_argument('--snapshots',type=Path,required=True);ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    SOURCE_VALUES.update({k:bytes.fromhex(v) for k,v in json.loads(args.snapshots.read_text()).items()})
    report=json.loads(args.oracle.read_text());cases=report['cases']
    con=sqlite3.connect('file:'+str(args.db.resolve())+'?mode=ro',uri=True)
    con.execute('PRAGMA query_only=ON')
    assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    flows=set(x[0] for x in con.execute('SELECT flow FROM flows'))
    assert len(flows)==len(cases)==len(set(c['flow'] for c in cases))
    hist=defaultdict(dict)
    for source,epoch,revoked in con.execute('SELECT name,epoch,revoked FROM policy_history ORDER BY name,epoch'):
        assert epoch not in hist[source]
        hist[source][epoch]=bool(revoked)
    assert set(hist)=={'R','K'} and hist['R'][0] is False
    reads=defaultdict(list)
    for _,flow,source,source_sha,seq in con.execute('SELECT id,flow,source,content_sha,seq FROM read_events ORDER BY id'):
        assert flow in flows
        assert digest(SOURCE_VALUES[source])==source_sha
        reads[seq].append((flow,source))
    issued={}
    for seq,flow,sources,output,nonce,output_hash,epoch in con.execute(
        'SELECT seq,flow,sources,output,nonce,output_hash,issued_epoch FROM issued'):
        assert seq not in issued and flow in flows and digest(output.encode())==output_hash
        issued[seq]={'flow':flow,'sources':json.loads(sources),'output':output,'nonce':nonce,'epoch':epoch}
    effects=defaultdict(list)
    for eff_id,flow,seq,out,epoch,sources in con.execute('SELECT id,flow,seq,output,epoch,sources FROM effects'):
        assert flow in flows
        effects[flow].append({'id':eff_id,'seq':seq,'output':out,'epoch':epoch,'sources':json.loads(sources)})
    decisions=defaultdict(list)
    for flow,operation,verdict,reason,epoch in con.execute(
        'SELECT flow,operation,verdict,reason,epoch FROM decisions ORDER BY id'):
        decisions[flow].append({'operation':operation,'verdict':verdict,'reason':reason,'epoch':epoch})
    source_versions={k:v for k,v in SOURCE_VALUES.items()}
    by_scenario=defaultdict(Counter); totals=Counter();rows=[]
    for case in cases:
        flow=case['flow'];name=case['scenario'];truth=case['true_sources']
        d=case['done'];ready=case['ready']
        recorded=effects[flow]
        assert len(recorded)<=1,(name,flow)
        assert d['flow']==flow and ready['flow']==flow
        if case['expected']=='DERIVE_BLOCKED':
            assert not recorded and not ready['issued_ok'] and d['results']==[]
            assert decisions[flow][-1]['verdict']=='BLOCK'
        else:
            assert ready['issued_ok'] and len(d['results'])>=1
            verdict=d['results'][0]['verdict']
            assert verdict==case['expected']
            control_flow=('wrong-flow-'+flow) if name=='cross_flow_capability' else flow
            confirm=decisions[control_flow][-1] if name!='duplicate_replay' else decisions[control_flow][-2]
            assert confirm['operation']=='commit' and confirm['verdict']==verdict
            assert bool(recorded)==(verdict=='ALLOW'),(name,verdict,recorded)
            if name=='duplicate_replay':
                assert d['results'][1]['verdict']=='BLOCK' and decisions[flow][-1]['reason']=='DUPLICATE_EFFECT'
        if name in ('missing_issuance_record','missing_read_evidence'):
            # Source truth was used before deliberate privileged evidence loss.
            assert ready['issued_ok'] and case['expected']=='UNRESOLVED'
        if recorded:
            row=recorded[0]
            assert row['sources']==truth
            assert row['seq']==ready['seq']
            source_status=hist['R']
            # Need policy state at commit; global R epochs monotonically increase.
            event_epoch=max(x for x in source_status if x<=row['epoch'])
            revoked=source_status[event_epoch]
            assert not ('R' in truth and revoked),('unsafe SQL write',name)
            assert row['seq'] in issued
            assert issued[row['seq']]['output']==row['output']
        src=issued.get(ready.get('seq'))
        recovery='NO_SAVED_ISSUANCE'
        if src:
            recovery,hits=recover(src['output'],src['nonce'],source_versions)
            assert recovery=='UNIQUE' and hits==[truth],(name,hits,truth)
            assert [n for (f,n) in reads[ready['seq']]]==truth or case['fault']=='read_events'
        counter=by_scenario[name]
        counter['cases']+=1
        counter['commits']+=int(bool(recorded))
        counter['unresolved']+=int(case['expected']=='UNRESOLVED')
        counter['derivation_blocked']+=int(case['expected']=='DERIVE_BLOCKED')
        totals['cases']+=1;totals['effects']+=len(recorded)
        if 'R' not in truth and case['timing'] in ('revoke','before'):
            totals['clean_still_allowed']+=int(bool(recorded))
            totals['clean_eligible']+=1
        rows.append({'scenario':name,'policy':case['order'],'seed':case['seed'],'flow':flow,
            'physical_effects':len(recorded),'expected':case['expected'],'recovery_status':recovery})
    assert sum(len(x) for x in effects.values())==totals['effects']
    assert report['security']['policy_admin_http_denied']
    assert report['security']['test_fault_http_denied']
    assert report['security']['untracked_http_endpoint_absent']
    assert all(report['security'][x]=='ACCESS_DENIED' for x in ('vault','signing_key','admin_key','protected_db'))
    nonce='0123456789abcdef0123456789abcdef'
    candidate_out=digest(b'|'.join([SOURCE_VALUES['K'],SOURCE_VALUES['R']])+b'::'+nonce.encode())
    missing=recover(candidate_out,nonce,{'K':SOURCE_VALUES['K']})
    obsolete=recover(candidate_out,nonce,{'R':SOURCE_VALUES['R']+b'_OLD','K':SOURCE_VALUES['K']})
    none=recover(candidate_out,'',SOURCE_VALUES)
    amb_out=digest(SOURCE_VALUES['K']+b'::'+nonce.encode())
    ambiguous=recover(amb_out,nonce,{'K':SOURCE_VALUES['K'],'L':SOURCE_VALUES['K']})
    assert missing[0]=='UNRESOLVED_NO_MATCH'
    assert obsolete[0]=='UNRESOLVED_NO_MATCH'
    assert none[0]=='UNRESOLVED_NONCE'
    assert ambiguous[0]=='UNRESOLVED_AMBIGUOUS'
    controls={'original_source_absent':missing[0],'wrong_source_version':obsolete[0],
              'nonce_missing':none[0],'same_bytes_two_names':ambiguous[0]}
    result={'schema':'trusted-custody-readback-v1','partition':report['plan']['partition'],
       'passed':True,'totals':dict(totals),'scenario_counts':{k:dict(v) for k,v in by_scenario.items()},
       'negative_controls':controls,'security':report['security'],'rows':rows,
       'scope':['Real Linux DAC process isolation with UID 12001 broker and UID 12002 worker',
         'No raw source bytes returned to worker; broker signs source custody and output digest',
         'Every protected SQL effect checks signed certificate and current policy inside transaction',
         'Missing proof and missing original source candidates are distinguished, never guessed clean',
         'Unauthorized raw-data bypass denied by OS permissions in this disposable container',
         'Not a complete sandbox against all host paths, network channels or agent memory sidechannels',
         'Policy administration mediated by test-only private admin secret',
         'No real privileged source, legal work, cloud service or LLM included']}
    args.out.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print('INDEPENDENT_AUDIT '+json.dumps({'partition':result['partition'],'totals':result['totals'],
        'missing_evidence_controls':controls,'security':result['security']},sort_keys=True),flush=True)
    con.close()

if __name__=='__main__':main()
