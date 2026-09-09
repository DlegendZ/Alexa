"""Calendar and mail.

The interesting half of milestone 13 is not the HTTP. It is that two new tools
now read the most private things on the machine, and the guarantee the whole
design rests on has to survive them: what they return can never reach the
composer that writes an outgoing query.

That guarantee is structural rather than careful. `AIRLOCK_VISIBLE` is `user`
and `public`; these are `private`. So the tests here check the label, the
scrubbing that happens before the agent sees a message at all, and the three
things the specification says to drop or cap.
"""

from __future__ import annotations

import json

import pytest

from sunday import config, guardrail
from sunday import tools as tool_registry
from sunday.state import AIRLOCK_VISIBLE
from sunday.tools import google


@pytest.fixture
def signed_in(monkeypatch):
    """A live access token, without a browser or a network."""
    monkeypatch.setattr(google, "_access", {"token": "test-token", "expires": 1e12})
    return "test-token"


# -- the guarantee ---------------------------------------------------------


def test_nothing_that_reads_your_own_things_is_visible_to_the_airlock():
    """The rule, enumerated rather than sampled.

    A tool that reads the user's files, calendar or mailbox must carry a label
    the airlock cannot see. Naming the two new ones would test the two new
    ones; the point is that the next one is covered before it is written.
    """
    yours = {"read_file", "list_dir", "calendar_read", "mail_search"}
    for tool in tool_registry.all_tools():
        if tool.name not in yours:
            continue
        assert tool.provenance not in AIRLOCK_VISIBLE, (
            f"{tool.name} is labelled {tool.provenance}, which the airlock can "
            "see -- its results could be composed into a query that leaves"
        )


def test_both_tools_run_on_this_machine():
    """Scope decides whether the orb goes teal and whether the door applies.
    Google is the user's own account, reached from here: it is not the web
    door, and labelling it `external` would put the calendar behind the
    airlock, which cannot compose a query out of anything private anyway."""
    for name in ("calendar_read", "mail_search"):
        assert tool_registry.get(name).scope == "local"


def test_the_token_file_is_on_the_credential_list():
    """It holds a refresh token, so the agent reading it shuts the door."""
    assert guardrail.is_secret_path(config.GOOGLE_TOKEN_PATH)
    assert guardrail.is_secret_path("google_token.json")


def test_the_scopes_are_read_only():
    """Read-only by construction, not by promise: Google enforces this, and
    the enforcement is only as good as the strings asked for."""
    assert google.SCOPES
    for scope in google.SCOPES:
        assert scope.endswith(".readonly"), scope


# -- refusals --------------------------------------------------------------


def every_refusal(monkeypatch) -> dict[str, str]:
    """Every way these two can say no. Enumerated, because a rule that says
    *never* is tested by listing the paths and not the bug reports -- this
    repo has now paid twice for testing the reported ones."""
    monkeypatch.setattr(google, "_access", {"token": "", "expires": 0.0})
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "")
    no_client = {
        "calendar with no client configured": google.calendar_read(),
        "mail with no client configured": google.mail_search("anything"),
    }

    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "id")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setattr(google, "load_token", lambda: None)
    not_signed_in = {
        "calendar with nobody signed in": google.calendar_read(),
        "mail with nobody signed in": google.mail_search("anything"),
    }

    monkeypatch.setattr(google, "load_token", lambda: {"refresh_token": "stale"})
    monkeypatch.setattr(
        google.net, "post_json", lambda *a, **k: {"error": "invalid_grant"}
    )
    stale = {
        "calendar with a token Google no longer accepts": google.calendar_read(),
        "mail with a token Google no longer accepts": google.mail_search("anything"),
    }

    # And the same thing the way it actually arrives. Google answers a refused
    # refresh token with a 400, and `net.request` raises on any 4xx before the
    # body is ever looked at -- so the branch above, which needs a 200 carrying
    # an error field, is a path Google does not take. Mocking the polite one
    # and not this one is how the refusal a user really met was `HTTP 400 from
    # oauth2.googleapis.com`.
    def refused(*a, **k):
        raise google.net.HttpError("HTTP 400 from oauth2.googleapis.com")

    monkeypatch.setattr(google.net, "post_json", refused)
    expired = {
        "calendar with an expired sign-in": google.calendar_read(),
        "mail with an expired sign-in": google.mail_search("anything"),
    }
    return {**no_client, **not_signed_in, **stale, **expired}


