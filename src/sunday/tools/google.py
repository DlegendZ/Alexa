r"""Calendar and mail -- Google, read-only, and read-only by construction.

The scopes requested are `calendar.readonly` and `gmail.readonly`. That is not
a promise about what the code does; it is a fact about what the token is
allowed to do, checked by Google rather than by this file. An assistant that
can send mail is a different risk conversation, and this one does not have it.

Consent happens once, outside a turn:

    .venv\Scripts\sunday-google.exe

Doing it inside a tool call would block the turn on a browser window that may
never open. So a missing token is a refusal that says which command to run --
the same rule every other refusal here follows.

Plain HTTP rather than `google-api-python-client`, which brings a client
library, a discovery cache and two transitive auth stacks to make four
requests. The same argument as the canceller being numpy.

Both tools are labelled `private`. That label is the mechanism, not a comment:
`AIRLOCK_VISIBLE` is `user` and `public`, so nothing here can reach the
composer that writes an outgoing query, however the turn goes.
"""

from __future__ import annotations

import base64
import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sunday import config, guardrail, net
from sunday.tools import Tool, register

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
GMAIL_LIST_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
GMAIL_GET_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/{id}"

#: Read-only, both of them, and this list is the whole of what the token can
#: ever do. Widening it is a decision, not a detail.
SCOPES = (
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
)

#: Default and hard cap for a mail search, and how much of one message the
#: agent may see. A mailbox is unbounded and a context window is not.
MAIL_DEFAULT = 5
MAIL_CAP = 20
BODY_CAP = 4000

#: How many events one call may return. A year of a busy calendar would fill
#: the window on its own.
EVENT_CAP = 50

#: Refresh a little early. An access token that expires between the check and
#: the request fails as a 401, which reads to the model as "no calendar".
EXPIRY_MARGIN_S = 60

RUN_AUTH = (
    "Ask the user to run sunday-google once in a terminal to sign in to "
    "Google; it only has to happen the first time."
)

_access: dict[str, Any] = {"token": "", "expires": 0.0}


# -- the token ------------------------------------------------------------


def _client() -> tuple[str, str] | None:
    if config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET:
        return config.GOOGLE_CLIENT_ID, config.GOOGLE_CLIENT_SECRET
    return None


def load_token() -> dict[str, Any] | None:
    try:
        return json.loads(config.GOOGLE_TOKEN_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_token(token: dict[str, Any]) -> None:
    path = config.GOOGLE_TOKEN_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(token, indent=2), encoding="utf-8")


def access_token() -> str:
    """A live access token, refreshed from the stored refresh token.

    Raises `net.HttpError` with a message already fit to hand to the model,
    because that is what every caller here does with it.
    """
    now = time.time()
    if _access["token"] and _access["expires"] > now + EXPIRY_MARGIN_S:
        return str(_access["token"])

    client = _client()
    if client is None:
        raise net.HttpError(
            "no Google client is configured. Ask the user to put "
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in the .env file at the "
            "project root, then run sunday-google once."
        )
    stored = load_token()
    if not stored or not stored.get("refresh_token"):
        raise net.HttpError(f"nobody has signed in to Google yet. {RUN_AUTH}")

    client_id, client_secret = client
    payload = net.post_json(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": stored["refresh_token"],
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    token = payload.get("access_token")
    if not token:
        raise net.HttpError(
            f"the saved Google sign-in is no longer accepted. {RUN_AUTH}"
        )
    _access["token"] = token
    _access["expires"] = now + float(payload.get("expires_in", 3600))
    return str(token)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token()}"}


# -- calendar -------------------------------------------------------------


def _day(value: str, *, fallback: datetime) -> datetime:
    """Accept a date, a datetime, or nothing.

    The model writes dates three ways and two of them are `2026-09-09` and
    `2026-09-09T00:00:00Z`. Refusing either would be a refusal about
    punctuation.
    """
    text = (value or "").strip()
    if not text:
        return fallback
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return fallback


