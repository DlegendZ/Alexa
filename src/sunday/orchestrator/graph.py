from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from sunday import config
from sunday.memory import general_store
from sunday.orchestrator.state import SundayState
from sunday.tools.assets import get_asset_price
from sunday.tools.weather import get_weather

TOOLS = [get_weather, get_asset_price]


def _debug(node: str, msg: str) -> None:
    print(f"[debug] {node}: {msg}")

AVAILABLE_ROUTES = {
    "general_external": "needs a tool/function call or external lookup: weather, asset/commodity "
    "prices, news, general web info",
    "none": "plain chitchat, opinions, or general knowledge you can answer directly, no tool call needed",
}

SUB_AGENT_SYSTEM_PROMPT = (
    "You are Sunday's general-purpose sub-agent. Use the available tools to answer "
    "weather and asset price (gold, silver, crypto, etc.) questions with real, current data. "
    "Be concise."
)

MERGE_SYSTEM_PROMPT = (
    "You are Sunday, a personal assistant. A sub-agent may have already gathered data for the "
    "user's request — if given, base your reply only on that data. Relevant past conversation "
    "context may also be given — use it only if it helps answer this request, ignore it "
    "otherwise. If no data is given, this is plain chitchat: just reply directly. Write the "
    "final reply to the user: natural, concise."
)


def _text(content) -> str:
    """Anthropic-format responses return content as a list of blocks, not a plain string."""
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "") for block in content if isinstance(block, dict)
    )


THINKING_BUDGET_TOKENS = 2048


def _orchestrator_llm() -> ChatAnthropic:
    """DeepSeek Flash, thinking mode on — used by the orchestrator (classify + merge)."""
    config.require_deepseek_key()
    return ChatAnthropic(
        model=config.DEEPSEEK_MODEL_FLASH,
        api_key=config.DEEPSEEK_API_KEY,
        base_url=config.DEEPSEEK_BASE_URL,
        max_tokens=4096,
        thinking={"type": "enabled", "budget_tokens": THINKING_BUDGET_TOKENS},
    )


def _flash_llm() -> ChatAnthropic:
    """DeepSeek Flash, plain — used by the general/external sub-agent."""
    config.require_deepseek_key()
    return ChatAnthropic(
        model=config.DEEPSEEK_MODEL_FLASH,
        api_key=config.DEEPSEEK_API_KEY,
        base_url=config.DEEPSEEK_BASE_URL,
        temperature=0,
    )


def orchestrator_classify(state: SundayState) -> dict:
    """DeepSeek Flash (thinking mode) picks which route handles this task."""
    _debug("classify", f"task received -> {state['task']!r}")
    llm = _orchestrator_llm()
    route_list = "\n".join(f"- {k}: {v}" for k, v in AVAILABLE_ROUTES.items())
    prompt = [
        SystemMessage(
            content=(
                "Classify the user's request into exactly one route key from this list, "
                f"reply with ONLY the key, nothing else:\n{route_list}"
            )
        ),
        HumanMessage(content=state["task"]),
    ]
    result = llm.invoke(prompt)
    route = _text(result.content).strip()
    if route not in AVAILABLE_ROUTES:
        _debug("classify", f"model returned unrecognized route {route!r}, defaulting to general_external")
        route = "general_external"
    _debug(
        "classify",
        f"route decided -> {route!r} "
        f"({'will call sub_agent_general' if route == 'general_external' else 'skips sub-agent, orchestrator replies directly'})",
    )
    return {"route": route}


def sub_agent_general(state: SundayState) -> dict:
    """DeepSeek Flash + tools handle the general/external branch."""
    _debug("sub_agent_general", f"called, tools available: {[t.name for t in TOOLS]}")
    llm = _flash_llm().bind_tools(TOOLS)
    tool_map = {t.name: t for t in TOOLS}

    messages = [
        SystemMessage(content=SUB_AGENT_SYSTEM_PROMPT),
        HumanMessage(content=state["task"]),
    ]
    response = llm.invoke(messages)
    messages.append(response)

    if not response.tool_calls:
        _debug("sub_agent_general", "no tool calls made, model answered from its own knowledge")

    while response.tool_calls:
        for call in response.tool_calls:
            _debug("sub_agent_general", f"tool call -> {call['name']}({call['args']})")
            tool_result = tool_map[call["name"]].invoke(call["args"])
            _debug("sub_agent_general", f"tool result <- {tool_result!r}")
            messages.append(
                ToolMessage(content=str(tool_result), tool_call_id=call["id"])
            )
        response = llm.invoke(messages)
        messages.append(response)

    result = _text(response.content)
    _debug("sub_agent_general", f"result -> {result!r}")
    return {"sub_agent_result": result}


def memory_store(state: SundayState) -> dict:
    """General memory store: RAG retrieval over past general-branch interactions."""
    context = general_store.retrieve_context(state["task"])
    if context:
        turn_count = context.count("\n---\n") + 1
        _debug("memory_store", f"pulled {turn_count} relevant past turn(s):\n{context}")
    else:
        _debug("memory_store", "no relevant past context found (store empty or no match)")
    return {"memory_context": context}


def orchestrator_merge(state: SundayState) -> dict:
    """DeepSeek Flash (thinking mode) formats the final response, then writes this turn to memory."""
    used_sub_agent = bool(state.get("sub_agent_result"))
    used_memory = bool(state.get("memory_context"))
    _debug(
        "merge",
        f"sub_agent_result used={used_sub_agent}, memory_context used={used_memory}, "
        f"conversation length so far={len(state['messages'])}",
    )
    llm = _orchestrator_llm()
    content = f"User asked: {state['task']}"
    if state.get("sub_agent_result"):
        content += f"\n\nSub-agent result:\n{state['sub_agent_result']}"
    if state.get("memory_context"):
        content += f"\n\nRelevant past context:\n{state['memory_context']}"

    prompt = [SystemMessage(content=MERGE_SYSTEM_PROMPT), HumanMessage(content=content)]
    result = llm.invoke(prompt)
    final_response = _text(result.content)
    messages = state["messages"] + [AIMessage(content=final_response)]

    general_store.add_interaction(
        state["task"], state["sub_agent_result"], final_response
    )
    _debug("merge", f"final reply -> {final_response!r}")
    _debug("merge", "wrote this turn to Chroma memory")

    return {"final_response": final_response, "messages": messages}


def _route_selector(state: SundayState) -> str:
    return state["route"]


def build_graph():
    graph = StateGraph(SundayState)
    graph.add_node("orchestrator_classify", orchestrator_classify)
    graph.add_node("sub_agent_general", sub_agent_general)
    graph.add_node("memory_store", memory_store)
    graph.add_node("orchestrator_merge", orchestrator_merge)

    graph.add_edge(START, "orchestrator_classify")
    graph.add_conditional_edges(
        "orchestrator_classify",
        _route_selector,
        {"general_external": "sub_agent_general", "none": "memory_store"},
    )
    graph.add_edge("sub_agent_general", "memory_store")
    graph.add_edge("memory_store", "orchestrator_merge")
    graph.add_edge("orchestrator_merge", END)

    return graph.compile()
