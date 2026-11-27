import socket, threading, sys
sys.path.insert(0,"/home/claude/invester_os/src")
from investos.acquire.transport import UrllibTransport
got=[]
srv=socket.socket(); srv.bind(("127.0.0.1",0)); srv.listen(5); port=srv.getsockname()[1]
def run():
    while True:
        try: c,_=srv.accept()
        except OSError: return
        data=b""
        while b"\r\n\r\n" not in data: data+=c.recv(4096)
        got.append(data)
        if b"/redir" in data.split(b"\r\n")[0]:
            c.sendall(b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:%d/final\r\nContent-Length: 2\r\n\r\nhi")
        else:
            c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        c.close()
threading.Thread(target=run,daemon=True).start()
t=UrllibTransport(allowed_schemes=("http",))
import os
os.environ["HTTP_PROXY"]="http://127.0.0.1:1"
for h in ({}, {"User-Agent":"u","Accept-Encoding":"identity"}):
    r=t.get(f"http://127.0.0.1:{port}/a?x=1",headers=h,timeout=2)
    print(h, "->", repr(got[-1]), r.status)
r=t.get(f"http://127.0.0.1:{port}/redir",headers={},timeout=2)
print("redirect:",r.status,r.headers.get("location"),"requests so far",len(got))
for u in ("file:///etc/passwd","https://127.0.0.1:1/"):
    try: UrllibTransport(allowed_schemes=("http",)).get(u,headers={},timeout=1)
    except Exception as e: print(u,type(e).__name__)
