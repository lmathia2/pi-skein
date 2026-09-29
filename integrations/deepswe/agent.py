"""Pier adapter for comparing source-built Pi with and without Pi-Skein.

The adapter runs inside the DeepSWE task container. No Codex installation is
needed on the host or in the container.
"""

import json
import os
import shlex
import tempfile
from pathlib import Path

from pier.agents.base import BaseAgent
from pier.environments.base import BaseEnvironment
from pier.models.agent.context import AgentContext
from pier.models.agent.network import NetworkAllowlist


class PiSkeinAgent(BaseAgent):
    def __init__(
        self, *args, mode: str = "skein", runtime_tar: str | None = None,
        max_iterations: int = 96, **kwargs
    ):
        super().__init__(*args, **kwargs)
        if mode not in {"skein", "baseline"}:
            raise ValueError("mode must be 'skein' or 'baseline'")
        if self.model_name and not self.model_name.startswith("anthropic/"):
            raise ValueError("This adapter only supports Anthropic models")
        max_iterations = int(max_iterations)
        if not 1 <= max_iterations <= 500:
            raise ValueError("max_iterations must be between 1 and 500")
        self.mode = mode
        self.max_iterations = max_iterations
        self.runtime_tar = Path(runtime_tar or os.environ["PI_SKEIN_RUNTIME_TAR"])

    @staticmethod
    def name() -> str:
        return "pi-skein-deepswe"

    def version(self) -> str:
        return "1"

    def network_allowlist(self) -> NetworkAllowlist:
        return NetworkAllowlist(domains=["api.anthropic.com"])

    async def setup(self, environment: BaseEnvironment) -> None:
        if not self.runtime_tar.is_file():
            raise FileNotFoundError(self.runtime_tar)
        await environment.upload_file(self.runtime_tar, "/tmp/pi-skein-runtime.tar.gz")
        result = await environment.exec(
            "mkdir -p /opt/pi-eval && tar -xzf /tmp/pi-skein-runtime.tar.gz -C /opt/pi-eval "
            "&& node --version && python3 --version && test -f /opt/pi-eval/pi/cli.js",
            timeout_sec=180,
        )
        if result.return_code:
            raise RuntimeError(f"Pi runtime setup failed: {result.stderr or result.stdout}")

    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY must be set on the sandbox host")
        prompt_path = self.logs_dir / "instruction.md"
        prompt_path.write_text(
            instruction.rstrip() + "\n\nCommit your completed code changes with git before ending. "
            "The evaluator collects the committed diff from HEAD.\n"
        )
        await environment.upload_file(prompt_path, "/tmp/pi-task.md")
        with tempfile.NamedTemporaryFile(
            mode="w", dir=self.logs_dir, prefix=".anthropic-key-", delete=False
        ) as secret_file:
            secret_path = Path(secret_file.name)
            secret_file.write(key)
        try:
            await environment.upload_file(secret_path, "/tmp/pi-anthropic-key")
        finally:
            secret_path.unlink(missing_ok=True)
        flags = (
            "--no-builtin-tools --extension /opt/pi-eval/pi-skein/src/index.ts"
            if self.mode == "skein" else ""
        )
        model = (self.model_name or "anthropic/claude-opus-4-8").split("/", 1)[1]
        command = (
            'ANTHROPIC_API_KEY="$(cat /tmp/pi-anthropic-key)" '
            "node /opt/pi-eval/pi/cli.js --print --mode json --no-session "
            "--no-extensions --no-skills --no-context-files "
            f"--provider anthropic --model {shlex.quote(model)} {flags} "
            "-- @/tmp/pi-task.md > /logs/agent/pi-events.jsonl 2> /logs/agent/pi-stderr.txt"
        )
        result = await environment.exec(
            command,
            cwd="/app",
            env=environment.agent_process_env({
                "PI_SKEIN_PYTHON": "python3",
                "PI_SKEIN_STATE_DIR": "/logs/agent/skein",
                "PI_SKEIN_TASK_JSON": json.dumps({"max_iterations": self.max_iterations}),
                "PI_OFFLINE": "1",
                "NODE_OPTIONS": "--use-env-proxy",
            }),
            timeout_sec=10800,
        )
        context.metadata = {
            "mode": self.mode,
            "pi_exit_code": result.return_code,
            "max_iterations": self.max_iterations if self.mode == "skein" else None,
        }
        if result.return_code:
            raise RuntimeError(
                f"Pi exited {result.return_code}; inspect pi-stderr.txt and pi-events.jsonl"
            )
