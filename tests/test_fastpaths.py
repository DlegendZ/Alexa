"""Stage 04's insurance policy: the two most common asks never depend on a 2b
extracting an argument correctly."""

from __future__ import annotations

import pytest

from sunday import fastpaths


@pytest.mark.parametrize(
    "task,city",
    [
        ("what's the weather in Jakarta", "Jakarta"),
        ("weather for Bandung please", "Bandung"),
        ("check the weather at Kuala Lumpur right now", "Kuala Lumpur"),
        ("Weather in New York today", "New York"),
    ],
)
def test_weather_phrasings(task, city):
    assert fastpaths.match(task) == fastpaths.FastPath("get_weather", {"city": city})


@pytest.mark.parametrize(
    "task,symbol",
    [
        ("how much is gold", "XAU"),
        ("what is the price of silver", "XAG"),
        ("price of bitcoin", "BTC"),
        ("how much is ETH right now", "ETH"),
        ("what's the cost of copper", "HG"),
    ],
)
def test_price_phrasings(task, symbol):
    assert fastpaths.match(task) == fastpaths.FastPath(
        "get_asset_price", {"symbol": symbol}
    )


@pytest.mark.parametrize(
    "task",
    [
        "how much is rent this month",
        "what did I say about the weather",
        "hello there",
        "price check my notes file",
        "what is the weather like",
    ],
)
def test_it_stays_out_of_the_way_otherwise(task):
    assert fastpaths.match(task) is None


def test_the_fast_path_result_reaches_the_agent(cfg, monkeypatch):
    from sunday.agent.llm import Reply
    from sunday.runtime import Runtime
    from sunday.tools import weather

    monkeypatch.setattr(
        weather.net, "get_json", lambda *a, **k: (_ for _ in ()).throw(
            weather.net.HttpError("offline in tests")
        )
    )

    class Agent:
        def __init__(self):
            self.seen: list[list] = []

        def chat(self, messages, *, tools=None, think=False, max_tokens=None):
            self.seen.append(list(messages))
            return Reply(content="")

        def stream(self, messages, *, think=False):
            yield "It is warm."

        def preflight(self):
            return None

    class NoMemory:
        def retrieve(self, query, **kwargs):
            return []

        def add_turn(self, **kwargs):
            return None

        def add_session_summary(self, **kwargs):
            return None

        def count(self):
            return 0

    agent = Agent()
    runtime = Runtime(cfg, agent=agent, memory=NoMemory())  # type: ignore[arg-type]
    state = runtime.run_turn("what is the weather in Jakarta")

    assert [r.tool for r in state["tool_results"]] == ["get_weather"]
    assert state["tool_results"][0].args == {"city": "Jakarta"}
    # The model saw the result before it was asked to do anything.
    first_round = agent.seen[0]
    assert any(
        isinstance(m, dict) and m.get("role") == "tool" for m in first_round
    )


# -- the clauses a compound message is made of -----------------------------
#
# `fastpaths` refuses a compound message a shortcut; retrieval searches one
# as its clauses. Two consumers, one rule, so the connectives live in
# `sunday.clauses` and neither module owns a second copy of them -- the
# `mkdir`-on-`write_file` failure, which this repository has now paid for
# four times.


def test_a_compound_question_is_searched_as_its_clauses_too():
    from sunday import clauses

    assert clauses.queries_for(
        "what is my name and what did we do last session"
    ) == [
        "what is my name and what did we do last session",
        "what is my name",
        "what did we do last session",
    ]


def test_a_single_clause_question_is_searched_once():
    from sunday import clauses

    assert clauses.queries_for("what is my name") == ["what is my name"]


@pytest.mark.parametrize(
    "task",
    [
        "where is the salt and pepper",
        "read notes.txt and a.txt",
        "gold and silver",
    ],
)
def test_a_connective_joining_two_nouns_is_not_two_questions(task):
    """`and` between list items is not a second instruction.

    Splitting there costs an embedding and hands back the same documents, but
    the wider damage is a one-word fragment as a query: "pepper" is close to
    nothing and near enough to anything short. A split is taken only when
    every piece of it is long enough to be a question on its own.
    """
    from sunday import clauses

    assert clauses.queries_for(task) == [task]


def test_both_consumers_read_the_same_connectives():
    """A second copy of this list is a rule that half-applies.

    `fastpaths` used to hold the only `_COMPOUND`. The moment retrieval needed
    the same fact, the choice was one home or two that drift -- and the house
    rule is one.
    """
    from sunday import clauses

    assert fastpaths.match("what can you do and what is the weather in Jakarta") is None
    assert clauses.is_compound("what can you do and what is the weather in Jakarta")
