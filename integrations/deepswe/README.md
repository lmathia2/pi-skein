# DeepSWE evaluation on the sandbox

This uses the source-built [Pi harness](https://github.com/earendil-works/pi)
and this Pi-Skein checkout. It runs Pi inside each DeepSWE task container;
Codex stays on the local Mac and connects to the sandbox over SSH.

On the sandbox, keep these independent Git checkouts under your own workspace:

```text
/home/ec2-user/e968720/pi-skein   # github.com/lmathia2/pi-skein
/home/ec2-user/e968720/pi         # github.com/earendil-works/pi
/home/ec2-user/e968720/deepswe-eval/deep-swe # frozen benchmark snapshot
```

The benchmark snapshot is commit `2f9953e380bc7ba702b8a0dcd1b6a2665ccdb008`
of `datacurve-ai/deep-swe`. Pier 0.3.1 is installed in
`/home/ec2-user/.venvs/pier`. The sandbox copy of
`/home/ec2-user/r686921/deepswe-benchmarking/deep-swe/README.md` has useful
local Pier examples; read it without editing that user's checkout.

Build the Pi checkout with `npm run build:offline`, install Pi-Skein's runtime
dependencies with `npm install --no-package-lock --ignore-scripts`, then pack
the small runtime bundle:

```bash
bash /home/ec2-user/e968720/pi-skein/integrations/deepswe/prepare-runtime.sh /home/ec2-user/e968720
```

Run one task at a time on this shared machine. Read the Anthropic credential
instructions in `/home/ec2-user/r686921/api-keys.txt` without editing that
file. Its Anthropic helper command supplies the key below. Do not copy the key
to the Mac or use OpenRouter.

```bash
export ANTHROPIC_API_KEY="$(/usr/local/bin/anthropic-key.sh)"
export PI_SKEIN_RUNTIME_TAR=/home/ec2-user/e968720/deepswe-eval/runtime/pi-skein-runtime.tar.gz
export PYTHONPATH=/home/ec2-user/e968720/pi-skein/integrations/deepswe
cd /home/ec2-user/e968720/deepswe-eval
/home/ec2-user/.venvs/pier/bin/pier run \
  -p deep-swe/tasks/kombu-single-active-consumer-priority \
  --agent-import-path agent:PiSkeinAgent \
  --agent-kwarg mode=skein \
  -m anthropic/claude-opus-4-8 \
  -o jobs -n 1 --job-name skein-kombu-001
```

For a paired run, change `mode=skein` to `mode=baseline` and give the job a
distinct name. Keep the same task path, model, and other agent settings for
both modes. DeepSWE's separate verifier collects a patch from committed changes
and grades it in a pristine container, so check that the agent committed its
work. Pi's JSON events and Pi-Skein state remain in each trial's `agent/` log
directory; `verifier/reward.json`, `verifier/ctrf.json`, and
`verifier/test-stdout.txt` hold the score and test details.
Pi-Skein's task iteration limit defaults to 96 in this adapter; change it with
`--agent-kwarg max_iterations=NUMBER` (maximum 500).

For a deterministic 10-task subset, replace `-p deep-swe/tasks/<task-id>` with
`-p deep-swe/tasks --n-tasks 10 --sample-seed 0` in both runs. For a named
subset, use `-p deep-swe/tasks` and repeat `-i <task-id>`. For the full
113-task corpus, use `-p deep-swe/tasks` alone. Keep `-n 1` on the shared
instance unless the other active users agree to more concurrency. Give every
job a fresh `--job-name`, and do not edit the adapter while a job is running;
for long runs, launch it in `screen` and write its log under your own
`/home/ec2-user/e968720/deepswe-eval` directory. Start with a single-task
pilot and inspect the collected patch and verifier output before scaling up.
