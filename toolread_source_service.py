#!/usr/bin/env python3
"""A separate HTTP source reader and transactional effect receiver.

The service records actual tool reads as they happen. /read_untracked is an
explicit bypass negative control. The output registration does NOT attest that
all contributing reads passed through this service.
"""
import argparse
import hashlib
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SOURCES = {'R': 'SYNTHETIC_REVOKED_DISCOVERY_RECORD_V1', 'K': 'SYNTHETIC_INDEPENDENT_LEGAL_RECORD_V1'}

def digest(s):
    return hashlib.sha256(s.encode('utf-8')).hexdigest()

def setup(db):
    with sqlite3.connect(db) as c:
        c.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS flow(flow_id TEXT PRIMARY KEY, epoch INTEGER NOT NULL DEFAULT 0,
          revoked INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS source_reads(
          id INTEGER PRIMARY KEY AUTOINCREMENT,flow_id TEXT NOT NULL,source TEXT NOT NULL,
          content_digest TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS transforms(
          id INTEGER PRIMARY KEY AUTOINCREMENT,flow_id TEXT NOT NULL,
          output_digest TEXT NOT NULL,observed_read_count INTEGER NOT NULL,
          nonce TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS policy_history(
          flow_id TEXT NOT NULL,epoch INTEGER NOT NULL,revoked INTEGER NOT NULL,
          PRIMARY KEY(flow_id,epoch));
        CREATE TABLE IF NOT EXISTS decisions(
          id INTEGER PRIMARY KEY AUTOINCREMENT,flow_id TEXT NOT NULL, mode TEXT NOT NULL,
          verdict TEXT NOT NULL,epoch INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS effects(
          id INTEGER PRIMARY KEY AUTOINCREMENT,flow_id TEXT NOT NULL,output TEXT NOT NULL,
          mode TEXT NOT NULL,epoch INTEGER NOT NULL);
        """)

def normalize(sources):
    return (isinstance(sources,list) and bool(sources)
            and all(isinstance(s,str) and s in SOURCES for s in sources))

class Handler(BaseHTTPRequestHandler):
    db=None
    def log_message(self, *_): pass
    def reply(self, data, status=200):
        raw=json.dumps(data,sort_keys=True,separators=(',',':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def do_GET(self):
        self.reply({'ready':True} if self.path=='/health' else {'error':'not_found'},
                   200 if self.path=='/health' else 404)
    def do_POST(self):
        try:
            length=int(self.headers.get('Content-Length',0))
            assert 0<length<100000
            data=json.loads(self.rfile.read(length))
            with sqlite3.connect(self.db,timeout=30,isolation_level=None) as c:
                c.execute('BEGIN IMMEDIATE')
                flow=data.get('flow_id')
                if self.path=='/setup':
                    c.execute('INSERT INTO flow(flow_id,epoch,revoked) VALUES(?,0,0)',(flow,))
                    c.execute('INSERT INTO policy_history VALUES(?,0,0)',(flow,))
                    reply={'ok':True,'epoch':0}
                elif self.path in ('/read','/read_untracked'):
                    source=data['source_id']
                    assert source in SOURCES
                    assert c.execute('SELECT 1 FROM flow WHERE flow_id=?',(flow,)).fetchone()
                    value=SOURCES[source]
                    if self.path=='/read':
                        c.execute('INSERT INTO source_reads(flow_id,source,content_digest) VALUES(?,?,?)',
                                  (flow,source,digest(value)))
                        read_id=c.execute('SELECT last_insert_rowid()').fetchone()[0]
                    else:
                        read_id=None
                    reply={'value':value,'source_event_id':read_id}
                elif self.path=='/transform':
                    output=data['output']; nonce=data['nonce']
                    assert isinstance(output,str) and isinstance(nonce,str)
                    num=c.execute('SELECT COUNT(*) FROM source_reads WHERE flow_id=?',(flow,)).fetchone()[0]
                    c.execute('INSERT INTO transforms(flow_id,output_digest,observed_read_count,nonce) VALUES(?,?,?,?)',
                              (flow,digest(output),num,nonce))
                    transform_id=c.execute('SELECT last_insert_rowid()').fetchone()[0]
                    reply={'transform_id':transform_id,'observed_reads':num}
                elif self.path=='/policy':
                    row=c.execute('SELECT epoch FROM flow WHERE flow_id=?',(flow,)).fetchone()
                    assert row is not None
                    epoch=row[0]+1; revoked=int(bool(data['revoked']))
                    c.execute('UPDATE flow SET epoch=?,revoked=? WHERE flow_id=?',(epoch,revoked,flow))
                    c.execute('INSERT INTO policy_history VALUES(?,?,?)',(flow,epoch,revoked))
                    reply={'epoch':epoch,'revoked':bool(revoked)}
                elif self.path=='/effect':
                    current=c.execute('SELECT epoch,revoked FROM flow WHERE flow_id=?',(flow,)).fetchone()
                    assert current is not None
                    epoch,revoked=current; mode=data['mode']; output=data['output']
                    if mode=='cached':
                        verdict='ALLOW' if data.get('cached_ok') is True else 'UNRESOLVED'
                    elif mode=='declared':
                        x=data.get('declared_sources')
                        verdict='UNRESOLVED' if not normalize(x) else ('BLOCK' if revoked and 'R' in x else 'ALLOW')
                    elif mode=='tool_trace':
                        r=c.execute('SELECT flow_id,output_digest,observed_read_count FROM transforms WHERE id=?',
                                    (data.get('transform_id'),)).fetchone()
                        if r is None or r[0]!=flow or r[1]!=digest(output):
                            verdict='UNRESOLVED'
                        else:
                            rows=c.execute('SELECT source FROM source_reads WHERE flow_id=? ORDER BY id',(flow,)).fetchall()
                            if not rows or len(rows)!=r[2]:
                                verdict='UNRESOLVED'
                            else:
                                verdict='BLOCK' if revoked and any(s=='R' for (s,) in rows) else 'ALLOW'
                    else:raise ValueError('Unknown guard')
                    c.execute('INSERT INTO decisions(flow_id,mode,verdict,epoch) VALUES(?,?,?,?)',
                              (flow,mode,verdict,epoch))
                    if verdict=='ALLOW':
                        c.execute('INSERT INTO effects(flow_id,output,mode,epoch) VALUES(?,?,?,?)',
                                  (flow,output,mode,epoch))
                    reply={'verdict':verdict,'committed':verdict=='ALLOW','epoch':epoch}
                else:raise ValueError('Unknown endpoint '+self.path)
                c.commit()
            self.reply(reply)
        except Exception as exc:
            self.reply({'error':type(exc).__name__+': '+str(exc)},400)

def main():
    a=argparse.ArgumentParser();a.add_argument('--db',required=True);a.add_argument('--port',type=int,default=8778)
    x=a.parse_args();Path(x.db).parent.mkdir(parents=True,exist_ok=True)
    setup(x.db);Handler.db=x.db
    print('REAL_SOURCE_SERVICE_READY',flush=True)
    ThreadingHTTPServer(('127.0.0.1',x.port),Handler).serve_forever()
if __name__=='__main__':main()
