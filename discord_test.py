"""Milestone A - Discord local RPC test.

  python discord_test.py --setup --client-id X --client-secret Y
      one-time OAuth authorization (consent inside Discord), saves credentials
  python discord_test.py
      connects, reads VOICE_SETTINGS, prints mute/deafen, listens for 10s

Credentials live in %LOCALAPPDATA%\\DiscordLedBridge\\credentials.json with ACL
restricted to the current user only; never in the repo.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from discord_rpc import DiscordIPC, DiscordRPCError, voice_state

APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA", "."), "DiscordLedBridge")
CRED_PATH = os.path.join(APP_DIR, "credentials.json")
TOKEN_URL = "https://discord.com/api/v10/oauth2/token"
DEFAULT_REDIRECT = "http://localhost:53123"


# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------

def _restrict_acl(path):
    """ACL for the current user only (Windows equivalent of mode 600)."""
    user = os.environ.get("USERNAME", "")
    subprocess.run(
        ["icacls", path, "/inheritance:r", f"/grant:r", f"{user}:(R,W)"],
        check=False,
        capture_output=True,
    )


def load_credentials():
    if not os.path.exists(CRED_PATH):
        print(f"No credentials: run  python discord_test.py --setup first")
        sys.exit(1)
    with open(CRED_PATH) as f:
        return json.load(f)


def save_credentials(data):
    os.makedirs(APP_DIR, exist_ok=True)
    with open(CRED_PATH, "w") as f:
        json.dump(data, f, indent=2)
    _restrict_acl(CRED_PATH)
    print(f"Credentials saved to {CRED_PATH}")


def refresh_access_token(cred):
    body = urllib.parse.urlencode({
        "client_id": cred["client_id"],
        "client_secret": cred["client_secret"],
        "grant_type": "refresh_token",
        "refresh_token": cred["refresh_token"],
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body)
    req.add_header("User-Agent", "DiscordLedBridge/1.0")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req) as resp:
        tok = json.load(resp)
    cred["access_token"] = tok["access_token"]
    if tok.get("refresh_token"):
        cred["refresh_token"] = tok["refresh_token"]
    cred["expires_at"] = time.time() + tok.get("expires_in", 604800)
    save_credentials(cred)
    return cred


# ---------------------------------------------------------------------------
# one-time OAuth
# ---------------------------------------------------------------------------

class _RedirectHandler(BaseHTTPRequestHandler):
    auth_code = None
    state = None

    def log_message(self, *a):
        pass

    def do_GET(self):
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        if params.get("code"):
            self.__class__.auth_code = params["code"][0]
            body = b"<h1>OK. You can close this page.</h1>"
            self.send_response(200)
        else:
            body = (f"Error: {params.get('error', ['?'])[0]} - "
                    f"{params.get('error_description', [''])[0]}").encode()
            self.send_response(400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def oauth_flow(client_id, client_secret, redirect_uri):
    parsed = urllib.parse.urlparse(redirect_uri)
    port = parsed.port or 80
    server = HTTPServer(("127.0.0.1", port), _RedirectHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    params = urllib.parse.urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": "rpc rpc.voice.read",
        "prompt": "consent",
    })
    auth_url = f"https://discord.com/api/oauth2/authorize?{params}"
    print("Open the consent in Discord...")
    os.startfile(auth_url)

    deadline = time.time() + 180
    while time.time() < deadline and _RedirectHandler.auth_code is None:
        time.sleep(0.3)
    server.shutdown()
    if _RedirectHandler.auth_code is None:
        print("Timeout: no authorization received.")
        sys.exit(1)

    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": _RedirectHandler.auth_code,
        "redirect_uri": redirect_uri,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body)
    req.add_header("User-Agent", "DiscordLedBridge/1.0")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req) as resp:
            tok = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        print(f"Token exchange failed ({exc.code}): {detail}")
        sys.exit(1)
    save_credentials({
        "client_id": client_id,
        "client_secret": client_secret,
        "access_token": tok["access_token"],
        "refresh_token": tok.get("refresh_token", ""),
        "expires_at": time.time() + tok.get("expires_in", 604800),
    })
    print("Authorization completed.")


# ---------------------------------------------------------------------------
# test
# ---------------------------------------------------------------------------

def run_test(duration=10):
    cred = load_credentials()
    with DiscordIPC() as rpc:
        try:
            rpc.handshake(cred["client_id"])
            try:
                rpc.authenticate(cred["access_token"])
            except DiscordRPCError as exc:
                if "4004" in str(exc) or "4005" in str(exc) or "4010" in str(exc):
                    print("Token expired/invalid, refreshing with refresh token...")
                    cred = refresh_access_token(cred)
                    rpc.authenticate(cred["access_token"])
                else:
                    raise
            print("Authenticated. Reading voice settings...")
            settings = rpc.get_voice_settings()
            state = voice_state(settings)
            print(f"VOICE_SETTINGS -> {state}")
            if state is None:
                print("mute/deaf fields not present in payload:", list(settings.keys())[:15])

            print("Subscribing to VOICE_SETTINGS_UPDATE and listening for", duration, "s...")

            def handler(frame):
                evt = frame.get("evt")
                if evt == "VOICE_SETTINGS_UPDATE":
                    print("EVENT:", voice_state(frame.get("data", {})))
                return False

            stop = threading.Event()
            stop_timer = threading.Timer(duration, stop.set)
            stop_timer.start()
            rpc.listen(handler, stop=stop)
            stop_timer.cancel()
        except DiscordRPCError as exc:
            print("RPC ERROR:", exc)
            sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", action="store_true", help="one-time OAuth authorization")
    ap.add_argument("--client-id", help="Application ID of the Discord app")
    ap.add_argument("--client-secret", help="Client Secret of the Discord app")
    ap.add_argument("--redirect", default=DEFAULT_REDIRECT,
                    help=f"registered redirect URI (default {DEFAULT_REDIRECT})")
    ap.add_argument("--duration", type=int, default=10,
                    help="seconds of event listening (default 10)")
    args = ap.parse_args()

    if args.setup:
        if not args.client_id or not args.client_secret:
            print("--setup requires --client-id and --client-secret")
            sys.exit(1)
        oauth_flow(args.client_id, args.client_secret, args.redirect)
    else:
        run_test(args.duration)


if __name__ == "__main__":
    main()