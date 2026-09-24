# Pi-Skein

Pi-Skein is an installable [Pi](https://github.com/earendil-works/pi) extension that brings [Skein](https://github.com/lmathia2/skein)'s persistent Python tool (PTC) into Pi's TUI and SDK. Pi remains the model and conversation runtime. Pi-Skein adds a brokered `code` tool, durable task evidence, and host verification without ADK or a Skein checkout.

The default model-facing path follows Skein's measured Pi PTC v4.1 adapter: one static `code` tool description, Pi's native text conversation, plain-text cell results, and retained results paged by ID. It does not inject Skein task JSON or a dynamic system packet by default. The v4.1 projection preserves shell diagnostics, partial-read notices, mutation receipts, state delta metadata, and a bounded 50 KB observation. Full cell records remain pageable for the last 32 cells. The measured benchmark had a small edge on its 20-task panel, but that result is not evidence of a general performance advantage.

## Core features

- Persistent CPython cells with `read`, `write`, `edit`, `bash`, and `verify` helpers. The broker records workspace effects; `verify` uses Bash pipefail and raises on failure.
- Append-only, fsynced JSONL task trace with a reduced task ledger, operation receipts, immutable content-addressed artifacts, and a materialized Jupyter notebook.
- Plain-data checkpoints with event-stream and workspace revision checks. Unknown effects stop further execution rather than being replayed blindly.
- Pi-native tool messages and model context. An optional Markdown task packet can be enabled for experiments; the default is the measured v4.1-style Pi surface.
- The same extension factory runs in Pi's TUI or programmatically through Pi's SDK. Offline evidence inspection reads terminal outcome, verification, and unresolved effects without model credentials.

## Install and run

Requirements: Pi, Node 22.19+ (as required by Pi), and Python 3.12+. From a local checkout:

```sh
pi install /absolute/path/to/pi-skein
```

For one invocation without installing:

```sh
pi -e /absolute/path/to/pi-skein --tools code
```

Inside Pi, ask for a coding task. Use `/skein status` to see the task trace, `/skein verify <command>` to run a host check, and `/skein off` or `/skein on` to toggle the extension's tool selection. Pi-Skein selects `code` while active. The model can call `code(code="...")` to run a cell and `code(more="r_...", offset=0, limit=51200)` to page retained output.

Set `PI_SKEIN_PYTHON` if `python3` is older than 3.12. Task state defaults to `~/.pi/skein`; `PI_SKEIN_STATE_DIR` overrides it. Set `PI_SKEIN_TASK_JSON` to a host-owned task contract, for example:

```sh
export PI_SKEIN_TASK_JSON='{"goal":"Fix parsing","criteria":["Empty input works"],"verification_requirements":["python -m unittest"]}'
```

`PI_SKEIN_VERIFY_COMMANDS` accepts a JSON array of host checks when a full task contract is unnecessary. Pi-Skein records a `complete` outcome only after the configured host checks pass. Without configured checks, the run remains open for inspection. `PTC_EVIDENCE_REVIEW=1` enables the v4.1 adapter's one-time evidence-review follow-up. Programmatic callers can set `taskPacket: true` for the experimental Skein Markdown context packet; this changes what the model sees and is off by default.

## Programmatic evaluation

Load `createSkeinExtension({ task, stateDir, python })` from `pi-skein/extension` through Pi's `DefaultResourceLoader`, then create a Pi session with `createAgentSession`. [The SDK setup example](examples/sdk-setup.mjs) shows the wiring, and [the faux-provider run](examples/faux-evaluation.mjs) exercises a complete tool loop without a paid model. Use a separate Pi session, workspace, and state directory for each trial.

After `session.prompt(...)`, read the `pi-skein-task` branch entry with `getSkeinTaskPointer` and call `readSkeinEvidence(stateDir, pointer.taskId)` from `pi-skein/evidence`. Score success from its durable `outcome.status === "complete"`, passing verification, and empty unresolved-effects list, not from final assistant prose. Dispose the Pi session and extension in `finally`.

## Development and scope

```sh
pnpm install --ignore-scripts
pnpm check
PYTHONPATH=python python3 -m unittest discover -s python/tests -v
node examples/check-load.mjs
node examples/integration-check.mjs
node examples/sdk-setup.mjs
node examples/faux-evaluation.mjs
SKEIN_FAUX_FAIL=1 node examples/faux-evaluation.mjs
```

[The port plan](PLAN.md) and [source assessment](PORTABILITY.md) document the broader Skein concepts and remaining gaps. This release has a reduced operational ledger and verification contract; it does not yet implement Skein's full analytical ledger, evidence-strength and scope verification, approval backend, or complete cross-epoch read reuse. The worker's Python restrictions are defense in depth, not an OS sandbox; shell helpers run with the host user's permissions.
