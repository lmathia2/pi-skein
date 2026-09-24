# Pi-Skein: an installable Pi extension

Written 2026-09-23. Implementation started on 2026-09-23. [README.md](README.md) describes the working experimental extension and its current limits.

The current-source and ADR assessment is in [PORTABILITY.md](PORTABILITY.md), including implementation gaps and public Pi API compatibility boundaries.

Current implementation: one installable Pi package, copied persistent worker and v4.1 text-result projection, local broker, append-only task events, reduced task ledger, artifacts, notebook projection and materialized `.ipynb`, plain checkpoints with event/workspace integrity checks, Pi `code` tool and user-shell routing, Pi session pointers, static v4.1 Pi tool prompt, context/tool projection evidence, retained read references, host verification, and offline evidence inspection. The dynamic Markdown packet is opt-in; the default preserves Pi's native message context as in the measured v4.1 adapter. Python tests, Pi package loading, Pi SDK tool execution, typechecking, a packed temporary Pi installation, and a complete faux-provider Pi run have passed. The remaining phases below are still planned; in particular complete context selection, scope/evidence-strength verification, approvals, full analytical ledger semantics, and strict provider-budget admission are not complete.

## Goal and recommendation

Make `pi-skein` a self-contained, ADK-free extension installed into Pi. Use the same extension in Pi's TUI and in Pi SDK sessions for programmatic evaluations. Both use the same task controller, durable append-only trace, reduced task ledger, notebook, broker, recovery, and host verification. Pi owns model calls, provider authentication, native tool continuation, conversation history, streaming, compaction, and the user interface.

Use a TypeScript extension plus a supervised Python runtime process. Preserve the Python cell language and existing worker semantics first. Do not rewrite PTC in JavaScript or embed the ADK agent loop inside Pi. Extract the necessary runtime into this repository so an installed package does not depend on sibling source checkouts.

“Standalone” means `pi-skein` can be installed as a Pi extension without the original Skein checkout, a Pi source checkout, ADK, or Harbor. Pi is the required host. Deliver one Pi extension package containing its implementation and runtime assets. Programmatic evaluation loads this package through Pi's existing SDK; there is no separate pi-skein agent distribution, SDK package, or CLI product. This is a port of Skein's harness concepts, not just its `code` tool.

## Source baseline

Both repositories were cloned from their default `main` branches into `/Users/mathiasl/src`:

