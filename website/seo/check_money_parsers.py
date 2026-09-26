#!/usr/bin/env python3
"""The website's two money parsers must read typed amounts the way the app does.

There are three copies of one rule for turning "12,50" or "$1,250" into a
number: the app's `MoneyInput.normalized` (MarketplaceFees.swift), the Thrift
Flip calculator's `num()` on index.html, and the /guess game's `parse()`
(written by build_guess.py). Each web copy says in a comment that it must match
the app, and nothing checked it.

That is how they drifted. The app fixed an asymmetry in da0b242 — the
three-digit grouping test was applied to the comma but not to the point, so
"1.250" read as 1.25 — and both web copies kept it: a visitor typing a
dot-grouped price got a value a thousand times off, and "Skip it" on a flip
worth making.

This runs the app's own MoneyInput test cases through both web functions with
node, so a case added to the Swift suite is checked here too. The cases are
read from `MoneyInputTests` in ProductionHardeningTests.swift:

- `XCTAssertEqual(MoneyInput.parse("…"), n)`  → both must return n
- `XCTAssertNil(MoneyInput.parse("…"))`        → num() returns 0 (render()
  needs a number), parse() returns null
- `("…", "…")` pairs, the symmetry invariant   → both inputs read the same

Run: python3 website/seo/check_money_parsers.py   (needs node on PATH)
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
WEBSITE = HERE.parent
REPO = WEBSITE.parent
SWIFT_TESTS = REPO / "ios/SnapWorthTests/ProductionHardeningTests.swift"

# (file, function name) for each web copy of the rule.
WEB_COPIES = [
    (WEBSITE / "index.html", "num"),
    (WEBSITE / "guess.html", "parse"),
]

EQUAL = re.compile(r'XCTAssertEqual\(MoneyInput\.parse\("((?:[^"\\]|\\.)*)"\),\s*([0-9.]+)')
NIL = re.compile(r'XCTAssertNil\(MoneyInput\.parse\("((?:[^"\\]|\\.)*)"\)\)')
PAIR = re.compile(r'\("((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"\)')

# Fewer than this means the extractor stopped finding the suite, not that the
# suite shrank; failing is better than passing on nothing.
MIN_CASES = 12
MIN_PAIRS = 3


def swift_string(literal: str) -> str:
    # The cases are plain ASCII; JSON's escapes cover `\"` and `\\`.
    return json.loads(f'"{literal}"')


def app_cases() -> tuple[list[tuple[str, float | None]], list[tuple[str, str]]]:
    source = SWIFT_TESTS.read_text(encoding="utf-8")
    start = source.find("final class MoneyInputTests")
    if start < 0:
        sys.exit(f"MoneyInputTests not found in {SWIFT_TESTS.relative_to(REPO)}")
    end = source.find("\n}\n", start)
    body = source[start:end if end > 0 else len(source)]

    cases: list[tuple[str, float | None]] = []
    cases += [(swift_string(s), float(n)) for s, n in EQUAL.findall(body)]
    cases += [(swift_string(s), None) for s in NIL.findall(body)]
    pairs = [(swift_string(a), swift_string(b)) for a, b in PAIR.findall(body)]
    if len(cases) < MIN_CASES or len(pairs) < MIN_PAIRS:
        sys.exit(f"found only {len(cases)} cases and {len(pairs)} pairs in "
                 "MoneyInputTests — has the suite moved or changed shape?")
    return cases, pairs


def function_source(path: pathlib.Path, name: str) -> str:
    """The text of `function <name>(…) { … }`, by brace matching.

    Neither function has a brace inside a string or a regex literal, which is
    what makes counting braces enough here.
    """
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"function {name}\([^)]*\)\s*\{{", text)
    if not match:
        sys.exit(f"function {name}() not found in {path.relative_to(REPO)}")
    depth = 0
    for i in range(match.end() - 1, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[match.start():i + 1]
    sys.exit(f"unbalanced braces in {name}() in {path.relative_to(REPO)}")


def run_node(fn_source: str, name: str, inputs: list[str]) -> list:
    script = (f"{fn_source}\n"
              f"var inputs = {json.dumps(inputs)};\n"
              f"process.stdout.write(JSON.stringify(inputs.map({name})));\n")
    result = subprocess.run(["node", "-e", script], capture_output=True,
                            text=True, check=False)
    if result.returncode != 0:
        sys.exit(f"node failed on {name}():\n{result.stderr}")
    return json.loads(result.stdout)


def main() -> int:
    if not shutil.which("node"):
        print("node is not on PATH")
        return 1

    cases, pairs = app_cases()
    inputs = [s for s, _ in cases] + [s for pair in pairs for s in pair]
    problems = 0

    for path, name in WEB_COPIES:
        got = dict(zip(inputs, run_node(function_source(path, name), name, inputs)))
        label = f"{path.name} {name}()"
        for text, expected in cases:
            if expected is None:
                # num() feeds render(), which needs a number; parse() refuses.
                want = 0 if name == "num" else None
            else:
                want = expected
            have = got[text]
            same = (have is None and want is None) or (
                have is not None and want is not None and abs(have - want) < 1e-9)
            if not same:
                print(f"{label}: {text!r} -> {have!r}, the app reads {want!r}")
                problems += 1
        for a, b in pairs:
            if got[a] != got[b]:
                print(f"{label}: {a!r} -> {got[a]!r} but {b!r} -> {got[b]!r}; "
                      "swapping the separator must not change the number")
                problems += 1

    print(f"{len(cases)} MoneyInput cases and {len(pairs)} symmetry pairs through "
          f"{len(WEB_COPIES)} web parsers, {problems} disagreement(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
