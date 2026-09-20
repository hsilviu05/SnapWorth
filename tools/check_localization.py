#!/usr/bin/env python3
"""Fail if a user-facing string literal has no entry in a String Catalog.

The app is English and Romanian. A string only reaches the Romanian table if
its key is in `ios/Localization/*.json`, and the failure when it is not is
silent: the English literal is shown and nothing anywhere says so. That is the
failure this catches — one `Text("…")` added without a catalog entry, six
weeks before anyone with a Romanian phone mentions it.

Scope is deliberately narrow, because a checker that cries wolf gets deleted:

  * only literals with no interpolation, whose key is therefore exactly the
    literal. An interpolated literal's key depends on the *type* of each
    interpolation (`%lld` for an integer, `%@` for a string), which cannot be
    known without a compiler, and this repository has none.
  * only positions that definitely take a key: `Text`, `Button`, `alert`,
    `String(localized:)`, `configurationDisplayName`, and the rest of the list
    below. Not a `String` variable that happens to hold a sentence.

`DELIBERATELY_ENGLISH` is the other half of the contract: a string that is not
translated on purpose says so here, with the reason, rather than being missing
in a way nobody can tell from a mistake.

Run it with no arguments from anywhere in the repository.
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TARGETS = [
    (os.path.join("ios", "SnapWorth"), os.path.join("ios", "Localization", "App.json")),
    (os.path.join("ios", "SnapWorthWidgets"), os.path.join("ios", "Localization", "Widgets.json")),
]

# Initialisers and modifiers whose first argument is a `LocalizedStringKey`,
# a `LocalizedStringResource`, or `String.LocalizationValue`.
KEY_CALLS = {
    "Text", "Button", "Label", "Toggle", "Picker", "Section", "Link",
    "NavigationLink", "TextField", "SecureField", "DatePicker", "Stepper",
    "ContentUnavailableView", "ProgressView", "LocalizedStringKey",
    "navigationTitle", "navigationBarTitle", "alert", "confirmationDialog",
    "accessibilityLabel", "accessibilityHint", "accessibilityValue", "help",
    "configurationDisplayName", "description", "displayName",
    "IntentDescription", "localized", "searchable",
}

# Argument labels that carry a key rather than a value.
KEY_LABELS = {
    "localized", "title", "label", "prompt", "message", "heading", "text",
    "placeholder", "caption", "priceDetail", "badge",
}

# Not copy, and why. Checked before the catalog, so a name that also happens to
# be a translated string elsewhere is still allowed here.
DELIBERATELY_ENGLISH = {
    # Brand and feature names. A Romanian user looking for "Thrift Flip" is
    # looking for the words on the button.
    "SnapWorth", "SnapWorth Pro", "Snap → Sell", "Thrift Flip", "Flip", "PRO",
    "eBay", "Poshmark", "Mercari", "Depop", "Vinted", "Facebook", "OLX",
    # Stored identities: raw values, dispatch queue labels, symbol names.
    # Translating one of these changes what is persisted or looked up.
    "All", "Owned", "Listed", "Sold", "Scanned", "Date", "Profit", "ROI",
    "Newest", "Most Valuable", "New", "Like New", "Good", "Used",
    "High", "Medium", "Low", "Feature Request", "Bug Report", "General Feedback",
    "com.snapworth.camera",
    # The privacy policy and the terms. These are the English documents served
    # at api.snapworth.eu, and a translated copy of a legal text is a second
    # legal text — see ios/Localization/README.md.
    "Information We Collect", "How We Use Your Information", "Service Providers",
    "Data Retention", "Children's Privacy", "Changes to This Policy", "Contact",
    "Use of Service", "Subscriptions", "Prohibited Use", "Disclaimer",
    # Xcode preview titles: `#if DEBUG`, never in a shipped binary.
    "Recent finds — medium", "Profit this month — small", "Scans left — small",
    "Haul — small", "Lock Screen — rectangular",
}

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")
HAS_WORD = re.compile(r"[A-Za-z]{3}")


# ── A small Swift lexer ──────────────────────────────────────────────────────
#
# Enough of the grammar to be trusted here: line and block comments (nested, as
# Swift allows), ordinary and multiline literals, raw literals with any number
# of `#`, and `\( )` interpolation. Comments matter more than usual in this
# repository, which writes paragraphs of them and quotes copy inside them.

class Literal:
    def __init__(self, text, start, multiline, interpolated):
        self.text = text
        self.start = start
        self.multiline = multiline
        self.interpolated = interpolated


def literals(src):
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c == "/" and src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j + 1
        elif c == "/" and src.startswith("/*", i):
            depth, i = 1, i + 2
            while i < n and depth:
                if src.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif src.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
        elif c in '#"':
            j, hashes = i, 0
            while j < n and src[j] == "#":
                hashes, j = hashes + 1, j + 1
            if j < n and src[j] == '"':
                lit, i = _string(src, j, hashes)
                if lit:
                    out.append(lit)
                    continue
            i = j if hashes else i + 1
        else:
            i += 1
    return out


def _string(src, i, hashes):
    n, h = len(src), "#" * hashes
    multiline = src.startswith('"""', i)
    open_len, close = (3, '"""' + h) if multiline else (1, '"' + h)
    body, j = i + open_len, i + open_len
    esc, interpolated = "\\" + h, False
    while j < n:
        if src.startswith(esc + "(", j):
            interpolated = True
            k, depth = j + len(esc) + 1, 1
            while k < n and depth:
                if src[k] == "(":
                    depth += 1
                elif src[k] == ")":
                    depth -= 1
                elif src[k] == '"':
                    _, k = _string(src, k, 0)
                    continue
                k += 1
            j = k
        elif src.startswith(esc, j):
            j += len(esc) + 1
        elif src.startswith(close, j):
            return Literal(src[body:j], i, multiline, interpolated), j + len(close)
        elif src[j] == "\n" and not multiline:
            return None, i + 1
        else:
            j += 1
    return None, n


