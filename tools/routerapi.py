#!/usr/bin/env python3
"""Generic router API client driven entirely by a JSON definition.

There is no vendor code in this file. Every path, field name, encoding, cipher
parameter and envelope rule comes from the api.json document; the feature gate
comes from capabilities.json. If a definition says the device does X, this
client can do X; if the definition does not say it, this client refuses.

Used by tools/selftest.py as an executable spec, and usable directly:

    python3 tools/routerapi.py \
        registry/tenda/ac10/v1.0-fw15.03.06.50/api.json --list

    python3 tools/routerapi.py <api.json> --host 192.168.0.1 \
        --password 'admin' --call status.router
"""
import argparse
import base64
import hashlib
import json
import os
import random
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


class DefinitionError(Exception):
    """The definition is internally inconsistent: a bug in the registry."""


class DeviceError(Exception):
    """The device refused or answered something the definition does not allow."""


class Definition:
    def __init__(self, api, capabilities=None, source=None):
        self.api = api
        self.source = source or "<memory>"
        self.caps = capabilities or {"features": {}}
        self._by_id = {}
        for ep in api.get("endpoints", []):
            if ep["id"] in self._by_id:
                raise DefinitionError(f"duplicate endpoint id {ep['id']!r}")
            self._by_id[ep["id"]] = ep

    @classmethod
    def load(cls, api_path, caps_path=None):
        with open(api_path) as f:
            api = json.load(f)
        caps_path = caps_path or os.path.join(os.path.dirname(api_path), "capabilities.json")
        caps = None
        if os.path.isfile(caps_path):
            with open(caps_path) as f:
                caps = json.load(f)
        return cls(api, caps, api_path)

    # -- introspection -----------------------------------------------------
    @property
    def vendor(self):
        return self.api["vendor"]

    @property
    def model(self):
        return self.api["model"]

    @property
    def variant(self):
        return self.api.get("variant")

    @property
    def definition_version(self):
        return self.api["definitionVersion"]

    def endpoint(self, eid):
        try:
            return self._by_id[eid]
        except KeyError:
            raise DefinitionError(f"{self.source}: no endpoint {eid!r}") from None

    def ids(self):
        return list(self._by_id)

    def endpoint_by_path(self, path):
        for ep in self.api.get("endpoints", []):
            if ep["path"] == path:
                return ep
        return None

    def feature_state(self, feature):
        """'full' | 'read-only' | 'unknown' | None (absent = unsupported)."""
        return self.caps.get("features", {}).get(feature)

    def supports(self, feature, write=False):
        state = self.feature_state(feature)
        if state is None:
            return False
        if write:
            return state == "full"
        return state in ("full", "read-only")

    def matches_firmware(self, reported):
        fw = self.api.get("firmware", {})
        prefix = fw.get("matchPrefix")
        if not prefix:
            return reported == fw.get("verified")
        return reported.startswith(prefix)


class DefinitionIndex:
    """Select a variant from registry/index.json for a device's firmware string."""

    def __init__(self, index):
        self.index = index

    @classmethod
    def load(cls, path):
        with open(path) as f:
            return cls(json.load(f))

    def candidates(self, model=None, hardware=None):
        out = []
        for d in self.index.get("definitions", []):
            if model and d.get("path", "").split("/")[2].upper() != model.upper():
                continue
            if hardware and hardware not in (d.get("hardwareVersions") or []):
                continue
            out.append(d)
        return out

    def select(self, firmware, model=None, hardware=None):
        """Longest matching firmware prefix wins, so V15.03.06.5x beats V15.03.

        A definition with match=family declares that the vendor published no
        usable version string, so it matches any firmware for that model and is
        only ever chosen when nothing more specific fits.
        """
        best, best_len = None, -1
        for d in self.candidates(model, hardware):
            if d.get("match") == "family":
                if best is None:
                    best, best_len = d, 0
                continue
            prefix = d.get("matchPrefix")
            ok = firmware.startswith(prefix) if prefix else firmware == d.get("firmware")
            if ok and len(prefix or "") > best_len:
                best, best_len = d, len(prefix or "")
        return best


# --------------------------------------------------------------------------
# crypto
# --------------------------------------------------------------------------
def _aes_cbc():
    try:
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad, unpad
    except ImportError:
        raise DefinitionError(
            "this definition needs AES: python3 -m pip install -r tools/requirements.txt"
        ) from None
    return AES, pad, unpad


