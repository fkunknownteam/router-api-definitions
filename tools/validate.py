#!/usr/bin/env python3
"""Validate every definition in registry/ and keep the index honest.

Checks, in order:
  1. JSON Schema validation of api.json and capabilities.json.
  2. Directory name, api.variant and capabilities.variant must agree.
  3. definitionVersion must be identical in api.json and capabilities.json.
  4. Endpoint ids unique per file; every endpoint.feature must exist in the
     capabilities file of the same directory.
  5. A write endpoint may not sit behind a "read-only" or "unknown" feature.
  6. If a definition changed against git HEAD, its definitionVersion must have
     been bumped (see docs/changelog-process.md).
  7. registry/index.json must list every variant with a matching sha256.

Usage:
  python3 tools/validate.py            # full check
  python3 tools/validate.py --no-git   # skip the version-bump rule
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

try:
    from jsonschema import Draft202012Validator
except ImportError:
    sys.exit("jsonschema is required: python3 -m pip install -r tools/requirements.txt")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_DIR = os.path.join(ROOT, "schema")
REGISTRY = os.path.join(ROOT, "registry")
INDEX = os.path.join(REGISTRY, "index.json")
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")

errors = []
warnings = []


def err(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


def load_schema(name):
    with open(os.path.join(SCHEMA_DIR, name)) as f:
        return json.load(f)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def git_show(rel_path):
    try:
        out = subprocess.run(
            ["git", "-C", ROOT, "show", "HEAD:" + rel_path],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def variant_dirs():
    """Yield (vendor, model, variant, dirpath) for every definition directory."""
    for vendor in sorted(os.listdir(REGISTRY)):
        vdir = os.path.join(REGISTRY, vendor)
        if not os.path.isdir(vdir) or vendor.startswith("."):
            continue
        for model in sorted(os.listdir(vdir)):
            mdir = os.path.join(vdir, model)
            if not os.path.isdir(mdir):
                continue
            for variant in sorted(os.listdir(mdir)):
                d = os.path.join(mdir, variant)
                if os.path.isdir(d) and (
                        os.path.isfile(os.path.join(d, "api.json"))
                        or os.path.isfile(os.path.join(d, "capabilities.json"))):
                    yield vendor, model, variant, d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-git", action="store_true", help="skip the version-bump rule")
    args = ap.parse_args()

    api_schema = load_schema("api-definition.schema.json")
    caps_schema = load_schema("capabilities.schema.json")
    api_val = Draft202012Validator(api_schema)
    caps_val = Draft202012Validator(caps_schema)

    seen = []
    index_entries = {}

    for vendor, model, variant, d in variant_dirs():
        rel = os.path.relpath(d, ROOT)
        api_path = os.path.join(d, "api.json")
        caps_path = os.path.join(d, "capabilities.json")

        for p in (api_path, caps_path):
            if not os.path.isfile(p):
                err(f"{rel}: missing {os.path.basename(p)}")
        if not (os.path.isfile(api_path) and os.path.isfile(caps_path)):
            continue

        try:
            with open(api_path) as f:
                api = json.load(f)
            with open(caps_path) as f:
                caps = json.load(f)
        except json.JSONDecodeError as e:
            err(f"{rel}: invalid JSON: {e}")
            continue

        for e in api_val.iter_errors(api):
            where = "/".join(str(x) for x in e.absolute_path) or "<root>"
            err(f"{rel}/api.json [{where}]: {e.message}")
        for e in caps_val.iter_errors(caps):
            where = "/".join(str(x) for x in e.absolute_path) or "<root>"
            err(f"{rel}/capabilities.json [{where}]: {e.message}")

        if api.get("variant") != variant:
            err(f"{rel}: directory name {variant!r} != api.json variant {api.get('variant')!r}")
        if caps.get("variant") != variant:
            err(f"{rel}: directory name {variant!r} != capabilities.json variant {caps.get('variant')!r}")
        if (api.get("vendor"), api.get("model")) != (vendor, model.upper()):
            err(f"{rel}: vendor/model {api.get('vendor')}/{api.get('model')} does not match the path")
        if caps.get("vendor") != api.get("vendor") or caps.get("model") != api.get("model"):
            err(f"{rel}: capabilities vendor/model differ from api.json")

        av, cv = api.get("definitionVersion"), caps.get("definitionVersion")
        if av != cv:
            err(f"{rel}: definitionVersion mismatch api.json={av!r} capabilities.json={cv!r}")
        for label, v in (("api.json", av), ("capabilities.json", cv)):
            if isinstance(v, str) and not SEMVER.match(v):
                err(f"{rel}/{label}: definitionVersion {v!r} is not MAJOR.MINOR.PATCH")

        ids = set()
        features = caps.get("features", {})
        for ep in api.get("endpoints", []):
            eid = ep.get("id")
            if eid in ids:
                err(f"{rel}: duplicate endpoint id {eid!r}")
            ids.add(eid)
            feat = ep.get("feature")
            if feat is None:
                if ep.get("writes"):
                    warn(f"{rel}: write endpoint {eid!r} has no feature, so the app cannot gate it")
                continue
            if feat not in features:
                err(f"{rel}: endpoint {eid!r} uses feature {feat!r} which capabilities.json does not define")
                continue
            state = features[feat]
            if ep.get("writes") and state != "full":
                err(f"{rel}: write endpoint {eid!r} backs feature {feat!r} marked {state!r}; "
                    f"a verified setter means the feature is 'full'")

        if not args.no_git:
            rel_api = os.path.join(rel, "api.json").replace(os.sep, "/")
            old = git_show(rel_api)
            if old is not None:
                try:
                    old_doc = json.loads(old)
                except json.JSONDecodeError:
                    old_doc = None
                if old_doc is not None:
                    if old_doc != api and old_doc.get("definitionVersion") == av:
                        err(f"{rel}: api.json changed but definitionVersion is still {av}; "
                            f"bump it and add a CHANGELOG entry")

        seen.append({
            "vendor": vendor,
            "model": api.get("model"),
            "variant": variant,
            "path": rel.replace(os.sep, "/"),
            "firmware": api.get("firmware", {}).get("verified"),
            "matchPrefix": api.get("firmware", {}).get("matchPrefix"),
            "hardwareVersions": api.get("hardwareVersions", []),
            "definitionVersion": av,
            "schemaVersion": api.get("schemaVersion"),
            "endpointCount": len(api.get("endpoints", [])),
            "sha256": {"api": sha256(api_path), "capabilities": sha256(caps_path)},
        })
        index_entries[rel.replace(os.sep, "/")] = seen[-1]["sha256"]

    if os.path.isfile(INDEX):
        with open(INDEX) as f:
            idx = json.load(f)
        listed = {}
        for entry in idx.get("definitions", []):
            listed[entry.get("path")] = entry.get("sha256", {})
        for path, digests in index_entries.items():
            if path not in listed:
                err(f"registry/index.json: missing entry for {path} (run tools/build_index.py)")
                continue
            for k, v in digests.items():
                if listed[path].get(k) != v:
                    err(f"registry/index.json: {path} {k} digest is stale (run tools/build_index.py)")
        for path in listed:
            if path not in index_entries:
                err(f"registry/index.json: entry {path} has no directory")
    else:
        err("registry/index.json is missing (run tools/build_index.py)")

    for w in warnings:
        print("WARN  " + w)
    for e in errors:
        print("ERROR " + e)

    print(f"\n{len(seen)} definition(s) checked, {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
