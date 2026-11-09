#!/usr/bin/env python3
"""Builds library/index.json from the recipes in library/.

    python3 tools/build_index.py           rebuild the index
    python3 tools/build_index.py --check   fail if a recipe breaks the rules or the index is out of date

The index holds only each recipe's [match] rules plus a fingerprint, so Omni can work out
on your PC which recipe you need before downloading anything.
"""
import hashlib
import json
import os
import sys
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from omni.library import ICON_OK, INDEX_VERSION, NAME_OK, check_recipe  # noqa: E402

LIBRARY = os.path.join(ROOT, "library")
ICONS = os.path.join(ROOT, "icons")
ICON_MAX = 512 * 1024
INDEX = os.path.join(LIBRARY, "index.json")


def icon_names():
    if not os.path.isdir(ICONS):
        return set()
    return {f[:-4] for f in os.listdir(ICONS) if f.endswith(".png")}


def build():
    recipes, problems = {}, []
    names = icon_names()
    for fname in sorted(os.listdir(LIBRARY)):
        if not fname.endswith(".toml"):
            continue
        name = fname[:-5]
        if not NAME_OK.match(name):
            problems.append(f"{fname}: file name must be lowercase letters, digits and dashes")
            continue
        with open(os.path.join(LIBRARY, fname), "rb") as f:
            raw = f.read()
        try:
            data = tomllib.loads(raw.decode())
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
            problems.append(f"{fname}: {e}")
            continue
        problems += [f"{fname}: {p}" for p in check_recipe(data, names)]
        recipes[name] = {"match": data.get("match", {}), "sha256": hashlib.sha256(raw).hexdigest()}
        if data.get("requires", 1) != 1:
            recipes[name]["requires"] = data["requires"]
    icons = {}
    for fname in sorted(os.listdir(ICONS)) if os.path.isdir(ICONS) else []:
        if fname.endswith((".md", ".toml")):
            continue
        key = fname[:-4]
        if not fname.endswith(".png") or not ICON_OK.match(key):
            problems.append(f"icons/{fname}: must be a .png named with lowercase letters, digits and _")
            continue
        with open(os.path.join(ICONS, fname), "rb") as f:
            raw = f.read()
        if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            problems.append(f"icons/{fname}: not a PNG file")
        elif len(raw) > ICON_MAX:
            problems.append(f"icons/{fname}: bigger than 512 KB")
        else:
            icons[key] = hashlib.sha256(raw).hexdigest()[:12]
    aliases = {}
    alias_file = os.path.join(ICONS, "aliases.toml")
    if os.path.exists(alias_file):
        with open(alias_file, "rb") as f:
            try:
                table = tomllib.load(f)
            except tomllib.TOMLDecodeError as e:
                table = {}
                problems.append(f"icons/aliases.toml: {e}")
        for target, names in table.items():
            if target not in icons:
                problems.append(f"icons/aliases.toml: there's no icons/{target}.png")
                continue
            if not isinstance(names, list):
                problems.append(f"icons/aliases.toml: {target} must be a list of names")
                continue
            for n in names:
                if not isinstance(n, str) or not ICON_OK.match(n):
                    problems.append(f"icons/aliases.toml: {n!r} must be lowercase letters, digits and _")
                elif n in icons:
                    problems.append(f"icons/aliases.toml: {n} is already an icon file")
                elif n in aliases and aliases[n] != target:
                    problems.append(f"icons/aliases.toml: {n} is listed for both {aliases[n]} and {target}")
                else:
                    aliases[n] = target
    text = json.dumps({"version": INDEX_VERSION, "recipes": recipes, "icons": icons, "icon_aliases": aliases}, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
    return text, problems


def main():
    text, problems = build()
    for p in problems:
        print("problem:", p)
    if problems:
        sys.exit(1)
    if "--check" in sys.argv:
        try:
            with open(INDEX) as f:
                current = f.read()
        except OSError:
            current = ""
        if current != text:
            sys.exit("library/index.json is out of date: run python3 tools/build_index.py and commit it")
        print("library is fine")
        return
    with open(INDEX, "w") as f:
        f.write(text)
    print(f"wrote {INDEX} ({text.count('sha256')} recipes, {len(json.loads(text)['icons'])} icons)")


if __name__ == "__main__":
    main()