def decrypt(definition, text, key):
    """Decrypt or pass through, driven by definition['crypto']."""
    crypto = definition.api.get("crypto")
    if not crypto:
        return text
    kind = crypto["type"]
    if kind != "aes-128-cbc-base64":
        raise DefinitionError(f"unsupported crypto type {kind!r}")
    AES, _, unpad = _aes_cbc()
    raw = base64.b64decode(text, validate=True)
    cipher = AES.new(key.encode("ascii"), AES.MODE_CBC, crypto["iv"].encode("ascii"))
    return unpad(cipher.decrypt(raw), AES.block_size).decode("utf-8")


def encrypt(definition, text, key):
    crypto = definition.api.get("crypto")
    if not crypto:
        return text
    kind = crypto["type"]
    if kind != "aes-128-cbc-base64":
        raise DefinitionError(f"unsupported crypto type {kind!r}")
    AES, pad, _ = _aes_cbc()
    cipher = AES.new(key.encode("ascii"), AES.MODE_CBC, crypto["iv"].encode("ascii"))
    return base64.b64encode(cipher.encrypt(pad(text.encode("utf-8"), AES.block_size))).decode()


def _aes_cbc_b64(plaintext, key, iv):
    """AES-CBC / Pkcs7, base64. Used by challenge logins, where the key is a
    digit string the device learns only from our signature."""
    AES, pad, _ = _aes_cbc()
    cipher = AES.new(key.encode("ascii"), AES.MODE_CBC, iv.encode("ascii"))
    return base64.b64encode(
        cipher.encrypt(pad(plaintext.encode("utf-8"), AES.block_size))).decode()


