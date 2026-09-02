"""`ask_external` -- the only way anything leaves this machine.

The function registered here is a declaration, not an implementation. The tool
loop intercepts this name before dispatch, because what the model passes is an
*intent*, not a query: the query itself is composed by the airlock, in a fresh
context the private half of the turn was never in. See sunday/airlock.py.
"""

from __future__ import annotations

from sunday.tools import Tool, register

NOT_DISPATCHED = (
    "error: ask_external must be handled by the airlock, not called directly"
)


def ask_external(intent: str) -> str:  # pragma: no cover - never dispatched
    return NOT_DISPATCHED


register(
    Tool(
        name="ask_external",
        description=(
            "Look something up on the open web: news, current events, public "
            "facts you do not know. Pass a short description of what you want "
            "to find out, e.g. 'current analyst view on gold'. This is the one "
            "tool that sends anything off this machine, so never put the "
            "user's files, mail, calendar or personal details in it."
        ),
        parameters={
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "description": "What you want to find out, in a few words",
                }
            },
            "required": ["intent"],
        },
        fn=ask_external,
        provenance="public",
        scope="external",
    )
)
