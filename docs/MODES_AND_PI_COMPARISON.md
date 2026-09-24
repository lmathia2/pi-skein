# Mode boundaries and comparison with default Pi

Reviewed against the local Pi coding-agent source at `7fd564cbb78f35f3de14d5382fea692b87ec4026` and executable Pi SDK/faux-provider runs. This document describes the implementation in this repository, not a proposed split into multiple installable extensions.

## Decision

Keep one Pi extension package with three independent controls:

1. **Code mode** selects Skein's persistent Python `code` tool. Turning it off restores the active Pi tool set captured before PTC took over; when no prior set is available, it selects Pi's built-in `read`, `bash`, `edit`, and `write` tools. The worker and task journal are not started for a Pi-native turn.
2. **Pi observation trace** records model-context messages, completed messages, and model tool calls/results in a separate fsynced JSONL stream with content-addressed artifacts. It works with Pi-native tools or PTC and does not execute tools or change model input. Turning it off suppresses these Pi observation records.
3. **PTC model-facing contract** chooses between the full v4.1-style `code` description and a minimal usable description. The optional Markdown task packet and evidence-review follow-up run only when both code mode and contract are on. With code mode off, this switch has no model-facing effect.

The PTC task journal is not the optional Pi observation trace. Whenever code mode executes a cell, its broker must persist effect intents/results, cell results, checkpoint markers, and verification decisions. Those events support replay refusal and checkpoint/workspace validation. Allowing `trace off` to remove them would make code mode unsafe, so `trace off` means *general Pi observation off*. A separate volatile PTC implementation would need its own explicit execution/recovery contract and is outside this change.

The switches are session-local commands (`/skein code on|off`, `/skein trace on|off`, `/skein contract on|off`) and SDK options (`codeMode`, `traceExecution`, `modelContract`). Environment defaults are `PI_SKEIN_CODE_MODE`, `PI_SKEIN_TRACE`, and `PI_SKEIN_MODEL_CONTRACT`, with `0` meaning off. `/skein on|off` remains an alias for the code switch. Startup defaults are all on.

## Authority and data paths

```text
Pi TUI or SDK session
  ├─ code off  → Pi's own read/bash/edit/write tools and model loop
  ├─ trace on  → src/pi-trace.ts → pi-trace/<sha256(session ID)>/{events.jsonl,artifacts/}
  ├─ contract  → src/ptc-prompt.ts → active code tool description only
  └─ code on   → src/runtime-client.ts → Python PTC worker/broker
                                  → <sha256(task ID)>/{events.jsonl,artifacts/,checkpoint.json,notebook.ipynb}
```

`src/modes.ts` owns tool selection and the three switch values. `src/index.ts` composes the mode controller, Pi lifecycle hooks, `code` tool, command handlers, optional Pi observer, and Python runtime. The Python `runtime.py` owns the PTC task ledger and safety decisions. Neither `src/modes.ts` nor `src/pi-trace.ts` can mark a PTC task complete. `src/evidence.ts` reads both stores offline: `readSkeinEvidence` reduces PTC task outcomes, while `readPiTrace` and `readPiTraceArtifact` inspect Pi observations. The Pi session transcript remains the conversation authority.

The Pi trace stores exact hook-visible context/messages/tool inputs and results as immutable local artifacts, with hashes/URIs in its event stream. It records a `pi.tool_started` before a model tool call and `pi.tool_completed` afterward. An unmatched start is visible but is **not** the same as a PTC broker receipt or recovery verdict. In code mode, a separate PTC journal records broker operations; with code mode off, Pi executes its native tools without a Python worker or PTC task pointer.

## Executable comparison with default Pi

`examples/mode-matrix.mjs` runs isolated Pi SDK sessions with a fake model. The extension-free baseline and each Pi-Skein mode receive the same user prompt. It compares the provider-visible system sections, active tool names, and actual native Bash continuation. The comparison passed with the checked Pi version:

