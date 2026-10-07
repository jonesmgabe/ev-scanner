#!/usr/bin/env python3
"""Fail (exit 1) if the Odds API key, or anything shaped like an API key/token,
appears in the given files, the git index, or git history.

  python3 tools/check_secrets.py --git            # every tracked/staged file
  python3 tools/check_secrets.py --git --history  # + every blob in history
  python3 tools/check_secrets.py site/            # a directory (e.g. the built site)

The key is read from ODDS_API_KEY and compared in memory; it is never printed.
Only file names are reported, never the matching text."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

PATTERNS = [
    re.compile(rb"apiKey=[0-9a-fA-F]{32}"),
    re.compile(rb"ODDS_API_KEY\s*[=:]\s*['\"]?[0-9a-fA-F]{32}"),
    re.compile(rb"\b(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
]


def hits(blob: bytes, key: bytes) -> bool:
    return (bool(key) and key in blob) or any(p.search(blob) for p in PATTERNS)


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def main(argv: list[str]) -> int:
    key = (os.environ.get("ODDS_API_KEY") or "").strip().encode()
    if not key:
        print("[check_secrets] warning: ODDS_API_KEY not set - only pattern checks run", file=sys.stderr)
    bad, n = [], 0
    targets = [a for a in argv if not a.startswith("--")]
    for t in targets:
        p = Path(t)
        for f in ([p] if p.is_file() else [x for x in p.rglob("*") if x.is_file()]):
            n += 1
            if hits(f.read_bytes(), key):
                bad.append(str(f))
    if "--git" in argv:
        for name in git("ls-files", "-z", "--cached").split(b"\0"):
            if not name:
                continue
            n += 1
            staged = git("show", f":{name.decode()}")
            work = Path(name.decode()).read_bytes() if Path(name.decode()).exists() else b""
            if hits(staged, key) or hits(work, key):
                bad.append(name.decode())
    if "--history" in argv:
        objs = git("rev-list", "--all", "--objects").decode().split("\n")
        shas = [o.split(" ", 1)[0] for o in objs if o]
        if shas:
            proc = subprocess.run(["git", "cat-file", "--batch"], input="\n".join(shas).encode(),
                                  capture_output=True, check=True)
            data, i = proc.stdout, 0
            while i < len(data):
                nl = data.index(b"\n", i)
                sha, typ, size = data[i:nl].split(b" ")
                body = data[nl + 1: nl + 1 + int(size)]
                i = nl + 2 + int(size)
                n += 1
                if typ in (b"blob", b"commit", b"tag") and hits(body, key):
                    bad.append(f"history object {sha.decode()[:10]} ({typ.decode()})")
    if bad:
        print(f"[check_secrets] FAIL: possible secret in {len(bad)} item(s):", file=sys.stderr)
        for b in bad:
            print("  " + b, file=sys.stderr)
        return 1
    print(f"[check_secrets] OK: {n} item(s) scanned, no API key or token found")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