def calendar_read(start: str = "", end: str = "") -> str:
    """Events between two times. Descriptions are dropped on purpose."""
    now = datetime.now(timezone.utc)
    begins = _day(start, fallback=now)
    finishes = _day(end, fallback=begins + timedelta(days=7))
    if begins.tzinfo is None:
        begins = begins.replace(tzinfo=timezone.utc)
    if finishes.tzinfo is None:
        finishes = finishes.replace(tzinfo=timezone.utc)
    if finishes <= begins:
        finishes = begins + timedelta(days=1)

    try:
        payload = net.get_json(
            CALENDAR_URL,
            params={
                "timeMin": begins.isoformat(),
                "timeMax": finishes.isoformat(),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": EVENT_CAP,
            },
            headers=_headers(),
            timeout=15,
        )
    except net.HttpError as exc:
        return f"refused: {exc} Say that plainly; do not guess what is in the calendar."

    events = payload.get("items") or []
    if not events:
        return (
            f"No events between {begins.date()} and {finishes.date()}. "
            "The calendar was read; it is empty for that range."
        )

    lines = []
    for event in events:
        # The description is the field most likely to hold something you would
        # not want read out loud in a room with other people in it, and it is
        # never the answer to "what is on today".
        when = _when(event)
        where = (event.get("location") or "").strip()
        summary = (event.get("summary") or "(no title)").strip()
        lines.append(f"{when} — {summary}" + (f" — {where}" if where else ""))
    return "\n".join(lines)


def _when(event: dict[str, Any]) -> str:
    start = event.get("start") or {}
    end = event.get("end") or {}
    if start.get("date"):
        return f"{start['date']} (all day)"
    begins = str(start.get("dateTime", ""))[:16].replace("T", " ")
    finishes = str(end.get("dateTime", ""))[11:16]
    return f"{begins}–{finishes}" if finishes else begins


# -- mail -----------------------------------------------------------------


def _decode(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data + "===").decode("utf-8", "replace")
    except (ValueError, TypeError):
        return ""


def _body(part: dict[str, Any]) -> str:
    """The first text/plain part, depth first. HTML is not unwrapped: a
    message that is only HTML is reported as such rather than handed over as
    markup for the model to read tags out of."""
    mime = part.get("mimeType", "")
    body = part.get("body") or {}
    if mime == "text/plain" and body.get("data"):
        return _decode(body["data"])
    for child in part.get("parts") or []:
        found = _body(child)
        if found:
            return found
    return ""


def _header(message: dict[str, Any], name: str) -> str:
    for header in (message.get("payload") or {}).get("headers") or []:
        if header.get("name", "").lower() == name.lower():
            return str(header.get("value", ""))
    return ""


def mail_search(query: str = "", limit: int = MAIL_DEFAULT) -> str:
    """Search the mailbox and return what was found, scrubbed."""
    try:
        wanted = max(1, min(int(limit), MAIL_CAP))
    except (TypeError, ValueError):
        wanted = MAIL_DEFAULT

    try:
        listing = net.get_json(
            GMAIL_LIST_URL,
            params={"q": query or "", "maxResults": wanted},
            headers=_headers(),
            timeout=15,
        )
    except net.HttpError as exc:
        return f"refused: {exc} Say that plainly; do not guess what the mail said."

    ids = [item.get("id") for item in (listing.get("messages") or []) if item.get("id")]
    if not ids:
        return f"No messages matched {query!r}. The mailbox was searched; nothing matched."

    found: list[str] = []
    redactions = 0
    for message_id in ids[:wanted]:
        try:
            message = net.get_json(
                GMAIL_GET_URL.format(id=message_id),
                params={"format": "full"},
                headers=_headers(),
                timeout=15,
            )
        except net.HttpError:
            continue
        body = _body(message.get("payload") or {})[:BODY_CAP]
        # Before the agent sees it, not after. Mail is the most common way a
        # real credential arrives in a context window, and a key that has been
        # read is a key that can be repeated.
        body, hits = guardrail.redact(body)
        redactions += hits
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        found.append(
            f"From: {_header(message, 'From')}\n"
            f"Date: {_header(message, 'Date')}\n"
            f"Subject: {_header(message, 'Subject')}\n"
            f"{body}"
        )

    if not found:
        return "Nothing could be read: the messages matched but none of them opened."
    out = "\n\n---\n\n".join(found)
    if redactions:
        out += (
            f"\n\n[{redactions} thing(s) that looked like a credential were "
            "removed before you saw this]"
        )
    return out


