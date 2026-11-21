from common import *
from investos.acquire.base import Collector, BodyVerdict, Outcome
from investos.acquire.manifest import ManifestStore
root, cfg = make_root()
st = ManifestStore.from_config(cfg)
for h, v in [("Authorization","Bearer x"),("Host","www.nseindia.com"),("Forwarded","for=1.2.3.4"),("X-Client-IP","1.2.3.4"),
             ("True-Client-IP","1.2.3.4"),("Via","1.1 x"),("X-Forwarded-Host","nseindia.com"),("Cookie2","a=b"),
             ("Referer ","https://www.nseindia.com/"),(" referer","x"),("Sec-Fetch-Site","same-origin"),
             ("Accept","*/*"),("X-Ok","a\r\nCookie: nsit=1"),("User_Agent","Mozilla/5.0"),("Origin","x")]:
    C = type("C", (Collector,), {"source":"s","source_version":"v","extra_headers":{h:v},
         "keys":lambda s,r:[], "url":lambda s,k:"https://x.test/", "check_body":lambda s,k,r:BodyVerdict(Outcome.FETCHED)})
    try:
        C(transport=None, manifest=st, cfg=cfg, run_id="r"); print(f"ACCEPTED  {h!r}: {v!r}")
    except ValueError as e: print(f"refused   {h!r}")
