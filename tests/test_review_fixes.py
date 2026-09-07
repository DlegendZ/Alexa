"""Regressions for the findings in CODE_REVIEW.md.

Each test names the finding it pins. They live together because they were found
together; if one starts failing, the review entry explains what it was for.
"""

from __future__ import annotations

import pytest

from sunday import airlock, guardrail, web
from sunday.agent import prompts
from sunday.agent.llm import Reply, ToolCall
from sunday.memory import budget
from sunday.runtime import (
    CAP_SPENT,
    DOOR_OFF,
    DOOR_OFF_SYSTEM,
    EXTERNAL_TOOL,
    HOPS_SPENT,
    NO_ROOM,
    WRITE_DECLINED,
    WRITE_UNATTENDED,
    Runtime,
)
from sunday.state import Result


class NoMemory:
    def retrieve(self, query, **kwargs):
        return []

    def add_turn(self, **kwargs):
        return None

    def add_session_summary(self, **kwargs):
        return None

    def count(self):
        return 0


class Scripted:
    """Answers the tool loop, and plays a fixed query for the airlock."""

    def __init__(self, replies=None, query="a cleared query"):
        self.replies = list(replies or [])
        self.query = query
        self.seen: list[list] = []

    def chat(self, messages, *, tools=None, think=False, max_tokens=None):
        self.seen.append(list(messages))
        if messages and messages[0].get("content") == prompts.AIRLOCK_SYSTEM:
            return Reply(content=self.query)
        return self.replies.pop(0) if self.replies else Reply(content="")

    def stream(self, messages, *, think=False):
        yield "done"

    def preflight(self):
        return None


def _runtime(cfg, agent):
    return Runtime(cfg, agent=agent, memory=NoMemory())  # type: ignore[arg-type]


def _system_lines(messages):
    return [
        m["content"]
        for m in messages
        if isinstance(m, dict) and m.get("role") == "system"
    ]


# -- 1. the outgoing-query redaction notice can never fire -----------------


def test_a_key_in_the_composed_query_is_counted_and_announced(cfg, monkeypatch):
    """compose() scrubs and reports. Scrubbing again in the caller counted the
    already-clean string, so the notice was unreachable code."""
    monkeypatch.setattr(
        web, "run", lambda q, budget=None: web.WebResult("public text", hops=1)
    )

    leaky = "gold price ghp_16C7e42F292c6912E7710c838347Ae178B4a news"
    agent = Scripted(
        [Reply(tool_calls=[ToolCall(EXTERNAL_TOOL, {"intent": "gold"})])],
        query=leaky,
    )
    state = _runtime(cfg, agent).run_turn("what is gold doing")

    assert state["redactions"] == 1
    assert guardrail.NOTICE_REDACTED in state["notices"]  # type: ignore[typeddict-item]
    assert "ghp_" not in state["tool_results"][0].args["query"]


def test_compose_returns_the_count_with_the_query(cfg):
    cleared = airlock.compose(
        Scripted(query="sk-abcdefghijklmnop and news"),  # type: ignore[arg-type]
        {"task": "t", "tool_results": []},  # type: ignore[arg-type]
        "intent",
    )
    assert cleared.redactions == 1
    assert "sk-" not in cleared.query


# -- 2. a truncated private key is not redacted ----------------------------


def test_a_private_key_whose_end_line_was_cut_off_is_still_redacted(cfg):
    whole = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA7f8ndhsSECRETBODY\n"
        "-----END RSA PRIVATE KEY-----\n"
    )
    cut = whole[: whole.index("-----END")]

    for text in (whole, cut):
        cleaned, hits = guardrail.redact(text)
        assert hits >= 1
        assert "MIIEpAIBAAKCAQEA7f8ndhs" not in cleaned
        assert "SECRETBODY" not in cleaned


