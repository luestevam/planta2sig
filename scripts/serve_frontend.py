"""Local frontend dev server, with a same-origin API proxy. No Node build required."""
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from pathlib import Path
import os

os.chdir(Path(__file__).resolve().parents[1]/'frontend')
class Handler(SimpleHTTPRequestHandler):
    def proxy(self):
        data=self.rfile.read(int(self.headers.get('Content-Length','0'))) if self.command=='POST' else None
        headers={k:v for k,v in self.headers.items() if k.lower() not in ['host','connection','accept-encoding','content-length']}
        req=Request('http://127.0.0.1:8000'+self.path,data=data,headers=headers,method=self.command)
        try: r=urlopen(req,timeout=180)
        except HTTPError as e: r=e
        self.send_response(r.status)
        for k,v in r.headers.items():
            if k.lower() not in ['transfer-encoding','connection','server','date']: self.send_header(k,v)
        self.end_headers(); self.wfile.write(r.read())
    def do_GET(self):
        if self.path.startswith('/api/'): self.proxy()
        else: super().do_GET()
    def do_POST(self): self.proxy()

ThreadingHTTPServer(('127.0.0.1',5173),Handler).serve_forever()