def without_comments(src):
    """The source with comments blanked out, same length, so offsets hold."""
    out, i, n = list(src), 0, len(src)
    while i < n:
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            out[i:j] = " " * (j - i)
            i = j
        elif src.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if src.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif src.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        else:
            i += 1
    return "".join(out)


def takes_a_key(code, pos):
    """Is the literal at `pos` in a position that is looked up in a catalog?"""
    j = pos - 1
    while j >= 0 and code[j] in " \t\n":
        j -= 1
    if j < 0:
        return False
    if code[j] == "(":                       # first argument of a call
        k = j - 1
        while k >= 0 and code[k] in " \t\n":
            k -= 1
        m = IDENT.search(code[:k + 1])
        return bool(m) and m.group(0) in KEY_CALLS
    if code[j] == ":":                       # a labelled argument
        m = IDENT.search(code[:j])
        return bool(m) and m.group(0) in KEY_LABELS
    return False


def unescape(text):
    return (text.replace("\\n", "\n").replace("\\t", "\t")
                .replace('\\"', '"').replace("\\\\", "\\"))


def main():
    problems = []
    for target, catalog in TARGETS:
        with open(os.path.join(ROOT, catalog), encoding="utf-8") as f:
            known = set(json.load(f)["strings"])
        for dirpath, _, files in os.walk(os.path.join(ROOT, target)):
            for name in sorted(files):
                if not name.endswith(".swift"):
                    continue
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8") as f:
                    src = f.read()
                code = without_comments(src)
                for lit in literals(src):
                    if lit.interpolated or lit.multiline:
                        continue
                    if not takes_a_key(code, lit.start):
                        continue
                    key = unescape(lit.text)
                    if not HAS_WORD.search(key):
                        continue
                    if key in DELIBERATELY_ENGLISH or key in known:
                        continue
                    line = src.count("\n", 0, lit.start) + 1
                    problems.append((os.path.relpath(path, ROOT), line, key, catalog))

    for path, line, key, catalog in problems:
        print(f"::error file={path},line={line}::{key!r} is shown to the user but "
              f"has no entry in {catalog} — add it there and run "
              f"tools/build_xcstrings.py, or list it in "
              f"tools/check_localization.py's DELIBERATELY_ENGLISH with a reason")
        print(f"{path}:{line}  {key!r}", file=sys.stderr)

    if problems:
        print(f"\n{len(problems)} untranslated string(s)", file=sys.stderr)
        return 1
    print("every literal shown to the user has a catalog entry")
    return 0


if __name__ == "__main__":
    sys.exit(main())
