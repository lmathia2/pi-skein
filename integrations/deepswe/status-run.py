"""Summarize a Pier/DeepSWE job from files that update during the run."""

import argparse
import json
import re
from collections import Counter, deque
from pathlib import Path


JOBS_DIR = Path("/home/ec2-user/e968720/deepswe-eval/jobs")


def summarize(job: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", job):
        raise SystemExit("Job name must contain only letters, digits, hyphens, or underscores")
    base = JOBS_DIR / job
    if not base.is_dir():
        raise SystemExit(f"Job does not exist: {base}")

    trials = sorted(base.glob("*/agent/pi-events.jsonl"))
    if not trials:
        print(f"{job}: waiting for Pi event stream")
        return

    for events in trials:
        counts: Counter[str] = Counter()
        recent: deque[str] = deque(maxlen=6)
        for line in events.open(encoding="utf-8"):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:  # The writer may be appending this line.
                continue
            kind = event.get("type", "unknown")
            counts[kind] += 1
            if kind == "tool_execution_start":
                code = event.get("args", {}).get("code", "")
                if isinstance(code, str):
                    first_line = next((part.strip() for part in code.splitlines() if part.strip()), "")
                    first_line = re.sub(r"sk-[A-Za-z0-9_-]{16,}", "[REDACTED]", first_line)
                    recent.append(f"code: {first_line[:120]}")
            elif kind == "auto_retry_start":
                recent.append(f"API retry: {event.get('errorMessage', 'unknown error')}")
            elif kind == "agent_end":
                recent.append("Pi agent ended")

        trial_dir = events.parent.parent
        patch = trial_dir / "artifacts/model.patch"
        print(f"Job: {job}  Trial: {trial_dir.name}")
        print(
            f"Pi turns: {counts['turn_end']}  "
            f"Tool calls: {counts['tool_execution_end']}  "
            f"API retries: {counts['auto_retry_start']}  "
            f"Agent ended: {bool(counts['agent_end'])}"
        )
        print(f"Collected patch: {patch.stat().st_size} bytes" if patch.exists() else "Collected patch: pending")
        for item in recent:
            print(f"  {item}")

    result_path = base / "result.json"
    if result_path.exists():
        result = json.loads(result_path.read_text(encoding="utf-8"))
        print(f"Pier finished: {bool(result.get('finished_at'))}")
        stats = result.get("stats", {})
        for evaluation in stats.get("evals", {}).values():
            for metrics in evaluation.get("metrics", []):
                print(f"Reward: {metrics.get('reward')}  Partial: {metrics.get('partial')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", help="Pier --job-name under the e968720 workspace")
    summarize(parser.parse_args().job)
