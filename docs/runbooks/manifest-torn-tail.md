# Runbook: torn manifest or ledger tail

## What it is
A torn tail is a last line that was only partly written, so the file does not end with a newline. It happens after
a power loss, a full disk (ENOSPC) or a killed process. The code never repairs it (specs/core_manifest.md R7): every
store and lookup for that source raises `ManifestCorruptError`, and `investos check manifest` reports `bad_line`
for the last line. Fix it by hand, as below. A reader that merely catches a writer mid-append waits for the lock
and does not see this error, so a persistent error means a real torn tail.

Files: `data/manifests/<source>.jsonl` (lock: `data/manifests/<source>.jsonl.lock`). The same steps apply to a
ledger tail: `registry/trials.jsonl` and `data/ledger/decisions.jsonl` (locks: `<file>.lock`). Ledger rows
are hash-chained, so only an unterminated final fragment may be removed; if any complete line is damaged, stop and
treat it as tampering.

## Steps
1. Diagnose. Run `investos check manifest` (or `investos check registry`). Confirm the only problem is the last
   line of one file. Check the tail: `tail -c 300 <file> | xxd | tail`. The last byte is not `0a`.
2. Stop every collector and routine that writes to that source (the poller, backfill runs, cron jobs). Check with
   `lsof <file>.lock` that nobody holds the lock.
3. Back up. Copy the file aside before touching it: `cp -p <file> <file>.torn-$(date -u +%Y%m%dT%H%M%SZ)`.
   Keep the copy outside the tracked folders, or do not `git add` it.
4. Take the lock for the edit so nothing appends meanwhile: `flock <file>.lock <command>`.
5. Find the end of the last complete line. The fragment is everything after the last newline:
   `python3 -c "d=open('<file>','rb').read(); print(d.rfind(b'\n')+1, len(d))"`.
   Look at the fragment. It must be unterminated, with no newline, and be the final line only.
6. Truncate to the last complete line, nothing else: `flock <file>.lock truncate -s <offset from step 5> <file>`.
   Never remove a terminated line.
7. Re-run `investos check manifest` (and `investos check all`). For a manifest the raw file of the lost line may
   exist without a row; the collector's next run re-fetches that key and adds the row. For a ledger, `investos
   check registry` must pass, with roots.
8. Record a decision: add a row (or a file in `docs/decisions/`) with the date, the file, the bytes removed
   (offset before and after), the cause if known, and the backup name.
9. Restart the collectors.

## Manifest schema v2 lines seen by older code
Since S1-T2 every new manifest line carries `"schema": 2` (v2, thirteen fields). A checkout from before that change treats a v2 line as corrupt (extra fields) and reports `bad_line`, or raises `ManifestCorruptError`. If the `bad_line` is on a complete (newline-terminated) line, it can mean "a v2 line seen by old code": update the code (pull `main`), do not truncate. Truncate only a genuinely torn, unterminated last line, as above.
