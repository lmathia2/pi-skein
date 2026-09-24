"""Versioned JSONL stdio boundary for the Pi extension."""

from __future__ import annotations

import json
import signal
import sys

from .runtime import SkeinRuntime


def main() -> None:
    runtime = None
    def stop(_signum, _frame):
        if runtime is not None:
            runtime.broker.cancel()
        raise SystemExit(143)
    signal.signal(signal.SIGTERM, stop)
    try:
        for line in sys.stdin:
            request = json.loads(line)
            identity = request.get("id")
            try:
                if request.get("version") != 1:
                    raise ValueError("unsupported protocol version")
                method = request.get("method")
                params = request.get("params") or {}
                if method == "open":
                    if runtime is not None:
                        raise ValueError("task already open")
                    runtime = SkeinRuntime(**params)
                    value = runtime.status()
                elif runtime is None:
                    raise ValueError("open a task first")
                elif method == "execute":
                    value = runtime.execute(**params)
                elif method == "page":
                    value = runtime.page(**params)
                elif method == "verify":
                    value = runtime.verify_task(**params)
                elif method == "status":
                    value = runtime.status()
                elif method == "project_context":
                    value = runtime.project_context(**params)
                elif method == "trace":
                    value = runtime.trace(**params)
                elif method == "notebook":
                    value = runtime.notebook()
                elif method == "record":
                    value = runtime.record(**params)
                elif method == "shell":
                    value = runtime.shell(**params)
                elif method == "finish":
                    value = runtime.finish(**params)
                elif method == "close":
                    value = None
                    runtime.close()
                    runtime = None
                else:
                    raise ValueError("unknown method")
                response = {"id": identity, "ok": True, "value": value}
            except Exception as error:
                response = {"id": identity, "ok": False,
                            "error": f"{type(error).__name__}: {error}"}
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    finally:
        if runtime is not None:
            runtime.close()


if __name__ == "__main__":
    main()
