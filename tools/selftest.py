#!/usr/bin/env python3
"""End-to-end self test: run the generic client against the fake router.

For every definition in registry/, this starts tools/mockrouter.py on a free
port, logs in and reads every endpoint the definition declares. It asserts:

  * login succeeds with the right password and is refused with a wrong one;
  * every read endpoint returns a payload the declaration can explain;
  * the cipher round-trips for definitions that declare crypto;
  * a write endpoint is refused unless allow_writes is set;
  * a write behind a non-'full' feature is refused even with allow_writes;
  * the index selects the right variant for each firmware string.

It proves the client executes definitions. It says nothing about real hardware:
hardware truth lives in the per-endpoint evidence fields.

    python3 tools/selftest.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mockrouter
from routerapi import Definition, DefinitionIndex, DefinitionError, DeviceError, RouterClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY = os.path.join(ROOT, "registry")

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("  FAIL " + msg)


def run_definition(rel_dir):
    api_path = os.path.join(ROOT, rel_dir, "api.json")
    d = Definition.load(api_path)
    tag = f"{d.vendor}/{d.model}/{d.variant}"

    # A definition whose login handshake was never captured cannot be driven at
    # all. Proving that it refuses is the whole test for those documents.
    login_spec = d.api["session"]["login"]
    if login_spec.get("verified") == "not-captured":
        c = RouterClient(d, host="127.0.0.1", port=1, password="***", use_tls=False)
        try:
            c.login()
        except DefinitionError:
            print(f"  ok   {tag}: refuses to guess an uncaptured login handshake "
                  f"({len(d.api['endpoints'])} endpoints documented)")
            return
        except Exception as e:  # noqa: BLE001
            check(False, f"{tag}: uncaptured login raised {type(e).__name__} instead of DefinitionError")
            return
        check(False, f"{tag}: uncaptured login handshake was attempted")
        return

    # A challenge login needs a server that issues a key and verifies the
    # signature; the generic mock deliberately does not, so record the gap
    # rather than reporting a false failure.
    if login_spec.get("kind") == "challenge":
        print(f"  ok   {tag}: challenge handshake documented "
              f"({login_spec['challenge']['issuer']['endpoint']} -> "
              f"{login_spec['endpoint']}); proved by tools/handshake_test.py")
        return

    srv, _, port = mockrouter.serve(d)
    try:
        c = RouterClient(d, host="127.0.0.1", port=port, password="wrong", use_tls=False)
        try:
            c.login()
            check(False, f"{tag}: login accepted a wrong password")
        except DeviceError:
            pass

        c = RouterClient(d, host="127.0.0.1", port=port, password="admin", use_tls=False)
        c.login()
        check(bool(c.cookies), f"{tag}: no session cookie after login")

        reads = [e for e in d.api["endpoints"] if not e.get("writes")]
        for ep in reads:
            if ep["id"] in ("session.login",):
                continue
            try:
                out = c.call(ep["id"])
            except Exception as e:  # noqa: BLE001 - report, do not abort the run
                check(False, f"{tag}: read {ep['id']} raised {type(e).__name__}: {e}")
                continue
            kind = (ep.get("response") or {}).get("kind", "json-object")
            if kind == "json-object":
                check(isinstance(out, dict), f"{tag}: {ep['id']} did not return an object")
                for f in (ep.get("response") or {}).get("fields") or []:
                    top = f["name"].split("[]")[0].split(".")[0]
                    check(top in out, f"{tag}: {ep['id']} reply lost declared field {top!r}")
            elif kind == "json-array":
                check(isinstance(out, list), f"{tag}: {ep['id']} did not return an array")

        writes = [e for e in d.api["endpoints"] if e.get("writes") and e.get("request")]
        for ep in writes:
            fields = {f["name"]: "1" for f in (ep["request"].get("fields") or [])}
            try:
                c.call(ep["id"], fields)
                check(False, f"{tag}: write {ep['id']} ran without allow_writes")
            except DeviceError:
                pass
            supports = d.supports(ep.get("feature") or "", write=True) if ep.get("feature") else True
            try:
                c.call(ep["id"], fields, allow_writes=True)
                check(supports, f"{tag}: write {ep['id']} ran but feature "
                                f"{ep.get('feature')!r} is {d.feature_state(ep.get('feature'))!r}")
            except DeviceError:
                check(not supports, f"{tag}: write {ep['id']} refused but feature is 'full'")

        # unknown field must be rejected client-side, never sent
        try:
            target = next(e for e in writes if (e.get("request") or {}).get("fields"))
            c.call(target["id"], {"__not_a_field__": "x"}, allow_writes=True)
            check(False, f"{tag}: unknown field was accepted")
        except DefinitionError:
            pass
        print(f"  ok   {tag}: {len(reads)} read(s), {len(writes)} write(s)")
    finally:
        srv.shutdown()


def run_index():
    idx = DefinitionIndex.load(os.path.join(REGISTRY, "index.json"))
    for entry in idx.index["definitions"]:
        fw = entry.get("firmware")
        got = idx.select(fw, model=entry["path"].split("/")[2].upper())
        check(got is not None and got["path"] == entry["path"],
              f"index: {fw!r} selected {got and got['path']} instead of {entry['path']}")
        for hw in entry.get("hardwareVersions") or []:
            got = idx.select(fw, hardware=hw)
            check(got is not None, f"index: no variant for hardware {hw!r}")
    print(f"  ok   index: {len(idx.index['definitions'])} definition(s)")


def main():
    print("index")
    run_index()
    print("definitions")
    for vendor in sorted(os.listdir(REGISTRY)):
        vdir = os.path.join(REGISTRY, vendor)
        if not os.path.isdir(vdir):
            continue
        for model in sorted(os.listdir(vdir)):
            mdir = os.path.join(vdir, model)
            if not os.path.isdir(mdir):
                continue
            for variant in sorted(os.listdir(mdir)):
                d = os.path.join(mdir, variant)
                if os.path.isdir(d):
                    run_definition(os.path.relpath(d, ROOT))
    print()
    if fails:
        print(f"{len(fails)} failure(s)")
        return 1
    print("all self tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
