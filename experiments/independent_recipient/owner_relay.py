#!/usr/bin/env python3
"""Trusted effect relay owns only the recipient's write credential."""
import argparse,hashlib,json,urllib.request
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

class H(BaseHTTPRequestHandler):
    key=''; target=''
    def log_message(self,*a):pass
    def send(self,obj,status=200):
        b=json.dumps(obj,sort_keys=True).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    def do_GET(self):return self.send({'ready':True} if self.path=='/health' else {'error':'not found'},200 if self.path=='/health' else 404)
    def do_POST(self):
        if self.path!='/relay':return self.send({'error':'unknown route'},404)
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<10000:raise ValueError('body')
            obj=json.loads(self.rfile.read(size))
            # A relay cannot edit lineage: the recipient validates the signed permit.
            request=urllib.request.Request(self.target+'/commit',data=json.dumps({'case':obj['case'],'body':obj['body'],'token':obj.get('token')}).encode(),headers={'Content-Type':'application/json','X-Owner-Write':self.key},method='POST')
            with urllib.request.urlopen(request,timeout=15) as r:ans=json.loads(r.read())
            self.send(ans)
        except Exception as e:self.send({'error':type(e).__name__},400)
def main():
    a=argparse.ArgumentParser();a.add_argument('--secret',required=True);a.add_argument('--target',required=True);a.add_argument('--port',type=int,default=8886);x=a.parse_args()
    H.key=Path(x.secret).read_text().strip();H.target=x.target
    print('RELAY_READY',flush=True);ThreadingHTTPServer(('127.0.0.1',x.port),H).serve_forever()
if __name__=='__main__':main()
