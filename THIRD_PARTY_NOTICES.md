# Source provenance

`python/pi_skein/worker.py` and `python/pi_skein/preflight.py` were copied from the
Skein repository at commit `bf991fde0013f4957ab849d5ebbb366ed30e0b4a`
(`harness/ptc/repl/`). Skein declares Apache-2.0 in its project metadata. This
package includes the Apache-2.0 license text in `LICENSE`.

`src/ptc-prompt.ts` adapts Skein's v4.1 PTC model instruction from
`app/agent/config.py` at the same commit.
`python/pi_skein/context.py` adapts the required-section task-packet structure from
`harness/core/orchestration/core.py` at the same commit. The Pi adapter keeps the
model-facing packet in Markdown and leaves the conversation to Pi.

The remaining runtime, Pi extension, and tests in this package were written for
pi-skein. Pi packages are peer dependencies supplied by the Pi host; no Pi source
files are copied into this package.

`python/pi_skein/v41_projection.py` copies the pure text-result projection from
`scripts/pi_code_tool_harbor.py` at the same Skein commit. `src/ptc-prompt.ts`
now adapts the measured Pi adapter's stable prompt and optional evidence review
from `scripts/pi_skein_ptc_extension.mjs` at that commit.