def test_a_whole_key_block_does_not_swallow_the_text_after_it(cfg):
    text = (
        "before\n-----BEGIN RSA PRIVATE KEY-----\nBODY\n"
        "-----END RSA PRIVATE KEY-----\nafter the key"
    )
    cleaned, _ = guardrail.redact(text)
    assert cleaned.startswith("before")
    assert "after the key" in cleaned


def test_a_truncated_key_in_a_file_never_reaches_the_agent(cfg, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]
    cfg.files.max_read_bytes = 90
    (root / "notes.txt").write_text(
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA7f8ndhsLEAKED\n"
        "-----END RSA PRIVATE KEY-----\n",
        encoding="utf-8",
    )

    agent = Scripted(
        [Reply(tool_calls=[ToolCall("read_file", {"path": str(root / "notes.txt")})])]
    )
    state = _runtime(cfg, agent).run_turn("what is in notes.txt")
    assert "LEAKED" not in state["tool_results"][0].content


# -- 3. `[external] enabled = false` is a silent door ----------------------


def test_switching_the_web_off_tells_the_model(cfg):
    cfg.external.enabled = False
    agent = Scripted()
    _runtime(cfg, agent).run_turn("look up the news")

    assert any(DOOR_OFF_SYSTEM in line for line in _system_lines(agent.seen[0]))


def test_reaching_for_a_disabled_door_is_refused_and_reported(cfg):
    cfg.external.enabled = False
    agent = Scripted([Reply(tool_calls=[ToolCall(EXTERNAL_TOOL, {"intent": "news"})])])
    state = _runtime(cfg, agent).run_turn("look up the news")

    assert state["blocked"] is True
    assert state["unresolved"]
    assert state["tool_results"][0].content == DOOR_OFF
    assert state["tool_results"][0].ok is False
    assert guardrail.NOTICE_EXTERNAL_OFF in state["notices"]  # type: ignore[typeddict-item]


# -- 4. the hop cap is not enforced across calls ---------------------------


def test_the_hop_cap_refuses_rather_than_tallies(cfg, monkeypatch):
    cfg.external.max_hops = 2
    cfg.limits.tool_calls = 5
    calls: list[str] = []

    def fake_run(query, budget=None):
        calls.append(query)
        return web.WebResult("public text", hops=2)

    monkeypatch.setattr(web, "run", fake_run)

    call = ToolCall(EXTERNAL_TOOL, {"intent": "news"})
    agent = Scripted([Reply(tool_calls=[call, call, call])])
    state = _runtime(cfg, agent).run_turn("look it all up")

    # The first call spends the budget; the rest are refused, not run.
    assert len(calls) == 1
    assert state["hops"] <= cfg.external.max_hops
    refusals = [r for r in state["tool_results"] if r.content.startswith("refused:")]
    assert len(refusals) == 2
    assert HOPS_SPENT.format(cap=2) in refusals[0].content
    assert state["blocked"] is True


# -- 5. web.gather fetches once ---------------------------------------------


def test_gather_attempts_one_fetch_and_counts_it(cfg, monkeypatch):
    cfg.external.max_hops = 2
    hits = [
        web.Hit("t1", "https://one.example", "nothing relevant"),
        web.Hit("t2", "https://two.example", "nothing relevant"),
    ]
    monkeypatch.setattr(web, "search", lambda query, limit=5: hits)

    fetched: list[str] = []

    def failing_fetch(url):
        fetched.append(url)
        raise web.net.HttpError("timed out after 10s")

    monkeypatch.setattr(web, "fetch", failing_fetch)
    result = web.gather("something the snippets do not answer at all")

    assert fetched == ["https://one.example"]  # not two
    assert result.hops == 2  # the attempt is the hop
    assert result.ok is True  # falls through to the snippets


def test_an_extraction_bug_does_not_kill_the_turn(cfg, monkeypatch):
    monkeypatch.setattr(
        web, "search", lambda query, limit=5: [web.Hit("t", "https://x.example", "no")]
    )
    monkeypatch.setattr(web, "fetch", lambda url: "<html></html>")
    monkeypatch.setattr(
        web, "extract", lambda html: (_ for _ in ()).throw(ValueError("bug"))
    )
    result = web.gather("a query the snippet does not cover")
    assert result.ok is True


