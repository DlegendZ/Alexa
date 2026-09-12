"""The rules that were enforced only by the code currently being right.

An audit of the package against its own documents found four load-bearing
invariants with no test behind them. Each is written down in `CLAUDE.md` under
"Things that bit us", each was correct in the code at the time of writing, and
each would have survived the next refactor without anything going red. That is
the exact condition this repository has already been burned by twice --
`mkdir(parents=True)` surviving on `write_file` after the other two lost it, and
the compound guard covering two fast paths out of three.

So these tests enumerate paths rather than reciting incidents, which is the
house rule: *when a rule says never, the test enumerates the paths, not the bug
reports.*
"""

from __future__ import annotations

import pytest

from sunday import runtime as runtime_module
from sunday import tools as tool_registry
from sunday.agent import prompts
from sunday.memory import budget, session as memory_session
from sunday.state import AIRLOCK_VISIBLE


# -- the door's tool list --------------------------------------------------


def test_every_tool_that_takes_a_path_is_watched_by_the_door():
    """`PATH_TOOLS` is a hand-written literal and the web door keys off it.

    A tool that takes a `path` and is not in that set can read a credential
    file without tainting the turn -- the door stays open, the next lookup
    goes out, and nothing anywhere says so. The set is right today; the point
    is that the twelfth-and-a-half tool does not silently fall outside it.
    """
    takes_a_path = {
        tool.name
        for tool in tool_registry.all_tools()
        if set((tool.parameters or {}).get("properties", {})) & set(runtime_module.PATH_ARGS)
    }
    assert takes_a_path == set(runtime_module.PATH_TOOLS), (
        "PATH_TOOLS and the registry disagree. Every tool whose schema names a "
        "path argument has to be watched, or reading a credential through it "
        "leaves the web door open for the rest of the turn.\n"
        f"  in the registry, not watched: {sorted(takes_a_path - set(runtime_module.PATH_TOOLS))}\n"
        f"  watched, not in the registry: {sorted(set(runtime_module.PATH_TOOLS) - takes_a_path)}"
    )


def test_the_path_argument_names_have_one_home():
    """They were two identical tuples in two modules. The copy in `runtime`
    is the one the door keys off, so a divergence is a hole rather than an
    inconsistency."""
    assert runtime_module.PATH_ARGS is memory_session.PATH_ARGS


# -- what the airlock may see ----------------------------------------------

#: Every registered tool, classified once. The point of writing it this way
#: rather than as a set of "yours" is that a *new* tool fails this test until
#: somebody decides which side it is on -- which is what the old version, a
#: bare list of four names with a `continue`, could not do.
READS_YOUR_OWN_THINGS = {
    "read_file",
    "list_dir",
    "calendar_read",
    "mail_search",
}
DOES_NOT = {
    "write_file",
    "move_file",
    "copy_file",
    "delete_file",
    "get_weather",
    "get_asset_price",
    "ask_external",
    "list_capabilities",
}


def test_every_tool_is_classified():
    """The enumeration itself. A tool that is neither listed nor excluded is a
    tool nobody decided about, and this is where that gets noticed."""
    registered = {tool.name for tool in tool_registry.all_tools()}
    assert registered == READS_YOUR_OWN_THINGS | DOES_NOT, (
        "a tool was added or removed without saying whether it reads the "
        "user's own things:\n"
        f"  unclassified: {sorted(registered - (READS_YOUR_OWN_THINGS | DOES_NOT))}\n"
        f"  classified but gone: {sorted((READS_YOUR_OWN_THINGS | DOES_NOT) - registered)}"
    )


@pytest.mark.parametrize("name", sorted(READS_YOUR_OWN_THINGS))
def test_nothing_that_reads_your_own_things_is_visible_to_the_airlock(name):
    """`AIRLOCK_VISIBLE` is `user` and `public`. Anything that reads what is
    yours must be labelled `private` or stronger, or its contents can reach the
    context that composes an outgoing query."""
    tool = tool_registry.get(name)
    assert tool is not None, f"{name} is no longer registered"
    assert tool.provenance not in AIRLOCK_VISIBLE, (
        f"{name} reads the user's own things and is labelled "
        f"{tool.provenance!r}, which the airlock can see"
    )


# -- the loop's own instructions -------------------------------------------

#: Everything the loop can push into a turn to steer the *model*, as opposed to
#: facts about the turn. All of it must be gone before the final pass: the 2b
#: answered *with* these once, in sentences nobody wrote.
SCAFFOLDING = (
    prompts.SECOND_CHANCE,
    prompts.RETRY_HINT,
    prompts.FINISH_IT,
    prompts.FAST_PATH_PARTIAL,
)


def test_every_scaffold_is_cleared_by_the_same_mechanism():
    """`TurnContext.scaffold` in, `clear_scaffolding` out, keyed by identity.

    Exercised directly rather than through a turn, so it covers all four
    nudges instead of whichever two a scripted conversation happens to fire.
    """
    from sunday import telemetry

    ctx = runtime_module.TurnContext(telemetry.TurnLog("test", "text"))
    ctx.messages = [{"role": "system", "content": "a fact about this turn"}]
    for text in SCAFFOLDING:
        ctx.scaffold(text)

    assert len(ctx.messages) == 1 + len(SCAFFOLDING)
    ctx.clear_scaffolding()

    left = "\n".join(m.get("content", "") for m in ctx.messages)
    for text in SCAFFOLDING:
        assert text not in left, (
            "a loop instruction survived into the final pass. The 2b answers "
            "with these -- 'I cannot use tools in this session' was one of "
            "them, delivered as its own words."
        )
    assert "a fact about this turn" in left, (
        "clear_scaffolding removed a fact as well. Facts about the turn stay; "
        "instructions about how to think go."
    )


def test_the_reservation_counts_every_nudge_the_loop_can_add():
    """`overhead_tokens` is paid every turn whether or not a tool is called.

    The existing reservation test counts three of the four scaffolds; this one
    counts all four, so the fourth cannot be the one that quietly overruns
    `num_ctx` -- which Ollama answers by dropping the oldest messages without
    saying so.
    """
    from sunday import config

    cfg = config.get()
    nudges = sum(budget.count(text) for text in SCAFFOLDING)
    assert nudges < cfg.models.overhead_tokens, (
        f"the loop's four nudges alone come to {nudges} tokens against an "
        f"overhead reservation of {cfg.models.overhead_tokens}"
    )


# -- the suite's own footprint --------------------------------------------


def test_no_test_can_reach_the_real_long_term_store(tmp_path_factory):
    """A test run must not file turns into the user's own memory.

    `conftest` isolated the file sandbox and the config and left the store
    alone, so every `Runtime(cfg, agent=agent)` built without an explicit
    `memory=` opened `config.MEMORY_DIR` -- the real one. Two call sites did
    that, and running the suite filed turns like "read my .env / ok" into a
    person's long-term memory, where the next session would recall them.

    Enumerated rather than recited: the home, the store, the logs, and a
    default-constructed store, which is the path the two call sites actually
    took. Naming only the two would leave the third free to appear.
    """
    from sunday import config
    from sunday.memory import LongTermMemory

    base = tmp_path_factory.getbasetemp()
    for name in ("SUNDAY_HOME", "MEMORY_DIR", "LOG_DIR"):
        path = getattr(config, name)
        assert base == path or base in path.parents, (
            f"config.{name} is {path}, outside pytest's temp root. A test run "
            f"would write to the real one."
        )
    default = LongTermMemory()._path
    assert base in default.parents, (
        f"LongTermMemory() with no path opens {default}. That is the store a "
        f"Runtime built without memory= will write to."
    )