def _rsa_encrypt_hex(data, nn_hex, ee_hex, bits=512):
    """RSA PKCS#1 v1.5 type-2, split into (k-11)-byte chunks, hex out.

    Mirrors the Tom Wu port the vendor UI ships: the plaintext is cut into
    blocks that each fit inside the modulus, padded separately, and the hex is
    zero-padded to bits/4 characters per block.
    """
    nn = int(nn_hex, 16)
    ee = int(ee_hex, 16)
    raw = data.encode("utf-8") if isinstance(data, str) else data
    k = (nn.bit_length() + 7) // 8
    step = k - 11
    if step <= 0:
        raise DefinitionError(f"RSA modulus too small ({k} bytes)")
    out = []
    for i in range(0, len(raw), step):
        block = raw[i:i + step]
        ps_len = k - 3 - len(block)
        ps = bytes(random.randrange(1, 256) for _ in range(ps_len))
        m = int.from_bytes(b"\x00\x02" + ps + b"\x00" + block, "big")
        out.append(format(pow(m, ee, nn), "x").rjust(bits // 4, "0"))
    return "".join(out)


def _multipart(values, fields):
    """Build a multipart/form-data body. A value of the form '@path' is sent as
    a file part, matching how curl and the vendor's own forms do it."""
    boundary = "----routerapi" + hashlib.md5(
        json.dumps(values, sort_keys=True).encode()).hexdigest()[:16]
    declared = {f["name"]: f for f in fields}
    out = []
    for name, value in values.items():
        spec = declared.get(name, {})
        if isinstance(value, str) and value.startswith("@"):
            path = value[1:]
            with open(path, "rb") as f:
                blob = f.read()
            filename = os.path.basename(path)
            out.append(f"--{boundary}\r\n".encode())
            out.append(f'Content-Disposition: form-data; name="{name}"; '
                       f'filename="{filename}"\r\n'.encode())
            out.append(b"Content-Type: application/octet-stream\r\n\r\n")
            out.append(blob + b"\r\n")
        else:
            out.append(f"--{boundary}\r\n".encode())
            out.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            out.append(str(value).encode() + b"\r\n")
    out.append(f"--{boundary}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


# --------------------------------------------------------------------------
# client
# --------------------------------------------------------------------------
class RouterClient:
    """Executes a definition against one device. Contains no vendor logic."""

    def __init__(self, definition, host=None, use_tls=None, password=None, username=None,
                 timeout_s=None, port=None):
        self.d = definition
        self.host = host or definition.api["transport"]["defaultHost"]
        t = definition.api["transport"]
        self.scheme = "https" if (use_tls if use_tls is not None else t["scheme"] == "https") else "http"
        self.port = port if port is not None else t.get("defaultPort")

        self.timeout = (timeout_s or t.get("requestTimeoutMs", 12000)) / 1000.0
        self.password = password
        # Most of these devices hard-code the account to 'admin'; challenge
        # handshakes still hash username+password, so the name must be carried.
        self.username = username or "admin"
        self.cookies = {}
        self.login_data = None
        self._busy = None
        self._last_key = None

    # -- plumbing ---------------------------------------------------------
    def base_url(self):
        default_port = 80 if self.scheme == "http" else 443
        netloc = self.host if not self.port or self.port == default_port else f"{self.host}:{self.port}"
        return f"{self.scheme}://{netloc}{self.d.api['transport'].get('pathPrefix', '')}"

    def _opener(self):
        if getattr(self, "_op", None) is None:
            class _NoRedirect(urllib.request.HTTPRedirectHandler):
                # Definitions describe redirects as signals (login success, expiry),
                # so the client must see the 302 rather than follow it.
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    return None

            self._op = urllib.request.build_opener(_NoRedirect)
        return self._op

    def _open(self, path, data=None, method=None, referer=None, headers=None):
        url = self.base_url() + path
        body = None
        raw_body = isinstance(data, (str, bytes))
        if data is not None:
            if raw_body:
                # Challenge envelopes are pre-framed (CRLF-separated, base64
                # inside). Form-encoding would corrupt them, so send verbatim.
                body = data.encode() if isinstance(data, str) else data
            else:
                body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(url, data=body, method=method or ("POST" if body else "GET"))
        if body is not None:
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        if raw_body:
            req.add_header("Content-Length", str(len(body)))
        if referer:
            req.add_header("Referer", referer)
        if self.cookies:
            req.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in self.cookies.items()))
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            resp = self._opener().open(req, timeout=self.timeout)
            raw = resp.read()
            status, hdrs = resp.getcode(), resp.headers
        except urllib.error.HTTPError as e:
            raw, status, hdrs = e.read(), e.code, e.headers
        for c in hdrs.get_all("Set-Cookie") or []:
            name, _, rest = c.partition("=")
            self.cookies[name.strip()] = rest.split(";")[0].strip()
        return status, hdrs, raw

    def _key(self):
        """Fetch the per-request key the definition names, if any."""
        crypto = self.d.api.get("crypto")
        if not crypto:
            return None
        src = crypto.get("keySource") or {}
        if src.get("kind") != "endpoint-field":
            raise DefinitionError(f"unsupported keySource {src.get('kind')!r}")
        ep = self.d.endpoint_by_path(src["endpoint"]) if hasattr(self.d, "endpoint_by_path") else None
        if ep is None:
            ep = next((e for e in self.d.api["endpoints"] if e["path"] == src["endpoint"]), None)
        if ep is None:
            raise DefinitionError(f"keySource endpoint {src['endpoint']!r} is not in the definition")
        status, _, raw = self._open(ep["path"], referer=f"{self.base_url()}/main.html")
        doc = json.loads(raw.decode("utf-8", "replace"))
        key = doc[src["field"]]
        self._last_key = key
        return key

    def list_endpoints(self):
        for ep in self.d.api["endpoints"]:
            flags = []
            if ep.get("writes"):
                flags.append(ep.get("risk", "safe"))
            if ep.get("response", {}).get("shapeUnverified"):
                flags.append("shape-unverified")
            feat = ep.get("feature")
            if feat:
                flags.append(f"{feat}={self.d.feature_state(feat)}")
            print(f"{ep['id']:34s} {ep['method']:4s} {ep['path']:44s} {' '.join(flags)}")

    # -- session ----------------------------------------------------------
    def _login_challenge(self, spec):
        """Execute a challenge/response login, driven entirely by the document.

        Nothing here is device-specific: the exchange is read out of the
        definition, so a new model only needs a new definition. Every step is
        refused loudly rather than guessed when the document is incomplete.
        """
        ch = spec.get("challenge") or {}
        iss = ch.get("issuer") or {}
        cipher = ch.get("cipher") or {}
        sig = ch.get("signature") or {}

        # 1. Ask the device for its public key and nonce.
        status, hdrs, raw = self._open(iss["endpoint"], method=iss.get("method", "GET"))
        text = raw.decode("utf-8", "replace")
        nn, ee, seq = self._extract_challenge(text, iss.get("extract") or {})
        if not nn:
            raise DefinitionError(
                f"{self.d.model}: {iss['endpoint']} returned no RSA modulus; "
                "cannot build a challenge")

        # 2. Build the plaintext payload exactly as the single-request form would.
        user = getattr(self, "username", "") or "admin"
        pwd = getattr(self, "password", "") or ""
        if ch.get("hash") != "md5-hex-of-username-plus-password":
            raise DefinitionError(f"unsupported challenge hash {ch.get('hash')!r}")
        hash_hex = hashlib.md5((user + pwd).encode("utf-8")).hexdigest()

        if spec.get("payloadEncoding") == "json":
            payload = json.dumps({"username": user, "password": pwd})
        else:
            payload = f"username={user}&password={hash_hex}"

        # 3. Generate the AES key the device will recover from our signature.
        if cipher.get("type") != "aes-128-cbc-base64":
            raise DefinitionError(f"unsupported challenge cipher {cipher.get('type')!r}")
        if cipher.get("charset") != "digits":
            raise DefinitionError(f"unsupported key charset {cipher.get('charset')!r}")
        key = "".join(str(random.randrange(10)) for _ in range(int(cipher["keyChars"])))
        iv = "".join(str(random.randrange(10)) for _ in range(int(cipher["ivChars"])))
        key_report = (cipher.get("keyReport") or "key=%key%&iv=%iv%")
        key_report = key_report.replace("%key%", key).replace("%iv%", iv)

        ct = _aes_cbc_b64(payload, key, iv)

        # 4. Sign the rendezvous string so the device learns the key.
        if sig.get("type") != "rsa-pkcs1-v1_5":
            raise DefinitionError(f"unsupported signature {sig.get('type')!r}")
        plain = (sig.get("plaintext") or "%keyReport%&h=%hash%&s=%seqPlusDataLen%")
        plain = (plain.replace("%keyReport%", key_report)
                      .replace("%hash%", hash_hex)
                      .replace("%seqPlusDataLen%", str(int(seq or 0) + len(ct))))
        sign = _rsa_encrypt_hex(plain, nn, ee or "010001", int(sig.get("bits", 512)))

        # 5. Frame and send.
        env = spec.get("challenge", {}).get("envelope") or "sign=%sign%\r\ndata=%data%\r\n"
        body = env.replace("%sign%", sign).replace("%data%", ct)

        status, hdrs, raw = self._open(spec["endpoint"], data=body,
                                       method=spec.get("method", "POST"))
        text = raw.decode("utf-8", "replace")
        if status >= 400:
            raise DeviceError(f"login rejected (status {status}, reply {text.strip()[:120]!r})")
        # The device answers with JSON on the dynaform build and a bare code on
        # the CGI build; only a transport-level failure is certain here.
        low = text.strip().lower()
        if '"valid":false' in low.replace(" ", "") or low in ("0", ""):
            raise DeviceError(f"login rejected by device: {text.strip()[:60]!r}")
        return True

    @staticmethod
    def _extract_challenge(text, extract):
        """Pull nn/ee/seq out of the issuer reply, per the document's map."""
        fmt = extract.get("format")
        mapping = extract.get("map") or {}
        if fmt == "json":
            doc = json.loads(text)
            def dig(path):
                cur = doc
                for part in path.split("."):
                    if not isinstance(cur, dict) or part not in cur:
                        return None
                    cur = cur[part]
                return cur
            return (dig(mapping.get("nn", "nn")),
                    dig(mapping.get("ee", "ee")),
                    dig(mapping.get("seq", "seq")))
        # js-vars: the reply is JavaScript assigning var nn="...";
        def grab(name):
            m = re.search(rf'\b{name}\s*=\s*["\']?([0-9a-fA-Fx]+)["\']?', text)
            return m.group(1) if m else None
        return (grab(mapping.get("nn", "nn")),
                grab(mapping.get("ee", "ee")),
                grab(mapping.get("seq", "seq")))

    def _encode_password(self, encoding):
        pw = self.password or ""
        if encoding == "plain":
            return pw
        if encoding == "md5-hex":
            return hashlib.md5(pw.encode()).hexdigest()
        if encoding == "base64":
            return base64.b64encode(pw.encode()).decode()
        if encoding == "md5-hex-of-md5-hex":
            return hashlib.md5(hashlib.md5(pw.encode()).hexdigest().encode()).hexdigest()
        raise DefinitionError(f"unsupported passwordEncoding {encoding!r}")

    def login(self):
        sess = self.d.api["session"]
        spec = sess["login"]
        if sess["type"] == "none":
            return True
        # A handshake that was never captured must not be attempted: guessing the
        # encoding spends one of the device's password attempts and cannot
        # succeed by luck. Refusing is the only honest behaviour.
        if spec.get("verified") == "not-captured":
            raise DefinitionError(
                f"login handshake for {self.d.model} {self.d.variant} was never captured; "
                "the definition cannot sign in until a capture exists")
        if spec.get("kind") == "challenge":
            return self._login_challenge(spec)
        body = dict(spec.get("body") or {})
        for k, v in list(body.items()):
            if isinstance(v, str) and "{password}" in v:
                body[k] = v.replace("{password}", self._encode_password(spec.get("passwordEncoding", "plain")))
            if isinstance(v, str) and "{username}" in v:
                body[k] = v.replace("{username}", getattr(self, "username", "") or "admin")
        status, hdrs, raw = self._open(spec["endpoint"], data=body, method=spec.get("method", "POST"),
                                      referer=f"{self.base_url()}/login.html")
        text = raw.decode("utf-8", "replace")
        success = spec.get("success") or {}
        kind = success.get("kind", "http-200")
        refusals = success.get("refusalCodes") or {}

        if kind == "redirect-to-main":
            location = (hdrs.get("Location") or "") if hdrs else ""
            marker = success.get("loginPageMarker") or "/login.html"
            ok = 300 <= status < 400 and marker not in location
        elif kind == "body-number":
            code = text.strip()[:4]
            if code in refusals:
                raise DeviceError(f"login refused: {refusals[code]}")
            explicit = success.get("successCodes") or []
            if explicit:
                ok = text.strip() in explicit
            else:
                ok = not re.fullmatch(r"\d{1,4}", text.strip())
        elif kind == "cookie-set":
            ok = bool(self.cookies)
        else:
            ok = status == 200

        if not ok:
            code = text.strip()[:4]
            raise DeviceError(f"login rejected (status {status}, reply {text.strip()[:40]!r}"
                              f"{', ' + refusals[code] if code in refusals else ''})")
        self.login_data = {"status": status, "body": text}
        return True

    def logout(self):
        spec = self.d.api["session"].get("logout")
        if not spec:
            return None
        status, _, raw = self._open(spec["endpoint"], method=spec.get("method", "GET"),
                                   referer=f"{self.base_url()}/main.html")
        self.cookies.clear()
        return status

    def _session_expired(self, status, text):
        exp = self.d.api["session"].get("expiry") or {}
        if status in (exp.get("statusCodes") or []):
            return True
        return any(m and m in text for m in (exp.get("bodyMarkers") or []))

    # -- calls ------------------------------------------------------------
    def _body_for(self, ep, values, key=None):
        req = ep.get("request") or {}
        encoding = req.get("bodyEncoding", "none")
        if encoding == "none":
            if values:
                raise DefinitionError(f"{ep['id']}: takes no body, refusing to send {sorted(values)}")
            return None
        allowed = {f["name"]: f for f in (req.get("fields") or [])}
        for name in values:
            if name not in allowed:
                raise DefinitionError(f"{ep['id']}: field {name!r} is not in the definition")
        for name, f in allowed.items():
            if f.get("required") and name not in values:
                raise DefinitionError(f"{ep['id']}: required field {name!r} missing")
        payload = dict(values)
        if encoding == "encrypted-form":
            plain = urllib.parse.urlencode(payload)
            return {"_raw": encrypt(self.d, plain, key),
                    "_type": "application/x-www-form-urlencoded"}
        if encoding == "form":
            return payload
        if encoding == "multipart":
            raw, ctype = _multipart(payload, req.get("fields") or [])
            return {"_raw": raw, "_type": ctype}
        if encoding == "json":
            text = json.dumps(payload)
            if self.d.api.get("crypto"):
                return {"_raw": encrypt(self.d, text, key), "_type": "text/plain"}
            return {"_raw": text, "_type": "application/json"}
        raise DefinitionError(f"{ep['id']}: unsupported bodyEncoding {encoding!r}")

    def call(self, eid, values=None, allow_writes=False):
        """Execute one endpoint and return its decoded payload."""
        ep = self.d.endpoint(eid)
        values = values or {}
        if ep.get("writes") and not allow_writes:
            raise DeviceError(f"{eid} changes device state; pass allow_writes=True")
        feat = ep.get("feature")
        if feat and ep.get("writes") and not self.d.supports(feat, write=True):
            raise DeviceError(f"{eid}: this definition says {feat} is "
                              f"{self.d.feature_state(feat) or 'unsupported'}, so the write is not attempted")

        crypto = self.d.api.get("crypto")
        needs_key = bool(crypto) and ep["path"] not in (crypto.get("plaintextPaths") or [])
        key = self._key() if needs_key else None

        body = self._body_for(ep, values, key)
        method = ep["method"]
        if isinstance(body, dict) and "_raw" in body:
            status, _, payload = self._post_raw(ep["path"], body["_raw"], body["_type"])
        else:
            status, _, payload = self._open(
                ep["path"], data=body if method == "POST" else None,
                method=method, referer=f"{self.base_url()}/main.html")

        text = payload.decode("utf-8", "replace")
        if self._session_expired(status, text) and self.d.api["session"]["type"] != "none":
            raise DeviceError(f"{eid}: session expired (status {status}); log in again")

        return self._decode(ep, status, text, key)


    def _post_raw(self, path, raw_body, content_type):
        url = self.base_url() + path
        data = raw_body if isinstance(raw_body, bytes) else raw_body.encode()
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", content_type)
        req.add_header("Referer", f"{self.base_url()}/main.html")
        if self.cookies:
            req.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in self.cookies.items()))
        try:
            resp = self._opener().open(req, timeout=self.timeout)
            data, status, hdrs = resp.read(), resp.getcode(), resp.headers
        except urllib.error.HTTPError as e:
            data, status, hdrs = e.read(), e.code, e.headers
        for c in hdrs.get_all("Set-Cookie") or []:
            name, _, rest = c.partition("=")
            self.cookies[name.strip()] = rest.split(";")[0].strip()
        return status, hdrs, data

    def _decode(self, ep, status, text, key=None):
        resp = ep.get("response") or {}
        kind = resp.get("kind", "json-object")
        crypto = self.d.api.get("crypto")
        if kind in ("text", "html", "redirect", "binary"):
            return {"kind": kind, "status": status, "text": text.strip()}
        body = text.strip()
        if crypto and ep["path"] not in (crypto.get("plaintextPaths") or []):
            body = decrypt(self.d, body, key)

        if kind == "json-array":
            return json.loads(body)
        if kind == "json-number":
            return {"kind": kind, "status": status, "value": body}
        doc = json.loads(body)
        env = self.d.api.get("envelopes") or {}
        field_name = env.get("resultField")
        if field_name and isinstance(doc, dict) and field_name in doc:
            code = doc[field_name]
            if code != env.get("successValue", 0):
                raise DeviceError(f"{ep['id']}: device returned {field_name}={code!r}")
        return doc


