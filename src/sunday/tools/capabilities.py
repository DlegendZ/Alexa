"""`list_capabilities` -- the tool that answers "what can you do?".

A 2b asked what it can do will not read its own bound schemas and report them.
It writes the most plausible-sounding paragraph instead, and the paragraph is
confident and wrong: asked this on a live build it claimed a marketplace
database, an email drafter and a grammar checker, none of which exist, while
failing to mention that it can delete a file, which does.

That is not a prompt-wording problem. "What are your tools" is a question about
the machine's own state, exactly like "what is in this folder", and the fix is
the same one the rest of this codebase already uses: make it a tool call, so
the answer is read off the registry rather than recalled from a hunch. The
fast path in `sunday.fastpaths` fires it before the model gets a vote, for the
same reason the weather one exists.

Labelled `private`, because the list of folders it may open is a map of your
machine and has no business in an outgoing search query.

Written in the second person throughout, and with no name in it. The result is
a script the model relays, and a name written down here is a name that
disagrees with `[assistant] name` the day that changes -- note 83's rule, in
the one tool whose whole output is prose about itself.
"""

from __future__ import annotations

from sunday import config
from sunday.tools import Tool, all_tools, register

#: Named here rather than described, so the sentence the model reads matches
#: the tool name it would have to call.
_SELF = "list_capabilities"


def list_capabilities() -> str:
    """Everything the registry actually holds, plus the folders it may open.

    Built from `all_tools()` rather than from the bound list: the question is
    "what can you do", not "what may you do this turn". When the web door has
    been bolted the runtime says so separately, in its own system line and its
    own notice -- that is a per-turn fact, and burying it in a capability list
    would make it look permanent.
    """
    cfg = config.get()

    # Written as plain sentences, never as a bulleted list. A 2b copies the
    # shape of whatever it read last, so a list of dashes here comes back as a
    # reply full of dashes -- in a reply that may be read aloud, where every
    # one of them gets spoken. Same trap as the retrieved-memory framing.
    lines = ["These are the tools you have, read from the registry."]
    for tool in sorted(all_tools(), key=lambda t: t.name):
        where = "leaves this machine" if tool.scope == "external" else "on this machine"
        first = tool.description.split(". ")[0].rstrip(".")
        lines.append(f"{tool.name} ({where}) {first[0].lower() + first[1:]}.")

    if cfg.files.roots:
        listed = " and ".join(
            f"{root.label} ({root.path})" for root in cfg.files.entries()
        )
        lines.append(
            f"The folders you can read, write and delete inside are {listed}. "
            f"Any other path is refused before the disk is touched, and the "
            f"user can add folders under [files] roots in config.toml."
        )
    else:
        lines.append(
            "No folders are configured, so every file path is refused. The "
            "user can add one under [files] roots in config.toml."
        )

    lines.append(
        f"You will make at most {cfg.limits.tool_calls} tool calls in one turn "
        f"and at most {cfg.external.max_hops} web lookups."
    )
    lines.append(
        "Relay this in plain sentences. Do not add abilities that are not "
        "here, do not leave any out, and do not write it as a list."
    )
    return "\n".join(lines)


register(
    Tool(
        name=_SELF,
        description=(
            "List the tools you actually have and the folders you are allowed "
            "to open. Call this whenever the user asks what you can do, what "
            "your tools are, or which folders you can reach. Never answer "
            "those from memory."
        ),
        parameters={"type": "object", "properties": {}},
        fn=list_capabilities,
        provenance="private",
    )
)