# -- 6. provenance="secret" is never assigned ------------------------------


def test_reading_a_credential_path_labels_the_result_secret(cfg, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]
    (root / ".env").write_text("MAIL_PASSWORD=budi1990", encoding="utf-8")

    agent = Scripted(
        [Reply(tool_calls=[ToolCall("read_file", {"path": str(root / ".env")})])]
    )
    state = _runtime(cfg, agent).run_turn("read my .env")

    assert state["tool_results"][0].provenance == "secret"
    assert state["tainted"] is True

    from sunday.memory import store

    assert store.strongest(state["tool_results"]) == "secret"


def test_an_ordinary_file_stays_private(cfg, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]
    (root / "gold.txt").write_text("sell at 4600", encoding="utf-8")

    agent = Scripted(
        [Reply(tool_calls=[ToolCall("read_file", {"path": str(root / "gold.txt")})])]
    )
    state = _runtime(cfg, agent).run_turn("what is my target")
    assert state["tool_results"][0].provenance == "private"


# -- 8. a result clipped to zero room becomes only [truncated] -------------


def test_a_result_with_no_room_left_refuses_instead_of_looking_empty(cfg, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]
    cfg.files.max_read_bytes = 100_000
    cfg.memory.slice_tools = 40
    cfg.limits.tool_calls = 3
    (root / "big.txt").write_text("x" * 50_000, encoding="utf-8")

    call = ToolCall("read_file", {"path": str(root / "big.txt")})
    agent = Scripted([Reply(tool_calls=[call, call])])
    state = _runtime(cfg, agent).run_turn("read it twice")

    second = state["tool_results"][1]
    assert second.content == NO_ROOM
    assert second.ok is False
    assert second.content.strip() != "[truncated]"


# -- 9. the tool-call cap orphans the rest of the batch --------------------


def test_every_declared_call_gets_an_answer(cfg, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]
    cfg.limits.tool_calls = 2
    (root / "a.txt").write_text("a", encoding="utf-8")

    call = ToolCall("read_file", {"path": str(root / "a.txt")})
    agent = Scripted([Reply(tool_calls=[call, call, call, call])])
    state = _runtime(cfg, agent).run_turn("read it over and over")

    assert state["tool_calls"] == 2
    assert len(state["tool_results"]) == 4  # one answer per declared call
    assert [r.content for r in state["tool_results"][2:]] == [CAP_SPENT, CAP_SPENT]
    assert "stopped after 2 tool calls" in (state["unresolved"] or "")


# -- 17. Ctrl-C tears the turn down properly -------------------------------


def test_ctrl_c_during_a_turn_is_cancelled_not_raised(cfg):
    class Interrupting(Scripted):
        def stream(self, messages, *, think=False):
            yield "half a "
            raise KeyboardInterrupt

    runtime = _runtime(cfg, Interrupting())
    events: list[dict] = []
    state = runtime.run_turn("a question", on_event=events.append)

    assert state["committed"] is False
    assert events[-1] == {"type": "done", "committed": False}
    # The turn was torn down: no sinks and no context left behind.
    assert runtime._ctx is None
    assert runtime._on_event is None


# -- 18. a refused path must not taint -------------------------------------


def test_naming_a_credential_path_outside_the_roots_does_not_shut_the_door(
    cfg, tmp_path
):
    """The read was refused and returned nothing, so there is nothing to taint
    on. Otherwise the model can close its own door by naming a file."""
    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]

    agent = Scripted(
        [Reply(tool_calls=[ToolCall("read_file", {"path": "C:/Users/me/.ssh/id_rsa"})])]
    )
    state = _runtime(cfg, agent).run_turn("read my ssh key")

    assert state["tool_results"][0].ok is False
    assert state["tainted"] is False


