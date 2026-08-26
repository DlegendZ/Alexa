from typing import TypedDict

from langchain_core.messages import BaseMessage


class SundayState(TypedDict):
    messages: list[BaseMessage]
    task: str
    route: str
    sub_agent_result: str
    memory_context: str
    final_response: str
