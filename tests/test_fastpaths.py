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