# -- signing in, once -----------------------------------------------------


def authorise(*, open_browser: bool = True) -> int:
    """The desktop OAuth flow, run from a terminal and not from a turn.

    A loopback redirect, which is the flow Google documents for installed
    applications: the code comes back to a socket on this machine rather than
    being pasted, so it never reaches a clipboard or a shell history.
    """
    import http.server
    import secrets
    import urllib.parse
    import webbrowser

    client = _client()
    if client is None:
        print(
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are not set.\n"
            f"Put them in {config.ENV_PATH} and run this again."
        )
        return 1
    client_id, client_secret = client

    state = secrets.token_urlsafe(16)
    caught: dict[str, str] = {}

    class Catcher(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - the stdlib's spelling
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            caught.update({k: v[0] for k, v in params.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            done = "code" in caught and caught.get("state") == state
            self.wfile.write(
                b"<body style='font:16px system-ui;padding:40px'>"
                + (b"Signed in. You can close this tab." if done else b"Sign-in failed.")
                + b"</body>"
            )

        def log_message(self, *_args: Any) -> None:
            """Quiet. The console is showing the flow, not the web server."""

    server = http.server.HTTPServer(("127.0.0.1", 0), Catcher)
    redirect = f"http://127.0.0.1:{server.server_port}"
    url = AUTH_URL + "?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            # Without both of these Google returns no refresh token on a second
            # consent, and the whole point is that this happens once.
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )

    print("Opening your browser to sign in to Google.")
    print("If it does not open, paste this:\n")
    print(url + "\n")
    if open_browser:
        webbrowser.open(url)

    server.handle_request()
    server.server_close()

    if caught.get("state") != state:
        print("The reply did not carry the state we sent. Nothing was saved.")
        return 1
    code = caught.get("code")
    if not code:
        print(f"No code came back ({caught.get('error', 'no reason given')}).")
        return 1

    try:
        payload = net.post_json(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect,
            },
            timeout=20,
        )
    except net.HttpError as exc:
        print(f"Could not exchange the code: {exc}")
        return 1

    if not payload.get("refresh_token"):
        print("Google returned no refresh token. Revoke the app's access and try again.")
        return 1

    save_token(
        {
            "refresh_token": payload["refresh_token"],
            "scopes": list(SCOPES),
            "saved": time.time(),
        }
    )
    print(f"Signed in. The refresh token is at {config.GOOGLE_TOKEN_PATH}.")
    print("That file is on the credential list: reading it shuts the web door for a turn.")
    return 0


def main() -> int:
    return authorise()


if __name__ == "__main__":  # pragma: no cover - an entry point, not a branch
    raise SystemExit(main())


# -- registration ---------------------------------------------------------

register(
    Tool(
        name="calendar_read",
        description=(
            "Read the user's Google Calendar between two times. Both are "
            "optional: with neither, it reads the next seven days. Returns the "
            "title, time and location of each event. Runs on this machine and "
            "nothing about it can be searched for on the web."
        ),
        parameters={
            "type": "object",
            "properties": {
                "start": {
                    "type": "string",
                    "description": "ISO date or datetime, e.g. 2026-09-09. Default: now.",
                },
                "end": {
                    "type": "string",
                    "description": "ISO date or datetime. Default: seven days after start.",
                },
            },
            "required": [],
        },
        fn=calendar_read,
        provenance="private",
    )
)

register(
    Tool(
        name="mail_search",
        description=(
            "Search the user's Gmail and read what matches. The query is Gmail "
            "search syntax, so 'from:bank newer_than:7d' works. Read-only. "
            "Runs on this machine and nothing about it can be searched for on "
            "the web."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Gmail search query, e.g. 'from:hr subject:contract'",
                },
                "limit": {
                    "type": "integer",
                    "description": f"How many messages, {MAIL_DEFAULT} by default, {MAIL_CAP} at most.",
                },
            },
            "required": ["query"],
        },
        fn=mail_search,
        provenance="private",
    )
)
