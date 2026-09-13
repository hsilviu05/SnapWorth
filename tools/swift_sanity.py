#!/usr/bin/env python3
"""Cheap checks for Swift source, for an environment with no Swift toolchain.

Not a parser. It looks for a short list of mistakes that are silent here and
only surface as a red CI run five minutes later — the ones that come from
writing Swift with another language's reflexes.

**What it cannot catch**, stated plainly so it is not mistaken for a compiler:
anything that needs type information. Availability, overload resolution, and
whether a `nonisolated static let` is permitted on a global-actor-isolated
type. For those, CI is still the compiler.

Actor isolation was on that list, and one *shape* of it has since come off:
a test method that is neither `@MainActor` itself nor inside a `@MainActor`
class, touching a member of a type this repo declares `@MainActor`. That does
not need type inference — only the declarations, which are in these files — and
it is by some distance the most expensive mistake here, having cost three CI
cycles. The general case is still out of reach: isolation that arrives through
a protocol, a closure's inherited context, or a type declared outside this
repo, none of which a text scan can see.

The rule below is deliberately narrow, and every exclusion in it earned its
place by producing a false positive on code that compiles: members declared
`nonisolated`, nested type names reached through their parent, anything inside
a string literal (a source-scanning test naming a file is not a call), and
anything preceded by `await` (an async test hopping to the actor is correct).

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

# A `case` pattern that uses `as` to *convert a constant* rather than to bind a
# downcast. In a pattern position `x as String` is read as a cast pattern, not
# as an expression, so the case keeps the operand's type:
# `case (kSecAttrKeyTypeRSA as String, 2048)` has type `CFString` and cannot
# match a `String`. The identical text inside an `if` is an ordinary expression
# and compiles, which is what makes this so easy to write.
#
# `case let error as URLError` and `case is Foo` are the legitimate forms and
# are excluded by the `let`/`var` test below — flagging those would make this
# rule noise, and a noisy rule gets deleted along with its true positives.
CASE_CAST = re.compile(r"^\s*case\b([^:]*)\bas[!?]?\s+[A-Z]\w*")
CASE_BINDING = re.compile(r"\b(let|var)\b")


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
        case_cast = CASE_CAST.match(line)
        if case_cast and not CASE_BINDING.search(case_cast.group(1)):
            problems.append(
                f"{path}:{i + 1}: `as` inside a `case` pattern is a cast "
                f"pattern, not an expression — compare in an `if` instead"
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


# ── Actor isolation, the one case a text scan can reach ──────────────────────
#
# The docstring above says isolation needs type information, and it does in
# general. One shape does not: a test method that is neither `@MainActor` itself
# nor inside a `@MainActor` class, calling a member of a type this repo declares
# `@MainActor`. That is a hard error — "call to main actor-isolated ... in a
# synchronous nonisolated context" — and it has now cost three CI cycles.
#
# Everything here is deliberately conservative. A name is only flagged when the
# type is declared `@MainActor` *in this repo*, the member is not declared
# `nonisolated` anywhere, and the member is not itself a type name. Each of
# those exclusions exists because leaving it out produced a false positive on
# code that compiles today.
TYPE_DECL = re.compile(
    r"^\s*(?:public\s+|internal\s+|private\s+|fileprivate\s+|final\s+)*"
    r"(?:class|struct|enum|actor)\s+([A-Z]\w*)")
ATTRIBUTE = re.compile(r"^\s*@\w+")
NONISOLATED_MEMBER = re.compile(
    r"\bnonisolated\b[^\n]*?\b(?:func|var|let)\s+([a-zA-Z_]\w*)")
TEST_FUNC = re.compile(r"^\s*func\s+(test\w*)\s*\(")
CLASS_DECL = re.compile(r"^\s*(?:final\s+)?class\s+(\w*Tests)\b")
STRING_LITERAL = re.compile(r'"(?:[^"\\\\]|\\\\.)*"')


def isolation_facts(files: list[Path]) -> tuple[set[str], set[str], set[str]]:
    """Main-actor types, names that are safe to touch anyway, and all type names."""
    isolated: set[str] = set()
    exempt: set[str] = set()
    all_types: set[str] = set()
    for path in files:
        if "Tests" in path.name:
            continue
        lines = path.read_text(encoding="utf-8").split("\n")
        pending_main_actor = False
        for line in lines:
            match = TYPE_DECL.match(line)
            if match:
                all_types.add(match.group(1))
                if pending_main_actor:
                    isolated.add(match.group(1))
                pending_main_actor = False
                continue
            if ATTRIBUTE.match(line):
                if line.strip().startswith("@MainActor"):
                    pending_main_actor = True
                continue
            if line.strip():
                pending_main_actor = False
        exempt.update(NONISOLATED_MEMBER.findall("\n".join(lines)))
    return isolated, exempt, all_types


def check_actor_isolation(files: list[Path]) -> list[str]:
    isolated, exempt, all_types = isolation_facts(files)
    if not isolated:
        return []
    problems = []
    for path in files:
        if "Tests" not in path.name:
            continue
        lines = path.read_text(encoding="utf-8").split("\n")
        skip = raw_string_spans(lines)
        class_is_isolated = False
        pending_main_actor = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if CLASS_DECL.match(line):
                class_is_isolated = pending_main_actor
                pending_main_actor = False
                continue
            test = TEST_FUNC.match(line)
            if test:
                if class_is_isolated or pending_main_actor:
                    pending_main_actor = False
                    continue
                pending_main_actor = False
                body = []
                for later in lines[i + 1:]:
                    if later.rstrip() == "    }":
                        break
                    body.append(later)
                # String literals are stripped first: a source-scanning test
                # naming "ViewModels/ResultViewModel.swift" is not a call.
                text = STRING_LITERAL.sub('""', "\n".join(body))
                for type_name in isolated:
                    for hit in re.finditer(
                            rf"\b{type_name}\s*(?:\.\s*(\w+)|\()", text):
                        name = hit.group(1) or "init"
                        if name in exempt or name in all_types:
                            continue
                        # `await MockPurchaseService()` from an async test hops
                        # to the actor and is correct.
                        if "await" in text[max(0, hit.start() - 12):hit.start()]:
                            continue
                        problems.append(
                            f"{path}:{i + 1}: {test.group(1)} touches "
                            f"{type_name}.{name}, and {type_name} is @MainActor "
                            f"— mark the test or its class @MainActor"
                        )
                        break
                    else:
                        continue
                    break
                continue
            if ATTRIBUTE.match(line):
                if stripped.startswith("@MainActor"):
                    pending_main_actor = True
                continue
            if stripped and i not in skip:
                pending_main_actor = False
    return problems


def main(argv: list[str]) -> int:
    roots = [Path(a) for a in argv[1:]] or [Path("ios")]
    files: list[Path] = []
    for root in roots:
        files.extend([root] if root.is_file() else sorted(root.rglob("*.swift")))
    problems = [p for f in files for p in check(f)]
    problems += check_actor_isolation(files)
    for problem in problems:
        print(problem)
    print(f"{len(files)} files checked, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
