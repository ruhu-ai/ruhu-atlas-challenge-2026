from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from .agent_document import AgentDocument
from .heuristics import interpreter_by_name
from .interpreter import SemanticInterpreter
from .interpreters import build_named_interpreter
from .kernel import ConversationKernel
from .loader import load_agent_document_source, load_transcript
from .schemas import RuntimeTurn, SimulationRun
from .tools.runtime import ToolRuntime


def simulate_transcript(
    workflow: AgentDocument,
    utterances: list[str],
    *,
    conversation_id: str = "simulated",
    channel: str = "web_chat",
    interpreter: SemanticInterpreter | None = None,
    tool_runtime: ToolRuntime | None = None,
    agent_id: str = "simulated_agent",
    agent_name: str = "Simulated Agent",
) -> SimulationRun:
    kernel = ConversationKernel(interpreter=interpreter, tool_runtime=tool_runtime)
    agent_document = workflow
    resolved_agent_id = agent_id
    resolved_agent_name = agent_name
    fallback_final_step = agent_document.start_step_id
    start = kernel.start_conversation(
        conversation_id,
        agent_document=agent_document,
        agent_id=resolved_agent_id,
        agent_name=resolved_agent_name,
        agent_version_id=f"simulated:{resolved_agent_id}",
        mode="simulation",
    )
    results = []
    for index, utterance in enumerate(utterances, start=1):
        result = kernel.process_turn(
            conversation_id,
            RuntimeTurn(
                turn_id=f"turn_{index}",
                dedupe_key=f"turn_{index}",
                channel=channel,  # type: ignore[arg-type]
                modality="text",
                event_type="user_message",
                text=utterance,
                received_at=datetime.now(timezone.utc),
            ),
            agent_document=agent_document,
            agent_id=resolved_agent_id,
            agent_name=resolved_agent_name,
        )
        results.append(result)

    final_state = kernel.load_conversation(conversation_id)
    return SimulationRun(
        start=start,
        turns=results,
        final_step_id=final_state.step_id if final_state else fallback_final_step,
        final_facts=final_state.facts if final_state else {},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate a transcript against a Ruhu agent document.")
    parser.add_argument("--agent-document-file", type=Path, required=True, help="Path to an agent-document JSON file.")
    parser.add_argument("--transcript-file", type=Path)
    parser.add_argument("--interpreter")
    parser.add_argument("--model-path", type=Path, default=Path("/tmp/gemma-4-E4B-it"))
    parser.add_argument("--conversation-id", default="simulated")
    parser.add_argument(
        "--channel",
        default="web_chat",
        choices=["phone", "whatsapp", "web_chat", "web_widget", "browser"],
    )
    parser.add_argument("--json", action="store_true", help="Print the full simulation run as JSON.")
    parser.add_argument("utterance", nargs="*", help="User turns to simulate in order.")
    args = parser.parse_args()

    document, agent_id, agent_name = load_agent_document_source(args.agent_document_file)
    interpreter = build_named_interpreter(args.interpreter, model_path=args.model_path)

    utterances = list(args.utterance)
    if args.transcript_file:
        utterances = load_transcript(args.transcript_file)
    if not utterances:
        raise SystemExit("No utterances provided. Use positional utterances or --transcript-file.")

    run = simulate_transcript(
        document,
        utterances,
        conversation_id=args.conversation_id,
        channel=args.channel,
        interpreter=interpreter,
        agent_id=agent_id,
        agent_name=agent_name,
    )
    if args.json:
        print(run.model_dump_json(indent=2))
        return
    print(f"start: {run.start.step_before} -> {run.start.step_after}")
    for result in run.turns:
        texts = [message.text for message in result.emitted_messages]
        print(
            f"{result.turn_id}: {result.step_before} -> {result.step_after} | "
            f"{result.chosen_action.type} | messages={texts} | tool_calls={len(result.tool_calls)}"
        )
    print(f"final_step_id={run.final_step_id}")
    print(f"final_facts={run.final_facts}")


if __name__ == "__main__":
    main()
