# Repo context

The agent-facing documentation for this repo lives in `AGENT.md` files. This
stub exists only so they get loaded automatically — edit `AGENT.md`, not this.

@AGENT.md

The LangSmith eval suites have their own `AGENT.md` files — one umbrella at
`tests/langsmith/AGENT.md` and one per suite (`fs/`, `research/`) — loaded
automatically when a session touches files in those directories. They are
deliberately not imported here: they're long, and most sessions don't need
them.
