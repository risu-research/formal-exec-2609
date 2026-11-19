#!/usr/bin/env python3
"""Out-of-process synthetic effects service with a transactional write gate.

The test deliberately exposes /write_unguarded as an insecure negative control.
Only /write_atomic enforces policy and receipt integrity in the SQL transaction.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

def digest(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()

def init(db: str) -> None:
    with sqlite3.connect(db) as c:
        c.executescript("""
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS policies (
            case_id TEXT PRIMARY KEY, revoked INTEGER NOT NULL, epoch INTEGER NOT NULL
          );
          CREATE TABLE IF NOT EXISTS policy_events (
            case_id TEXT NOT NULL, epoch INTEGER NOT NULL, revoked INTEGER NOT NULL,
            PRIMARY KEY(case_id, epoch)
          );
          CREATE TABLE IF NOT EXISTS receipts (
            case_id TEXT PRIMARY KEY, value TEXT
          );
          CREATE TABLE IF NOT EXISTS writes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL,
            payload TEXT NOT NULL, entrypoint TEXT NOT NULL, epoch INTEGER NOT NULL
          );
          CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL,
            api TEXT NOT NULL, verdict TEXT NOT NULL, epoch INTEGER NOT NULL
          );
        """)

def policy_decision(c: sqlite3.Connection, case_id: str, payload: str):
    row=c.execute("SELECT revoked,epoch FROM policies WHERE case_id=?",(case_id,)).fetchone()
    if row is None: raise ValueError("Uninitialized case")
    revoked,epoch=row
    raw=c.execute("SELECT value FROM receipts WHERE case_id=?",(case_id,)).fetchone()
    receipt=json.loads(raw[0]) if raw and raw[0] is not None else None
    if not isinstance(receipt,dict) or receipt.get("complete") is not True:
        return "UNRESOLVED",epoch
    if receipt.get("payload_digest")!=digest(payload):
        return "UNRESOLVED",epoch
    src=receipt.get("sources")
    if not isinstance(src,list) or not src or any(x not in ("R","K") for x in src):
        return "UNRESOLVED",epoch
    return ("BLOCK" if revoked and "R" in src else "ALLOW"),epoch

class Handler(BaseHTTPRequestHandler):
    db: str
    def log_message(self,*_): pass
    def send(self,obj,status=200):
        raw=json.dumps(obj,sort_keys=True,separators=(",",":")).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def do_GET(self):
        if self.path=="/health":self.send({"ready":True})
        else:self.send({"error":"not found"},404)
    def do_POST(self):
        try:
            size=int(self.headers.get("Content-Length","0"))
            if size>100000:raise ValueError("Request too large")
            x=json.loads(self.rfile.read(size))
            op=self.path
            with sqlite3.connect(self.db,timeout=30,isolation_level=None) as c:
                c.execute("BEGIN IMMEDIATE")
                if op=="/setup":
                    case_id=x["case_id"]
                    if c.execute("SELECT 1 FROM policies WHERE case_id=?",(case_id,)).fetchone():
                        raise ValueError("Case already created")
                    c.execute("INSERT INTO policies VALUES(?,0,0)",(case_id,))
                    c.execute("INSERT INTO policy_events VALUES(?,0,0)",(case_id,))
                    r=json.dumps(x.get("receipt"),sort_keys=True) if x.get("receipt") is not None else None
                    c.execute("INSERT INTO receipts VALUES(?,?)",(case_id,r))
                    result={"created":True,"epoch":0}
                elif op=="/set_policy":
                    case_id=x["case_id"]
                    current=c.execute("SELECT epoch FROM policies WHERE case_id=?",(case_id,)).fetchone()
                    if current is None:raise ValueError("Unknown case")
                    epoch=current[0]+1
                    revoked=int(bool(x["revoked"]))
                    c.execute("UPDATE policies SET revoked=?,epoch=? WHERE case_id=?",(revoked,epoch,case_id))
                    c.execute("INSERT INTO policy_events VALUES(?,?,?)",(case_id,epoch,revoked))
                    result={"revoked":bool(revoked),"epoch":epoch}
                elif op=="/authorize":
                    case_id=x["case_id"]
                    verdict,epoch=policy_decision(c,case_id,x["payload"])
                    c.execute("INSERT INTO decisions(case_id,api,verdict,epoch) VALUES(?,?,?,?)",
                              (case_id,"authorize",verdict,epoch))
                    result={"verdict":verdict,"epoch":epoch}
                elif op in ("/write_unguarded","/write_atomic"):
                    case_id=x["case_id"]
                    payload=x["payload"]
                    if op=="/write_atomic":
                        verdict,epoch=policy_decision(c,case_id,payload)
                    else:
                        row=c.execute("SELECT epoch FROM policies WHERE case_id=?",(case_id,)).fetchone()
                        if row is None:raise ValueError("Unknown case")
                        epoch=row[0]
                        verdict="ALLOW"
                    c.execute("INSERT INTO decisions(case_id,api,verdict,epoch) VALUES(?,?,?,?)",
                              (case_id,op,verdict,epoch))
                    committed=verdict=="ALLOW"
                    if committed:
                        c.execute("INSERT INTO writes(case_id,payload,entrypoint,epoch) VALUES(?,?,?,?)",
                                  (case_id,payload,op,epoch))
                    result={"verdict":verdict,"committed":committed,"epoch":epoch}
                else:raise ValueError("Unknown operation")
                c.commit()
            self.send(result)
        except Exception as exc:
            self.send({"error":type(exc).__name__+": "+str(exc)},400)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--db",required=True)
    ap.add_argument("--port",type=int,default=8765)
    args=ap.parse_args()
    Path(args.db).parent.mkdir(parents=True,exist_ok=True)
    init(args.db)
    Handler.db=args.db
    print("EFFECT_SINK_READY",args.db,flush=True)
    ThreadingHTTPServer(("127.0.0.1",args.port),Handler).serve_forever()

if __name__=="__main__":main()
