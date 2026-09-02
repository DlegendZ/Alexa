"""What actually happens behind the door.

    search -> decide -> fetch -> extract -> summarise

Steps one to four are our own code, so the only thing the summariser ever
receives is a query string and text that was already on the public web. The hop
count is capped: one search, at most one page fetch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sunday import config, net

#: Words too common to say anything about whether a snippet answered the query.
_STOP = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "in", "is", "it", "of", "on", "or", "the", "to", "what", "when", "where",
    "which", "who", "why", "with",
}

#: A page whose snippets cover this much of the query is not worth fetching.
COVERAGE_ENOUGH = 0.6
SNIPPETS_ENOUGH_CHARS = 300


@dataclass
class Hit:
    title: str
    url: str
    snippet: str


@dataclass
class WebResult:
    text: str
    hops: int
    ok: bool = True
    sources: list[str] = field(default_factory=list)
    fetched: str | None = None


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP and len(w) > 2}


def coverage(query: str, snippets: str) -> float:
    """How much of the query the snippets actually speak to. Plain arithmetic,
    no model: this decision is not worth a generation."""
    wanted = _words(query)
    if not wanted:
        return 0.0
    have = _words(snippets)
    return len(wanted & have) / len(wanted)


def search(query: str, limit: int = 5) -> list[Hit]:
    from ddgs import DDGS

    rows = DDGS().text(query, max_results=limit)
    hits: list[Hit] = []
    for row in rows or []:
        url = row.get("href") or row.get("url") or ""
        if not url:
            continue
        hits.append(
            Hit(
                title=(row.get("title") or "").strip(),
                url=url,
                snippet=(row.get("body") or "").strip(),
            )
        )
    return hits


def fetch(url: str) -> str:
    """One URL, one timeout, one size cap, redirects followed once. A browser
    user-agent, no cookies, no JavaScript."""
    cfg = config.get().external
    response = net.request(
        "GET",
        url,
        headers={"User-Agent": net.BROWSER_UA, "Accept": "text/html,*/*"},
        timeout=cfg.fetch_timeout_s,
        max_bytes=cfg.fetch_max_bytes,
        follow_redirects=True,
    )
    return response.text


def extract(html: str) -> str:
    """Strip navigation, ads and boilerplate down to article text."""
    import trafilatura

    text = trafilatura.extract(html, include_comments=False, include_tables=False)
    if not text:
        return ""
    cap = config.get().external.extract_max_chars
    return text[:cap]


def gather(query: str) -> WebResult:
    """Steps 1 to 4. Everything here is our own code and stays on this machine
    apart from the query itself."""
    cfg = config.get().external
    try:
        hits = search(query, limit=5)
    except Exception as exc:  # noqa: BLE001 - any search failure reads the same
        return WebResult(f"the web search failed ({type(exc).__name__})", 1, ok=False)

    if not hits:
        return WebResult("the web search returned nothing", 1, ok=False)

    snippets = "\n".join(f"- {h.title}: {h.snippet}" for h in hits)
    sources = [h.url for h in hits[:3]]
    hops = 1

    enough = (
        len(snippets) >= SNIPPETS_ENOUGH_CHARS
        and coverage(query, snippets) >= COVERAGE_ENOUGH
    )
    if enough or hops >= cfg.max_hops:
        return WebResult(snippets, hops, sources=sources)

    for hit in hits[:2]:
        try:
            body = extract(fetch(hit.url))
        except (net.HttpError, Exception):  # noqa: B014 - readable failure only
            continue
        if len(body) > 400:
            return WebResult(
                f"{snippets}\n\nFrom {hit.url}:\n{body}",
                hops + 1,
                sources=sources,
                fetched=hit.url,
            )
        break

    return WebResult(snippets, hops, sources=sources)


def summarise(query: str, material: str) -> str:
    """The one call that is not local. It receives a cleared query and text
    that was already public, and nothing else."""
    from sunday.agent import prompts

    if not config.DEEPSEEK_API_KEY:
        # Degrade rather than fail: the material is public either way.
        return material[:2000]

    import anthropic

    client = anthropic.Anthropic(
        api_key=config.DEEPSEEK_API_KEY,
        base_url=config.DEEPSEEK_BASE_URL,
    )
    message = client.messages.create(
        model=config.get().models.summariser,
        max_tokens=600,
        system=prompts.SUMMARISE_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": f"Query: {query}\n\nText:\n{material}",
            }
        ],
    )
    parts = [
        block.text
        for block in message.content
        if getattr(block, "type", "") == "text"
    ]
    return "".join(parts).strip()


def run(query: str) -> WebResult:
    """The whole pipeline, with every failure ending as a readable sentence."""
    gathered = gather(query)
    if not gathered.ok:
        return gathered

    try:
        summary = summarise(query, gathered.text)
    except Exception as exc:  # noqa: BLE001
        return WebResult(
            f"found web results but could not summarise them ({type(exc).__name__}); "
            f"raw snippets follow:\n{gathered.text[:1500]}",
            gathered.hops,
            ok=False,
            sources=gathered.sources,
            fetched=gathered.fetched,
        )

    if gathered.sources:
        summary += "\n(sources: " + ", ".join(gathered.sources[:3]) + ")"
    return WebResult(
        summary,
        gathered.hops,
        sources=gathered.sources,
        fetched=gathered.fetched,
    )
