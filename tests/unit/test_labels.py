from __future__ import annotations

import base64

from claude_manage.labels import PALETTE, color_for, sequences


def test_color_is_stable_per_group() -> None:
    assert color_for("frontend") == color_for("frontend")
    assert color_for("frontend") in PALETTE


def test_sequences() -> None:
    seq = sequences("frontend", (1, 2, 3))
    badge = base64.b64encode(b"frontend").decode()
    assert f"\033]1337;SetBadgeFormat={badge}\a" in seq
    assert "\033]6;1;bg;red;brightness;1\a" in seq
    assert "\033]6;1;bg;blue;brightness;3\a" in seq
    cleared = sequences("", None)
    assert "\033]1337;SetBadgeFormat=\a" in cleared
    assert "\033]6;1;bg;*;default\a" in cleared
    assert "\n" not in seq  # display state only: nothing a shell could run
