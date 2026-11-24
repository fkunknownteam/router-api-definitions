# Changelog

Per-variant definition history. Newest first.

Format and rules: `docs/changelog-process.md`. Every entry states what a user
would notice, not just what was edited.

---

## tplink/ARCHER-C6 — 1.0.0 — 2026-11-24

### Added — Archer C6 (AC1200), hardware V2 and V2.80
117 endpoints and a working login, derived from TP-Link's own firmware rather
than from a device: `ArcherC6v2_us_tw_c6us-up-ver1-2-1-P1[20260902-rel38654].bin`,
firmware 1.2.1 Build 260902 (US), published 2026-10-19. 46 of the 117 are
writes.

The login is the same challenge family as TL-WR840N/WR841N and TL-WR940N, but a
different build of it — three differences that would each break a naive client:

```
issuer    GET  /login?form=auth  ->  {"key":[<nn>,<ee>],"seq":<n>}
          the key is a two-element ARRAY, not named fields
keyReport k=<key>&i=<iv>          not key=/iv=
envelope  POST /login?form=login  sign=<hex>&data=<b64>
          two percent-encoded form fields, not a CRLF-framed body
payload   operation=login&password=<raw password>
          not username=<user>&password=<md5>
```

The signed string is still `%keyReport%&h=<md5>&s=<seq+len(data)>`, and the RSA
still signs `k-11`-byte chunks (53 bytes at 512 bits). Success is a `stok` token
in the JSON reply, not a cookie.

### Verified how, without a router
1. The firmware's own `tpEncrypt.js`, `encrypt.js` and `cryptoJS.min.js` were
   executed under Node with a fixed key and IV. The Python port reproduced the
   MD5, the base64 ciphertext and the signed string **byte for byte**:
   `172eee54aa664e9dd0536b063796e54e` and
   `k=0123456789012345&i=0011223344556677&h=172eee…&s=56694`.
2. `tools/handshake_test.py` now stands up a server holding the RSA private key,
   verifies the signature, recovers the AES key from inside it and decrypts the
   payload. Run in CI.

### Changed — the engine, driven by the document
- `session.login.challenge.payloadTemplate` replaces the hardcoded plaintext. It
  was assumed every build signs `username=<user>&password=<hash>`; the C6 signs
  `operation=login&password=<raw>`. A build that differs now says so instead of
  being unsupported.
- `success.kind: "json-token"` with `tokenField`, for devices that answer with a
  token rather than a cookie.
- JSON issuer maps may index arrays (`key.0`, `key.1`).
- Single-line envelopes are percent-encoded before sending. The firmware builds
  them with jQuery, which encodes the value; sending raw base64 invites a form
  parser to turn `+` into a space. JSONDATA envelopes are left raw.

### Not verified
No C6 was present, so nothing here is confirmed against a real device. The
handshake is proven *constructible* — correct signatures, correct ciphertext —
not proven *accepted*.

---

## tplink/TL-WR840N, TL-WR841N, TL-WR940N — 1.1.0 — 2026-11-23

### Added — the login handshake is now known
These three builds' login pages were still in the extracted firmware, so the
handshake could be read straight out of the device's own JavaScript instead of
being marked unknown. Two distinct families emerged:

**TL-WR840N and TL-WR841N** (`web/frame/login.htm` is byte-identical between
them) use a CGI challenge:

```
GET  /cgi/getParm     -> var nn="<rsa modulus>"; var ee="<exponent>"; var seq="<nonce>";
hash  = MD5(username + password)                 lowercase hex
key   = 16 random digits, iv = 16 random digits  (AES-128)
data  = base64(AES-CBC-Pkcs7(key, iv, payload))
sign  = RSA-PKCS1v1.5(key="key=..&iv=..&h=<hash>&s=<seq + len(data)>")
POST /cgi/login       "sign=<sign hex>\r\ndata=<data>\r\n"
```

**TL-WR940N** uses the dynaform scheme:

```
GET  /login/getRsa.json -> {"rsa":{"nn":..,"mm":..},"seq":..}
POST /login/login.json  "JSONDATA: {"sign":"<hex>","data":"<base64>"}"
```

### Verified how, without a router
- **Against the firmware's own code.** The shipped `tpEncrypt.js` and
  `cryptoJS.min.js` were executed under Node with a fixed key/IV, and the
  output compared with the reimplementation: AES ciphertext, MD5 digest and
  signed plaintext all matched byte for byte.