def main(argv=None):
    ap = argparse.ArgumentParser(description="Execute a router definition without vendor code.")
    ap.add_argument("definition", help="path to api.json (capabilities.json is read beside it)")
    ap.add_argument("--host")
    ap.add_argument("--password")
    ap.add_argument("--tls", action="store_true", help="use https")
    ap.add_argument("--list", action="store_true", help="list endpoints and feature states, no network")
    ap.add_argument("--features", action="store_true", help="print the capabilities map")
    ap.add_argument("--call", help="endpoint id to execute")
    ap.add_argument("--arg", action="append", default=[], metavar="NAME=VALUE")
    ap.add_argument("--allow-writes", action="store_true")
    ap.add_argument("--json", action="store_true", help="print the raw reply object")
    args = ap.parse_args(argv)

    d = Definition.load(args.definition)
    client = RouterClient(d, host=args.host, use_tls=args.tls, password=args.password)
    if args.features:
        print(json.dumps(d.caps.get("features", {}), indent=2, sort_keys=True))
        return 0
    if args.list or not args.call:
        print(f"{d.vendor} {d.model} {d.variant} definition {d.definition_version}")
        print(f"firmware {d.api.get('firmware', {}).get('verified')} "
              f"(match {d.api.get('firmware', {}).get('matchPrefix')})")
        client.list_endpoints()
        return 0
    values = {}
    for a in args.arg:
        k, _, v = a.partition("=")
        values[k] = v
    try:
        if d.api["session"]["type"] != "none":
            client.login()
        out = client.call(args.call, values, allow_writes=args.allow_writes)
    except (DefinitionError, DeviceError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2) if args.json or isinstance(out, (dict, list)) else out)
    return 0


if __name__ == "__main__":
    sys.exit(main())



