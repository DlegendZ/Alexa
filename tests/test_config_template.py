"""The shipped template and the code's own defaults must say the same thing.

`config.example.toml` is what a stranger copies to `config.toml`, and
`config.Config()` is what runs when there is no config file at all. Those are
two statements of the same set of decisions, written in two places, and this
repository already knows what happens to a number that lives in two places:
note 51, note 69, and the retrieval cutoff that was documented as "tune with
real turns" and never tuned.

Writing this test found the case worth having it for. The template shipped
`[ui] autostart = true` while the dataclass, the README and the design document
all said off-by-default -- "something that holds several gigabytes of a graphics
card should not start itself unasked". Anyone copying the template got an
assistant that added itself to the `Run` key without being asked, and nothing
anywhere would have said so.

Lists are compared by hand rather than here: the file roots are meant to be
edited, and the guardrail lists are long enough that a mismatch is visible.
"""

from __future__ import annotations

import dataclasses
import tomllib
from pathlib import Path

from sunday import config

TEMPLATE = Path(__file__).resolve().parents[1] / "config.example.toml"


def scalars():
    """Every `section.key = value` in the template, paired with the default."""
    raw = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    blank = config.Config()
    for section, values in raw.items():
        holder = getattr(blank, section, None)
        assert holder is not None and dataclasses.is_dataclass(holder), (
            f"the template has a [{section}] block and Config has no such field"
        )
        fields = {f.name for f in dataclasses.fields(holder)}
        for key, value in values.items():
            assert key in fields, f"the template sets {section}.{key}, which Config ignores"
            if isinstance(value, list):
                continue
            yield f"{section}.{key}", value, getattr(holder, key)


def test_the_template_is_the_defaults_written_down():
    drift = [
        f"{name}: template {value!r}, default {default!r}"
        for name, value, default in scalars()
        if value != default
    ]
    assert not drift, (
        "config.example.toml and config.Config() disagree, so what a stranger "
        "copies is not what the code does without a config file:\n  "
        + "\n  ".join(drift)
    )


def test_the_template_names_nothing_the_code_does_not_have():
    """Covered by the assertions inside `scalars`; run so a stale key fails
    here rather than only as a side effect of the test above."""
    assert list(scalars())


def test_autostart_is_off_in_the_shipped_template():
    """The one this test was written from, kept as its own case.

    It is a rule with a reason rather than a preference: a program that holds
    several gigabytes of a graphics card should not add itself to the `Run`
    key on somebody else's machine because a template said so.
    """
    raw = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    assert raw["ui"]["autostart"] is False
    assert config.Config().ui.autostart is False


def test_the_microphone_is_not_opened_by_the_shipped_template():
    """Same rule, the other two switches that open a microphone unasked.

    Both are `true` in the local config on the machine this was built on, and
    that split is deliberate and documented -- but a default is a thing a
    stranger inherits without reading, and these two are the difference between
    an assistant and a listening device.
    """
    raw = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    assert raw["audio"]["listen_on_start"] is False
    assert raw["wake"]["follow_up_ms"] <= 60_000
