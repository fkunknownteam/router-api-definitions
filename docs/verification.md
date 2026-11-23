# Verification: what is proven, and what is not

The registry's whole value is that a client can trust it. So the evidence for
every claim is written down, and the tooling proves a narrower thing than people
assume. This page is explicit about both.

## Three separate questions

| Question | Answered by | Strength |
|---|---|---|
| Does the definition describe the real device? | `verified` + `evidence` per endpoint, `provenance` per file | as good as the capture behind it, no better |
| Is the definition self-consistent and schema-valid? | `tools/validate.py` | mechanically proven |
| Can a generic client actually execute it? | `tools/selftest.py`, `tools/java_selftest.sh` | mechanically proven |

The self tests run against `tools/mockrouter.py`, a fake router that speaks
whatever the definition declares. That proves the **client executes the
document**. It proves nothing about the device. A definition that is wrong but
internally consistent passes the self test — which is exactly why the field-level
`evidence` matters and why `app-observed` is weaker than `live-capture`.

## Verification levels in the data

`verified` on an endpoint, weakest first:

* `absent` — the handler is not compiled into this firmware. Contributing code
  lists these so a client can say "this build has no USB" instead of retrying.
* `ui-code` — the device's own JavaScript proves the path, fields and values,
  but no request was made. Good for a form contract, not for a reply shape.
* `not-captured` — the code path exists and was seen, never exercised. Always
  paired with `shapeUnverified`.
* `app-observed` — a shipping client exercised it against real hardware. The
  wire contract is real; the reply field list is as complete as that client
  needed, which may be less than the device returns.
* `live-capture` — a raw reply was recorded and is quoted in `evidence`.
* `live-roundtrip` — read, wrote, read back, and the read reflected the write.
  The strongest claim in this repo, and the only one that proves a setter.

`response.shapeUnverified: true` means: the endpoint exists, but nobody has seen
its reply. A client may call it; it must treat every declared field as absent and
must not render the result as complete.

## Reproducing the checks

```bash
python3 tools/build_index.py     # regenerate registry/index.json
python3 tools/validate.py        # schemas, cross-file consistency, version bumps
python3 tools/selftest.py        # Python client vs. fake router, every definition
bash tools/java_selftest.sh      # Android engine vs. fake router, every definition
```

`validate.py` checks more than schema validity:

* directory name == `variant` in both files
* `vendor`/`model` match the path
* `definitionVersion` identical in `api.json` and `capabilities.json`, and SemVer
* endpoint ids unique, every `feature` exists in the capabilities file
* a write endpoint behind a `read-only` or `unknown` feature is an error
* an `api.json` changed against git `HEAD` without a version bump is an error
* every `sha256` in the index matches the file on disk

The self tests additionally assert that:

* a wrong password is refused by every definition
* every declared read endpoint returns a payload matching its declared kind
* every write is refused without an explicit opt-in
* a write behind a non-`full` feature is refused even with the opt-in
* an undeclared field is rejected client-side and never sent
* the index selects the right variant from a firmware string, longest prefix first

## What is deliberately not claimed

* **No endpoint is inferred from a page name.** A page existing does not mean the
  getter exists; the v1.0 AC10 map lists USB handlers it knows are compiled out.
* **No reply shape is filled in from a guess.** Two v1.0 endpoints carry
  `shapeUnverified` and no fields.
* **No cross-firmware transfer.** The v3.0 AC10 definition lists the endpoints
  exercised on `V03.03.16.11` and says in `provenance.notes` that the wider map
  from the `V15.03.06` line probably exists but was not re-verified. "Probably"
  is not a claim, and it is written as one.
* **No capability marked `full` without a working setter.** The v4.0 AC10 marks
  VPN as `read-only` because only its status getters were exercised.

## When a definition turns out to be wrong

1. Reproduce it on hardware and capture the actual reply.
2. Fix the endpoint, set `verified` to what you actually did, and quote the
   capture in `evidence`.
3. Bump `definitionVersion` (PATCH if only evidence changed, MINOR if a field or
   shape was added, MAJOR if a client would break).
4. Add the `CHANGELOG.md` entry describing what a user would have noticed.
5. Say in the entry that the old claim was wrong. A silently corrected file
   teaches the next reader nothing.
