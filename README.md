# Pi-Skein

Pi-Skein is an installable extension for [Pi](https://github.com/earendil-works/pi). It gives Pi a persistent Python `code` tool derived from Skein's PTC v4.1 work, plus a durable record of cells, workspace effects, task state, and verification. It runs in Pi's interactive TUI and in sessions created with Pi's SDK. Pi still owns the model loop, conversation, provider configuration, streaming, and compaction. Pi-Skein has no ADK, Harbor, or original Skein checkout dependency.

## What the model gets

While Pi-Skein is active, Pi exposes one model tool, `code`. A call with `code="..."` runs a Python cell; variables and functions persist between cells in the same worker. The following functions are preloaded and called without `await`:

| Function | Behavior |
| --- | --- |
| `read(path, offset=1, limit=400)` | Read workspace text by line, up to 400 lines per call. The returned string also exposes `sha256`, line coverage, and a retained source reference. |
| `write(path, content, expected_sha256=None, expected_absent=False)` | Atomically write a complete file, optionally checking its prior hash or absence. |
| `edit(path, old_text, new_text, expected_sha256=None)` | Replace exactly one matching span, optionally checking the file hash. |
| `bash(command, timeout_seconds=120)` | Run Bash in the workspace and return output plus exit status. A nonzero exit does not itself fail the Python cell. |
| `verify(command, timeout_seconds=120)` | Run Bash with `pipefail`; raise in the Python cell if the command fails. |

For example, a model can run `source = read('src/app.py'); print(source[:500]); print(verify('pytest -q'))`. Cell output is ordinary text: printed stdout, a final expression shown after `=>`, and relevant diagnostics. The tool's `details` carry state changes and broker outcome metadata for Pi/SDK consumers. The model is not asked to produce JSON or a structured task result.

The default tool description and bounded result projection follow Skein's measured Pi PTC v4.1 adapter. The observation is capped at roughly 50 KB; omitted output is retained under a `r_...` result ID and can be paged with `code(more="r_...", offset=0, limit=51200)` or `code(result_id="r_...")`. The 32 most recent cell result IDs remain pageable; individual retained records are capped at 256 KB. Pi-Skein does **not** inject a dynamic task packet into the model's system context by default. That keeps Pi's native text conversation and tool messages intact. `taskPacket: true` is an opt-in SDK option for an experimental Markdown task packet.

## What Pi-Skein records and controls

Each task has an append-only, fsynced JSONL event trace. A deterministic reducer derives a task ledger with its goal, criteria, iteration and verification budgets, changed and read paths, latest verification, unresolved effects, and terminal outcome. Broker operations record intent and completion, argument/result hashes, changed paths, and immutable result artifacts. Cell source and outputs also materialize as a Jupyter `notebook.ipynb`. Pi model-context and tool-output hashes link what the model saw to this trace without replacing Pi's conversation history.

Successful cells can checkpoint plain Python values. On restart, Pi-Skein checks the checkpoint against the event stream and workspace revision before restoring it. Functions, open handles, and other opaque values are not guaranteed to survive restart. Interrupted or uncertain effects block further execution; Pi-Skein does not blindly replay a shell command or write. Saved reads retain source identity and coverage, and repeated reads of the same file version can be abbreviated in the live worker's model projection.

A host-supplied task contract can specify verification commands. Before a successful Pi turn settles, Pi-Skein runs those checks through its broker. A passing check with an unchanged workspace records a `complete` task outcome. Failures can prompt repair within the configured attempt budget; exhausted runs become `blocked`. A model's final prose alone is never recorded as verified completion. Without configured verification commands, the task remains active and has no `complete` outcome. `/skein verify <command>` runs and records a manual host check.

Pi-Skein also routes Pi's user `!` shell command through the broker while active, selects `code` as the active model tool, and blocks calls to other model tools in that mode. `/skein off` restores the prior active tool set; `/skein on` selects `code` again. These controls apply inside Pi, not to external editors or processes.

## Install and use in the TUI

Requirements: a Pi installation, Node 22.19 or newer, Python 3.12 or newer, and a POSIX host (macOS or Linux). Clone this repository, then install the local Pi package:

```sh
git clone https://github.com/lmathia2/pi-skein.git
pi install ./pi-skein
```

For a single Pi invocation, use `pi -e /absolute/path/to/pi-skein --tools code`. If `python3` is older than 3.12, set `PI_SKEIN_PYTHON` to a Python 3.12+ executable. The bundled Python runtime has no third-party Python dependencies and starts lazily when needed.

Ask Pi for a coding task as usual. `/skein status` shows the current task status and trace path. `/skein verify <command>` performs a recorded host check. `/skein on` and `/skein off` toggle the tool selection. A task contract is supplied by the host, not by model output:

```sh
export PI_SKEIN_TASK_JSON='{"goal":"Fix parsing","criteria":["Empty input works"],"verification_requirements":["python -m unittest"],"max_iterations":24,"max_verification_attempts":3}'
pi
```

`PI_SKEIN_VERIFY_COMMANDS='["python -m unittest"]'` is a shorter way to set verification commands. `PI_SKEIN_STATE_DIR` changes the state root from `~/.pi/skein`. `PTC_EVIDENCE_REVIEW=1` enables the v4.1 adapter's optional one-time follow-up when a final answer has known gaps or changes lack an in-cell `verify(...)` call.

Task files live under `<state root>/<sha256(task ID)>/`: `events.jsonl` is the canonical trace, `artifacts/` holds content-addressed bodies, `checkpoint.json` holds recoverable plain values, and `notebook.ipynb` is a trace-derived view. Pi stores a `pi-skein-task` pointer in the session branch so the extension can reopen the matching task.

## Use in Pi's SDK or an evaluation

Import `createSkeinExtension` from `pi-skein/extension` and pass its factory to Pi's `DefaultResourceLoader`. Supply a separate workspace, Pi session, state directory, and task contract for each trial. Configure a model and credentials through Pi; this package does not create a second agent loop. After `session.prompt(...)`, inspect the durable outcome:

```js
import { createSkeinExtension } from 'pi-skein/extension';
import { getSkeinTaskPointer, readSkeinEvidence } from 'pi-skein/evidence';
import { createAgentSession, DefaultResourceLoader, SessionManager } from '@earendil-works/pi-coding-agent';
import { join } from 'node:path';

const workspace = process.cwd();
const stateDir = join(workspace, '.pi-skein-eval');
const extension = createSkeinExtension({
  task: { goal: 'Fix parsing', criteria: ['Empty input works'],
          verification_requirements: ['python -m unittest'] },
  stateDir,
  python: process.env.PI_SKEIN_PYTHON || 'python3',
});
const resourceLoader = new DefaultResourceLoader({
  cwd: workspace, agentDir: join(workspace, '.pi-skein-agent'),
  extensionFactories: [extension], noExtensions: true,
});
await resourceLoader.reload();
const { session } = await createAgentSession({
  cwd: workspace, resourceLoader,
  sessionManager: SessionManager.inMemory(workspace),
});
try {
  await session.bindExtensions({});
  await session.prompt('Fix parsing so empty input works');
  const pointer = getSkeinTaskPointer(session.sessionManager.getBranch());
  if (!pointer) throw new Error('No Skein task pointer');
  const evidence = readSkeinEvidence(stateDir, pointer.taskId);
  const passed = evidence.outcome?.status === 'complete' &&
    evidence.verification?.passed === true && evidence.unresolvedEffects.length === 0;
  console.log({ passed, trace: evidence.tracePath });
} finally {
  session.dispose();
  extension.dispose();
}
```

[`examples/sdk-setup.mjs`](examples/sdk-setup.mjs) contains the loader/session setup, and [`examples/faux-evaluation.mjs`](examples/faux-evaluation.mjs) runs a complete tool loop with a fake provider. `readSkeinEvidence` works offline; it does not need a model credential. A missing terminal outcome, provider failure, or unresolved effect is not a passing evaluation.

## Current scope and safety

This package ports the persistent worker, direct helpers, v4.1-style Pi prompt/result surface, durable operational trace, reduced task ledger, artifacts, notebook, checkpoints, and host command verification. It does **not** contain Skein's full analytical ledger or its complete criterion-level evidence-strength and scope verification, approval backend, or guaranteed cross-epoch source-read reuse. The optional dynamic task packet differs from the measured default. The worker's import restrictions are defense in depth, not an OS sandbox: brokered shell commands run with the host user's permissions.

To check this checkout locally:

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

`PLAN.md` and `PORTABILITY.md` contain the broader port design and source comparison; the behavior above describes what this extension currently implements.
