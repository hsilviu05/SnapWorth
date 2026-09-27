"""DO NOT MERGE. A deliberately failing test for #209's negative run.

It exists only on tmp/209-negative, to prove that `All required checks`
turns red when a backend test fails. The branch is deleted once it has.
"""


def test_deliberately_fails_for_the_209_negative_run():
    assert False, "deliberate failure: negative run for #209"
