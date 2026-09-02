"""The guardrail: what a credential looks like, and where it lives.

Deliberately narrow. The threat model is stated plainly:

    Credentials must never leave. Topical context may.

A company name reaching a search engine is accepted -- it is the same as
typing it into a chat box yourself. An API key reaching a search engine is a
failure. Entropy scoring and Luhn checks are out of scope on purpose; they
block git hashes, UUIDs, order numbers and invoice references for a guarantee
that still misses a password like `budi1990`.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from sunday import config

REDACTED = "[redacted]"

#: Shapes we can recognise with no false-positive tax. Each is a prefix, so the
#: pattern is the prefix plus the run of token characters that follows it.
_SHAPE_TAIL = r"[A-Za-z0-9_\-\./+=]{8,}"

_PEM = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)


def _shape_patterns() -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = []
    for shape in config.get().guardrail.key_shapes:
        if shape.startswith("-----BEGIN"):
            patterns.append(_PEM)
        else:
            patterns.append(re.compile(re.escape(shape) + _SHAPE_TAIL))
    return patterns


def is_secret_path(path: str | Path) -> bool:
    """Source taint. Catches secrets that look ordinary: a password in your
    .env is just a word and no pattern will ever match it -- its file path
    will.
    """
    text = str(path)
    if not text.strip():
        return False
    normalised = os.path.normcase(text).replace("\\", "/")
    name = normalised.rsplit("/", 1)[-1]

    for rule in config.get().guardrail.secret_paths:
        rule_norm = os.path.normcase(rule).replace("\\", "/")
        if rule_norm.endswith("/"):
            # A directory rule: anywhere under it counts.
            if f"/{rule_norm}" in f"/{normalised}/" or normalised.startswith(rule_norm):
                return True
            continue
        if fnmatch.fnmatch(name, rule_norm) or name == rule_norm:
            return True
        if normalised.endswith("/" + rule_norm):
            return True
    return False


def redact(text: str) -> tuple[str, int]:
    """Strip known key shapes. Returns the cleaned text and how many were
    removed."""
    if not text:
        return text, 0
    count = 0
    for pattern in _shape_patterns():
        text, hits = pattern.subn(REDACTED, text)
        count += hits
    return text, count


def scrub_query(query: str) -> tuple[str, int]:
    """What leaves through the airlock: key shapes stripped, then the length
    cap. A search query has no reason to be an essay."""
    cleaned, count = redact(query)
    cleaned = " ".join(cleaned.split())
    cap = config.get().external.query_max_chars
    if len(cleaned) > cap:
        cleaned = cleaned[:cap].rstrip()
    return cleaned, count


NOTICE_REDACTED = "I removed something that looked like a credential before searching."
NOTICE_BLOCKED = (
    "This turn read a credential file, so I did not look anything up on the web."
)
