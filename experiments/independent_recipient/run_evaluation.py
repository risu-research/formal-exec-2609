#!/usr/bin/env python3
"""Owner-controlled external recipient experiment with real LangGraph replay.

The target service, owner relay, and untrusted worker have separate Linux UIDs.
Held-out cases are frozen and not inspected by the worker as oracle truth.
"""
from __future__ import annotations
import argparse,hashlib,json,os,secrets,sqlite3,subprocess,sys,tempfile,time,urllib.error,urllib.request
from pathlib import Path

MODES=('open','static','live','owner_live')
POLICIES=('acosta_notice','necec_challenge')
# label, true contributing sources, declared permit sources, owner revocation event,
# target policy delivered, token mutation, audience, repeated effect
CASES=(
 ('revoked_after_notice',['R'],['R'],True,True,'normal','authorized-recipient',False),
 ('mixed_after_notice',['R','K'],['R','K'],True,True,'normal','authorized-recipient',False),
 ('clean_independent',['K'],['K'],True,True,'normal','authorized-recipient',False),
 ('revoked_before_notice',['R'],['R'],False,False,'normal','authorized-recipient',False),
 ('revoked_after_release',['R'],['R'],True,True,'normal','authorized-recipient',False),
 ('forge_clean_label',['R'],['R'],True,True,'forge','authorized-recipient',False),
 ('tamper_clean_output',['K'],['K'],True,True,'tamper','authorized-recipient',False),
 ('wrong_target_clean',['K'],['K'],True,True,'normal','other-recipient',False),
 ('missing_token_clean',['K'],['K'],True,True,'missing','authorized-recipient',False),
 ('duplicate_clean',['K'],['K'],True,True,'duplicate','authorized-recipient',True),
 ('false_clean_issuance',['R'],['K'],True,True,'normal','authorized-recipient',False),
 ('revocation_not_delivered',['R'],['R'],True,False,'normal','authorized-recipient',False),
)
DEV=(211,212)
HELDOUT=(8211,8212,8213,8214,8215,8216)

def sha(x):return hashlib.sha256(x.encode()).hexdigest()
def post(url,path,obj,admin=None):
    hdr={'Content-Type':'application/json'}
    if admin:hdr['X-Owner-Admin']=admin
    req=urllib.request.Request(url+path,data=json.dumps(obj).encode(),headers=hdr,method='POST')
    try:
        with urllib.request.urlopen(req,timeout=15) as r:data=json.loads(r.read())
    except urllib.error.HTTPError as e:raise RuntimeError((path,e.code,e.read().decode()))
    if 'error' in data:raise RuntimeError((path,data))
    return data

def poll(url):
    for _ in range(80):
        try:
            with urllib.request.urlopen(url+'/health',timeout=2) as x:
                if json.loads(x.read())['ready']:return
        except Exception:time.sleep(.1)
    raise RuntimeError('server did not start: '+url)

def demote(uid):
    def set_ids():
        os.setgroups([]);os.setgid(uid);os.setuid(uid)
    return set_ids

def start_service(cmd,log,uid=None):
    f=open(log,'w',encoding='utf8')
    p=subprocess.Popen(cmd,stdout=f,stderr=subprocess.STDOUT,preexec_fn=demote(uid) if uid is not None else None)
    return p,f

