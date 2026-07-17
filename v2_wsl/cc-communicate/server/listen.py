"""Debug CLI for listen (T24: now delegates to user_functions.listen, which
polls the kernel's ATOMIC listen_scan - no direct file scan, so no scan/write
race). The CC uses the `listen` MCP tool, not this CLI; this is for manual
debugging from a shell.

Usage: python listen.py <session_id> [timeout] [acked_ts]
Prints {messages, watermark} as JSON on stdout. Exits 0 if messages arrived,
2 on timeout (matching the old CLI's exit codes for compatibility).

The watermark ACK lifecycle (T24):
  - pass 0 as acked_ts the first time;
  - pass the returned `watermark` as acked_ts on the next call (the kernel
    archives only what you've confirmed, so an interrupted call loses nothing);
  - if you lose the watermark, the MCP tool query_my_ACK_timestamp recovers it.
"""
import json
import sys

import user_functions


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("usage: listen.py <session_id> [timeout] [acked_ts]\n")
        sys.exit(2)
    sid = sys.argv[1]
    timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
    acked_ts = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    # UTF-8 stdout so non-ASCII messages don't crash print on Windows (C5): the
    # default pipe encoding (cp936/cp1252) can't encode many chars.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    r = user_functions.listen(sid, acked_ts, timeout)
    if r.get("messages"):
        print(json.dumps(r, ensure_ascii=False))
        sys.exit(0)
    sys.exit(2)


if __name__ == "__main__":
    main()
