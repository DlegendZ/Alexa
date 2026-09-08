"""Milestone 10 follow-up: the agent knows what it can do, and where.

Asked "what can you do", the 2b wrote a confident paragraph about a marketplace
database, an email drafter and a grammar checker -- none of which exist -- and
did not mention that it can delete a file, which it now can. A question about
the machine's own state has to be answered by reading the machine.
"""

from __future__ import annotations

from sunday import fastpaths, tools as tool_registry
from sunday.tools import capabilities


def test_every_registered_tool_appears(cfg):
    cfg.files.roots = ["C:/Users/User/Documents/Sunday"]
    listed = capabilities.list_capabilities()
    for tool in tool_registry.all_tools():
        assert tool.name in listed, tool.name


def test_the_folders_are_named_not_merely_alluded_to(cfg):
    cfg.files.roots = ["C:/Users/User/Documents/Sunday", "E:/Work/Sunday"]
    listed = capabilities.list_capabilities()
    assert "C:/Users/User/Documents/Sunday" in listed
    assert "E:/Work/Sunday" in listed


def test_no_folders_configured_says_so_rather_than_saying_nothing(cfg):
    cfg.files.roots = []
    listed = capabilities.list_capabilities()
    assert "No folders are configured" in listed
    assert "config.toml" in listed


def test_the_external_tool_is_marked_as_leaving_the_machine(cfg):
    listed = capabilities.list_capabilities()
    line = next(l for l in listed.splitlines() if l.startswith("ask_external"))
    assert "leaves this machine" in line


def test_the_list_is_not_markdown(cfg):
    """A 2b copies the shape of what it read last, and a reply may be spoken."""
    cfg.files.roots = ["E:/Work/Sunday"]
    for line in capabilities.list_capabilities().splitlines():
        assert not line.lstrip().startswith(("-", "*", "#", "|"))
        assert "**" not in line


def test_the_question_never_reaches_the_model(cfg):
    """The fast path fires in code, so a 2b never gets the chance to invent."""
    for asked in (
        "what can you do?",
        "list your tools",
        "what tools do you have",
        "apa saja kemampuan kamu?",
        "kamu bisa apa aja?",
        "which folders can you read",
    ):
        shortcut = fastpaths.match(asked)
        assert shortcut is not None, asked
        assert shortcut.tool == "list_capabilities", asked


def test_an_ordinary_question_is_not_swallowed_by_the_pattern(cfg):
    """The capability pattern runs first, so a false positive costs a whole
    turn. Ordinary sentences must fall straight through it."""
    for asked in (
        "read the file gold.txt",
        "what is the weather in Jakarta",
        "tell me what you did yesterday",
        "can you check the price of gold",
    ):
        shortcut = fastpaths.match(asked)
        assert shortcut is None or shortcut.tool != "list_capabilities", asked


def test_a_multi_line_tool_result_gets_the_no_list_reminder(cfg):
    """The no-markdown rule sits in the system prompt, which is the furthest
    thing from a 2b's attention by the time it answers. The reminder has to sit
    next to the thing that triggers it."""
    from sunday.agent import loop
    from sunday.state import Result

    listing = Result("list_dir", {}, "folder:\na.txt  5 bytes\nb.txt  6 bytes", "private")
    assert loop.list_instruction({"tool_results": [listing]}) is not None

    one_line = Result("get_weather", {}, "Jakarta: 31C", "public")
    assert loop.list_instruction({"tool_results": [one_line]}) is None
    assert loop.list_instruction({}) is None


def test_a_failed_call_is_told_it_may_try_something_else(cfg, tmp_path):
    """A refusal that is never acted on is a turn that gives up. Asked to move
    a file, the model read it, listed two folders, read it again, listed a
    third, ran out of budget and told the user to do it themselves -- never
    reaching the tool that does the job."""
    from sunday.agent import prompts
    from sunday.agent.llm import Reply, ToolCall
    from sunday.runtime import Runtime

    from tests.test_review_fixes import NoMemory, Scripted

    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]

    agent = Scripted(
        [
            Reply(tool_calls=[ToolCall("read_file", {"path": str(root / "gone.txt")})]),
            Reply(content="I could not read that file."),
        ]
    )
    Runtime(cfg, agent=agent, memory=NoMemory()).run_turn("read my note")  # type: ignore[arg-type]

    # The round after the failure sees the hint; the first round does not.
    assert prompts.RETRY_HINT not in [m.get("content") for m in agent.seen[0]]
    assert prompts.RETRY_HINT in [
        m.get("content") for m in agent.seen[1] if isinstance(m, dict)
    ]


def test_the_retry_hint_is_said_once_not_once_per_failure(cfg, tmp_path):
    """Repeating it every round is how a 2b ends up sending the same broken
    call until the cap stops it."""
    from sunday.agent import prompts
    from sunday.agent.llm import Reply, ToolCall
    from sunday.runtime import Runtime

    from tests.test_review_fixes import NoMemory, Scripted

    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]

    missing = ToolCall("read_file", {"path": str(root / "gone.txt")})
    agent = Scripted(
        [
            Reply(tool_calls=[missing]),
            Reply(tool_calls=[missing]),
            Reply(content="I could not read it."),
        ]
    )
    Runtime(cfg, agent=agent, memory=NoMemory()).run_turn("read my note")  # type: ignore[arg-type]

    last = [m.get("content") for m in agent.seen[-1] if isinstance(m, dict)]
    assert last.count(prompts.RETRY_HINT) == 1


def test_a_compound_message_gets_no_fast_path(cfg):
    """A fast path answers the clause it matched and nothing else, and the
    model reads a result already in the transcript as the turn being done. So
    "the price of gold and the weather in Jakarta" came back about the weather
    alone. Saying so in a system line was tried first and did not hold."""
    for asked in (
        "what is the price of gold and what is the weather in Jakarta",
        "read gold.txt and tell me the weather in Jakarta",
        "get the gold price then write it to a file",
        "cek cuaca Jakarta dan harga emas",
        "weather in Jakarta; price of gold",
    ):
        assert fastpaths.match(asked) is None, asked


def test_a_single_clause_still_gets_its_fast_path(cfg):
    assert fastpaths.match("weather in Jakarta").tool == "get_weather"
    assert fastpaths.match("price of gold").tool == "get_asset_price"
    assert fastpaths.match("weather in Jakarta now").args == {"city": "Jakarta"}


def test_a_city_whose_name_contains_a_stop_word_survives(cfg):
    """The same left-edge bug the guardrail already learned once: without a
    word boundary the "and" branch cut inside the word and "weather in
    Thailand" asked for the weather in Thail."""
    assert fastpaths.match("weather in Thailand").args == {"city": "Thailand"}
    assert fastpaths.match("weather in Poland").args == {"city": "Poland"}
