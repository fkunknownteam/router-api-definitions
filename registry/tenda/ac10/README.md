# Tenda AC10 — three firmware lines, one model name

The AC10 is the reason this registry is keyed on firmware, not on model. Three
lines are documented here and they are not interchangeable on the wire.

| Variant | Firmware | Hardware | Endpoints | Evidence |
|---|---|---|---|---|
| `v1.0-fw15.03.06.50` | `V15.03.06.50_multi` | V1.0 | 102 | `live-capture` / `live-roundtrip` |
| `v3.0-fw03.03.16.11` | `V03.03.16.11_multi` | V3.0 | 7 | `live-capture` / `live-roundtrip` |
| `v4.0-fw16.03` | `V16.03.x` | V4.0 | 36 | `app-observed` |

## Telling them apart

Read after login, from `status.system`:

```
adv_firm_ver   V15.03.06.50_multi     -> matchPrefix V15.03.06
adv_hard_ver   V1.0
```

`matchPrefix` is what the client compares; `verified` is the full string that was
actually observed. The prefix is short on purpose — point releases within a line
do not change the API, and pinning a full build string would make every minor
firmware update look like an unknown device.

Check **both** `adv_hard_ver` and `adv_firm_ver`. The V3.0 and V4.0 lines are
different firmware generations with different login replies; picking wrong fails
at the first decryption, not at login, so the error surfaces as "corrupt response"
rather than "wrong model".

## The differences that matter

### Transport

| | fw15 (V1.0) | fw03 (V3.0) | fw16 (V4.0) |
|---|---|---|---|
| Crypto | none | AES-128-CBC | AES-128-CBC |
| Key source | — | `/goform/stokCfg` → `sign` | same |
| Key lifetime | — | one request | one request |
| IV | — | `EU5H62G9ICGRNI43` (ASCII) | same |
| Login reply | cookie set | 302 → `/main.html` | bare number |
| Reboot form | plain | encrypted | **plain** |

The key from `stokCfg` is regenerated on every call. Fetch it immediately before
the request it protects and decrypt that reply with the same key. A cached key
produces garbage that is indistinguishable from a corrupt response.

### Field traps, recorded because they cost time

* **`doubleBand`** (fw03, fw16) is the vendor's *band-merge* switch: `1` means
  both radios share one SSID and password. It is **not** a radio count. Reading it
  as "this device has 5 GHz" hides the 5 GHz card on every normal router. Detect
  5 GHz from `wrlEn_5g` / `ssid_5g` / `wrlPwd_5g` / `security_5g` / `wl5gName`.
* **`wanStatus`** on fw16 uses the literal `1310209` for connected. On fw15 the
  7-digit code's last two digits index a status table (`209` = connected).
* **`wifi.basic.set`** resets every field omitted from the form. Read first,
  re-send every field. When `doubleBand` is `1`, both bands must carry the same
  name and password or the save splits or drops the second band.
* **`errCode`** is an unquoted JSON number, not a string. A naive string compare
  against `"0"` reports every successful call as a failure.
* **`getOnlineList`** returns rows without `devName` that are summaries, not
  devices. Skip them.

### What fw15 does not have

The V1.0 build has no USB port and sets `CONFIG_USB_MODULES="n"`. Every USB,
printer, Samba, DLNA and Thunder handler answers `Form <Name> is not defined`.
They are listed under `provenance.unsupported` so a client can explain the gap
instead of retrying.

The blacklist and the MAC filter are **two different lists**. Conflating them
unblocks a device the user meant to keep off.

## Why v3.0 is only seven endpoints

It is the honest size of what was exercised on that build. The wider fw15 map
very likely exists there too — page names and handler names are shared across
AC10 builds — but "likely" is not "verified", so it is not claimed. A client must
probe with a read endpoint before offering a write, and must treat a missing
feature as `unknown`, not absent.

When someone verifies more of that line, each endpoint moves from unclaimed to
listed with its own `verified` and `evidence`. The file grows; nothing is
retro-fitted.

## Why v4.0 is weaker evidence

The endpoints were exercised by a shipping Android client against real hardware,
but the raw replies were not archived. That is `app-observed`, not
`live-capture`, and the file says so rather than implying a stronger claim.
The field-level notes above are the durable part: they were learned from
failures, and they are recorded so the next client does not repeat them.
