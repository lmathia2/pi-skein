# Skein concepts and Pi extension compatibility

Source review: 2026-09-23. A fresh fetch confirmed Skein `main` at `bf991fde0013f4957ab849d5ebbb366ed30e0b4a`. Pi API baseline: `7fd564cbb78f35f3de14d5382fea692b87ec4026`. This assessment distinguishes source behavior from proposed port behavior; integration compatibility still needs executable tests.

## Conclusion

Skein's core can be delivered as an installable Pi extension without preserving ADK. The right unit to port is its task/evidence/execution layer, with Pi replacing ADK as the sole model and conversation runtime. A Python core preserves existing semantics; a thin TypeScript adapter connects it to Pi. The same adapter can be loaded by Pi's programmatic SDK for evaluations.

The benchmark extension is useful contract evidence, but is not a substitute for porting the production harness. The PTC ADR explicitly identifies the parity scripts as evaluation adapters, not runtime architecture.

## ADRs reviewed

| ADR | Core rule to preserve under Pi |
| --- | --- |
| [ADK-native core](../skein/docs/adr/adk-native-ptc-v4.1-core.md) | One native agent loop, one `code` tool, brokered helpers, ordinary final prose, one host completion authority. Replace the selected loop engine; preserve the separation. |
| [Programmatic tool calling](../skein/docs/adr/programmatic-tool-calling.md) | Persistent CPython; bounded/pageable output; failed worker epochs discarded; safe checkpoints; notebook distinct from conversation and heap. |
| [Execution and recovery](../skein/docs/adr/execution-and-recovery.md) | One effect boundary, optimistic writes, receipts, recovery admission, explicit verification, no blind replay of uncertain effects. |
| [Context and memory](../skein/docs/adr/context-and-memory.md) | Stable instructions separate from dynamic task packets; reduced task state distinct from conversation; addressed evidence and offline-only learning. |
| [Pi comparison](../skein/docs/adr/harness-comparison.md) | Pi already owns native messages, tool scheduling, sessions, compaction, and extensibility. Skein adds evidence and task policy rather than duplicating those systems. Its Pi comparison uses an older commit than this review. |

## Portability by concept

| Concept and implementation | Decision | Pi integration |
| --- | --- | --- |
| `TaskRequest`, `TaskLedger`, criteria and plan rows in [task.py](../skein/harness/core/models/task.py) | Port data models and invariants. Preserve criteria IDs, scope, changed/read files, blockers, progress and validation state. | TUI commands and SDK both submit a normalized task; render compact state in status and task messages. |
| `HarnessEvent`, `LedgerPatch`, deterministic reduction in [events.py](../skein/harness/evidence/state/events.py) | Port event/reducer contract; version any schema changes. Operational observations do not implicitly alter task state: explicit ledger patches do. | Persist Pi IDs as correlations, not as replacement state. Rebuild after restart independently of Pi compaction. |
| Durable JSONL [EventStore](../skein/harness/evidence/state/event_store.py) | Port append/idempotency semantics, strengthen crash and multiprocess handling. | Runtime is sole writer; extension sends observations and stores durable pointers in Pi entries. |
| Analytical [LedgerStore](../skein/harness/evidence/ledger/base.py) and [shadow adapter](../skein/harness/evidence/ledger/shadow.py) | Preserve analytical capability and provenance. Make authority explicit in the port; do not blindly copy dual-store migration wiring. | Offline trace queries/export in SDK; optional indexed backends. No database required to show TUI status. |
| SQLite [tool receipts](../skein/harness/evidence/state/receipts.py) | Port independently of ADK despite its module docstring. Bind tool/operation IDs, argument hashes, results, and workspace before/after. | Correlate Pi tool calls with nested capability receipts; do not conflate their identities. |
| [Recovery admission](../skein/harness/evidence/state/recovery.py) and checkpoint publication | Port integrity checks and unknown-effect refusal; adapt invocation/session identity to Pi. | On resume/fork/navigation, reconcile core state, active Pi branch, and actual workspace before enabling effects. |
| Persistent worker and notebook in `harness/ptc/` | Port Python execution, checkpoint rules, cells, artifacts and result projections. Extract ADK-bound session coordination from `app/agent/ptc.py`. | `registerTool`, sequential execution, cancellation signal, custom renderers; one worker per active task branch. |
| Broker and coding/environment/sandbox modules in `harness/execution/` | Extract neutral operations and policy from `AdkCodingTools`; preserve receipts, confinement, redaction, optimistic writes, timeouts and approvals. | `code` invokes broker; user shell routes through `user_bash`; active tools guarded while Skein mode is enabled. |
| [Pure orchestration](../skein/harness/core/orchestration/core.py) and [progress](../skein/harness/evidence/state/progress.py) | Port bounded packets, observed-action progress, route decisions and steering rules. Adapt legacy step structures internally without requiring structured model replies. | Context/task messages and bounded continuation; Pi still schedules model calls and tool batches. |
| [Verification runner](../skein/harness/verification/runner.py), discovery, scope and managed execution | Port checks, evidence strength, required validations, criteria mapping and baseline handling. Extract workflow glue. | Core returns verification decision; `agent_before_settle` turns it into follow-up or terminal outcome. |
| [Production workflow](../skein/app/agent/workflow.py) | Extract task initialization, budget accounting, verification transitions, steering completion fence, outcome publication. Replace ADK `Context`, nodes and `Workflow`. | Shared adapter invokes framework-neutral controller at Pi lifecycle boundaries. |
| ADK tracing/metrics/artifact plugins | Replace adapters; retain durable event/metric meaning. | Observe request/message/tool/error/usage events. Do not serialize Pi messages through ADK schemas. |
| [Verified learning episodes](../skein/harness/evidence/learning.py) | Preserve as offline export, strengthened by canonical recovery/terminal validation. | SDK utility over persisted traces; no runtime prompt mutation. |
| ADK factories, runner, persistence services, providers, AG-UI transport | Omit. | Pi supplies providers, auth, conversation, streaming and TUI; SDK supplies headless sessions. |
| Harbor/Pier campaign plumbing | Optional later adapter. | Calls the same headless API with isolated trials and distinct benchmark scoring. |

