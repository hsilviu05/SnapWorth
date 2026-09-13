#!/usr/bin/env python3
"""Cheap checks for Swift source, for an environment with no Swift toolchain.

Not a parser. It looks for a short list of mistakes that are silent here and
only surface as a red CI run five minutes later — the ones that come from
writing Swift with another language's reflexes.

**What it cannot catch**, stated plainly so it is not mistaken for a compiler:
anything that needs type or isolation information. Actor isolation is the
obvious one — calling a `@MainActor` member from a synchronous nonisolated
test is a hard error and looks perfectly ordinary to a text scanner; that
happened on the commit right after this file was added. So is availability,
overload resolution, and whether a `nonisolated static let` is permitted on a
global-actor-isolated type. For those, CI is still the compiler.

The array-literal rule below has a matching blind spot worth naming: it sees
`[1.0, 6, 24 * 3]` because every element is a literal or an arithmetic
expression over literals, and it cannot see `[1.0, someInt * 3]`, which fails
the same way. Deciding that one needs to know what `someInt` is.

A third mistake also belongs on that list, and a rule for it was written and
then thrown away: inserting a declaration between a binding attribute
(`@ViewBuilder`, `@MainActor`) and the declaration it belonged to, which
silently re-targets the attribute. The detector for it fired on 13 files that
compile perfectly, because "an attributed declaration with a sibling after it"
is simply the normal shape — telling the two apart needs to know which
declaration the author meant. A check with a 13/13 false-positive rate is worse
than no check: it gets ignored, and then deleted, and takes the true positives
with it.

Usage: python3 tools/swift_sanity.py [paths...]     (default: ios/)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# A line that is only a string literal, following a line that ends in one.
# Swift has no implicit adjacent-literal concatenation (C and Python do), so
# this is `error: expected ',' separator` every time.
LITERAL_ONLY = re.compile(r'^"(?:[^"\\]|\\.)*"\s*[,)\]]*\s*$')

# An array literal that mixes a float literal with an *integer arithmetic
# expression*. `[1.0, 6, 24, 24 * 3]` reads as homogeneous and is not: bare
# integer literals unify with `1.0` to `Double`, but `24 * 3` is an `Int`
# expression, so the whole literal becomes `[Any]` and every use of an element
# fails with "cannot convert value of type 'Any'". Python makes the same list
# without complaint, which is how it gets written.
#
# Deliberately narrow — only elements that are *entirely* integer literals and
# operators count, because anything else needs to know a type.
ARRAY_LITERAL = re.compile(r"\[([^\[\]{}()\"]*)\]")
FLOAT_LITERAL = re.compile(r"^\d+\.\d+$")
INT_ARITHMETIC = re.compile(r"^\d+(?:\s*[*/+-]\s*\d+)+$")


def raw_string_spans(lines: list[str]) -> set[int]:
    """Line numbers inside a multi-line string, where the rules do not apply."""
    inside, spans = False, set()
    for i, line in enumerate(lines):
        opens = line.count('"""')
        if inside:
            spans.add(i)
        if opens % 2 == 1:
            inside = not inside
            spans.add(i)
    return spans


def check(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").split("\n")
    skip = raw_string_spans(lines)
    problems = []
    for i in range(1, len(lines)):
        if i in skip or i - 1 in skip:
            continue
        prev, cur = lines[i - 1].rstrip(), lines[i].strip()
        if cur.startswith("//") or prev.lstrip().startswith("//"):
            continue
        if prev.endswith('"') and LITERAL_ONLY.match(cur):
            problems.append(
                f"{path}:{i + 1}: adjacent string literals — Swift needs `+` "
                f"between them"
            )
    for i, line in enumerate(lines):
        if i in skip or line.strip().startswith("//"):
            continue
        for match in ARRAY_LITERAL.finditer(line):
            parts = [p.strip() for p in match.group(1).split(",")]
            if any(FLOAT_LITERAL.match(p) for p in parts) and \
               any(INT_ARITHMETIC.match(p) for p in parts):
                problems.append(
                    f"{path}:{i + 1}: array literal mixes a float literal with "
                    f"an integer expression — the whole literal becomes [Any]"
                )

    joined = "\n".join(lines)
    for pattern, message in [
        (r'\bf"', 'f-string — Swift uses "\\(value)" interpolation'),
        (r"^\s*elif\b", "`elif` — Swift uses `else if`"),
        (r'\bTrue\b|\bFalse\b|\bNone\b', "Python literal (True/False/None)"),
    ]:
        for match in re.finditer(pattern, joined, re.MULTILINE):
            line_no = joined.count("\n", 0, match.start()) + 1
            if (line_no - 1) in skip:
                continue
            text = lines[line_no - 1]
            if text.strip().startswith("//") or text.strip().startswith("///"):
                continue
            problems.append(f"{path}:{line_no}: {message}")
    return problems


def main(argv: list[str]) -> int:
    roots = [Path(a) for a in argv[1:]] or [Path("ios")]
    files: list[Path] = []
    for root in roots:
        files.extend([root] if root.is_file() else sorted(root.rglob("*.swift")))
    problems = [p for f in files for p in check(f)]
    for problem in problems:
        print(problem)
    print(f"{len(files)} files checked, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
