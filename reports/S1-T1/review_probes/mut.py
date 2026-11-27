import subprocess, sys, shutil, os, json
A="src/investos/acquire/access.py"; T="src/investos/acquire/transport.py"; B="src/investos/acquire/base.py"
C="src/investos/acquire/access_cli.py"; N="src/investos/core/notify.py"; G="src/investos/core/config.py"
M=[
("M01 honest mode allows cookie/referer/UA",B,"_DENIED_HONEST = _DENIED_ALWAYS | {\"user-agent\", \"referer\", \"cookie\"}","_DENIED_HONEST = _DENIED_ALWAYS"),
("M02 skip decision-file check (scoped_exception)",B,"        if entry is not None and entry.decision is not None and mode is not AccessMode.HONEST:\n            check_decision_file(","        if False:\n            check_decision_file("),
("M03 decision file only needs Adapter line",B,"[f\"Adapter: {source}\", f\"Access-mode: {mode.value}\"]","[f\"Adapter: {source}\"]"),
("M04 decision check: accept >1 files",A,"    if len(files) != 1:","    if len(files) < 1:"),
("M05 scoped_exception without decision allowed",B,"        if mode is AccessMode.SCOPED_EXCEPTION and entry.decision is None:","        if False:"),
("M06 config upgrade honest class allowed",B,"        if cfg_mode != mode.value:","        if cfg_mode != mode.value and mode is not AccessMode.HONEST:"),
("M07 scoped_exception deny list drops origin",B,"_DENIED_ALWAYS = frozenset({\n    \"origin\", ","_DENIED_ALWAYS = frozenset({\n    "),
("M08 scoped_exception unlocks authorization",B,"\"origin\", \"authorization\", ","\"origin\", "),
("M09 headers_for validated only at first call",B,"        extra = _validate_header_map(self.headers_for(key, url), self._denied, \"headers_for\")","        extra = dict(self.headers_for(key, url))"),
("M10 honest UA format changed (no contact)",B,"(personal research; {contact})","(personal research)"),
("M11 empty contact allowed",B,"    if contact is None or contact == \"\":\n        raise ValueError(\"collect.contact is not configured\")","    if contact is None:\n        raise ValueError(\"collect.contact is not configured\")\n    if contact == \"\": contact = \"anon@x.org\""),
("M12 contact semicolon allowed",B,"any(c in contact for c in \";()\")","any(c in contact for c in \"()\")"),
("M13 .invalid placeholder allowed w/ real transport",B,"    if contact.lower().endswith(\".invalid\") and","    if False and"),
("M14 browser mode accepts transport too",B,"            if browser is None or transport is not None:","            if browser is None:"),
("M15 honest accepts browser",B,"        elif transport is None or browser is not None:","        elif transport is None:"),
("M16 transport follows redirects (via 3xx loop)",T,"        body, hdrs = self._decode(raw, hdrs)\n        return Response(","        if status in (301,302,303,307,308) and 'location' in hdrs:\n            return self.get(hdrs['location'], headers=headers, timeout=timeout)\n        body, hdrs = self._decode(raw, hdrs)\n        return Response("),
("M17 transport adds default User-Agent",T,"                conn.putheader(\"Connection\", \"close\")","                conn.putheader(\"Connection\", \"close\"); conn.putheader(\"User-Agent\", \"Python-urllib/3\")"),
("M18 transport adds Accept-Encoding identity",T,"skip_accept_encoding=True","skip_accept_encoding=False"),
("M19 body cap not enforced (gzip)",T,"            if len(out) > self._max:\n                raise TransportError(\"body too large\")","            if False:\n                raise TransportError(\"body too large\")"),
("M20 raw body cap not enforced",T,"            if len(buf) > self._max:\n                raise TransportError(\"body too large\")","            if False:\n                raise TransportError(\"body too large\")"),
("M21 whole-call deadline removed",T,"            if time.monotonic() > deadline:","            if False:"),
("M22 https cert verify off",T,"context=ssl.create_default_context())","context=ssl._create_unverified_context())"),
("M23 block not persisted",A,"        _append(self.host_path(host), _dump(ev))\n        self.last_halt = None","        self.last_halt = None"),
("M24 fail open on bad state file (status)",B,"            reason = f\"access state unreadable: {e}\"","            reason = None"),
("M25 429 not a block",B,"_BLOCK_STATUSES = frozenset({401, 403, 429, 451})","_BLOCK_STATUSES = frozenset({401, 403, 451})"),
("M26 451 not a block",B,"_BLOCK_STATUSES = frozenset({401, 403, 429, 451})","_BLOCK_STATUSES = frozenset({401, 403, 429})"),
("M27 block scope per source (host file ignored by status)",A,"blocked = any(e.date == today for e in self._cache.read(self.host_path(host), \"host\", None))","blocked = any(e.date == today and e.source == self._source for e in self._cache.read(self.host_path(host), \"host\", None))"),
("M28 same-day blocks counted twice",A,"        days = {e.date for e in self._all_blocked() if e.source == self._source and e.ts > since and e.date in window}\n        if len(days) < HALT_THRESHOLD:","        days = [e.date for e in self._all_blocked() if e.source == self._source and e.ts > since and e.date in window]\n        if len(days) < HALT_THRESHOLD:"),
("M29 7-day window off by one (8 days)",A,"for i in range(HALT_WINDOW_DAYS)}","for i in range(HALT_WINDOW_DAYS + 1)}"),
("M30 7-day window off by one (6 days)",A,"for i in range(HALT_WINDOW_DAYS)}","for i in range(HALT_WINDOW_DAYS - 1)}"),
("M31 halt threshold 4",A,"HALT_THRESHOLD = 3","HALT_THRESHOLD = 4"),
("M32 halt counts other sources' blocks",A,"if e.source == self._source and e.ts > since and e.date in window","if e.ts > since and e.date in window"),
("M33 cleared does not reset count",A,"        since = max(cleared) if cleared else \"\"","        since = \"\""),
("M34 notifier not called on halt",B,"            try:\n                self._notifier.notify(","            try:\n                None and self._notifier.notify("),
("M35 notifier exception propagates",B,"            except Exception as e:                              # the alert never changes the outcome","            except ZeroDivisionError as e:"),
("M36 halted state does not block (status)",A,"        if halt is not None:\n            reason = f\"adapter halted","        if False:\n            reason = f\"adapter halted"),
("M37 clear_halt skips decision file",A,"    check_decision_file(cfg.repo_root, decision, [f\"Clears-halt: {source}\"])","    pass"),
("M38 clear_halt works when not halted",A,"    if _latest_halt_state(_parse_file(sp, \"source\", source)) is None:\n        raise ValueError(f\"{source} is not halted\")\n    check","    check"),
("M39 cap not enforced",A,"            if count >= cap:\n                return False","            if False:\n                return False"),
("M40 cap off by one",A,"            if count >= cap:","            if count > cap:"),
("M41 corrupt counter fails open",A,"        return False\n","        return True\n"),
("M42 counter per-process only (no lock)",A,"        with _flock(self._dir / f\"{self._source}.lock\", fcntl.LOCK_EX):\n            try:\n                text = path.read_text","        with _flock(self._dir / f\"{self._source}.{os.getpid()}.lock\", fcntl.LOCK_EX):\n            try:\n                text = path.read_text"),
("M43 limiter per-process only (lock per pid)",A,"        with _flock(self._dir / f\"{host}.lock\", fcntl.LOCK_EX):","        with _flock(self._dir / f\"{host}.{os.getpid()}.lock\", fcntl.LOCK_EX):"),
("M44 limiter ignores stamp file",A,"                if _INT_RE.fullmatch(text.strip()):","                if False:"),
("M45 limiter future stamp unclamped",A,"wait_wall = min(max(interval - (self._wall() - last), 0.0), interval)","wait_wall = max(interval - (self._wall() - last), 0.0)"),
("M46 limiter bad stamp = no wait",A,"                    wait_wall = interval\n","                    wait_wall = 0.0\n"),
("M47 floor 2.5 not applied",B,"        if self._mode is not AccessMode.HONEST:\n            interval = max(interval, _FLOOR_INTERVAL_S)","        pass"),
("M48 daily cap ignored for honest",B,"self._daily_cap = entry.daily_cap if entry is not None else None","self._daily_cap = entry.daily_cap if (entry is not None and mode is not AccessMode.HONEST) else None"),
("M49 skipped keys not first (block check before skip)",B,"        existing = self._manifest.get_by_key(self.source, key)\n        if existing is not None:\n            try:\n                rec = _Rec(Outcome.SKIPPED","        existing = None if self._access.status(\"x\").reason else self._manifest.get_by_key(self.source, key)\n        if existing is not None:\n            try:\n                rec = _Rec(Outcome.SKIPPED"),
("M50 state not re-read before retries",B,"            attempts += 1\n            self._gate(key, host, interval, attempts, cause)","            attempts += 1\n            if attempts == 1: self._gate(key, host, interval, attempts, cause)"),
("M51 429 retried (no block)",B,"        if resp.status in _BLOCK_STATUSES:","        if resp.status in _BLOCK_STATUSES and resp.status != 429:"),
("M52 signature scan whole body",B,"_SIGNATURE_SCAN_BYTES = 262144","_SIGNATURE_SCAN_BYTES = 10**9"),
("M53 undecoded content-encoding accepted",B,"        if enc.strip() and enc.strip().lower() != \"identity\":","        if False:"),
("M54 browser redirect not detected",B,"        if self._browser is not None and urldefrag(final_url)[0] != urldefrag(url)[0]:","        if False:"),
("M55 manifest access_mode recorded as honest",B,"access_mode=self._mode.value,","access_mode='honest',"),
("M56 fetch_host not recorded properly",B,"fetch_host=self._hostname())","fetch_host='x')"),
("M57 sidecar schema stays 1",B,"\"schema\": 2, \"access_mode\"","\"schema\": 1, \"access_mode\""),
("M58 osascript shell=True/ no platform guard",N,"        if sys.platform != \"darwin\":\n            return None","        pass"),
("M59 osascript quote not escaped",N,"    return s.replace(\"\\\\\", \"\\\\\\\\\").replace('\"', '\\\\\"')","    return s"),
("M60 osascript timeout removed",N,"timeout=5, ",""),
("M61 notifier failure logged not",N,"            log.get_logger(\"notify\").warning(\"notify_failed\", reason=f\"exit {r.returncode}\")","            pass"),
("M62 torn tail append allowed",A,"                if f.read(1) != b\"\\n\":","                if False:"),
("M63 reader accepts torn tail",A,"        if data and not data.endswith(b\"\\n\"):\n            raise AccessStateError(f\"{path.name}: torn tail (no final newline)\")","        pass"),
("M64 wrong date accepted",A,"        if obj[\"date\"] != ist_date(dt):","        if False:"),
("M65 IST day -> UTC day",A,"    return dt.astimezone(IST).date().isoformat()","    return dt.astimezone(timezone.utc).date().isoformat()"),
("M66 config: collect.contact str check dropped / unknown key allowed",G,"            raise ConfigError(f\"unknown config key: collect.{k}\")","            pass"),
("M67 config: daily_cap not required",G,"        if mode != \"honest\" and cap is None:","        if False:"),
("M68 config: scoped decision not required",G,"        if mode == \"scoped_exception\" and decision is None:","        if False:"),
("M69 cli: build_collector empty contact passes",C,"    if not cfg.collect.contact:","    if False:"),
("M70 cli: clear exit code 0 on refusal",C,"        print(f\"error: {e}\", file=sys.stderr)\n        return 1","        print(f\"error: {e}\", file=sys.stderr)\n        return 0"),
("M71 cli: default notifier always osascript",C,"    return OsascriptNotifier() if sys.platform == \"darwin\" else NullNotifier()","    return OsascriptNotifier()"),
("M72 collector default notifier = Osascript",B,"notifier if notifier is not None else NullNotifier()","notifier if notifier is not None else __import__('investos.core.notify',fromlist=['x']).OsascriptNotifier()"),
("M73 accept_encoding gzip advertised w/o brotli",T,"\"gzip, deflate, br\" if self._br is not None else \"gzip, deflate\"","\"gzip, deflate, br\""),
("M74 headers copy: Cookie logged (retry log url)",B,"self._log.warning(\"retry\", key=key, url=url,","self._log.warning(\"retry\", key=key, url=url, cookie=str(sent),"),
("M41 corrupt counter fails open",A,"reason=why)\n        return False","reason=why)\n        return True"),
("M75 host regex allows slash/path",A,"_HOST_RE = re.compile(r\"[a-z0-9][a-z0-9.-]{0,252}\"","_HOST_RE = re.compile(r\"[a-z0-9./][a-z0-9./-]{0,252}\""),
("M76 limiter drops monotonic rule",A,"            wait = max(wait_wall, wait_mono)","            wait = wait_wall"),
("M77 limiter drops wall/stamp rule",A,"            wait = max(wait_wall, wait_mono)","            wait = wait_mono"),
("M78 halt appended twice (guard off)",A,"        if _append(sp, _dump(ev), guard=not_yet_halted):","        if _append(sp, _dump(ev)):"),
("M79 deny list drops host",B,"    \"origin\", \"authorization\", \"proxy-authorization\", \"host\", ","    \"origin\", \"authorization\", \"proxy-authorization\", "),
("M80 any past halted counts after cleared",A,"    last = events[-1] if events else None\n    return last if last is not None and last.event == \"halted\" else None","    return next((e for e in events if e.event == \"halted\"), None)"),
("M81 whole-call deadline 40x",T,"deadline = time.monotonic() + 4 * timeout","deadline = time.monotonic() + 40 * timeout"),
("M82 content-length mismatch ignored",T,"        if cl is not None and cl.strip().isdigit() and int(cl) != len(raw):","        if False:"),
("M83 counter day UTC not IST",A,"        day = ist_date(self._clock())\n        path = self._dir","        day = self._clock().astimezone(timezone.utc).date().isoformat()\n        path = self._dir"),
("M84 http proxies honoured (env)",T,"            conn = http.client.HTTPConnection(host, port, timeout=timeout)","            import os as _o; p=_o.environ.get('HTTP_PROXY'); conn = http.client.HTTPConnection(*( (p.split('//')[1].split(':')[0], int(p.split(':')[-1])) if p else (host, port)), timeout=timeout)"),
("M85 browser real_browser sends extra headers allowed (denied set)",B,"                                     else _DENIED_HONEST, \"extra_headers\")","                                     else frozenset(), \"extra_headers\")"),
("M86 clear_halt does not reset (appends nothing)",A,"    if not _append(sp, _dump(ev), guard=still_halted):\n        raise ValueError(f\"{source} is not halted\")","    if False:\n        raise ValueError(f\"{source} is not halted\")"),
]
sel=sys.argv[3:] 
work=sys.argv[1]; out=sys.argv[2]
res=[]
for m in M:
    mid=m[0].split()[0]
    if sel and mid not in sel: continue
    name,f,old,new=m
    p=os.path.join(work,f); s=open(p).read()
    if s.count(old)!=1:
        res.append((name,"BADPATCH",s.count(old))); print(name,"BADPATCH",s.count(old),flush=True); continue
    open(p,"w").write(s.replace(old,new))
    try:
        r=subprocess.run([sys.executable,"-m","pytest","tests/unit/acquire","tests/unit/core","-x","-q","-p","no:cacheprovider"],cwd=work,capture_output=True,text=True,timeout=300)
        tail=[l for l in r.stdout.splitlines() if l.startswith("FAILED") or l.startswith("ERROR")]
        v="KILLED" if r.returncode!=0 else "SURVIVED"
        info=(tail[0][:110] if tail else r.stdout.strip().splitlines()[-1][:80])
    except subprocess.TimeoutExpired:
        v,info="KILLED(timeout)",""
    open(p,"w").write(s)
    res.append((name,v,info)); print(name,v,info,flush=True)
json.dump(res,open(out,"w"),indent=1)