def test_a_credential_path_inside_the_roots_still_taints(cfg, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]
    (root / ".env").write_text("KEY=1", encoding="utf-8")

    agent = Scripted(
        [Reply(tool_calls=[ToolCall("read_file", {"path": str(root / ".env")})])]
    )
    assert _runtime(cfg, agent).run_turn("read it")["tainted"] is True


# -- 12/13. the lists cover what actually happens --------------------------


@pytest.mark.parametrize(
    "path",
    [
        "C:/proj/.env.local",
        "C:/proj/.env.production",
        "C:/proj/token.json",
        "C:/proj/service-account-key.json",
        "C:/proj/secrets.yaml",
        "C:/proj/.npmrc",
        "C:/proj/.netrc",
        "C:/proj/cert.pfx",
        "C:/Users/me/.gnupg/secring.gpg",
        "C:/Users/me/.ssh/id_ed25519",
    ],
)
def test_the_credential_list_covers_what_really_turns_up(cfg, path):
    assert guardrail.is_secret_path(path) is True


@pytest.mark.parametrize(
    "text",
    [
        "glpat-abcdefghij1234567890",
        "AIzaSyD-9tSrke72PouQMnMX-a7eZSW0jkFMBWY",
        "hf_abcdefghijklmnopqrstuvwxyzABCD",
        "sk_live_abcdefghijklmnopqrstuvwx",
        "xapp-1-ABCDEFGH-1234567890",
        "dop_v1_abcdefghijklmnopqrstuvwxyz1234",
        "ya29.a0AfH6SMBabcdefghijklmnop",
    ],
)
def test_the_shape_list_covers_the_common_prefixes(cfg, text):
    cleaned, hits = guardrail.redact(f"the token is {text} ok")
    assert hits == 1
    assert text not in cleaned


def test_the_additions_carry_no_false_positive_tax(cfg):
    ordinary = (
        "The invoice is 4460101, commit a1b2c3d4e5f6, order AI12345, "
        "meeting at 3pm, uuid 4b2c1a9e-7f30-4a1b-9c2d-8e5f6a7b8c9d."
    )
    cleaned, hits = guardrail.redact(ordinary)
    assert hits == 0
    assert cleaned == ordinary


# -- 11. write confirmation (option 2: confirm on overwrite, create passes) --


def _write_call(path, text="new contents"):
    return ToolCall("write_file", {"path": str(path), "text": text})


def test_creating_a_new_file_does_not_ask(cfg, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]
    target = root / "fresh.txt"

    asked: list[str] = []
    agent = Scripted([Reply(tool_calls=[_write_call(target)])])
    state = Runtime(cfg, agent=agent, memory=NoMemory()).run_turn(  # type: ignore[arg-type]
        "write me a note", on_confirm=lambda q: asked.append(q) or True
    )

    assert asked == []  # nothing is lost by creating, so nothing interrupts
    assert target.read_text(encoding="utf-8") == "new contents"
    assert state["tool_results"][0].ok is True


def test_overwriting_asks_first_and_honours_yes(cfg, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]
    target = root / "gold.txt"
    target.write_text("sell at 4600", encoding="utf-8")

    asked: list[str] = []

    def yes(question):
        asked.append(question)
        return True

    agent = Scripted([Reply(tool_calls=[_write_call(target)])])
    state = Runtime(cfg, agent=agent, memory=NoMemory()).run_turn(  # type: ignore[arg-type]
        "replace my note", on_confirm=yes
    )

    assert len(asked) == 1
    assert "gold.txt" in asked[0].question
    assert "bytes" in asked[0].question  # the question says what is at stake
    assert asked[0].path == str(target)
    assert asked[0].action == "overwrite"
    assert target.read_text(encoding="utf-8") == "new contents"
    assert state["tool_results"][0].ok is True