| Setup | Active model tools | Provider-facing system sections | Execution and durable state |
| --- | --- | --- | --- |
| Default Pi, no extension | `read,bash,edit,write` | Baseline | Native Pi Bash; no Skein files |
| Pi-Skein, code off, trace off | `read,bash,edit,write` | Byte-for-byte equal to baseline sections in the fixture | Native Pi Bash; no PTC task or Pi trace |
| Pi-Skein, code off, trace on | `read,bash,edit,write` | Byte-for-byte equal to baseline sections in the fixture | Native Pi Bash; Pi trace contains context, call, result, message; no PTC task |
| Pi-Skein, code on, trace off, contract on | `code` | Full v4.1-style `code` guidance | Persistent Python; mandatory PTC task journal; no Pi observation trace |
| Pi-Skein, code on, trace off, contract off | `code` | Minimal `code` guidance, measurably different from full contract | Same PTC execution and mandatory task journal; no Pi observation trace |

The matrix also exercises `/skein code off/on`, `/skein trace on/off`, and `/skein contract on/off` in a live SDK session. After code off, Pi's four built-in tools return; after code on, only `code` is active. Changing contract re-registers the tool definition and updates what the next model request sees. The trace-only run verifies that a context artifact contains the hook-visible system message and that a native Bash completion is recorded. The code-without-Pi-trace run verifies a `cell.completed` event in the PTC task journal and absence of a Pi observation trace.

This is a compatibility regression check, not a quality or speed benchmark. It proves prompt/tool equivalence for the tested isolated default-Pi setup. It does not establish equivalence for every project instruction, extension ordering, provider, or Pi release. The earlier 20-task PTC v4.1 benchmark compared the `code` approach with Pi Code Tool; the new mode switches were not evaluated for task success rate.

## Review findings and limits

- **Default fallback:** Pi's SDK source declares `read`, `bash`, `edit`, and `write` as its default active built-ins. If Pi was launched with an explicit `--tools code` allowlist, Pi excludes those built-ins from the registry and an extension cannot restore them. The README's single-invocation command therefore omits `--tools code`; `/skein code off` warns if Bash is unavailable.
- **PTC durability:** General trace off still leaves the PTC task journal on. The Python runtime now excludes its whole state root from workspace snapshots when that root is inside the workspace, so Pi observation writes do not invalidate PTC checkpoints or host verification. A test closes and reopens a checkpoint after writing sibling Pi trace data.
- **Rejected recovery cleanup:** A checkpoint/workspace mismatch still refuses resume. The runtime now closes its worker and releases the task lock when initialization rejects that checkpoint; the prior path left an open file descriptor.
- **Native Pi execution:** Trace-only mode observes model-driven `tool_call`/`tool_result` events. It does not replace Pi's native tool implementations, their permissions, or result formatting. User-initiated `!` shell execution in code-off mode stays entirely with Pi and currently has no Pi-Skein completion event; in code-on mode it goes through the PTC broker.
- **Model-facing contract:** Contract off removes the extended guidance, optional task packet, and optional evidence-review follow-up. The `code` tool still needs a short description and schema to be usable. Contract toggles have no effect on the model when code mode is off. `taskPacket: true` remains opt-in and is suppressed when contract is off.
- **Observation scope:** The Pi trace records the context presented to Pi-Skein's `context_with_system` hook. Another extension later in the hook order may still modify the request or tool result. Exact provider-facing equality was verified only in the isolated mode-matrix fixture. Pi's own session transcript remains the source for resuming a conversation.
- **Concurrency and privacy:** The Pi trace validates sequence numbers on reopen and fsyncs appends; it does not yet provide cross-process single-writer locking or torn-record repair. Its artifacts contain local copies of model context and tool data and may include sensitive project content. New Pi trace directories/files are created with restrictive permissions. Keep the state root private and give each concurrent evaluation its own session/state directory.
- **Persistence of switches:** Slash-command changes live for the current Pi extension session. A new session initializes from SDK options or environment defaults. The modes are not persisted as PTC task events.

## Verification run

The following checks passed after the split:

```sh
pnpm check
PYTHONPATH=python python3 -m unittest discover -s python/tests -v
node examples/check-load.mjs
node examples/integration-check.mjs
node examples/sdk-setup.mjs
node examples/faux-evaluation.mjs
SKEIN_FAUX_FAIL=1 node examples/faux-evaluation.mjs
node examples/mode-matrix.mjs
pnpm pack --dry-run
```

The Python suite contains 13 tests, including checkpoint isolation from a sibling Pi trace. The Pi checks cover package loading, PTC execution, host-verification success and bounded failure, both-off baseline equivalence, trace-only native Bash, contract variation, and runtime switching. This review does not claim full Skein analytical-ledger, criterion-strength verification, or approval-backend parity.
