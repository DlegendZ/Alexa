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

#: A prefix only counts at the start of a word. Without this, `sk-` matched
#: inside "ta|sk-oriented" and "di|sk-image-backup", redacting ordinary English
#: mid-word and firing the credential notice over it.
_LEFT_EDGE = r"(?<![A-Za-z0-9_])"

#: Prefixes that need their real shape rather than a generic tail, because the
#: prefix alone is something people write. `ASIA` is an English word that `AKIA`
#: is not, and `hf_` collides with ordinary snake_case. Both are exact-length
#: credentials, so the precise form costs nothing and ends the ambiguity.
_PRECISE_TAILS = {
    "AKIA": r"[A-Z0-9]{16}",  # AWS access key id: 20 chars total
    "ASIA": r"[A-Z0-9]{16}",  # AWS temporary key id, same shape
    "hf_": r"[A-Za-z0-9]{30,}",  # Hugging Face token
    "AIza": r"[A-Za-z0-9_\-]{35}",  # Google API key: 39 chars total
}

#: A whole key block, and -- separately -- one whose END never arrives.
#: `read_file` truncates at max_read_bytes before the guardrail sees anything,
#: so a key straddling the cap would otherwise reach the model as plain base64.
#: The BEGIN line is unambiguous, so matching forward from it costs no false
#: positives, which is the bar Stage 05 sets.
_PEM = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
_PEM_UNTERMINATED = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*", re.MULTILINE
)


def _shape_patterns() -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = []
    for shape in config.get().guardrail.key_shapes:
        if shape.startswith("-----BEGIN"):
            # Order matters: the terminated form first, so a complete block is
            # replaced as one match rather than swallowed to end of text.
            patterns.append(_PEM)
            patterns.append(_PEM_UNTERMINATED)
        else:
            tail = _PRECISE_TAILS.get(shape, _SHAPE_TAIL)
            patterns.append(re.compile(_LEFT_EDGE + re.escape(shape) + tail))
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
NOTICE_REDACTED_RESULT = (
    "Something in what I read looked like a credential, so I stripped it before "
    "reading the rest."
)
NOTICE_BLOCKED = (
    "This turn read a credential file, so I did not look anything up on the web."
)
NOTICE_EXTERNAL_OFF = (
    "I could not look that up: web lookups are switched off in config.toml."
)
NOTICE_WRITE_DECLINED = "You said no, so {path} was left as it was."
NOTICE_WRITE_UNATTENDED = (
    "{path} already exists and nobody was attached to confirm the overwrite, "
    "so it was left as it was."
)