def test_saying_no_leaves_the_file_exactly_as_it_was(cfg, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]
    target = root / "gold.txt"
    target.write_text("sell at 4600", encoding="utf-8")

    agent = Scripted([Reply(tool_calls=[_write_call(target)])])
    state = Runtime(cfg, agent=agent, memory=NoMemory()).run_turn(  # type: ignore[arg-type]
        "replace my note", on_confirm=lambda q: False
    )

    assert target.read_text(encoding="utf-8") == "sell at 4600"
    result = state["tool_results"][0]
    assert result.ok is False
    assert result.content == WRITE_DECLINED
    assert any("left as it was" in n for n in state["notices"])  # type: ignore[typeddict-item]


def test_with_nobody_attached_the_overwrite_fails_closed(cfg, tmp_path):
    """Silence is not consent. A session with no confirm sink cannot approve."""
    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]
    target = root / "gold.txt"
    target.write_text("sell at 4600", encoding="utf-8")

    agent = Scripted([Reply(tool_calls=[_write_call(target)])])
    state = Runtime(cfg, agent=agent, memory=NoMemory()).run_turn(  # type: ignore[arg-type]
        "replace my note"
    )

    assert target.read_text(encoding="utf-8") == "sell at 4600"
    assert state["tool_results"][0].content == WRITE_UNATTENDED
    assert state["tool_results"][0].ok is False


def test_a_path_outside_the_roots_is_refused_without_asking(cfg, tmp_path):
    """The sandbox answers first; there is nothing to confirm."""
    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]
    outside = tmp_path / "outside.txt"
    outside.write_text("not yours", encoding="utf-8")

    asked: list[str] = []
    agent = Scripted([Reply(tool_calls=[_write_call(outside)])])
    Runtime(cfg, agent=agent, memory=NoMemory()).run_turn(  # type: ignore[arg-type]
        "overwrite that", on_confirm=lambda q: asked.append(q) or True
    )

    assert asked == []
    assert outside.read_text(encoding="utf-8") == "not yours"


def test_a_credential_file_is_still_refused_even_with_a_yes(cfg, tmp_path):
    """Confirmation widens what the user can allow; it does not widen the
    sandbox's own refusals."""
    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]
    target = root / ".env"
    target.write_text("KEY=1", encoding="utf-8")

    agent = Scripted([Reply(tool_calls=[_write_call(target)])])
    state = Runtime(cfg, agent=agent, memory=NoMemory()).run_turn(  # type: ignore[arg-type]
        "overwrite my env", on_confirm=lambda q: True
    )

    assert target.read_text(encoding="utf-8") == "KEY=1"
    assert "credential" in state["tool_results"][0].content


def test_the_question_is_not_also_broadcast_as_an_event(cfg, tmp_path):
    """One owner for the prompt.

    The runtime used to `_emit` a confirm event *and* call the sink, so a client
    watching both rendered two cards -- and the first had no id to answer with,
    so its buttons did nothing. The sink is the prompt; the event stream is for
    watching. See round-2 finding 2.
    """
    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]
    target = root / "gold.txt"
    target.write_text("sell at 4600", encoding="utf-8")

    events: list[dict] = []
    asked: list[object] = []
    agent = Scripted([Reply(tool_calls=[_write_call(target)])])
    Runtime(cfg, agent=agent, memory=NoMemory()).run_turn(  # type: ignore[arg-type]
        "replace it",
        on_event=events.append,
        on_confirm=lambda r: asked.append(r) or False,
    )

    assert len(asked) == 1
    assert [e for e in events if e["type"] == "confirm"] == []


# ===================== round 2 =====================


# -- 3. ASIA, hf_ and a missing left edge redacted ordinary text -----------


