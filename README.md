# durable-agent-proto

What happens to an AI agent's side effects when its process dies mid-task and comes back. A small, reproducible crash test of ADK's Resume feature on Gemini.

An ADK refund agent handles one request: look up an order, issue a refund, confirm to the customer. We kill the process at a chosen point, start a new process and recover the same session. Then we count refunds in the ledger. Exactly one is correct. Two is a double refund. Zero is a lost refund.

## Setup

- `agent.py` is an ADK `LlmAgent` on `gemini-3.5-flash` with two tools: `get_order`, which only reads, and `issue_refund`, which writes a row to a SQLite ledger. The `App` has `ResumabilityConfig(is_resumable=True)`.
- Sessions are stored with ADK's `DatabaseSessionService` on SQLite, so a new process sees what the old one saved.
- `worker.py` runs one phase in its own process, so a crash really kills it with `os._exit`.
- `trial.py` runs the first phase with a crash, then the recovery phase without one, and records the ledger count.

### Crash points

| Point | When the process dies |
|---|---|
| `before_tool` | The model has asked for the refund. The tool hasn't run |
| `after_effect` | The refund is written to the ledger. The tool hasn't returned |
| `after_tool` | The tool has returned. The agent hasn't continued |
| `before_reply` | The refund result is saved. The model is about to write the reply |

### Recovery modes

| Mode | What the new process does |
|---|---|
| `resume` | Calls `run_async` with the interrupted `invocation_id` and no new message. This is ADK Resume |
| `restart` | Sends the customer's message again. This is what a client retry does without Resume |

### Tool versions

| Version | What `issue_refund` does |
|---|---|
| `naive` | Writes a refund row every time it runs |
| `key_call_id` | Uses ADK's `function_call_id` as an idempotency key |
| `key_order` | Uses a business key, one refund per order, as an idempotency key |

## Results

20 trials per cell, 480 crash trials, 0 invalid. A no-crash control produced exactly one refund in 60 of 60 runs.

| Recovery | Tool | Correct | Double refund | Lost, but customer told it was done |
|---|---|---|---|---|
| resume | naive | 40/80 | 40 | 0 |
| resume | key_call_id | 80/80 | 0 | 0 |
| resume | key_order | 80/80 | 0 | 0 |
| restart | naive | 51/80 | 19 | 10 |
| restart | key_call_id | 46/80 | 23 | 11 |
| restart | key_order | 68/80 | 0 | 12 |

By crash point:

- With Resume and the naive tool, every crash between the ledger write and the saved tool result repeated the refund. That was 20 of 20 at `after_effect` and 20 of 20 at `after_tool`. Crashes at `before_tool` and `before_reply` recovered correctly every time.
- With Resume, either idempotency key removed every duplicate. The `function_call_id` key works here because Resume reruns the same function call with the same id.
- With restart, the `function_call_id` key does not help. The model issues a new call with a new id. Only the business key stopped duplicates.
- With restart and a crash at `before_tool`, the agent often saw its own unanswered refund request in the history and told the customer the refund was done without issuing it. This happened in 33 of 60 such trials across all three tool versions. No idempotency key can fix that. Resume did not show it once.

## What this does and doesn't show

- It shows a mechanism, not a production failure rate. Crashes are injected at fixed points, and real crashes land anywhere.
- One agent, one tool with a side effect and one model.
- `restart` outcomes depend on what the model does with an incomplete history, so they vary run to run. `resume` outcomes were the same in every trial.
- The ledger is local SQLite. A real payment API needs the idempotency key on the provider side.
- ADK documents Resume as experimental, with at-least-once tool execution. See the [ADK Resume guide](https://google.github.io/adk-docs/runtime/resume/).

## Run it

```
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
gcloud auth application-default login
export GOOGLE_CLOUD_PROJECT=your-project-id

./.venv/bin/python trial.py --variants naive key_order --modes resume --crash-points after_effect --trials 3 --out results/mine.jsonl
./.venv/bin/python analyze.py results/mine.jsonl
```

Reproduce the main table and the control:

```
./.venv/bin/python trial.py --trials 20 --out results/main.jsonl
./.venv/bin/python trial.py --modes none --crash-points none --trials 20 --out results/control.jsonl
./.venv/bin/python analyze.py results/main.jsonl
```

## Files

| File | Purpose |
|---|---|
| `agent.py` | Refund agent, ledger, tool versions and crash points |
| `worker.py` | One phase of one trial, in its own process |
| `trial.py` | Runs the grid of trials in parallel |
| `analyze.py` | Classifies outcomes and prints tables |
| `results/` | Raw JSONL for every trial, including the agent's final reply |
