#!/usr/bin/env python3
"""Prove the challenge handshake works end to end, with no hardware.

Stands up a server that behaves like the firmware's own CGI: it issues a real
RSA key pair, then VERIFIES the client's signature with the private half,
recovers the AES key from inside it, decrypts the payload, and checks the
password. If the client's crypto were wrong in any way the signature would not
verify and the login would fail.

This is the strongest test available without a device: the server is the
adversary, and it decrypts what we send.
"""
import base64
import hashlib
import json
import os
import random
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import routerapi  # noqa: E402

# ---------------------------------------------------------------------------
# Generate a real 512-bit RSA key pair so the server can verify signatures.
# ---------------------------------------------------------------------------
def _is_probable_prime(n, k=24):
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(k):
        a = random.randrange(2, n - 1)
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def gen_prime(bits):
    while True:
        p = random.getrandbits(bits) | (1 << (bits - 1)) | 1
        if _is_probable_prime(p):
            return p


def gen_key(bits=512):
    e = 65537
    while True:
        p, q = gen_prime(bits // 2), gen_prime(bits // 2)
        if p == q:
            continue
        phi = (p - 1) * (q - 1)
        if phi % e == 0:
            continue
        n = p * q
        if n.bit_length() != bits:
            continue
        return {"n": n, "e": e, "d": pow(e, -1, phi)}


KEY = gen_key(512)
SEQ = 56630
PASSWORD = "hunter2"
USERNAME = "admin"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/plain"):
        raw = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/cgi/getParm":
            # Exactly the shape the firmware emits: JS var assignments.
            self._send(200, (
                'var nn="%x";\nvar ee="%x";\nvar seq="%d";\n'
                % (KEY["n"], KEY["e"], SEQ)), "text/javascript")
        elif self.path == "/login/getRsa.json":
            self._send(200, json.dumps({
                "rsa": {"nn": "%x" % KEY["n"], "mm": "%x" % KEY["e"]},
                "seq": SEQ}), "application/json")
        elif self.path == "/login?form=auth":
            # The Archer C6 shape: key is a two-element array, not named fields.
            self._send(200, json.dumps({
                "key": ["%x" % KEY["n"], "%x" % KEY["e"]],
                "seq": SEQ}), "application/json")
        else:
            self._send(404, "no")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode("utf-8", "replace")
        try:
            env = self._parse(body)
        except Exception as e:  # noqa: BLE001
            self._send(400, "malformed envelope: %s" % e)
            return

        # --- Verify the signature with the PRIVATE key. This is the real test:
        # if the client's RSA, padding or chunking is wrong, this fails.
        # The firmware signs in (k-11)-byte chunks and concatenates the hex, so
        # the reply signature is several 128-hex-char blocks, each decrypted
        # separately and joined.
        hexsig = env["sign"]
        chunks = [hexsig[i:i + 128] for i in range(0, len(hexsig), 128)]
        pieces = []
        for ch in chunks:
            block = pow(int(ch or "0", 16), KEY["d"], KEY["n"]).to_bytes(64, "big")
            if block[0] != 0 or block[1] != 2:
                self._send(400, "bad pkcs1 header in block")
                return
            sep = block.index(b"\x00", 2)
            pieces.append(block[sep + 1:])
        signed = b"".join(pieces).decode()

        # --- Recover the key the client reported inside the signature.
        # Two namings exist in the field: key=/iv= on the CGI builds and
        # k=/i= on the Archer C6. Both arrive inside the same signed string.
        parts = dict(p.split("=", 1) for p in signed.split("&"))
        key = parts.get("key") or parts.get("k")
        iv = parts.get("iv") or parts.get("i")
        if key is None or iv is None:
            self._send(400, "signature carried no key/iv: %r" % sorted(parts))
            return
        h, s = parts["h"], parts["s"]

        expected_hash = hashlib.md5((USERNAME + PASSWORD).encode()).hexdigest()
        if h != expected_hash:
            self._send(403, "wrong hash")
            return
        if int(s) != SEQ + len(env["data"]):
            self._send(403, "bad seq+len")
            return

        # --- Decrypt the payload with the recovered key.
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import unpad
        raw = unpad(AES.new(key.encode(), AES.MODE_CBC, iv.encode())
                    .decrypt(base64.b64decode(env["data"])), 16)
        payload = raw.decode()

        if PASSWORD in payload or hashlib.md5((USERNAME + PASSWORD).encode()).hexdigest() in payload:
            if self.path == "/login?form=login":
                # Archer C6 signals success with a session token, not a cookie.
                self._send(200, json.dumps({"stok": "/TESTOKEN1234/"}),
                           "application/json")
            elif self.path == "/login/login.json":
                self._send(200, json.dumps({"valid": True, "sessionId": "TESTSESSION1234"}),
                           "application/json")
            else:
                self.send_response(200)
                self.send_header("Set-Cookie", "Authorization=%d; path=/" % SEQ)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")
        else:
            self._send(403, "wrong password")

    @staticmethod
    def _parse(body):
        if body.startswith("JSONDATA: "):
            return json.loads(body[len("JSONDATA: "):])
        if "\r\n" in body:
            out = {}
            for line in body.split("\r\n"):
                if "=" in line:
                    k, _, v = line.partition("=")
                    out[k.strip()] = v.strip()
            return out
        # Archer C6 sends sign/data as two ordinary form fields in one line.
        # The client percent-encodes them the way jQuery does, so decode here.
        return dict(urllib.parse.parse_qsl(body, keep_blank_values=True))


def run(port=0):
    srv = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def try_login(api_path, label):
    d = routerapi.Definition.load(api_path)
    srv, port = run()
    try:
        c = routerapi.RouterClient(d, host="127.0.0.1", port=port,
                                   username=USERNAME, password=PASSWORD)
        ok = c.login()
        print(f"  OK   {label}: signature verified, payload decrypted, "
              f"login accepted (cookies={list(c.cookies)})")
        return ok
    finally:
        srv.shutdown()


if __name__ == "__main__":
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "registry", "tplink")
    cases = [
        (f"{root}/tl-wr841n/v14-fw-evidence/api.json", "TL-WR841N rsa-aes  /cgi/login"),
        (f"{root}/tl-wr840n/v6-fw-evidence/api.json",   "TL-WR840N rsa-aes  /cgi/login"),
        (f"{root}/tl-wr940n/v6-fw-evidence/api.json",   "TL-WR940N dynaform /login/login.json"),
        (f"{root}/archer-c6/v2-fw-evidence/api.json",   "ARCHER-C6 dynaform /login?form=login"),
    ]
    ok = True
    for path, label in cases:
        try:
            ok &= try_login(path, label)
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL {label}: {type(e).__name__}: {e}")
            ok = False
    print()
    print("HANDSHAKE VERIFIED END TO END" if ok else "FAILURES ABOVE")
    raise SystemExit(0 if ok else 1)