def build_plan(partition):
    assert len(CASES)==12 and len({c[0] for c in CASES})==len(CASES)
    assert not set(DEV)&set(HELDOUT)
    return {'protocol':'recipient-owned-current-authority-v1','partition':partition,'modes':MODES,
            'policies':POLICIES,'scenarios':[a[0] for a in CASES],
            'development_seeds':DEV,'heldout_seeds':HELDOUT,
            'description':'pre-notice signed capabilities replayed after policy update into an independent HTTP/SQL recipient'}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--partition',choices=('plan','dev','holdout'),required=True)
    ap.add_argument('--out',default=None);ap.add_argument('--isolated-uids',action='store_true')
    ap.add_argument('--sink-port',type=int,default=8885);ap.add_argument('--relay-port',type=int,default=8886)
    args=ap.parse_args();plan=build_plan(args.partition)
    if args.partition=='plan':print('PROTOCOL '+json.dumps(plan,sort_keys=True));return
    if args.isolated_uids and os.getuid()!=0:raise RuntimeError('Linux UID test requires disposable-root container')
    root=Path(__file__).resolve().parent
    target=Path(args.out or root/('_run_'+args.partition)).resolve();target.mkdir(parents=True,exist_ok=True)
    sinkurl=f'http://127.0.0.1:{args.sink_port}';relayurl=f'http://127.0.0.1:{args.relay_port}'
    uid_sink=12003 if args.isolated_uids else None
    uid_relay=12001 if args.isolated_uids else None
    uid_agent=12002 if args.isolated_uids else None
    root_temp=Path(tempfile.mkdtemp(prefix='receiver-boundary-'))
    root_temp.chmod(0o755)
    sinkdir=root_temp/'sink';ownerdir=root_temp/'owner'
    for d,uid in ((sinkdir,uid_sink),(ownerdir,uid_relay)):
        d.mkdir();d.chmod(0o700)
        if uid is not None:os.chown(d,uid,uid)
    admin=secrets.token_bytes(32);sign=secrets.token_bytes(32);relay_secret=secrets.token_hex(32)
    signer=sinkdir/'secrets.json';owner=ownerdir/'owner.key'
    signer.write_text(json.dumps({'signer':sign.hex(),'admin':admin.hex(),'relay_sha256':sha(relay_secret)}));owner.write_text(relay_secret)
    for f,uid in ((signer,uid_sink),(owner,uid_relay)):
        f.chmod(0o400)
        if uid is not None:os.chown(f,uid,uid)
    db=sinkdir/'recipient.sqlite'
    services=[];handles=[];agent=None;workererr=None
    try:
        service,log=start_service([sys.executable,str(root/'recipient_service.py'),'--db',str(db),'--secrets',str(signer),'--port',str(args.sink_port)],target/'sink.log',uid_sink)
        services.append(service);handles.append(log)
        poll(sinkurl)
        relay,log=start_service([sys.executable,str(root/'owner_relay.py'),'--secret',str(owner),'--target',sinkurl,'--port',str(args.relay_port)],target/'relay.log',uid_relay)
        services.append(relay);handles.append(log)
        poll(relayurl)
        workererr=open(target/'agent.stderr','w')
        agent=subprocess.Popen([sys.executable,str(root/'agent_worker.py'),'--sink',sinkurl,'--relay',relayurl,'--owner-key-path',str(owner),'--enforce-uid',str(uid_agent if args.isolated_uids else -1)],
                                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=workererr,text=True,bufsize=1,
                                preexec_fn=demote(uid_agent) if uid_agent is not None else None)
        first=agent.stdout.readline().strip()
        if not first.startswith('SECURITY '):raise RuntimeError('agent process failed: '+first)
        security=json.loads(first.split(' ',1)[1]);print('AGENT_ACCESS '+json.dumps(security,sort_keys=True),flush=True)
        admin_token=admin.hex()
        seeds=DEV if args.partition=='dev' else HELDOUT
        cases=[];responses=[];events=[]
        for seed in seeds:
            for policy in POLICIES:
                for record in CASES:
                    name,truth,claim,owner_notice,delivered,mutation,audience,repeat=record
                    for mode in MODES:
                        cid=sha(f'{seed}|{policy}|{name}|{mode}')[:34]
                        body=sha(f'body:{seed}:{policy}:{name}')
                        post(sinkurl,'/setup',{'case':cid,'mode':mode},admin_token)
                        cap=post(sinkurl,'/issue',{'case':cid,'sources':claim,'body':body,'audience':audience},admin_token)['token']
                        spec={'case':cid,'mode':mode,'body':body,'token':cap,'mutation':mutation}
                        cases.append({'case':cid,'mode':mode,'scenario':name,'policy':policy,'seed':seed,
                            'true_sources':truth,'declared_sources':claim,'owner_notice':owner_notice,
                            'recipient_acknowledged':delivered,'mutation':mutation,'audience':audience,'repeat':repeat})
                        agent.stdin.write(json.dumps({'action':'START','case':spec})+'\n');agent.stdin.flush()
                        line=agent.stdout.readline().strip()
                        if not line.startswith('READY '):raise RuntimeError('worker READY failed: '+line)
                        before=json.loads(line.split(' ',1)[1]);assert before['case']==cid
                        # The signed capability is stored in the old checkpoint BEFORE the notice.
                        acks=[]
                        if delivered:
                            ack=post(sinkurl,'/revoke',{'case':cid,'revoked':True},admin_token)
                            assert ack['ack_epoch']==1
                            acks.append(ack)
                            if name=='revoked_after_release':
                                ack=post(sinkurl,'/revoke',{'case':cid,'revoked':False},admin_token)
                                assert ack['ack_epoch']==2
                                acks.append(ack)
                        agent.stdin.write(json.dumps({'action':'RESUME','case':cid})+'\n');agent.stdin.flush()
                        line=agent.stdout.readline().strip()
                        if not line.startswith('DONE '):raise RuntimeError('worker DONE failed: '+line)
                        ans=json.loads(line.split(' ',1)[1]);assert ans['historical_replay'] and ans['latest_repaired']
                        responses.append(ans)
                        events.append({'case':cid,'acks':acks})
        agent.stdin.write(json.dumps({'action':'STOP'})+'\n');agent.stdin.flush()
        agent.stdin.close();agent.wait(timeout=20)
        assert agent.returncode==0,agent.returncode
        # Shutdown services and merge WAL before copying the target's independent DB.
        for child in services:child.terminate()
        for child in services:child.wait(timeout=12)
        with sqlite3.connect(db) as c:
            status=c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            assert status[0]==0,status
            mode=c.execute('PRAGMA journal_mode=DELETE').fetchone()[0]
            assert mode.lower()=='delete',mode
            assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            effects=c.execute('SELECT count(*) FROM effects').fetchone()[0]
        (target/'oracle.json').write_text(json.dumps({'plan':plan,'security':security,'cases':cases,'owner_events':events},indent=2,sort_keys=True)+'\n')
        (target/'responses.json').write_text(json.dumps({'responses':responses},indent=2,sort_keys=True)+'\n')
        import shutil
        shutil.copy2(db,target/'recipient.sqlite')
        print('EXECUTION_RESULT '+json.dumps({'partition':args.partition,'cases':len(cases),'physical_effects':effects,'security':security,'out':str(target)},sort_keys=True),flush=True)
    finally:
        if agent is not None and agent.poll() is None:agent.kill();agent.wait()
        for child in services:
            if child.poll() is None:child.kill();child.wait()
        for f in handles:f.close()
        if workererr:workererr.close()
        import shutil
        shutil.rmtree(root_temp,ignore_errors=True)

if __name__=='__main__':main()