def test_every_refusal_tells_the_model_what_to_say(monkeypatch):
    """A refusal string is a script the model relays, not a status code. A bare
    one is how "path is outside the configured roots" reached a user as "C:
    isn't mounted"."""
    for why, out in every_refusal(monkeypatch).items():
        assert out.startswith("refused:"), (why, out)
        assert "user" in out.lower(), (why, out)


def test_no_refusal_writes_down_a_name(monkeypatch):
    """The assistant answers to whatever `[assistant] name` says. These are
    sentences it speaks, so a name in one is a name that goes stale in a voice
    that is now called something else.

The sign-in command is exempt and is the reason this test is worded around
    a command rather than a substring: the *program* is called sunday -- the
    package, the process, the data directory and the entry points -- and only
    the person it plays is called anything else. Telling the user to run a
    command is telling them the program's name, which is correct.
    """
    for why, out in every_refusal(monkeypatch).items():
        prose = out.replace("python -m sunday.tools.google", "")
        assert "sunday" not in prose.lower(), (why, out)


def test_a_missing_sign_in_names_the_command_to_run(monkeypatch):
    """Consent cannot happen inside a turn -- it needs a browser that may never
    open -- so the refusal has to say which command does it. A refusal with no
    way to act on it is the same failure as a prompt with no way to answer."""
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "id")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setattr(google, "_access", {"token": "", "expires": 0.0})
    monkeypatch.setattr(google, "load_token", lambda: None)
    assert "python -m sunday.tools.google" in google.calendar_read()


def test_an_expired_sign_in_names_the_command_too(monkeypatch):
    """The refusal that happens weekly, not the one that happens once.

    Google's testing mode expires a refresh token after seven days by design,
    and refuses it with a 400 -- which `net.request` turns into `HTTP 400 from
    oauth2.googleapis.com` before `access_token` can say anything useful. A
    status code is not an instruction, and this is the instruction the user
    needs most often.
    """
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "id")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setattr(google, "_access", {"token": "", "expires": 0.0})
    monkeypatch.setattr(google, "load_token", lambda: {"refresh_token": "old"})

    def refused(*a, **k):
        raise google.net.HttpError("HTTP 400 from oauth2.googleapis.com")

    monkeypatch.setattr(google.net, "post_json", refused)
    out = google.calendar_read()
    assert "python -m sunday.tools.google" in out
    assert "seven days" in out


# -- calendar --------------------------------------------------------------


def test_the_description_never_leaves_the_calendar(monkeypatch, signed_in):
    """The field most likely to hold something you would not want read out
    loud in a room with other people in it, and never the answer to "what is on
    today"."""
    secret = "the divorce lawyer, 400 pounds, do not tell anyone"
    monkeypatch.setattr(
        google.net,
        "get_json",
        lambda *a, **k: {
            "items": [
                {
                    "summary": "Meeting",
                    "description": secret,
                    "location": "Room 3",
                    "start": {"dateTime": "2026-09-09T10:00:00Z"},
                    "end": {"dateTime": "2026-09-09T11:00:00Z"},
                }
            ]
        },
    )
    out = google.calendar_read("2026-09-09", "2026-09-10")
    assert "Meeting" in out
    assert "Room 3" in out
    assert "divorce" not in out
    assert secret not in out


def test_an_empty_range_says_it_was_read(monkeypatch, signed_in):
    """Three outcomes look identical from outside: nothing on, nothing read,
    and a lookup that failed. Only the last is a fault, so the first says so
    in words -- the same reason the trace exists."""
    monkeypatch.setattr(google.net, "get_json", lambda *a, **k: {"items": []})
    out = google.calendar_read("2026-09-09", "2026-09-10")
    assert "was read" in out
    assert not out.startswith("refused:")


def test_a_date_or_a_datetime_both_work(monkeypatch, signed_in):
    """The model writes dates several ways. Refusing one of them would be a
    refusal about punctuation."""
    seen = {}

    def capture(url, **kwargs):
        seen.update(kwargs.get("params") or {})
        return {"items": []}

    monkeypatch.setattr(google.net, "get_json", capture)
    google.calendar_read("2026-09-09", "2026-09-10T18:00:00Z")
    assert seen["timeMin"].startswith("2026-09-09")
    assert seen["timeMax"].startswith("2026-09-10")


