#!/usr/bin/env python3
"""Narrow trusted reference monitor for synthetic protected-source workflows.

Only this process can open the protected source files and its own ledger.  Its
public API never returns raw source bytes.  The effect endpoint checks the
current policy and a signed, persisted provenance certificate in one SQL
transaction.  The trust boundary is Linux DAC + process identity in the CI job.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sqlite3


def hash_bytes(s: bytes) -> str:
    return hashlib.sha256(s).hexdigest()


def encode(payload: dict, key: bytes) -> str:
    data=json.dumps(payload,sort_keys=True,separators=(',',':')).encode()
    return base64.urlsafe_b64encode(data).decode().rstrip('=')+'.'+hmac.new(key,data,'sha256').hexdigest()


def decode(token: str, key: bytes) -> dict | None:
    if not isinstance(token,str) or len(token)>6000 or token.count('.')!=1:
        return None
    try:
        item,mac=token.split('.')
        data=base64.urlsafe_b64decode(item+'='*((-len(item))%4))
        if not hmac.compare_digest(hmac.new(key,data,'sha256').hexdigest(),mac):
            return None
        obj=json.loads(data)
        if not isinstance(obj,dict): return None
        if set(obj)!={'flow','seq','output_hash','sources'}: return None
        if not isinstance(obj['sources'],list) or not obj['sources'] or any(s not in ('R','K') for s in obj['sources']):return None
        if not isinstance(obj['seq'],int):return None
        if not isinstance(obj['flow'],str) or not isinstance(obj['output_hash'],str):return None
        return obj
    except (TypeError,ValueError,KeyError):
        return None


def init_db(path):
    with sqlite3.connect(path) as db:
        db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE policy(name TEXT PRIMARY KEY, revoked INTEGER NOT NULL, epoch INTEGER NOT NULL);
        CREATE TABLE policy_history(name TEXT, epoch INTEGER, revoked INTEGER, PRIMARY KEY(name,epoch));
        CREATE TABLE flows(flow TEXT PRIMARY KEY, state TEXT NOT NULL);
        CREATE TABLE read_events(id INTEGER PRIMARY KEY AUTOINCREMENT, flow TEXT, source TEXT, content_sha TEXT, seq INTEGER);
        CREATE TABLE issued(seq INTEGER PRIMARY KEY AUTOINCREMENT, flow TEXT NOT NULL, sources TEXT NOT NULL, output TEXT NOT NULL, nonce TEXT NOT NULL, output_hash TEXT NOT NULL, issued_epoch INTEGER NOT NULL);
        CREATE TABLE decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, flow TEXT, operation TEXT, verdict TEXT, reason TEXT, epoch INTEGER);
        CREATE TABLE effects(id INTEGER PRIMARY KEY AUTOINCREMENT, flow TEXT NOT NULL, seq INTEGER NOT NULL UNIQUE, output TEXT, epoch INTEGER, sources TEXT);
        ''')
        for name in ('R','K'):
            db.execute('INSERT INTO policy VALUES(?,0,0)',(name,))
            db.execute('INSERT INTO policy_history VALUES(?,0,0)',(name,))


class Monitor(BaseHTTPRequestHandler):
    db_path: str = ''
    key: bytes = b''
    admin_key: bytes = b''
    vault: str = ''
    def log_message(self,*_):pass
    def reply(self,obj,status=200):
        raw=json.dumps(obj,sort_keys=True,separators=(',',':')).encode()
        self.send_response(status);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        if self.path=='/health':self.reply({'ready':True})
        else:self.reply({'error':'not_found'},404)
    def do_POST(self):
        try:
            length=int(self.headers.get('Content-Length',0))
            if not 0<length<100000:raise ValueError('Bad request size')
            req=json.loads(self.rfile.read(length))
            if not isinstance(req,dict):raise ValueError('Body must be object')
            with sqlite3.connect(self.db_path,isolation_level=None,timeout=15) as db:
                db.execute('BEGIN IMMEDIATE')
                path=self.path
                if path=='/flow':
                    flow=req['flow']
                    if not isinstance(flow,str) or not (8<=len(flow)<=80):raise ValueError('Bad flow identifier')
                    db.execute('INSERT INTO flows VALUES(?,?)',(flow,'active'))
                    result={'created':True}
                elif path=='/derive':
                    flow=req['flow'];src=req['sources']
                    if not isinstance(src,list) or not (1<=len(src)<=2) or any(x not in ('R','K') for x in src):
                        raise ValueError('Bad source list')
                    if not db.execute('SELECT 1 FROM flows WHERE flow=?',(flow,)).fetchone():
                        raise ValueError('Unknown flow')
                    cur=db.execute('SELECT name,revoked FROM policy').fetchall()
                    revoked={n for n,v in cur if v}
                    if any(x in revoked for x in src):
                        db.execute('INSERT INTO decisions(flow,operation,verdict,reason,epoch) VALUES(?,?,?,?,?)',
                                    (flow,'derive','BLOCK','SOURCE_REVOKED',db.execute('SELECT max(epoch) FROM policy').fetchone()[0]))
                        result={'verdict':'BLOCK','reason':'SOURCE_REVOKED'}
                    else:
                        # This data is read only by the separately privileged monitor.
                        values=[]
                        for s in src:
                            path_obj=Path(self.vault)/(s+'.dat')
                            values.append(path_obj.read_bytes())
                        nonce=secrets.token_hex(16)
                        output=hash_bytes(b'|'.join(values)+b'::'+nonce.encode())
                        epoch=db.execute('SELECT max(epoch) FROM policy').fetchone()[0]
                        db.execute('INSERT INTO issued(flow,sources,output,nonce,output_hash,issued_epoch) VALUES(?,?,?,?,?,?)',
                                   (flow,json.dumps(src),output,nonce,hash_bytes(output.encode()),epoch))
                        seq=db.execute('SELECT last_insert_rowid()').fetchone()[0]
                        for i,s in enumerate(src):
                            db.execute('INSERT INTO read_events(flow,source,content_sha,seq) VALUES(?,?,?,?)',
                                       (flow,s,hash_bytes(values[i]),seq))
                        token=encode({'flow':flow,'seq':seq,'output_hash':hash_bytes(output.encode()),'sources':src},self.key)
                        result={'verdict':'ALLOW','output':output,'token':token,'seq':seq}
                elif path=='/policy':
                    if not hmac.compare_digest(self.headers.get('X-Admin-Secret',''),self.admin_key.hex()):
                        raise PermissionError('Admin permission required')
                    source=req['source'];revoked=int(bool(req['revoked']))
                    assert source in ('R','K')
                    row=db.execute('SELECT epoch FROM policy WHERE name=?',(source,)).fetchone()
                    epoch=row[0]+1
                    db.execute('UPDATE policy SET revoked=?,epoch=? WHERE name=?',(revoked,epoch,source))
                    db.execute('INSERT INTO policy_history VALUES(?,?,?)',(source,epoch,revoked))
                    result={'source':source,'revoked':bool(revoked),'epoch':epoch}
                elif path=='/test_inject_evidence_loss':
                    if not hmac.compare_digest(self.headers.get('X-Admin-Secret',''),self.admin_key.hex()):
                        raise PermissionError('Admin permission required')
                    seq=req['seq'];target=req['target']
                    if target=='issued':db.execute('DELETE FROM issued WHERE seq=?',(seq,))
                    elif target=='read_events':db.execute('DELETE FROM read_events WHERE seq=?',(seq,))
                    else:raise ValueError('Unknown fault target')
                    result={'fault_injected':True,'target':target,'seq':seq}
                elif path=='/commit':
                    flow=req.get('flow');token=req.get('token');output=req.get('output')
                    info=decode(token,self.key)
                    epoch=db.execute('SELECT max(epoch) FROM policy').fetchone()[0]
                    reason='VERIFIED';verdict='ALLOW'
                    if info is None or info.get('flow')!=flow:
                        verdict='UNRESOLVED';reason='NO_VALID_CERTIFICATE'
                    elif not isinstance(output,str) or hash_bytes(output.encode())!=info['output_hash']:
                        verdict='UNRESOLVED';reason='OUTPUT_BINDING_MISMATCH'
                    else:
                        row=db.execute('SELECT flow,sources,output,output_hash FROM issued WHERE seq=?',(info['seq'],)).fetchone()
                        if row is None or row[0]!=flow or row[1]!=json.dumps(info['sources']) or row[2]!=output or row[3]!=info['output_hash']:
                            verdict='UNRESOLVED';reason='MISSING_OR_INCONSISTENT_ISSUANCE'
                        elif db.execute('SELECT 1 FROM effects WHERE seq=?',(info['seq'],)).fetchone():
                            verdict='BLOCK';reason='DUPLICATE_EFFECT'
                        else:
                            nreads=db.execute('SELECT source FROM read_events WHERE seq=? ORDER BY id',(info['seq'],)).fetchall()
                            if [s for (s,) in nreads]!=info['sources']:
                                verdict='UNRESOLVED';reason='INCOMPLETE_READ_LEDGER'
                            else:
                                p=dict(db.execute('SELECT name,revoked FROM policy'))
                                if any(p[s] for s in info['sources']):
                                    verdict='BLOCK';reason='SOURCE_REVOKED'
                    db.execute('INSERT INTO decisions(flow,operation,verdict,reason,epoch) VALUES(?,?,?,?,?)',
                               (flow,'commit',verdict,reason,epoch))
                    if verdict=='ALLOW':
                        db.execute('INSERT INTO effects(flow,seq,output,epoch,sources) VALUES(?,?,?,?,?)',
                                   (flow,info['seq'],output,epoch,json.dumps(info['sources'])))
                    result={'verdict':verdict,'reason':reason,'committed':verdict=='ALLOW','epoch':epoch}
                else:
                    raise ValueError('Unknown endpoint')
                db.commit()
            self.reply(result)
        except Exception as exc:
            self.reply({'error':type(exc).__name__+': '+str(exc)},400)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--key',required=True)
    ap.add_argument('--vault',required=True);ap.add_argument('--admin-key',required=True);ap.add_argument('--port',type=int,default=8811)
    x=ap.parse_args()
    # The broker itself never creates world-readable secrets or vault data.
    Monitor.db_path=x.db;Monitor.key=Path(x.key).read_bytes();Monitor.admin_key=Path(x.admin_key).read_bytes();Monitor.vault=x.vault
    if len(Monitor.key)<32 or len(Monitor.admin_key)<32:raise RuntimeError('Broker keys too short')
    init_db(x.db)
    print('TRUSTED_MONITOR_READY',flush=True)
    ThreadingHTTPServer(('127.0.0.1',x.port),Monitor).serve_forever()

if __name__=='__main__':main()
