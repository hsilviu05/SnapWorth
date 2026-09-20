#!/usr/bin/env python3
"""Build the String Catalogs from `ios/Localization/*.json`, and check them.

Xcode writes `.xcstrings` itself when it extracts strings from a build, which
is not available here — there is no Swift toolchain in this repository's CI
container or in the agent environment that maintains it. So the catalogs are
generated from a plain source file that is diffable and reviewable, and this
script is also the check that they are safe to ship:

  * a translation must carry exactly the same format specifiers, in the same
    order, as its source string. A `%@` where the code passes an `Int` is a
    crash at the point the string is shown, not a mistranslation.
  * a plural entry must cover Romanian's three categories (one / few / other).
    Romanian agrees differently at 1, at 2–19, and at 20+ ("un articol",
    "2 articole", "20 de articole"), so `one`/`other` — enough for English —
    silently prints the wrong form for most numbers.
  * every key must be unique, and no key may be empty.

Run with `--check` to verify every string in the source files is in the
committed catalogs, with those values. That is what CI does.

The catalogs are not owned outright. Both targets build with
`SWIFT_EMIT_LOC_STRINGS = YES`, so Xcode extracts strings during its own builds
and adds any key it finds that the catalog does not have — which is how a key
whose format specifiers were guessed wrong here gets corrected, by the
compiler, with the types it can see. Entries this script does not know about
are therefore kept rather than deleted, and `--check` ignores them. What it
does not tolerate is a *managed* entry that has drifted: those are rewritten
from the source files, so editing one in Xcode's catalog editor loses the edit.

An extra entry Xcode added is an untranslated string. It is reported by name at
the end of a run, and `tools/check_localization.py` fails on the ones whose
keys are plain literals.
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "ios", "Localization")

# `%@` and friends, including positional forms like `%1$@`.
SPECIFIER = re.compile(r'%(?:\d+\$)?[-+ #0]*[\d*]*(?:\.[\d*]+)?(?:hh|h|ll|l|q|L|z|t|j)?[@dioux XeEfgGcsSpaAF%]')

PLURAL_CATEGORIES = {"zero", "one", "two", "few", "many", "other"}
# What each language must define. English inflects at 1; Romanian also has a
# distinct form from 20 upwards, where the noun takes "de".
REQUIRED = {"en": {"one", "other"}, "ro": {"one", "few", "other"}}


def specifiers(s):
    return [m.group(0) for m in SPECIFIER.finditer(s) if m.group(0) != "%%"]


def conversion(spec):
    """The trailing letter — `@`, `d`, `f`. What the argument is read as, and
    the only part of a specifier that can crash if it disagrees with the code."""
    return spec[-1]


def positional(spec):
    """The 1-based index of a `%2$@`-style specifier, or None."""
    m = re.match(r'%(\d+)\$', spec)
    return int(m.group(1)) if m else None


def specifier_problem(form, source, is_plural):
    """Why `form` cannot safely stand in for `source`, or None.

    Three shapes are allowed. Same specifiers in the same order — the ordinary
    translation. All-positional, in any order — how a language that needs the
    arguments in a different order says so. And, for a plural form only,
    dropping specifiers: Romanian's singular reads better as "o zi" than
    "1 zi", and a `.stringsdict` variant may leave the number out because the
    plural rule consumes the argument either way.
    """
    got, want = specifiers(form), specifiers(source)
    if got == want:
        return None

    indices = [positional(s) for s in got]
    if got and all(i is not None for i in indices):
        for spec, i in zip(got, indices):
            if not 1 <= i <= len(want):
                return f"%{i}$ has no argument {i} to refer to"
            if conversion(spec) != conversion(want[i - 1]):
                return (f"{spec} reads argument {i} as {conversion(spec)!r}, "
                        f"but it is {conversion(want[i - 1])!r}")
        return None

    if is_plural:
        rest = list(want)
        for spec in got:
            while rest and conversion(rest[0]) != conversion(spec):
                rest.pop(0)
            if not rest:
                return f"{spec} is not one of the source's arguments {want}"
            rest.pop(0)
        return None

    return f"{got} vs {want}"


def unit(value, state="translated"):
    return {"stringUnit": {"state": state, "value": value}}


def variations(forms):
    return {"variations": {"plural": {k: unit(v) for k, v in sorted(forms.items())}}}


def build(entries, path, errors, existing=None):
    """entries: {key: {comment?, en, ro}} where a value is a str or a dict of
    plural forms. Returns the catalog dict, with any entry already in the
    catalog and not in `entries` carried over untouched."""
    strings = dict(existing or {})
    for key, e in entries.items():
        if not key:
            errors.append(f"{path}: empty key")
            continue
        loc = {}
        base = e["en"]
        for lang in ("en", "ro"):
            value = e[lang]
            if isinstance(value, dict):
                missing = REQUIRED[lang] - set(value)
                if missing:
                    errors.append(f"{path}: {key!r} [{lang}] missing plural "
                                  f"{', '.join(sorted(missing))}")
                unknown = set(value) - PLURAL_CATEGORIES
                if unknown:
                    errors.append(f"{path}: {key!r} [{lang}] unknown plural "
                                  f"category {', '.join(sorted(unknown))}")
                loc[lang] = variations(value)
                forms = value.values()
            else:
                loc[lang] = unit(value)
                forms = [value]

            source = base["other"] if isinstance(base, dict) else base
            for form in forms:
                why = specifier_problem(form, source, isinstance(value, dict))
                if why:
                    errors.append(f"{path}: {key!r} [{lang}] {why}")

        entry = {"extractionState": "manual", "localizations": loc}
        if e.get("comment"):
            entry["comment"] = e["comment"]
        strings[key] = entry

    return {"sourceLanguage": "en", "strings": strings, "version": "1.0"}


def dump(catalog):
    """Xcode's own formatting: 2-space indent, space before the colon, keys
    sorted. Matching it keeps the diff of a regeneration empty when Xcode has
    opened the file in between."""
    text = json.dumps(catalog, indent=2, ensure_ascii=False, sort_keys=True,
                      separators=(",", " : "))
    return text + "\n"


def main():
    check = "--check" in sys.argv
    errors, changed = [], []

    for name in sorted(os.listdir(SRC)):
        if not name.endswith(".json"):
            continue
        source = os.path.join(SRC, name)
        with open(source, encoding="utf-8") as f:
            spec = json.load(f)
        out = os.path.join(ROOT, spec["output"])
        before = {}
        if os.path.exists(out):
            with open(out, encoding="utf-8") as f:
                before = json.load(f).get("strings", {})
        catalog = build(spec["strings"], os.path.relpath(source, ROOT), errors, before)
        text = dump(catalog)

        stale = [k for k in spec["strings"] if before.get(k) != catalog["strings"][k]]
        if stale:
            changed.append((os.path.relpath(out, ROOT), stale))
            if not check:
                with open(out, "w", encoding="utf-8") as f:
                    f.write(text)

        extra = sorted(set(before) - set(spec["strings"]))
        note = f", {len(extra)} not in {os.path.basename(source)}" if extra else ""
        print(f"{len(catalog['strings']):4} strings  "
              f"{os.path.relpath(out, ROOT)}{note}")
        for k in extra:
            print(f"       untranslated: {k!r}")

    for e in errors:
        print(f"error: {e}", file=sys.stderr)
    if errors:
        return 1
    if check and changed:
        print("error: these catalogs do not carry what ios/Localization says — "
              "run tools/build_xcstrings.py and commit the result:", file=sys.stderr)
        for path, keys in changed:
            print(f"  {path}", file=sys.stderr)
            for k in keys[:20]:
                print(f"    {k!r}", file=sys.stderr)
            if len(keys) > 20:
                print(f"    … and {len(keys) - 20} more", file=sys.stderr)
        return 1
    if changed:
        print(f"wrote {len(changed)} catalog(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
