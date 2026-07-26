"""
Lameh Intelligence - agent client
==================================
Thin wrapper around the orchestrator's /v0/chat endpoint - the Intelligence
agent under test. Used by run_eval.py as the "target" function LangSmith
calls for each dataset example; kept separate from the evaluators so it can
also be called standalone (e.g. to sanity-check the agent is reachable).

The endpoint streams Server-Sent Events rather than returning plain JSON:
  event: conversation_id   -> {"conversation_id": "..."}   thread id, for
                              linking a failed/truncated eval example back
                              to its LangSmith/orchestrator conversation
  event: text_delta        -> {"delta": "..."}             incremental text
  event: message_complete  -> {"full_answer": "...", ...}  final full text
  event: stream_status     -> progress/heartbeat, not part of the answer
  event: conversation_title -> cosmetic, not part of the answer
Lines starting with ":" are comments (keep-alive/ping) per the SSE spec.
"""

import json

import requests

from config import ORCHESTRATOR_API_KEY, ORCHESTRATOR_ORGANIZATION_ID, ORCHESTRATOR_URL, ORCHESTRATOR_USER_ID

CHAT_ENDPOINT = "/v0/chat"
DEFAULT_TIMEOUT_SECONDS = 120


def _headers():
    return {
        "accept": "application/json",
        "organization-id": ORCHESTRATOR_ORGANIZATION_ID,
        "x-api-key": ORCHESTRATOR_API_KEY,
        "x-user-id": ORCHESTRATOR_USER_ID,
        "Authorization": f"Bearer {ORCHESTRATOR_API_KEY}",
        "Content-Type": "application/json",
    }


def _iter_sse_events(response):
    """Yields (event_type, data_str) pairs from a text/event-stream response,
    skipping comment/keep-alive lines (those starting with ":")."""
    event_type = None
    data_lines = []
    for raw_line in response.iter_lines(decode_unicode=True):
        if raw_line is None or raw_line.startswith(":"):
            continue
        if raw_line == "":
            if event_type is not None or data_lines:
                yield event_type, "\n".join(data_lines)
            event_type, data_lines = None, []
        elif raw_line.startswith("event:"):
            event_type = raw_line[len("event:"):].strip()
        elif raw_line.startswith("data:"):
            data_lines.append(raw_line[len("data:"):].strip())
    if event_type is not None or data_lines:
        yield event_type, "\n".join(data_lines)


def ask_agent(message, ai_mode="expert", chat_model="advanced", reasoning_effort="high",
              conversation_id=None, timeout=DEFAULT_TIMEOUT_SECONDS):
    """Sends `message` to the Intelligence agent and returns:
        {"answer": str, "conversation_id": str | None, "completed": bool}
    `completed` is False if the stream ended without a message_complete event
    (i.e. a truncated/failed response)."""
    payload = {
        "message": message,
        "context": {
            "ai_mode": ai_mode,
            "chat_model": chat_model,
            "reasoning_effort": reasoning_effort,
        },
        "conversation_id": conversation_id,
    }
    response = requests.post(
        f"{ORCHESTRATOR_URL}{CHAT_ENDPOINT}",
        headers=_headers(),
        json=payload,
        timeout=timeout,
        stream=True,
    )
    response.raise_for_status()

    thread_id = None
    text_deltas = []
    full_answer = None
    completed = False

    for event_type, data in _iter_sse_events(response):
        if not data:
            continue
        try:
            body = json.loads(data)
        except json.JSONDecodeError:
            continue
        if event_type == "conversation_id":
            thread_id = body.get("conversation_id")
        elif event_type == "text_delta":
            text_deltas.append(body.get("delta", ""))
        elif event_type == "message_complete":
            full_answer = body.get("full_answer")
            completed = True

    answer = full_answer if full_answer is not None else "".join(text_deltas)
    return {"answer": answer, "conversation_id": thread_id, "completed": completed}


if __name__ == "__main__":
    result = ask_agent("hello")
    print(result)