- **End to end against an adversary.** `tools/handshake_test.py` stands up a
  server holding a real RSA private key. It verifies our signature, recovers
  the AES key from inside it, decrypts the payload and checks the password. All
  three run in CI, so a wrong byte in the crypto fails the build.

### Still not verified
- No live device, so **no reply body has ever been captured**. The login is
  proved to be *constructed correctly*, not proved to be *accepted by your
  router*. A first real login is the remaining confirmation.
- The remaining six TP-Link definitions keep `verified: not-captured` and still
  refuse to log in, but for two different reasons:
  - **TL-WR820N and TL-WR844N** do have a web UI, but it is a later generation
    that does not use URLs at all. The UI calls
    `ajax.request({proxy:"loginProxy", method:"login"})`, which the framework
    turns into `$.su.url("?code=" + HTTP_OP)` — numeric opcodes over one CGI
    entry point. That much is now recovered and verified: the opcode table reads
    `HTTP_INSTRUCT=0, HTTP_WRITE=1, HTTP_READ=2, HTTP_UPLOAD=3, HTTP_DOWNLOAD=4,
    HTTP_RESET=5, HTTP_REBOOT=6, HTTP_AUTH=7, ... HTTP_UPLOAD_IMG=19`, login is
    `HTTP_AUTH=7` against `loginCheck`, and the password encoding is
    `su.encrypt(pwd, key)` — an XOR against a server-supplied key indexed into a
    255-character alphabet, reproduced byte-identically against the firmware's
    own function. Modelling this family needs an opcode transport that the
    schema does not have yet, so these two remain `not-captured` until that
    exists rather than pretending a URL-based definition fits.
  - **TL-WR845N, TL-WR846N, TL-WR850N, ARCHER-C24** have no extracted firmware
    image in the source material at all, so there is nothing to read.

### Changed
- `schema/api-definition.schema.json`: `session.login` gained `kind`
  (`single`/`challenge`), `payloadEncoding`, and a `challenge` block describing
  the issuer, hash, cipher, signature, envelope and payload. A new model with a
  challenge login needs only a new definition — the client executes it
  generically.
- `tools/routerapi.py`: `login()` executes `kind: challenge` end to end; the
  HTTP layer sends pre-framed envelopes verbatim instead of form-encoding them;
  `RouterClient` now carries a `username`, which the handshake hashes.
- `tools/selftest.py`: records a documented challenge handshake as a pass and
  defers its proof to `tools/handshake_test.py`.

---

## tplink/* — 1.0.0 — 2026-11-23

### Added
- Nine TP-Link definitions converted from the `routerctl` firmware-extraction
  project, covering 300 endpoint paths in total:

  | model | variant | endpoints |
  |---|---|---|
  | TL-WR840N | v6-fw-evidence | 76 |
  | TL-WR841N | v14-fw-evidence | 94 |
  | TL-WR844N | v1-fw-evidence | 2 |
  | TL-WR845N | v3-fw-evidence | 6 |
  | TL-WR846N | v1-fw-evidence | 2 |
  | TL-WR850N | v3-fw-evidence | 7 |
  | TL-WR940N | v6-fw-evidence | 101 |
  | TL-WR820N | v2-fw-evidence | 2 |
  | ARCHER-C24 | v1-fw-evidence | 10 |

### Known limits — read before using
- Every endpoint is `verified: ui-code`, meaning the path and its form fields
  were read out of the extracted firmware image. **No live device was available**,
  so no reply body was ever captured and nothing is live-verified.
- The login handshake was **not captured** for any of these builds. Each
  document therefore carries `session.login.verified: "not-captured"`, and both
  clients (`tools/routerapi.py` and the Java engine) raise a definition error
  rather than attempting a guess. These definitions can be browsed and diffed
  but cannot sign in until a capture exists.
- `firmware.match` is `family`, not `prefix`: these builds ship build dates
  (e.g. `20260611`), not dotted firmware versions, so there is nothing reliable
  to prefix-match. A client identifies the model from the device's own UI.

### Changed
- `schema/api-definition.schema.json`: `session.login` accepts a `verified`
  field, so a document can say its handshake is unknown instead of omitting the
  block or lying about it.
- `schema/index.schema.json` and `tools/build_index.py`: the index carries
  `match` alongside `matchPrefix`.
