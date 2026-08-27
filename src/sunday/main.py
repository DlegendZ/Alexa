from langchain_core.messages import HumanMessage

from sunday.orchestrator.graph import build_graph


def main() -> None:
    graph = build_graph()
    messages = []

    print("Sunday is online. Type 'exit' to quit.")
    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            break

        messages.append(HumanMessage(content=user_input))
        result = graph.invoke(
            {
                "messages": messages,
                "task": user_input,
                "route": "",
                "sub_agent_result": "",
                "memory_context": "",
                "final_response": "",
            }
        )
        messages = result["messages"]
        print(f"sunday> {result['final_response']}")


if __name__ == "__main__":
    main()