@pytest.mark.parametrize(
    "text",
    [
        "Our ASIA-PACIFIC-2024 revenue report is in the shared drive.",
        "The ASIA_REGION_SUMMARY spreadsheet has the numbers.",
        "See hf_dataset_loader.py for the loader.",
        "import hf_hub_download",
        "ASIA and EMEA both reported growth this quarter.",
        # No left edge meant the prefix matched mid-word. These are the ones
        # the review did not reach: ordinary English, silently blanked.
        "We took a task-oriented approach to the rewrite.",
        "Restore from the disk-image-backup folder.",
        "The whisk-and-fold method works better here.",
    ],
)
def test_ordinary_prose_is_not_mistaken_for_a_credential(cfg, text):
    cleaned, hits = guardrail.redact(text)
    assert hits == 0
    assert cleaned == text


@pytest.mark.parametrize(
    "token",
    [
        "AKIAIOSFODNN7EXAMPLE",
        "ASIAY34FZKBOKMSDIQ7B",
        "hf_abcdefghijklmnopqrstuvwxyzABCD",
        "AIzaSyD-9tSrke72PouQMnMX-a7eZSW0jkFMBWY",
    ],
)
def test_the_precise_shapes_still_catch_the_real_thing(cfg, token):
    cleaned, hits = guardrail.redact(f"the key is {token} ok")
    assert hits == 1
    assert token not in cleaned


def test_a_prefix_at_the_start_of_the_text_still_matches(cfg):
    """The left edge must not require a preceding character."""
    cleaned, hits = guardrail.redact("ghp_16C7e42F292c6912E7710c838347Ae178B4a")
    assert hits == 1
    assert cleaned == guardrail.REDACTED


# -- 4. the hop cap was still reachable at three --------------------------


def test_two_lookups_cannot_spend_three_hops(cfg, monkeypatch):
    """A first lookup whose snippets sufficed spends one hop and leaves the
    counter at 1, which passed `1 >= 2` -- so the second searched and fetched
    and the turn ended at three."""
    cfg.external.max_hops = 2
    cfg.limits.tool_calls = 5

    budgets: list[int] = []

    def fake_gather(query, budget=None):
        budgets.append(budget)
        # A search always costs one; a fetch costs another when affordable.
        hops = 2 if (budget or 0) >= 2 else 1
        return web.WebResult("public text", hops=hops)

    monkeypatch.setattr(web, "gather", fake_gather)
    monkeypatch.setattr(web, "summarise", lambda q, m: "a summary")

    call = ToolCall(EXTERNAL_TOOL, {"intent": "news"})
    agent = Scripted([Reply(tool_calls=[call, call, call])])
    state = _runtime(cfg, agent).run_turn("look it up twice over")

    assert state["hops"] <= cfg.external.max_hops
    assert budgets[0] == 2  # the first lookup has the whole budget


def test_a_lookup_with_one_hop_left_searches_but_does_not_fetch(cfg, monkeypatch):
    monkeypatch.setattr(
        web,
        "search",
        lambda query, limit=5: [web.Hit("t", "https://x.example", "unrelated")],
    )

    def must_not_fetch(url):  # pragma: no cover - must not run
        raise AssertionError("fetched with only one hop left")

    monkeypatch.setattr(web, "fetch", must_not_fetch)
    result = web.gather("a query the snippet does not answer", budget=1)

    assert result.hops == 1
    assert result.ok is True


def test_no_budget_at_all_is_a_stated_gap(cfg):
    result = web.gather("anything", budget=0)
    assert result.ok is False
    assert result.hops == 0


# -- 5. the sidecar docstring was a syntax warning ------------------------


def test_the_entry_point_modules_compile_without_warnings():
    r"""`.venv\Scripts\...` in a plain docstring makes `\S` an invalid escape,
    and it printed at launch because sidecar.py is the entry point.

    This test's own docstring had the same bug on the first attempt, which is
    the neatest possible argument for pinning it.
    """
    import warnings
    from pathlib import Path

    for name in ("sidecar", "server", "main", "runtime"):
        source = Path("src/sunday") / f"{name}.py"
        with warnings.catch_warnings():
            warnings.simplefilter("error", SyntaxWarning)
            compile(source.read_text(encoding="utf-8"), str(source), "exec")
