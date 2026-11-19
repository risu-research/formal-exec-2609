#!/usr/bin/env python3
"""Independent recipient controls its effect ledger and source revocation policy.

The source workflow does not own this service's recipient HMAC key or policy.
HTTP /commit runs recipient-side auth in the same SQLite transaction as effect.
Four deliberately different recipient modes are evaluated, including a fully
unguarded negative control and a stale-permit negative control.
"""
from __future__ import annotations
import argparse, base64, hashlib, hmac, json, sqlite3, secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODES = {'open','static','live','owner_live'}
def sha(x):return hashlib.sha256(x.encode()).hexdigest()
def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':')).encode()
def encode(payload,key):
    data=canonical(payload)
    return base64.urlsafe_b64encode(data).decode().rstrip('=')+'.'+hmac.new(key,data,'sha256').hexdigest()
def decode(token,key):
    if not isinstance(token,str) or len(token)>2000 or token.count('.')!=1:return None
    try:
        head,signature=token.split('.')
        data=base64.urlsafe_b64decode(head+'='*((-len(head))%4))
        if not hmac.compare_digest(hmac.new(key,data,'sha256').hexdigest(),signature):return None
        obj=json.loads(data)
        if not isinstance(obj,dict) or set(obj)!={'case','sources','digest','audience','permit_id','epoch'}:return None
        if not isinstance(obj['sources'],list) or not obj['sources'] or any(x not in ('R','K') for x in obj['sources']):return None
        return obj
    except Exception:return None


def init(db):
    with sqlite3.connect(db) as c:
        c.executescript('''PRAGMA journal_mode=WAL;
          CREATE TABLE cases(case_id TEXT PRIMARY KEY, mode TEXT NOT NULL, r_revoked INTEGER NOT NULL DEFAULT 0, epoch INTEGER NOT NULL DEFAULT 0);
          CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT,case_id TEXT, epoch INTEGER,revoked INTEGER);
          CREATE TABLE permits(permit_id TEXT PRIMARY KEY,case_id TEXT,sources TEXT,digest TEXT,audience TEXT,epoch INTEGER);
          CREATE TABLE decisions(id INTEGER PRIMARY KEY AUTOINCREMENT,case_id TEXT, route TEXT,verdict TEXT,reason TEXT,epoch INTEGER);
          CREATE TABLE effects(id INTEGER PRIMARY KEY AUTOINCREMENT,case_id TEXT,permit_id TEXT,body TEXT,via TEXT,epoch INTEGER);
        ''')

class Server(ThreadingHTTPServer):
    daemon_threads=True
