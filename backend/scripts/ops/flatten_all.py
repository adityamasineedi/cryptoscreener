"""Kill-switch CLI for live paper (owner ruling D128d). Paper only; talks to the running backend's API.

    python backend/scripts/ops/flatten_all.py status
    python backend/scripts/ops/flatten_all.py flatten [--note "why"]       # asks you to type FLATTEN ALL
    python backend/scripts/ops/flatten_all.py clear manual_flatten          # asks you to type CLEAR

`flatten` closes every open paper position at the current price (ledger exit_reason KILL_SWITCH_FLATTEN) and
blocks new entries until `clear manual_flatten`. It only works when KILL_SWITCHES_ENABLED=true and the backend
holds the paper writer lock; otherwise the API answers 409 and nothing changes.

From the PC itself no token is needed (the API accepts loopback). From another machine pass --url and set the
admin token in an environment variable named by --token-env (default PAPER_ADMIN_TOKEN); the token is sent as the
X-Paper-Admin header and never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

FLATTEN_CONFIRM_TEXT = "FLATTEN ALL"
CLEAR_CONFIRM_TEXT = "CLEAR"


def _call(url: str, method: str, path: str, params: dict[str, str] | None, token_env: str) -> tuple[int, object]:
    full = url.rstrip("/") + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(full, method=method, data=b"" if method == "POST" else None)
    token = os.environ.get(token_env, "")
    if token:
        req.add_header("X-Paper-Admin", token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            body: object = json.loads(exc.read() or b"{}")
        except ValueError:
            body = {}
        return exc.code, body
    except urllib.error.URLError as exc:
        return 0, {"error": f"backend not reachable at {url}: {exc.reason}"}


def _confirm(expected: str, given: str | None) -> bool:
    if given is not None:
        return given == expected
    try:
        typed = input(f'Type "{expected}" to continue: ')
    except EOFError:
        return False
    return typed.strip() == expected


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="flatten_all", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--token-env", default="PAPER_ADMIN_TOKEN")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    f = sub.add_parser("flatten")
    f.add_argument("--note", default="")
    f.add_argument("--confirm", default=None, help=f'non-interactive: pass exactly "{FLATTEN_CONFIRM_TEXT}"')
    c = sub.add_parser("clear")
    c.add_argument("trip", choices=["manual_flatten", "heartbeat", "mismatch", "daily_loss_limit"])
    c.add_argument("--confirm", default=None, help=f'non-interactive: pass exactly "{CLEAR_CONFIRM_TEXT}"')
    a = ap.parse_args(argv)

    if a.cmd == "status":
        code, body = _call(a.url, "GET", "/api/paper/kill-switch", None, a.token_env)
    elif a.cmd == "flatten":
        if not _confirm(FLATTEN_CONFIRM_TEXT, a.confirm):
            print("not confirmed; nothing sent", file=sys.stderr)
            return 2
        code, body = _call(a.url, "POST", "/api/paper/kill-switch/flatten-all",
                           {"confirm": FLATTEN_CONFIRM_TEXT, "note": a.note}, a.token_env)
    else:
        if not _confirm(CLEAR_CONFIRM_TEXT, a.confirm):
            print("not confirmed; nothing sent", file=sys.stderr)
            return 2
        code, body = _call(a.url, "POST", "/api/paper/kill-switch/clear", {"trip": a.trip, "confirm": CLEAR_CONFIRM_TEXT},
                           a.token_env)
    print(json.dumps({"http_status": code, "body": body}, indent=2, default=str))
    return 0 if 200 <= code < 300 else 1


if __name__ == "__main__":
    sys.exit(main())
