"""A fake router that speaks whatever a definition says, used only by selftest.

It exists so the generic client can be exercised end to end without hardware.
It reads the definition and answers each endpoint with a payload built from the
declared response fields, honouring the declared crypto and session rules. It is
not a simulator of any real firmware: it proves the client executes the
definition, nothing about the device.
"""
import base64
import hashlib
import json
import os
import re
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from routerapi import Definition, decrypt, encrypt  # noqa: E402

KEY = "MOCKKEY012345678"  # exactly 16 bytes: AES-128
COOKIE = "mock-session-token"
USER = "admin"


def sample(field):
    kind = field.get("type", "string")
    if field.get("values"):
        return field["values"][0]
    if kind in ("integer", "number"):
        return 1
    if kind == "object[]":
        return [{}]
    if kind == "string[]":
        return ["1"]
    return "sample"


def build_payload(fields):
    """Turn a flat list of dotted field descriptors into a nested sample object.

    'a.b.c' becomes {'a': {'b': {'c': <sample>}}}. A descriptor that names a
    container ('a.b' typed 'object') is kept even when its children are listed
    separately, and 'x[]' children collect into a one-element array.
    """
    out = {}

    def put(path, value):
        parts = path.split(".")
        node = out
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        leaf = parts[-1]
        if leaf not in node or not isinstance(node[leaf], dict):
            node[leaf] = value

    for f in fields or []:
        name = f["name"]
        if "[]" in name:
            parent, _, child = name.partition("[].")
            parent = parent.split(".")[-1]
            rows = out.setdefault(parent, [])
            if not isinstance(rows, list):
                rows = []
                out[parent] = rows
            if not rows:
                rows.append({})
            row = rows[0]
            node = row
            parts = child.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = sample(f)
            continue
        if "." in name:
            put(name, sample(f))
            continue
        if name not in out or not isinstance(out[name], dict):
            out[name] = sample(f)
    return out


class Handler(BaseHTTPRequestHandler):
    definition = None

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/plain"):
        raw = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _authorised(self):
        return COOKIE in (self.headers.get("Cookie") or "")

    def do_GET(self):
        self._handle(None)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self._handle(self.rfile.read(n))

    def _handle(self, body):
        d = self.definition
        path = self.path

        def lookup(p):
            # exact path including the query wins: several endpoints share a
            # handler and differ only by ?module=&opt=, so a bare-path match
            # would answer with the wrong field set.
            for e in d.api["endpoints"]:
                if e["path"] == p:
                    return e
            bare = p.split("?")[0]
            for e in d.api["endpoints"]:
                if e["path"].split("?")[0] == bare:
                    return e
            return None

        sess = d.api["session"]

        if path == sess["login"]["endpoint"] and body is not None:
            form = urllib.parse.parse_qs(body.decode())
            pw = (form.get("password") or [""])[0]
            expect = hashlib.md5(USER.encode()).hexdigest()
            kind = (sess["login"].get("success") or {}).get("kind", "http-200")
            if pw != expect:
                if kind == "redirect-to-main":
                    self.send_response(302)
                    self.send_header("Location", "/login.html")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                elif kind == "cookie-set":
                    self._send(200, "1")
                else:
                    self._send(200, "1")
                return
            if kind == "redirect-to-main":
                self.send_response(302)
                self.send_header("Location", "/main.html")
                self.send_header("Set-Cookie", f"{COOKIE}=1; Path=/")
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Set-Cookie", f"{COOKIE}=1; Path=/")
                self.send_header("Content-Length", "0")
                self.end_headers()
            return

        crypto = d.api.get("crypto")
        plain = (crypto.get("plaintextPaths") or []) if crypto else []
        if crypto:
            src = crypto.get("keySource") or {}
            if path == src.get("endpoint"):
                self._send(200, json.dumps({src["field"]: KEY}), "application/json")
                return
        if path in plain and path != sess["login"]["endpoint"]:
            ep = lookup(path)
            payload = build_payload((ep.get("response") or {}).get("fields")) if ep else {}
            self._send(200, json.dumps(payload), "application/json")
            return

        if sess["type"] == "cookie-on-login" and not self._authorised():
            self.send_response(302)
            self.send_header("Location", "/login.html")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        ep = lookup(path)
        if ep is None:
            self._send(200, "<html>Form is not defined</html>", "text/html")
            return

        resp = ep.get("response") or {}
        kind = resp.get("kind", "json-object")
        if kind == "json-array":
            rows = build_payload(resp.get("fields"))
            payload = [rows] if rows else []
        elif kind == "json-number":
            payload = 0
        elif kind in ("text", "html", "redirect", "binary"):
            self._send(200, "ok")
            return
        else:
            payload = build_payload(resp.get("fields"))
            env = d.api.get("envelopes") or {}
            if env.get("resultField"):
                payload[env["resultField"]] = env.get("successValue", 0)

        text = payload if isinstance(payload, (int, float)) else json.dumps(payload)
        if crypto and path not in plain:
            self._send(200, encrypt(d, str(text), KEY))
        else:
            self._send(200, str(text))


def serve(definition, port=0):
    """Start the fake router on a free port. Returns (server, thread, port)."""
    handler = type("BoundHandler", (Handler,), {"definition": definition})
    srv = HTTPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, t, srv.server_address[1]


if __name__ == "__main__":
    path = sys.argv[1]
    d = Definition.load(path)
    srv, t, port = serve(d, int(sys.argv[2]) if len(sys.argv) > 2 else 8123)
    print(f"mock {d.model} {d.variant} on http://127.0.0.1:{port} (ctrl-c to stop)")
    try:
        t.join()
    except KeyboardInterrupt:
        srv.shutdown()