class H(BaseHTTPRequestHandler):
    db=''; signer=b''; admin=b''; relay_hash=''
    def log_message(self,*args):pass
    def send(self,x,status=200):
        data=canonical(x);self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def do_GET(self):
        if self.path=='/health':return self.send({'ready':True})
        return self.send({'error':'not_found'},404)
    def do_POST(self):
        try:
            ln=int(self.headers.get('Content-Length','0'))
            if not 0<ln<15000:raise ValueError('bad request size')
            req=json.loads(self.rfile.read(ln))
            if not isinstance(req,dict):raise ValueError('object only')
            path=self.path
            with sqlite3.connect(self.db,timeout=15,isolation_level=None) as db:
                db.execute('BEGIN IMMEDIATE')
                if path in ('/setup','/issue','/revoke'):
                    if not hmac.compare_digest(self.headers.get('X-Owner-Admin',''),self.admin.hex()):
                        result={'verdict':'DENY','reason':'OWNER_ADMIN_REQUIRED'}
                    elif path=='/setup':
                        cid=req['case'];mode=req['mode']
                        if mode not in MODES:raise ValueError('bad mode')
                        db.execute('INSERT INTO cases VALUES(?,?,0,0)',(cid,mode))
                        db.execute('INSERT INTO events(case_id,epoch,revoked) VALUES(?,0,0)',(cid,))
                        result={'created':True}
                    elif path=='/issue':
                        cid=req['case'];sources=req['sources'];body=req['body'];audience=req.get('audience','authorized-recipient')
                        if not isinstance(sources,list) or not sources or any(x not in ('R','K') for x in sources):raise ValueError('invalid sources')
                        epoch=db.execute('SELECT epoch FROM cases WHERE case_id=?',(cid,)).fetchone()[0]
                        permit_id=secrets.token_hex(16)
                        bodyhash=sha(body)
                        db.execute('INSERT INTO permits VALUES(?,?,?,?,?,?)',(permit_id,cid,json.dumps(sources),bodyhash,audience,epoch))
                        token=encode({'case':cid,'sources':sources,'digest':bodyhash,'audience':audience,'permit_id':permit_id,'epoch':epoch},self.signer)
                        result={'token':token,'permit_id':permit_id}
                    else:
                        cid=req['case'];revoked=int(bool(req['revoked']))
                        row=db.execute('SELECT epoch FROM cases WHERE case_id=?',(cid,)).fetchone()
                        if row is None:raise ValueError('unknown case')
                        epoch=row[0]+1
                        db.execute('UPDATE cases SET r_revoked=?,epoch=? WHERE case_id=?',(revoked,epoch,cid))
                        db.execute('INSERT INTO events(case_id,epoch,revoked) VALUES(?,?,?)',(cid,epoch,revoked))
                        result={'revoked':bool(revoked),'ack_epoch':epoch}
                elif path=='/commit':
                    cid=req['case'];body=req['body'];token=req.get('token')
                    row=db.execute('SELECT mode,r_revoked,epoch FROM cases WHERE case_id=?',(cid,)).fetchone()
                    if row is None:raise ValueError('unknown case')
                    mode,revoked,epoch=row;verdict='ALLOW';reason='OPEN_ENDPOINT';info=None
                    # Always authenticate at the *receiving* service, not in an agent callback.
                    if mode!='open':
                        info=decode(token,self.signer)
                        if info is None or info['case']!=cid:
                            verdict='DENY';reason='INVALID_PERMIT'
                        elif info['audience']!='authorized-recipient':
                            verdict='DENY';reason='WRONG_TARGET'
                        elif info['digest']!=sha(body):
                            verdict='DENY';reason='OUTPUT_MISMATCH'
                        else:
                            p=db.execute('SELECT sources,digest,audience,epoch FROM permits WHERE permit_id=? AND case_id=?',(info['permit_id'],cid)).fetchone()
                            if p is None or p!=(json.dumps(info['sources']),info['digest'],info['audience'],info['epoch']):
                                verdict='DENY';reason='INCONSISTENT_ISSUANCE'
                            elif db.execute('SELECT 1 FROM effects WHERE permit_id=?',(info['permit_id'],)).fetchone():
                                verdict='DENY';reason='TOKEN_ALREADY_USED'
                            else:
                                reason='ISSUED_PERMISSION_VALID'
                    if verdict=='ALLOW' and mode=='owner_live':
                        auth=self.headers.get('X-Owner-Write','')
                        if not hmac.compare_digest(sha(auth),self.relay_hash):
                            verdict='DENY';reason='NO_OWNER_WRITE_AUTHORITY'
                    if verdict=='ALLOW' and mode in ('live','owner_live') and revoked and 'R' in info['sources']:
                        verdict='DENY';reason='CURRENT_SOURCE_REVOKED'
                    db.execute('INSERT INTO decisions(case_id,route,verdict,reason,epoch) VALUES(?,?,?,?,?)',
                               (cid,'commit',verdict,reason,epoch))
                    if verdict=='ALLOW':
                        db.execute('INSERT INTO effects(case_id,permit_id,body,via,epoch) VALUES(?,?,?,?,?)',
                                   (cid,info['permit_id'] if info else None,body,'owner-authenticated' if mode=='owner_live' else 'direct',epoch))
                    result={'verdict':verdict,'reason':reason,'committed':verdict=='ALLOW','epoch':epoch}
                else:raise ValueError('unknown route')
                db.commit()
            self.send(result)
        except Exception as exc:self.send({'error':type(exc).__name__+': '+str(exc)},400)

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--secrets',required=True);p.add_argument('--port',type=int,default=8885);a=p.parse_args()
    s=json.loads(Path(a.secrets).read_text()); H.db=a.db;H.signer=bytes.fromhex(s['signer']);H.admin=bytes.fromhex(s['admin']);H.relay_hash=s['relay_sha256'];init(a.db)
    print('RECIPIENT_READY',flush=True)
    Server(('127.0.0.1',a.port),H).serve_forever()
if __name__=='__main__':main()