## Implementation details that change the plan

1. **There are two meanings of ledger.** `TaskLedger` is the reduced operational task projection. `LedgerStore` records canonicalized events from multiple sources for queries. The ADR describes analytical memory as optional, but `LedgerBackedEventStore` can read operational events through the analytical ledger after read-repair and dual-write appends. The proposed port chooses the task event stream as canonical and rebuildable analytical projections; that is an explicit simplification, not a claim that the current adapter already works that way.

2. **Receipts are more than log entries.** `ToolReceiptStore` uses SQLite keys and claims to refuse retry when an existing operation is unfinished or its arguments differ. Its mutable current receipt rows and the immutable causal trace have different jobs. Preserve both semantics, reconcile interrupted publication between them, and never claim arbitrary-shell exactly-once execution.

3. **Recovery is intentionally strict.** `validate_recovery_evidence` checks invocation/session identity, schema/reducer version, event/receipt hashes, checkpoint publication, rebuilt ledger, and a continuous chain of receipted workspace fingerprints. `unresolved_execution` explicitly admits no reconciliation event today. A later successful test or unchanged workspace cannot clear an unrelated unknown shell effect. The initial port must retain this refusal; a future explicit reconciliation operation needs its own contract and tests.

4. **Verification is richer than “run tests.”** `_verify_task` discovers validation plans, adds task requirements, checks scope and evidence strength, admits criterion evidence, handles recorded baselines, and consults unknown effects before running checks. Preserve these rules before replacing them with a generic exit-code gate. Shell observations may be reused by the host only under the existing command/environment/workspace validity conditions; a successful shell call alone is not a task verdict.

5. **Not every configured guard is enforced.** `_verification_transition` computes verification attempts and repeated failure counts, but assigns `blocked_on_verification = False`. Do not assert its retry limits already work. The port must define and test explicit repair limits while preserving broader task budgets. The `HarnessOutcome` model checks for a passing report, but the workflow carries additional evidence/steering guards; porting the model alone loses them.

6. **ADK removal requires dependency extraction.** Direct imports remain in `app/agent/ptc.py`, workflow/builders, core agent contracts, telemetry/tracing plugins, and provider/session adapters. Configuration also includes ADK-specific settings. Some neutral-looking packages can import coupled modules through `__init__.py`. Prove absence of ADK with a clean installed dependency graph and import/execution smoke test, not a rename or optional import.

7. **Durability has limits to address.** The JSONL implementation fsyncs each append and validates idempotency but has only a process-local thread lock and reads the stream on append. Preserve durable acknowledgments, add single-writer ownership and corruption tests, and measure scaling before introducing indexes. A snapshot is a cache, never replacement history.

## Pi compatibility boundaries

Use public extension/package APIs at the inspected baseline:

- `registerTool` with TypeBox and `executionMode: "sequential"`; propagate abort and output bounds.
- `session_start` / `session_shutdown` for resources; active branch entries for identity; test all navigation and reload paths.
- Structured prompt options and `context` for bounded projections without replacing native message semantics.
- `tool_call`, `setActiveTools`, and `user_bash` for the model-facing execution surface.
- `agent_before_settle` for actionable verification continuation, `agent_settled` for final notification.
- `appendEntry`, tool-result details and renderers for evidence references and TUI inspection.
- `createAgentSession` with explicitly supplied resources for reproducible evaluation sessions.

An installed extension can run the complete core, but cannot claim global control over trusted extensions, external file edits, all process permissions, or Pi's CLI exit code. A provider/request observer is not automatically a fail-closed admission gate: prove abort/error propagation before claiming strict pre-dispatch budget or durable-model-intent enforcement. These are phase-1 compatibility tests. Core effect execution can still refuse when persistence or policy fails. The standalone evaluation wrapper checks durable outcomes and controls its own exit status.

Interactive usage should preserve Pi's normal conversation: activating Skein starts explicit task state, a completed task remains immutable, and a later request starts a new task or an explicit recorded continuation. Questions and requests for explanation do not automatically trigger coding checks. Approval-required operations use Pi UI where available; headless runs use an explicit host policy or return blocked, never wait for an unavailable dialog.

Deliver one installable `pi-skein` extension package with host-provided Pi peer dependencies and bundled runtime assets. “Standalone” means this package needs no original Skein checkout or ADK; Pi remains its host. Programmatic evaluations load the same extension through Pi's SDK or subprocess interfaces. Do not create a separate pi-skein SDK/CLI distribution or bundle a Pi engine. Confirm installation from a packed artifact into a clean Pi environment, as well as TUI reload and uninstall cleanup. Compatibility means no Pi core patch, no private imports, no duplicate Pi runtime inside the extension, and no source-checkout dependency.

## Required first release

Ship PTC, broker, durable trace, task ledger reduction, receipts, notebook/artifacts, bounded projections, steering/progress/budgets, safe recovery, and host-verified outcomes together. Include TUI usage and programmatic run/resume/inspect/export. Trace and ledger are foundational from the first working slice, not later observability add-ons. Optional memory databases, search, campaigns and richer UI can follow.

See [PLAN.md](PLAN.md) for the implementation sequence and current progress. The assessment records the full target contract; the experimental implementation in [README.md](README.md) covers a narrower working slice.