- `tools/routerapi.py` and the Java `RouterClient`: `select()` honours
  `match: family`, and `login()` refuses an uncaptured handshake.
- `tools/selftest.py` and `JavaSelftest`: a definition with an uncaptured
  handshake is tested by proving it refuses, not by driving it.

---

## tenda/ac10 v1.0-fw15.03.06.50 — 1.0.0 — 2026-11-23

### Added
- Initial definition for the AC10 V1.0 on `V15.03.06.50_multi`: 102 endpoints
  covering dashboard status, both radios, guest network, WAN/LAN, DHCP
  reservations, client list, blacklist, bandwidth control, parental control, NAT
  (port forwarding, DMZ, UPnP), firewall, MAC filter, static routes, DDNS, IPTV,
  remote administration, VPN server and client, cloud management, LED, sleep,
  firmware upgrade, backup/restore, logs, time, reboot, factory reset and the
  admin password.

### Notes
- Transport is plaintext JSON; no crypto block. `errCode` arrives as an unquoted
  JSON number and is the only envelope field.
- The USB subsystem is not compiled into this build (`CONFIG_USB_MODULES="n"`).
  Those handlers are recorded under `provenance.unsupported` rather than omitted,
  so a client can explain the gap instead of retrying.
- Two endpoints exist but no reply was ever captured; they carry
  `response.shapeUnverified: true` and declare no fields.

---

## tenda/ac10 v3.0-fw03.03.16.11 — 1.1.0 — 2026-11-23

### Added
- `discovery.preAuthProbes`: `/goform/getLoginInfo` answers plaintext JSON before
  login, so an app can confirm the device is an AES-family build without spending
  a password attempt. It does not distinguish this firmware line from the other
  AES line; only `adv_hard_ver` after login does that.

### Changed
- The picker (`android/picker.md`) is now the primary way an app selects a
  variant. This probe is a cross-check, not a replacement.

---

## tenda/ac10 v3.0-fw03.03.16.11 — 1.0.0 — 2026-11-23

### Added
- Initial definition for the AC10 V3.0 on `V03.03.16.11_multi`: 7 endpoints —
  login, the per-request key, lockout state, system status, system summary,
  remote-web-admin read and write.

### Notes
- Every authenticated reply is AES-128-CBC (base64, fixed ASCII IV) using a key
  regenerated by `/goform/stokCfg` on each call. Reusing a key yields
  undecryptable data that looks like a corrupt response.
- **Deliberately partial.** Only endpoints exercised on this firmware line are
  claimed. The wider map from the `V15.03.06` line very likely exists here too,
  but it was not re-verified on this build, so it is not claimed. Recorded in
  `provenance.notes`.
- Capabilities beyond status and remote administration are `unknown`, not absent:
  an unverified feature must not disappear from the app.

---

## tenda/ac10 v4.0-fw16.03 — 1.1.0 — 2026-11-23

### Added
- `discovery.preAuthProbes`: `/goform/getLoginInfo` answers plaintext JSON before
  login, so an app can confirm the device is an AES-family build without spending
  a password attempt.

### Changed
- The picker (`android/picker.md`) is now the primary way an app selects a
  variant. This probe is a cross-check, not a replacement.

---

## tenda/ac10 v4.0-fw16.03 — 1.0.0 — 2026-11-23

### Added
- Initial definition for the AC10 V4.0 on the `V16.03` line: 36 endpoints
  covering status tiles, WiFi basic/radio/guest, WAN, clients, blacklist,
  MAC filter, bandwidth, parental control, reboot and logout.

### Fixed
- Recorded two field traps that cost real debugging time:
  - `doubleBand` in `wifi.basic.*` is the vendor's band-merge switch, **not** a
    radio count. Reading it as "has 5 GHz" hides the 5 GHz card on every normal
    router; a 5 GHz radio is detected from `wrlEn_5g` / `ssid_5g` / `wl5gName`.
  - `wanStatus` uses the literal `1310209` for connected on this build.
- `wifi.basic.set` resets any field omitted from the form, so a client must read
  first and re-send every field.

### Notes
- Evidence level is `app-observed` throughout: the shipping Android adapter
  exercises these endpoints against real hardware, but raw replies were not
  retained. Weaker than `live-capture`, and stated as such.
- VPN is `read-only` because only its status getters were exercised. Reboot is a
  *plain* submit on this line — sending an encrypted body fails.