| Repository | Local checkout | Inspected revision |
| --- | --- | --- |
| [Skein](https://github.com/lmathia2/skein) | `../skein` | `bf991fde0013f4957ab849d5ebbb366ed30e0b4a` |
| [Pi](https://github.com/earendil-works/pi) | `../pi` | `7fd564cbb78f35f3de14d5382fea692b87ec4026` |

Pi's coding-agent package declares version `0.87.1`. Treat the inspected commit as the initial API baseline; prove published-package compatibility rather than assuming every main-branch API is in the release with that version.

### Existing implementation to build on

- [Prototype extension](../skein/scripts/pi_skein_ptc_extension.mjs): already registers `code`, exposes result paging, assembles stable instructions, and optionally requests one evidence-review follow-up. It uses a Harbor HTTP bridge, keeps verification counters in memory, and does not implement the full host completion contract.
- [Harbor bridge and runner](../skein/scripts/pi_code_tool_harbor.py): connects `PersistentPythonWorker` to a Pier broker, retains results, and checkpoints plain values. Reuse behavior and fixtures, not the evaluation runner as the production runtime.
- [Worker](../skein/harness/ptc/repl/worker.py): owns the Python namespace and calls a parent broker. Its import/source restrictions explicitly are defense in depth, not an OS security sandbox.
- [Production PTC integration](../skein/app/agent/ptc.py): contains useful persistence and evidence logic, but imports ADK types and tools. Extract framework-neutral responsibilities instead of importing this module into the new runtime.
- [Architecture](../skein/docs/architecture.md) and [outcome contract](../skein/harness/core/models/outcome.py): host verification, not assistant prose, authorizes `complete`.
- Pi's [extension guide](../pi/packages/coding-agent/docs/extensions.md), [API declarations](../pi/packages/coding-agent/src/core/extensions/types.ts), and [package guide](../pi/packages/coding-agent/docs/packages.md) provide the current integration surface.

## Responsibility mapping

| Skein responsibility | Destination |
| --- | --- |
| ADK agent, runner, sessions, provider serialization | Pi; do not port |
| Persistent Python cells and helper contract | Python runtime extracted from `harness/ptc/` |
| Filesystem, shell, receipts, workspace checks | Python broker extracted from `harness/execution/` |
| Durable task events and causal trace | Core append-only event store; Pi lifecycle adapter records model/session observations |
| Reduced `TaskLedger`, progress, budgets, steering | Core deterministic reducers and task controller shared by both entry points |
| Analytical `LedgerStore` | Rebuildable query projection over canonical events, with optional indexed backends |
| Notebook, artifacts, output/context projections | Core durable workbench and content-addressed storage |
| Work-batch completion and retry policy | Core task controller and verifier; Pi adapter translates decisions into continuation |
| Stable PTC instructions and task packet | Pi tool descriptions/guidelines and bounded dynamic messages |
| Conversation compaction | Pi; preserve Skein state independently of compacted conversation |
| Checkpoints and uncertain effects | Python runtime recovery, selected by Pi session/branch identity |
| Harbor/Pier integration | Optional evaluation adapter after local functionality works |
| Offline learning and optional memory databases | Deferred; retain compatible evidence where practical |

Extract the framework-neutral semantics of Skein's workflow, including budgets, steering, progress, recovery, and verification. Replace its ADK wiring, provider adapters, and tracing plugins with a Pi lifecycle adapter. Avoid a second model/tool loop.

## Proposed design

```text
Pi TUI                       Pi SDK evaluation session
       |                         |
       +------------+------------+
                    |
          Installed pi-skein extension (TypeScript)
          tool registration, lifecycle, context, continuation
                    |
          Versioned private stdio protocol
                    |
          Bundled extension runtime (Python)
          task controller + deterministic TaskLedger reducer
          append-only events + analytical ledger projection
          notebook + artifacts + checkpoints + bounded projections
          broker + verifier + PersistentPythonWorker
```

The Python core is the sole task-policy authority. The TypeScript adapter requests decisions and translates them into Pi operations; the TUI and SDK do not implement separate completion, budget, or recovery logic.

### Programmatic evaluation surface

Use Pi's existing [session SDK](../pi/packages/coding-agent/docs/sdk.md), with `createAgentSession` and a resource loader that loads the installed pi-skein extension. Supply resources explicitly for evaluation so personal extensions, settings, and project discovery cannot silently change an experiment. Credentials and trusted project instructions are explicit inputs, not copied into manifests.

Provide a checked evaluation example that creates a Pi session, loads pi-skein, supplies the task contract and artifact directory, subscribes to events, prompts the session, reads the durable outcome, and disposes the session. Use Pi's existing steering, cancellation, and resume APIs. Expose only extension-specific configuration and typed outcome/trace/ledger access from this package where needed. Inspection/reduction must work offline without model credentials. Resume restores both the matching Pi conversation and core task state; ledger reconstruction alone is not conversation resume.

Python evaluators can use Pi's existing subprocess/RPC interfaces with pi-skein loaded. Keep any benchmark wrapper in `evals/`; it checks the extension's durable terminal outcome rather than inferring success from Pi's exit code. Do not introduce a second public command-line interface or agent loop.

Each trial gets its own task/attempt identity, Pi session, worker, trace, and isolated workspace. A campaign runner bounds concurrency across trials. Retry creates a new attempt linked to the previous one; resume continues the existing attempt. Record source/package versions, effective configuration hash, task manifest hash, model/provider settings, workspace baseline, and environment metadata. Separate harness verification from benchmark scoring; hidden benchmark tests never enter the model context.

### Extension and tool surface

Register synchronously at factory load time. Start the runtime lazily from `session_start`, a command, or the first tool call; Pi explicitly discourages starting processes in extension factories. Close resources through an idempotent `session_shutdown` handler.

Register `code` with a TypeBox schema matching the existing cell and result-page contract. Validate exactly one of `code`, `result_id`, or `more`. Keep helper signatures and stable instructions generated from one versioned contract. Use `executionMode: "sequential"` and a runtime queue because parallel cells cannot safely share one Python namespace.

In active Skein mode, select `code` with `pi.setActiveTools(["code"])`. Add a `tool_call` guard against accidental activation of other model tools while that mode is active. Preserve the previous active set for explicit disable. Route user `!` shell commands through the same broker using `user_bash`, or refuse them when routing is unavailable. Other trusted extensions and external editors can still modify the workspace; tool selection is not a sandbox.

Proposed commands: `/skein status`, `/skein verify`, `/skein reset`, and `/skein on|off`. Keep all runtime behavior usable in print, JSON, and RPC modes; terminal status/rendering is optional. Reset must state which live values it discards and preserve historical evidence.

### Runtime transport and packaging

Use private stdio rather than a localhost HTTP server for ordinary local use. Define a protocol version and request IDs, with methods for handshake/contract, execute, page result, verify task, restore, cancel, and shutdown. Responses carry typed status, bounded text, evidence references, and checkpoint metadata. Reserve stdout for protocol frames; logs go to stderr or artifacts. Set frame-size limits and validate inputs on both sides.

Forward Pi's tool `AbortSignal` to cancellation. Keep cancellation handling responsive while a cell runs; cancellation cannot sit behind the execution queue. Terminate shell process groups and the worker when graceful interruption fails, record any uncertain effect, and reap children on shutdown. Never retry an effectful request merely because its transport response was lost.

Ship the extracted Python source with the Pi package and provide an explicit setup command that creates a versioned environment using `uv` and a lockfile. Resolve packaged assets relative to the installed package, never `../skein`. Do not install Python dependencies during extension discovery. Report missing prerequisites with the exact setup command.

Target Python 3.12 initially, matching Skein's documented installation path, while noting its project metadata currently permits 3.11. Target Pi's Node minimum, currently 22.19.0. Declare host-provided Pi packages and `typebox` as peer dependencies; use pinned development dependencies for compatibility testing. The extension must not bundle or load a duplicate Pi engine. Ship the matching Python runtime with versioned assets and a locked environment. ADK must be absent from every runtime dependency graph, including transitive imports; Harbor, Trackio, and optional memory backends remain optional.

### Durable trace, ledgers, and state authority

Preserve Skein's distinct stores rather than calling all of them “session state”:

| Concept | Role and porting contract |
| --- | --- |
| Pi session | Native conversation and tool messages; authoritative for model continuation |
| `EventStore` / durable trace | Canonical append-only task facts and causality; sufficient to rebuild task state |
| `TaskLedger` | Deterministic reduction of task events: requirements, progress, changes, steering, budgets, verification, blockers, outcome |
| Notebook | Durable record of cells, results, and checkpoint references; distinct from the live heap |
| Worker heap | Transient execution state; only explicitly checkpointable values survive replacement |
| Artifact store | Immutable, content-addressed large bodies referenced by trace and notebook |
| Analytical `LedgerStore` | Queryable projection with event provenance, source watermarks, and content hashes; never a second task authority |

Port the contracts in [task events and reducer](../skein/harness/evidence/state/events.py), [event store](../skein/harness/evidence/state/event_store.py), [progress accounting](../skein/harness/evidence/state/progress.py), and [analytical ledger interface](../skein/harness/evidence/ledger/base.py). Retain a lightweight JSONL analytical implementation and exporter initially; DuckDB/search acceleration and memory programs can remain optional. Offline analysis is a supported core use case.

The canonical event envelope carries schema version, task/attempt/branch identity, monotonic sequence, event ID, timestamp, kind, payload, correlation/parent IDs, and idempotency key. Record task creation, steering received/exposed, model dispatch/result/error and usage, tool/cell lifecycle, capability intent/result, artifact publication, exact bounded context projections, workspace observations, checkpoint/recovery, verification, and terminal outcome. Replace ADK tracing callbacks with Pi event adapters while preserving causal links. Streaming tokens need not each be fsynced; finalized messages and model boundaries must have durable evidence references.

Start with one writer per task and an interprocess ownership lock. Skein's existing JSONL store fsyncs appends but uses only a thread lock; do not mistake it for multiprocess-safe storage. Require durable effect intent before execution and durable result receipts afterward. Write artifacts atomically before committing references. Lost acknowledgments use idempotency lookup; they do not authorize effect replay. Persistence failure stops further effects and prevents verified completion.

Reducer snapshots are disposable caches keyed by schema version and event watermark/hash. Rebuilding from the trace must yield the same ledger, progress, budgets, and outcome. Reject gaps, conflicting duplicates, and unsupported schemas; test torn final records and expose recovery explicitly without silently rewriting history. Corrections and reconciliation append new events. Branches reference a parent watermark and maintain distinct subsequent streams. Define schema migration and export before changing stored formats.

The initial port does not admit events that clear unknown effects: current Skein recovery explicitly rejects that. Any future effect-reconciliation event requires a separate validated contract. Rebuilding projections or matching Pi references must not clear execution uncertainty.

Pi session entries store pointers, not authoritative copies of the ledger. If a process dies between recording a core event and persisting its Pi reference, recover through stable correlation IDs and reconcile the two stores. Do not assume an atomic transaction across Pi and Skein. A terminal result is valid only after its supporting report, artifacts, and outcome event are durable.

### Evidence and recovery

Store large results and append-only records outside the workspace being verified. Map `(Pi session, active branch, Skein task)` to an isolated runtime and evidence directory. Persist compact versioned pointers through `pi.appendEntry()` and tool-result `details`; reconstruct branch-sensitive state from `sessionManager.getBranch()`, not the entire session file.

Record each cell's source, IDs, ordered broker receipts, status, output artifact, and checkpoint identity. Keep model-facing output bounded and pageable. Retain full evidence according to a documented retention policy; do not inherit the prototype's in-memory eviction as durable storage.

Restore only committed checkpointable plain values. Functions and other live-only state may disappear after restart and must be reported as lost. A failed cell's namespace rollback does not undo filesystem or shell effects already performed; retain those receipts. An interrupted operation with unknown effects blocks verified completion until reconciled. Do not replay writes, commands, or historical cells to rebuild a heap.

Session switching, tree navigation, forks, reload, and resume must never attach the wrong heap. On a branch change, stop the previous worker and select the checkpoint associated with the chosen branch, then compare against the actual workspace. Pi conversation navigation does not roll back files. Use a lock to prevent two processes from owning the same runtime identity concurrently.

### Verified completion

Use `agent_before_settle` as the actionable boundary. It exposes `outcome`, `context.canContinue`, and bounded continuation through returned entries plus `continue: true`. `agent_end` is too early because Pi may retry or continue afterward. Use `agent_settled` only for notification.

The shared core maintains an explicit task contract: goal, acceptance criteria, trusted verification commands, limits, and workspace scope. The TUI and evaluation SDK supply that contract through the same API. A model-issued `verify(...)` supplies evidence; it cannot replace that contract with an easier check. Reuse a passing report only when its commands, scope, and workspace revision still match.

At settlement:

1. Map provider errors and user aborts to `failed` and `cancelled`; never infer success from process exit zero or final prose.
2. Reject unresolved unknown effects and incomplete recovery.
3. Compare the workspace against the verified revision, including relevant untracked files, and run required host checks when evidence is missing or stale.
4. If checks fail and continuation is possible within task/time/repair limits, append concise evidence and request one continuation.
5. Otherwise persist a terminal `HarnessOutcome`-equivalent record: `complete`, `answered`, `blocked`, `failed`, or `cancelled`. Only a passing report permits `complete`; use `answered` only for eligible direct answers with unchanged workspace, no verification-triggering mutation, and no pending steering.

Catch verifier/coordinator errors and persist failure explicitly: Pi can report extension-handler errors and continue, so throwing alone is not a completion gate. Persist repair budgets across reload and never continue unconditionally.

Pi's settlement event itself does not offer a custom process exit status or retract already streamed assistant prose. Therefore the extension's durable outcome and visible verification status are authoritative. A small headless evaluation wrapper must require a valid terminal outcome and map it to exit status; if absent or inconsistent, fail the run. Prove this boundary in phase 1 before promising CLI-level success semantics.

### Workspace revisions and context

Replace the prototype's operation-count verification gate with evidence tied to a workspace revision. A `bash` call may mutate files; do not classify safety solely from command text. Measure revision checks after potentially effectful work and always reconcile at final verification to catch external edits. Define included paths and generated-output exclusions explicitly; use pre/post verification observations so checks that alter source cannot silently certify stale state.

Keep stable helper instructions in tool descriptions/guidelines. Add task-specific requirements and compact verification feedback through Pi messages. If using the `context` hook, preserve native tool call/result pairing and Pi's prompt handling. Port Skein's bounded task-packet behavior without replacing Pi's transcript or compaction. Required control sections must fail explicitly if they exceed the configured budget.

## Implementation sequence and acceptance gates

### 1. Validate shared core contracts and Pi integration

Create one Pi extension package manifest and a minimal adapter with a fake runtime. Specify versioned task, event, ledger, receipt, outcome and transport contracts. Load the same extension from the TUI and a Pi SDK test host. Exercise sequential `code`, tool activation, cancellation, session lifecycle, `agent_before_settle`, and non-interactive outcomes against the pinned Pi baseline. Verify whether the published package has the required API; document the initial supported version or commit. Test fail-closed budget/persistence admission separately from notification hooks.

**Exit:** a deterministic fake-provider run fails a check, continues once, passes, and records exactly one verified outcome. Abort, provider error, hook failure, and exhausted repair budget cannot produce `complete`. No Pi core fork is needed, or a precise API blocker is documented before further extraction.

### 2. Extract the ADK-free task and execution core

Bring over the worker, broker, durable event store, TaskLedger reducer, receipts, notebook/artifacts, bounded packets, verification and task-policy dependencies. Split ADK-specific integration from neutral code. Include durable task creation and effect intent/result events in the first executable slice. Record source provenance, preserve applicable Apache-2.0 notices from Skein and MIT notices for copied Pi code, and audit the transitive import graph. Implement the versioned stdio server and local workspace backend.

**Exit:** clean-environment installation runs persistent cells and all five helpers without ADK or Harbor installed. Existing relevant worker tests pass, and protocol tests cover malformed frames, version mismatch, oversize output, cell errors, and disconnects.

### 3. Deliver the extension for TUI and Pi SDK use

Connect the real runtime to `code`, result paging, cancellation, status rendering, explicit setup, and lifecycle cleanup. Add active-tool enforcement and brokered user shell execution. Implement optimistic write/edit checks and preserve useful Python exception details. Add checked Pi SDK and subprocess evaluation examples that load the installed extension. Expose trace and ledger inspection without credentials.

**Exit:** from a fresh installation, a fixture can be read, edited, tested, and inspected using only `code`. Pure Python values persist across calls; concurrent calls serialize; cancellation leaves no orphan processes. JSON/print/RPC behavior does not depend on UI dialogs.

### 4. Harden durability and implement recovery

Harden the foundational event/receipt stores; add atomic checkpoints, persistent pages, Pi entry references, locking, and branch-aware restore. Implement programmatic resume, ledger reconstruction, and analytical projection/export. Inject worker and host crashes around effect intent, completion receipts, and checkpoint commits. Retain current recovery refusal for unknown effects: a new reconciliation protocol is separate future work, not assumed present.

**Exit:** resume restores safe values without repeating effects; uncertain effects prevent completion; fork/navigation cannot reuse another branch's heap or verification; corruption and missing artifacts produce explicit recoverable failures.

### 5. Enforce task verification and bounded context

Integrate the trusted task contract, criteria/scope/evidence-strength checks, revision-sensitive reports, bounded repair continuation, terminal outcomes, and evaluation outcome handling. Implement shared steering fences, observed-action progress and task budgets. Preserve Pi's stable prompt and conversation ownership. Record fingerprint and verifier durations separately.

**Exit:** regression fixtures cover failing tests, edit after verification, read-only exploration after verification, external edits, untracked source changes, mutating checks, known blockers, provider failure, and budget exhaustion. Nothing completes on a model assertion alone.

### 6. Measure parity and package for use

Adapt the frozen Skein smoke fixtures and then the diagnostic E13 tasks. Keep Harbor/Pier as optional dependencies. Compare the extracted runtime with the existing Pi/Skein prototype and a Pi baseline using matched model, task set, budgets, and concurrency. Record valid-run rate, pass rate, cells, model turns, verification calls, tokens, cost, latency, and revision-scan time. Treat provider interruptions as invalid/error trials, not ordinary quality failures.

**Exit:** required contract tests all pass; a packed installation works without sibling repositories; valid smoke results have no unexplained semantic regressions. Agree evaluation thresholds before a paid campaign. Publish broader quality/performance claims only after matched evidence supports them.

## Proposed repository layout

```text
pi-skein/
  PLAN.md
  README.md
  PORTABILITY.md
  package.json                 # one Pi package; pi.extensions -> ./src/index.ts
  src/
    index.ts                   # extension registration and optional TUI rendering
    runtime-client.ts          # bundled Python runtime lifecycle and transport
    session-state.ts           # task/branch references
    settlement.ts              # translates core decisions into Pi actions
    evidence.ts                # typed outcome, trace and ledger access
  examples/
    evaluation.ts              # Pi SDK session loading this extension
  python/
    pyproject.toml
    uv.lock
    pi_skein_runtime/          # task controller, ledgers, worker, broker, verifier
  scripts/                    # explicit runtime setup and packaging
  tests/                      # extension and cross-language contracts
  python/tests/               # extracted and new runtime tests
  evals/                      # optional frozen benchmark adapter
```

## Scope and first milestone

The first development milestone is phases 1–3: one installable extension used in both Pi TUI and Pi SDK sessions, with persistent Python, brokered helpers, foundational durable events and task ledger, bounded output, and reliable cleanup. Mark it experimental until phases 4–5 establish recovery and verified completion. The first supported release includes phases 1–5; durable trace and ledger are required core functionality.

Defer a TypeScript PTC rewrite, custom Pi UI, online memory/learning, remote multi-user service, and mandatory containers. The supported initial boundary is a trusted local workspace; Python restrictions and Pi tool gating do not provide OS isolation. Keep container-backed execution available as a later backend without changing the public `code` contract.

The historical [v4.1 report](../skein/docs/experiments/e13-pi-skein-v4.1-comparison.md) found excessive repeated verification. The [v4.2 report](../skein/docs/experiments/e13-pi-skein-v4.2-comparison.md) reduced verification calls but did not establish an efficiency win and exposed invalid success reporting after provider errors. These findings motivate revision-based evidence, separate runtime-cost instrumentation, and explicit terminal outcomes; they are not proof that the new extension will outperform Pi.
