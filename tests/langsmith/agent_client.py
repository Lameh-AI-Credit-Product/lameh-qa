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
import time

import requests

from config import ORCHESTRATOR_API_KEY, ORCHESTRATOR_ORGANIZATION_ID, ORCHESTRATOR_URL, ORCHESTRATOR_USER_ID

CHAT_ENDPOINT = "/v0/chat"

# Per-read timeout: how long to wait for the *next* byte. The stream sends
# periodic keep-alive pings, so this alone can never bound a run - each ping
# resets it, and a stalled-but-chatty agent would stream forever.
DEFAULT_TIMEOUT_SECONDS = 120

# Total wall-clock budget for one answer. A prompt still running past this is
# treated as a non-answer: the connection is dropped and whatever text arrived
# is returned marked timed_out, rather than holding the whole eval open.
DEFAULT_DEADLINE_SECONDS = 600


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
              conversation_id=None, timeout=DEFAULT_TIMEOUT_SECONDS,
              deadline_seconds=DEFAULT_DEADLINE_SECONDS):
    """Sends `message` to the Intelligence agent and returns:
        {"answer": str, "conversation_id": str | None, "completed": bool,
         "timed_out": bool, "elapsed_seconds": float}

    `completed` is False if the stream ended without a message_complete event
    - a truncated response, whether the agent cut out on its own or we cut it
    off at the deadline.

    `timed_out` is True when `deadline_seconds` of wall clock elapsed before
    message_complete. The connection is dropped at that point and any text
    received so far is returned, so a hung prompt costs one deadline rather
    than blocking the run indefinitely. Pass deadline_seconds=None to wait as
    long as the agent takes."""
    payload = {
        "message": message,
        "context": {
            "ai_mode": ai_mode,
            "chat_model": chat_model,
            "reasoning_effort": reasoning_effort,
        },
        "conversation_id": conversation_id,
    }
    started = time.monotonic()
    deadline = started + deadline_seconds if deadline_seconds else None

    thread_id = None
    text_deltas = []
    full_answer = None
    completed = False
    timed_out = False

    # Closed via `with` so hitting the deadline actually releases the
    # connection instead of leaving the request streaming in the background.
    with requests.post(
        f"{ORCHESTRATOR_URL}{CHAT_ENDPOINT}",
        headers=_headers(),
        json=payload,
        timeout=timeout,
        stream=True,
    ) as response:
        response.raise_for_status()

        for event_type, data in _iter_sse_events(response):
            # Checked per event rather than per read: keep-alive pings count
            # as reads, so only wall-clock time bounds a stalled stream.
            if deadline is not None and time.monotonic() > deadline:
                timed_out = True
                break
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
    return {
        "answer": answer,
        "conversation_id": thread_id,
        "completed": completed,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }


if __name__ == "__main__":
    result = ask_agent("hello")
    print(result)
