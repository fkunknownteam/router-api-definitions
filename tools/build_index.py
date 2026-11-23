#!/usr/bin/env python3
"""Rebuild registry/index.json from the definition directories on disk.

The index is what a client fetches first: it lists every vendor/model/variant
with its firmware match prefix and the sha256 of both files, so a client can
pick a definition, verify the download and cache it by hash.

Run this after adding or editing any definition, then run tools/validate.py.
"""
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY = os.path.join(ROOT, "registry")
INDEX = os.path.join(REGISTRY, "index.json")
REPO = "fkunknownteam/router-api-definitions"
RAW = f"https://raw.githubusercontent.com/{REPO}/main/registry"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    definitions = []
    vendors = {}
    vendor_names = {}
    for vendor in sorted(os.listdir(REGISTRY)):
        vdir = os.path.join(REGISTRY, vendor)
        if not os.path.isdir(vdir) or vendor.startswith("."):
            continue
        models = []
        for model in sorted(os.listdir(vdir)):
            mdir = os.path.join(vdir, model)
            if not os.path.isdir(mdir):
                continue
            variants = []
            for variant in sorted(os.listdir(mdir)):
                d = os.path.join(mdir, variant)
                api = os.path.join(d, "api.json")
                caps = os.path.join(d, "capabilities.json")
                if not os.path.isdir(d):
                    continue  # a model-level README, not a variant
                if not (os.path.isfile(api) and os.path.isfile(caps)):
                    print(f"skip {d}: missing api.json or capabilities.json", file=sys.stderr)
                    continue
                with open(api) as f:
                    doc = json.load(f)
                vendor_names.setdefault(vendor, doc.get("vendorName") or vendor.title())
                model_name = doc.get("model")
                rel = os.path.relpath(d, ROOT).replace(os.sep, "/")
                hw = doc.get("hardwareVersions") or []
                fw_line = (doc.get("firmware") or {}).get("verified") or "?"
                label = (f"Hardware {'/'.join(hw)}" if hw else "Hardware unknown") + f" — firmware {fw_line}"
                entry = {
                    "path": rel,
                    "label": label,
                    "apiUrl": f"{RAW}/{vendor}/{model}/{variant}/api.json",
                    "capabilitiesUrl": f"{RAW}/{vendor}/{model}/{variant}/capabilities.json",
                    "definitionVersion": doc["definitionVersion"],
                    "schemaVersion": doc["schemaVersion"],
                    "firmware": doc.get("firmware", {}).get("verified"),
                    "matchPrefix": doc.get("firmware", {}).get("matchPrefix"),
                    "hardwareVersions": doc.get("hardwareVersions", []),
                    "endpointCount": len(doc.get("endpoints", [])),
                    "sha256": {"api": sha256(api), "capabilities": sha256(caps)},
                }
                variants.append(entry)
                definitions.append(entry)
            if variants:
                models.append({"model": model.upper(), "slug": model,
                               "variantCount": len(variants), "variants": variants})
        if models:
            vendors[vendor] = models

    index = {
        "$schema": "../schema/index.schema.json",
        "schemaVersion": "1.0",
        "generatedBy": "tools/build_index.py",
        "rawBase": RAW,
        "vendorNames": vendor_names,
        "vendors": vendors,
        "definitions": definitions,
    }
    with open(INDEX, "w") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {INDEX} with {len(definitions)} definition(s)")


if __name__ == "__main__":
    main()
