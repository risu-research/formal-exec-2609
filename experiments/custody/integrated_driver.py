#!/usr/bin/env python3
"""Prospective, process-separated source custody evaluation on Linux.

Disposable-root test orchestrator starts the broker as UID 12001 and an untrusted
LangGraph worker as UID 12002. All source reads and effect writes are performed
by the broker. The worker has no direct source, database or signing key access.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

BROKER_UID=12001
AGENT_UID=12002
SOURCE_VALUES={}
SCENARIOS=(
    # name, sources, worker action, timing, injected evidence fault, expected first decision
    ('revoked_historical', ['R'],'valid','revoke',None,'BLOCK'),
    ('independent_clean', ['K'],'valid','revoke',None,'ALLOW'),
    ('mixed_dependency', ['R','K'],'valid','revoke',None,'BLOCK'),
    ('forged_clean_provenance', ['R'],'forge_clean','revoke',None,'UNRESOLVED'),
    ('tampered_output', ['K'],'tamper_output','revoke',None,'UNRESOLVED'),
    ('missing_certificate', ['K'],'missing_token','revoke',None,'UNRESOLVED'),
    ('cross_flow_capability', ['K'],'cross_flow','revoke',None,'UNRESOLVED'),
    ('missing_issuance_record', ['K'],'valid','revoke','issued','UNRESOLVED'),
    ('missing_read_evidence', ['K'],'valid','revoke','read_events','UNRESOLVED'),
    ('duplicate_replay', ['K'],'duplicate','revoke',None,'ALLOW'),
    ('revocation_released', ['R'],'valid','release',None,'ALLOW'),
    ('revoked_source_read_denied', ['R'],'valid','before',None,'DERIVE_BLOCKED'),
    ('clean_read_after_revocation', ['K'],'valid','before',None,'ALLOW'),
    ('source_before_notice', ['R'],'valid','none',None,'ALLOW'),
)
ORDERS=('acosta_notice','necec_challenge')
DEV=(31,32)
HELDOUT=(5301,5302,5303,5304,5305,5306)

def post(url,path,obj,admin=None):
    header={'Content-Type':'application/json'}
    if admin is not None:header['X-Admin-Secret']=admin
    req=urllib.request.Request(url+path,json.dumps(obj,sort_keys=True).encode(),headers=header,method='POST')
    try:
        with urllib.request.urlopen(req,timeout=10) as resp:
            out=json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'{path}: {exc.code} {exc.read().decode()}') from exc
    if 'error' in out:raise RuntimeError(str(out))
    return out

def create_private(parent:Path):
    parent.mkdir(mode=0o755,parents=True,exist_ok=True)
    vault=parent/'vault';vault.mkdir(mode=0o700)
    for name in ('R','K'):
        content=secrets.token_bytes(48)
        SOURCE_VALUES[name]=content
        (vault/(name+'.dat')).write_bytes(content)
        os.chown(vault/(name+'.dat'),BROKER_UID,BROKER_UID)
        (vault/(name+'.dat')).chmod(0o400)
    for name in ('signing.key','admin.key'):
        p=parent/name;p.write_bytes(secrets.token_bytes(32));os.chown(p,BROKER_UID,BROKER_UID);p.chmod(0o400)
    os.chown(vault,BROKER_UID,BROKER_UID)
    os.chown(parent,BROKER_UID,BROKER_UID);parent.chmod(0o700)

def drop_to(uid):
    os.setgroups([])
    os.setgid(uid)
    os.setuid(uid)

def read_protocol_line(proc,prefix):
    msg=proc.stdout.readline()
    if not msg:raise RuntimeError(f'worker died unexpectedly: {proc.poll()}')
    if not msg.startswith(prefix+' '):raise RuntimeError(f'wrong worker response {msg[:500]}')
    return json.loads(msg[len(prefix)+1:])

def main():
    a=argparse.ArgumentParser()
    a.add_argument('--partition',choices=('plan','dev','holdout'),required=True)
    a.add_argument('--out',type=Path)
    a.add_argument('--port',type=int,default=8811)
    args=a.parse_args()
    assert len(SCENARIOS)==14 and len(set(x[0] for x in SCENARIOS))==14
    assert set(DEV).isdisjoint(HELDOUT)
    plan={'partition':args.partition,'orders':ORDERS,'dev_seeds':DEV,'heldout_seeds':HELDOUT,
          'scenarios':SCENARIOS,'broker_uid':BROKER_UID,'agent_uid':AGENT_UID,
          'schema':'trusted-custody-integrated-v1'}
    if args.partition=='plan':
        print('PLAN '+json.dumps(plan,sort_keys=True),flush=True)
        return
    if os.getuid()!=0:raise RuntimeError('Disposable Linux UID-isolation eval requires root inside container')
    seeds=DEV if args.partition=='dev' else HELDOUT
    target=args.out or Path.cwd()/('_results_'+args.partition)
    target=target.resolve();target.mkdir(parents=True,exist_ok=True)
    root=Path(tempfile.mkdtemp(prefix='rv-custody-',dir='/tmp'))
    root.chmod(0o755)
    private=root/'broker-only'
    create_private(private)
    url='http://127.0.0.1:'+str(args.port)
    script=Path(__file__).resolve().parent
    broker=subprocess.Popen([sys.executable,str(script/'trusted_broker.py'),
            '--db',str(private/'ledger.sqlite'),'--key',str(private/'signing.key'),
            '--admin-key',str(private/'admin.key'),'--vault',str(private/'vault'),'--port',str(args.port)],
            stdout=(root/'broker.log').open('w'),stderr=subprocess.STDOUT,cwd='/tmp',preexec_fn=lambda:drop_to(BROKER_UID))
    agent=None
    try:
        for _ in range(100):
            if broker.poll() is not None:raise RuntimeError('Broker terminated')
            try:
                with urllib.request.urlopen(url+'/health',timeout=1) as resp:
                    if json.loads(resp.read()).get('ready'):break
            except Exception:time.sleep(.05)
        else:raise RuntimeError('Broker not ready')
        agent=subprocess.Popen([sys.executable,str(script/'agent_worker.py'),
            '--endpoint',url,'--private',str(private),'--agent-uid',str(AGENT_UID)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=(root/'agent.stderr').open('w'),
            text=True,bufsize=1,cwd='/tmp',preexec_fn=lambda:drop_to(AGENT_UID))
        security=read_protocol_line(agent,'SECURITY_CHECK')
        admin= (private/'admin.key').read_bytes().hex()
        cases=[]
        for seed in seeds:
            for order in ORDERS:
                for name,sources,action,timing,fault,expected in SCENARIOS:
                    flow=hashlib.sha256(f'{order}|{seed}|{name}'.encode()).hexdigest()[:40]
                    # Each case starts with R allowed. Changes are serial and externally controlled.
                    post(url,'/policy',{'source':'R','revoked':False},admin)
                    post(url,'/flow',{'flow':flow})
                    if timing=='before':post(url,'/policy',{'source':'R','revoked':True},admin)
                    agent.stdin.write(json.dumps({'command':'START','flow':flow,'sources':sources,'action':action})+'\n')
                    agent.stdin.flush()
                    ready=read_protocol_line(agent,'READY')
                    assert ready['flow']==flow
                    if timing=='revoke':post(url,'/policy',{'source':'R','revoked':True},admin)
                    elif timing=='release':
                        post(url,'/policy',{'source':'R','revoked':True},admin)
                        post(url,'/policy',{'source':'R','revoked':False},admin)
                    if fault:
                        assert ready['issued_ok'],(name,'fault needs token')
                        post(url,'/test_inject_evidence_loss',{'seq':ready['seq'],'target':fault},admin)
                    agent.stdin.write(json.dumps({'command':'GO','flow':flow})+'\n');agent.stdin.flush()
                    done=read_protocol_line(agent,'DONE')
                    if expected=='DERIVE_BLOCKED':
                        assert ready['issued_ok'] is False and done['results']==[],(name,ready,done)
                    else:
                        assert ready['issued_ok'] and done['results'] and done['results'][0]['verdict']==expected,(name,ready,done)
                        if action=='duplicate':assert done['results'][1]['verdict']=='BLOCK'
                    cases.append({'flow':flow,'seed':seed,'order':order,'scenario':name,
                        'true_sources':sources,'action':action,'timing':timing,'fault':fault,
                        'expected':expected,'ready':ready,'done':done})
        agent.stdin.write(json.dumps({'command':'STOP'})+'\n');agent.stdin.flush()
        agent.stdin.close();agent.wait(timeout=15)
        if agent.returncode:raise RuntimeError('Agent failure '+str(agent.returncode))
    finally:
        if agent is not None and agent.poll() is None:agent.kill();agent.wait()
        broker.terminate()
        try:broker.wait(timeout=10)
        except subprocess.TimeoutExpired:broker.kill();broker.wait()
    # Ensure every committed effect and policy event resides in standalone SQL.
    database=private/'ledger.sqlite'
    with sqlite3.connect(database) as db:
        check=db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        assert check[0]==0,check
        mode=db.execute('PRAGMA journal_mode=DELETE').fetchone()[0]
        assert mode.lower()=='delete'
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    shutil.copy2(database,target/'ledger.sqlite')
    (target/'oracle.json').write_text(json.dumps({'plan':plan,'cases':cases,'security':security},indent=2,sort_keys=True)+'\n')
    (target/'source_snapshot_hashes.json').write_text(json.dumps({k:hashlib.sha256(v).hexdigest() for k,v in SOURCE_VALUES.items()},indent=2,sort_keys=True)+'\n')
    (target/'sealed_candidate_snapshots.json').write_text(json.dumps({k:v.hex() for k,v in SOURCE_VALUES.items()},sort_keys=True)+'\n')
    print('EXECUTION_PASS '+json.dumps({'partition':args.partition,'cases':len(cases),
        'security':security,'effects':sqlite3.connect(target/'ledger.sqlite').execute('SELECT COUNT(*) FROM effects').fetchone()[0]},sort_keys=True),flush=True)
    shutil.rmtree(root)

if __name__=='__main__':main()