def test_a_backwards_range_is_not_an_empty_one(monkeypatch, signed_in):
    """An end before its start returns nothing from Google and reads to the
    user as an empty calendar, which is the wrong answer to a typo."""
    seen = {}

    def capture(url, **kwargs):
        seen.update(kwargs.get("params") or {})
        return {"items": []}

    monkeypatch.setattr(google.net, "get_json", capture)
    google.calendar_read("2026-09-10", "2026-09-09")
    assert seen["timeMax"] > seen["timeMin"]


# -- mail ------------------------------------------------------------------


def message(body: str, *, subject: str = "Hello") -> dict:
    import base64

    encoded = base64.urlsafe_b64encode(body.encode()).decode()
    return {
        "payload": {
            "headers": [
                {"name": "From", "value": "someone@example.com"},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Tue, 9 Sep 2026 10:00:00 +0000"},
            ],
            "mimeType": "text/plain",
            "body": {"data": encoded},
        }
    }


def mailbox(monkeypatch, bodies: list[str]):
    def get_json(url, **kwargs):
        if url == google.GMAIL_LIST_URL:
            return {"messages": [{"id": str(i)} for i in range(len(bodies))]}
        index = int(url.rsplit("/", 1)[-1])
        return message(bodies[index])

    monkeypatch.setattr(google.net, "get_json", get_json)


def test_a_key_in_a_message_is_stripped_before_the_agent_sees_it(monkeypatch, signed_in):
    """Mail is the most common way a real credential arrives in a context
    window, and a key that has been read is a key that can be repeated. The
    scrubber runs before the agent, not after it."""
    mailbox(monkeypatch, ["Here is the deploy key: ghp_aaaabbbbccccddddeeeeffffgggghhhh"])
    out = google.mail_search("deploy")
    assert "ghp_aaaabbbbccccddddeeeeffffgggghhhh" not in out
    assert guardrail.REDACTED in out
    # And it says so, because a redaction nobody is told about is a redaction
    # that looks like the mail not saying it.
    assert "credential" in out


def test_ordinary_prose_survives_the_scrubber(monkeypatch, signed_in):
    """The other half of every key-shape rule: a prefix that matches mid-word
    eats real words. A positive test alone would never see it."""
    mailbox(monkeypatch, ["The task-oriented risk-assessment is on the disk-image backup."])
    out = google.mail_search("task")
    assert guardrail.REDACTED not in out
    assert "task-oriented" in out


def test_the_limit_is_capped(monkeypatch, signed_in):
    """A mailbox is unbounded and a context window is not."""
    seen = {}

    def get_json(url, **kwargs):
        if url == google.GMAIL_LIST_URL:
            seen.update(kwargs.get("params") or {})
            return {"messages": []}
        return {}

    monkeypatch.setattr(google.net, "get_json", get_json)
    google.mail_search("anything", limit=500)
    assert seen["maxResults"] == google.MAIL_CAP


def test_a_nonsense_limit_falls_back_rather_than_failing(monkeypatch, signed_in):
    seen = {}

    def get_json(url, **kwargs):
        seen.update(kwargs.get("params") or {})
        return {"messages": []}

    monkeypatch.setattr(google.net, "get_json", get_json)
    google.mail_search("anything", limit="lots")
    assert seen["maxResults"] == google.MAIL_DEFAULT


def test_a_long_message_is_truncated(monkeypatch, signed_in):
    """Counting the body rather than the message: the headers are not capped
    and `example.com` has an x in it, which is a funnier way to fail a test
    than it is a useful one."""
    mailbox(monkeypatch, ["x" * (google.BODY_CAP * 3)])
    out = google.mail_search("anything")
    body = out.split("Subject: Hello\n", 1)[1]
    assert len(body) <= google.BODY_CAP


def test_nothing_matching_says_the_mailbox_was_searched(monkeypatch, signed_in):
    monkeypatch.setattr(google.net, "get_json", lambda *a, **k: {"messages": []})
    out = google.mail_search("nothing at all")
    assert "was searched" in out
    assert not out.startswith("refused:")


def test_the_token_is_stored_with_nothing_but_what_is_needed(tmp_path, monkeypatch):
    """A refresh token and the scopes it was granted. Not the access token,
    which expires in an hour, and not the client secret, which lives in .env."""
    path = tmp_path / "google_token.json"
    monkeypatch.setattr(config, "GOOGLE_TOKEN_PATH", path)
    google.save_token({"refresh_token": "r", "scopes": list(google.SCOPES), "saved": 1})
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert set(stored) == {"refresh_token", "scopes", "saved"}
    assert "access_token" not in stored
