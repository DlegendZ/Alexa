"""Milestone 4: a private file plus a web search in one turn produces a query
with nothing private in it.

The guarantee is structural, so the test is too: it inspects the prompt the
query writer was actually handed, not just the query that came out.
"""

from __future__ import annotations

import pytest

from sunday import airlock, web
from sunday.agent import prompts
from sunday.agent.llm import Reply, ToolCall
from sunday.runtime import EXTERNAL_TOOL, Runtime
from sunday.state import Result, new_state

SECRET = "4600"
NOTE = f"I want to sell my gold when it hits {SECRET} an ounce."


def _state(results):
    state = new_state("what is my sell target and what do analysts say about gold", session_id="s", trace_id="t")
    state["tool_results"] = results
    state["context"] = "Earlier you told me your broker is Mandiri Sekuritas."
    return state


def test_private_results_are_never_assembled_into_the_prompt(cfg):
    state = _state(
        [
            Result("read_file", {"path": "gold.txt"}, NOTE, "private"),
            Result("get_asset_price", {"symbol": "XAU"}, "Gold (XAU): $4,370.90 USD", "public"),
        ]
    )
    messages = airlock.compose_prompt(state, "current analyst view on gold")
    blob = "\n".join(m["content"] for m in messages)

    assert SECRET not in blob  # the file's contents were never in the room
    assert NOTE not in blob
    assert "$4,370.90" in blob  # the public half may cross
    assert "Mandiri" not in blob  # retrieved memory never reaches the airlock
    # The user's own words for this turn are allowed, and are the only place
    # the word "sell" may legitimately appear.
    assert blob.count("sell") == 1


def test_the_composer_sees_only_user_and_public_labels(cfg):
    results = [
        Result("read_file", {}, "private thing", "private"),
        Result("mail_search", {}, "secret thing", "secret"),
        Result("get_weather", {}, "public thing", "public"),
    ]
    visible = airlock.visible_results(results)
    assert [r.provenance for r in visible] == ["public"]


def test_failed_public_results_are_not_forwarded_either(cfg):
    results = [Result("get_weather", {}, "error: timed out", "public", ok=False)]
    assert airlock.visible_results(results) == []


class ComposerAgent:
    """Answers the tool loop, and records what the airlock asked it."""

    def __init__(self, replies, query="gold price analyst outlook"):
        self.replies = list(replies)
        self.query = query
        self.airlock_prompts: list[str] = []

    def chat(self, messages, *, tools=None, think=False, max_tokens=None):
        if messages and messages[0].get("content") == prompts.AIRLOCK_SYSTEM:
            self.airlock_prompts.append("\n".join(m["content"] for m in messages))
            return Reply(content=self.query)
        return self.replies.pop(0) if self.replies else Reply(content="done")

    def stream(self, messages, *, think=False):
        yield "here is what I found"

    def preflight(self):
        return None


def test_a_private_file_and_a_web_search_in_one_turn(cfg, tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    (root / "gold.txt").write_text(NOTE, encoding="utf-8")
    cfg.files.roots = [str(root)]

    sent: list[str] = []

    def fake_run(query):
        sent.append(query)
        return web.WebResult("Analysts are split on gold.", hops=1)

    monkeypatch.setattr(web, "run", fake_run)

    agent = ComposerAgent(
        [
            Reply(
                tool_calls=[
                    ToolCall("read_file", {"path": str(root / "gold.txt")}),
                    ToolCall(EXTERNAL_TOOL, {"intent": "analyst view on gold"}),
                ]
            ),
            Reply(content="done"),
        ]
    )
    runtime = Runtime(cfg, agent=agent)  # type: ignore[arg-type]
    state = runtime.run_turn("what is my sell target, and what do analysts say")

    # The door stayed open -- an ordinary file is private, not secret.
    assert state["tainted"] is False
    assert state["blocked"] is False
    assert state["saw_private"] is True

    # And the query that left carried nothing from the file.
    assert sent == ["gold price analyst outlook"]
    assert SECRET not in sent[0]
    assert SECRET not in "\n".join(agent.airlock_prompts)

    # Both halves came back, and they were combined on this machine.
    kinds = [r.provenance for r in state["tool_results"]]
    assert kinds == ["private", "public"]


def test_an_empty_query_falls_back_to_the_users_own_words_not_the_intent(cfg):
    class Blank(ComposerAgent):
        def chat(self, messages, *, tools=None, think=False, max_tokens=None):
            if messages and messages[0].get("content") == prompts.AIRLOCK_SYSTEM:
                return Reply(content="   ")
            return Reply(content="done")

    state = _state([])
    query = airlock.compose(Blank([]), state, "sell target 4600 gold")  # type: ignore[arg-type]
    assert SECRET not in query
    assert query.startswith("what is my sell target")


def test_the_query_is_scrubbed_and_capped(cfg):
    cfg.external.query_max_chars = 30

    class Leaky(ComposerAgent):
        def chat(self, messages, *, tools=None, think=False, max_tokens=None):
            return Reply(content="ghp_16C7e42F292c6912E7710c838347Ae178B4a " + "gold " * 40)

    query = airlock.compose(Leaky([]), _state([]), "anything")  # type: ignore[arg-type]
    assert "ghp_" not in query
    assert len(query) <= 30


# -- the pipeline's own decisions -----------------------------------------


def test_coverage_decides_whether_a_fetch_is_needed():
    query = "gold price forecast 2026"
    good = "Gold price forecast for 2026: analysts see gold averaging higher."
    poor = "Sign in to continue. Cookies help us deliver our services."
    assert web.coverage(query, good) >= web.COVERAGE_ENOUGH
    assert web.coverage(query, poor) < web.COVERAGE_ENOUGH


def test_search_failure_degrades_to_a_stated_gap(cfg, monkeypatch):
    def boom(query, limit=5):
        raise RuntimeError("no network")

    monkeypatch.setattr(web, "search", boom)
    result = web.gather("anything")
    assert result.ok is False
    assert result.hops == 1
    assert "search failed" in result.text


def test_no_results_is_a_stated_gap_too(cfg, monkeypatch):
    monkeypatch.setattr(web, "search", lambda query, limit=5: [])
    result = web.gather("anything")
    assert result.ok is False
    assert "returned nothing" in result.text


def test_hops_never_exceed_the_cap(cfg, monkeypatch):
    cfg.external.max_hops = 1
    hits = [web.Hit("t", "https://example.com", "unrelated words here")]
    monkeypatch.setattr(web, "search", lambda query, limit=5: hits)

    def must_not_fetch(url):  # pragma: no cover - must not run
        raise AssertionError("fetched despite the hop cap")

    monkeypatch.setattr(web, "fetch", must_not_fetch)
    result = web.gather("something entirely different from the snippet")
    assert result.hops == 1


def test_summarise_degrades_without_a_key(cfg, monkeypatch):
    from sunday import config as config_module

    monkeypatch.setattr(config_module, "DEEPSEEK_API_KEY", "")
    out = web.summarise("q", "public material " * 10)
    assert out.startswith("public material")


def test_a_summariser_failure_still_returns_the_public_material(cfg, monkeypatch):
    monkeypatch.setattr(
        web, "gather", lambda q: web.WebResult("snippets here", hops=1, sources=[])
    )

    def boom(query, material):
        raise RuntimeError("deepseek unreachable")

    monkeypatch.setattr(web, "summarise", boom)
    result = web.run("q")
    assert result.ok is False
    assert "could not summarise" in result.text
    assert "snippets here" in result.text
