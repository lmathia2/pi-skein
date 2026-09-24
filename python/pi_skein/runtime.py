"""Task, effect, and evidence authority shared by Pi TUI and SDK sessions."""

from __future__ import annotations

import hashlib
import base64
import json
import os
import fcntl
import signal
import subprocess
import threading
import time
import difflib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .worker import PersistentPythonWorker
from .context import build_coding_packet
from .projection import state_updates, state_notice
from .v41_projection import _bounded_text, _ptc_record, _ptc_response, PTC_RESULT_BYTES, PTC_RESULT_COUNT


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + f".{uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class EventStore:
    """One writer per task; each acknowledged event is fsynced."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "events.jsonl"
        self._lock = threading.RLock()
        self.events = self.read()
        self.keys = {event["idempotency_key"]: event for event in self.events if event.get("idempotency_key")}

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events = []
        with self.path.open(encoding="utf-8") as stream:
            for line in stream:
                event = json.loads(line)
                if event["sequence"] != len(events) + 1:
                    raise ValueError("task event stream has a sequence gap")
                events.append(event)
        return events

    def append(self, kind: str, payload: dict[str, Any], key: str | None = None) -> dict[str, Any]:
        with self._lock:
            if key in self.keys and key is not None:
                old = self.keys[key]
                if old["kind"] != kind or old["payload"] != payload:
                    raise ValueError("idempotency key reused with different content")
                return old
            event = {"schema_version": 1, "sequence": len(self.events) + 1,
                     "event_id": uuid4().hex, "timestamp": datetime.now(UTC).isoformat(),
                     "kind": kind, "payload": payload,
                     "idempotency_key": key}
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            self.events.append(event)
            if key:
                self.keys[key] = event
            return event


@dataclass
class TaskLedger:
    task_id: str
    goal: str
    criteria: list[str]
    constraints: list[str] = field(default_factory=list)
    verification_requirements: list[str] = field(default_factory=list)
    max_iterations: int = 24
    max_verification_attempts: int = 3
    status: str = "active"
    phase: str = "implement"
    iteration: int = 0
    changed_paths: list[str] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    latest_verification: dict[str, Any] | None = None
    verification_attempts: int = 0
    unresolved_effects: list[str] = field(default_factory=list)
    outcome: dict[str, Any] | None = None


def reduce_ledger(events: list[dict[str, Any]]) -> TaskLedger | None:
    ledger = None
    pending: set[str] = set()
    for event in events:
        kind, payload = event["kind"], event["payload"]
        if kind == "task.created":
            if ledger is not None:
                raise ValueError("duplicate task creation")
            ledger = TaskLedger(payload["task_id"], payload["goal"], payload["criteria"],
                                payload.get("constraints", []), payload.get("verification_requirements", []),
                                payload.get("max_iterations", 24), payload.get("max_verification_attempts", 3))
        elif ledger is None:
            raise ValueError("event stream does not begin with task creation")
        elif kind == "cell.started":
            ledger.iteration += 1
            pending.add(payload["cell_id"])
        elif kind == "cell.completed":
            pending.discard(payload["cell_id"])
            if payload.get("effect_unknown"):
                ledger.unresolved_effects.append(payload["cell_id"])
        elif kind == "effect.started":
            pending.add(payload["operation_id"])
        elif kind == "effect.completed":
            pending.discard(payload["operation_id"])
            if payload.get("effect_unknown"):
                ledger.unresolved_effects.append(payload["operation_id"])
            for path in payload.get("changed_paths", []):
                if path not in ledger.changed_paths:
                    ledger.changed_paths.append(path)
            path = payload.get("read_path")
            if path and path not in ledger.files_read:
                ledger.files_read.append(path)
        elif kind == "verification.completed":
            ledger.latest_verification = payload
            ledger.verification_attempts += 1
            ledger.phase = "complete" if payload.get("passed") else "implement"
        elif kind == "task.finished":
            ledger.status = payload["status"]
            ledger.phase = payload["status"]
            ledger.outcome = payload
    if ledger is not None:
        ledger.unresolved_effects.extend(sorted(pending))
    return ledger


class LocalBroker:
    def __init__(self, runtime: SkeinRuntime):
        self.runtime = runtime
        self.active_shell_pid: int | None = None
        self.active_cell_id: str | None = None
        self.read_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.outcomes: list[dict[str, Any]] = []

    def cancel(self) -> None:
        if self.active_shell_pid is not None:
            try:
                os.killpg(self.active_shell_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def _path(self, raw: str) -> Path:
        path = (self.runtime.workspace / raw).resolve()
        if not path.is_relative_to(self.runtime.workspace):
            raise ValueError("path escapes workspace")
        return path

    def _effect(self, operation: str, arguments: dict[str, Any], action):
        if self.active_cell_id is not None:
            if len(self.outcomes) >= 64:
                raise ValueError("At most 64 helper calls per cell; split this batch")
            outcome: dict[str, Any] = {"operation": {"fs.read": "read", "fs.write": "write", "fs.edit": "edit", "shell.run": "bash", "shell.verify": "verify"}[operation], "status": "pending"}
            if operation.startswith("shell."):
                outcome["command"] = arguments["command"]
            self.outcomes.append(outcome)
        else:
            outcome = {}
        started = time.monotonic()
        operation_id = uuid4().hex
        before = self.runtime.snapshot() if operation != "fs.read" else None
        self.runtime.events.append("effect.started", {"operation_id": operation_id,
            "cell_id": self.active_cell_id,
            "operation": operation, "arguments_hash": digest(json.dumps(arguments, sort_keys=True).encode()),
            "workspace_before": self.runtime.snapshot_hash(before) if before is not None else None})
        try:
            result = action()
            result_hash = digest(json.dumps(result, sort_keys=True, ensure_ascii=False).encode())
            if outcome:
                data = dict(result.get("data", {}))
                for field, value in data.items():
                    if isinstance(value, str):
                        data[field], clipped = _bounded_text(value, PTC_RESULT_BYTES)
                        if clipped:
                            data[field] += "\n[retention truncated; suffix unavailable]"
                outcome.update(status=result["status"], data=data)
                outcome["original_stream_bytes"] = {key: len(result.get("data", {}).get(key, "").encode()) for key in ("stdout", "stderr")}
            result_uri = self.runtime.retain(json.dumps(result, sort_keys=True, ensure_ascii=False) + "\n")
            read_evidence = None
            if operation == "fs.read" and result.get("status") == "ok":
                data = result["data"]
                read_evidence = {key: data[key] for key in ("path", "sha256", "offset", "returned_lines")}
                coverage = {"whole_file": data["complete"], "total_lines": data["total_lines"],
                            "next_unread_offset": data["next_offset"]}
                result = {**result, "read_reference": {
                    "artifact_uri": result_uri, "task_id": reduce_ledger(self.runtime.events.events).task_id,
                    "operation_id": operation_id, **read_evidence, "source_coverage": coverage}}
                cache_key = (data["path"], data["sha256"])
                self.read_cache.setdefault(cache_key, []).append({
                    "offset": data["offset"], "returned_lines": data["returned_lines"],
                    "artifact_uri": result_uri})
                if len(self.read_cache[cache_key]) > 64:
                    self.read_cache[cache_key].pop(0)
            after = self.runtime.snapshot() if before is not None else None
            changed = sorted(path for path in set(before or {}) | set(after or {})
                             if (before or {}).get(path) != (after or {}).get(path))
            self.runtime.events.append("effect.completed", {"operation_id": operation_id,
                "cell_id": self.active_cell_id,
                "operation": operation, "changed_paths": changed,
                "read_path": arguments.get("path") if operation == "fs.read" else None,
                "read_evidence": read_evidence, "result_artifact_uri": result_uri,
                "read_reference": result.get("read_reference") if operation == "fs.read" else None,
                "result_hash": result_hash,
                "workspace_after": self.runtime.snapshot_hash(after) if after is not None else None,
                "effect_unknown": False})
            return result
        except Exception as error:
            if outcome:
                outcome.update(status="error", error=str(error))
            self.runtime.events.append("effect.completed", {"operation_id": operation_id,
                "cell_id": self.active_cell_id,
                "operation": operation, "effect_unknown": operation != "fs.read",
                "error": str(error)})
            raise
        finally:
            if outcome:
                outcome["duration_ms"] = int((time.monotonic() - started) * 1000)

    def read(self, path: str, offset: int = 1, limit: int = 400):
        if offset < 1 or limit < 1 or limit > 400:
            raise ValueError("invalid read range")
        def action():
            target = self._path(path)
            body = target.read_text(encoding="utf-8")
            lines = body.splitlines(keepends=True)
            selected = lines[offset - 1:offset - 1 + limit]
            text = "".join(selected)
            if len(text.encode()) > 512000:
                raise ValueError("read range exceeds result budget; request fewer lines")
            next_offset = offset + len(selected) if offset + len(selected) <= len(lines) else None
            relpath = str(target.relative_to(self.runtime.workspace))
            sha = digest(body.encode())
            covered: dict[int, str] = {}
            for saved in self.read_cache.get((relpath, sha), []):
                for line in range(saved["offset"], saved["offset"] + saved["returned_lines"]):
                    covered.setdefault(line, saved["artifact_uri"])
            requested = range(offset, offset + len(selected))
            reused = [line for line in requested if line in covered]
            result = {"status": "ok", "data": {"path": relpath,
                "text": text, "offset": offset, "returned_lines": len(selected),
                "total_lines": len(lines), "complete": offset == 1 and next_offset is None,
                "next_offset": next_offset, "sha256": sha}}
            if reused:
                result["read_reuse"] = {
                    "reused_lines": len(reused), "source_read_lines": len(selected) - len(reused),
                    "identity_probe_lines": int(bool(lines)),
                    "reused_artifact_uris": sorted({covered[line] for line in reused}),
                }
            return result
        return self._effect("fs.read", {"path": path, "offset": offset, "limit": limit}, action)

    def write(self, path: str, content: str, expected_sha256: str | None = None, expected_absent: bool = False):
        def action():
            target = self._path(path)
            old = target.read_bytes() if target.exists() else None
            if expected_absent and old is not None:
                raise ValueError("file already exists")
            if expected_sha256 is not None and (old is None or digest(old) != expected_sha256):
                raise ValueError("file changed since it was read")
            changed = old != content.encode()
            if changed:
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + f".{uuid4().hex}.tmp")
                temporary.write_text(content, encoding="utf-8")
                os.replace(temporary, target)
            return {"status": "ok", "data": {"path": path, "changed": changed,
                "sha256": digest(content.encode()), "diff": "".join(difflib.unified_diff(
                    (old or b"").decode("utf-8", errors="replace").splitlines(keepends=True),
                    content.splitlines(keepends=True), fromfile=path, tofile=path))}}
        return self._effect("fs.write", {"path": path, "content_hash": digest(content.encode())}, action)

    def edit(self, path: str, old_text: str, new_text: str, expected_sha256: str | None = None):
        def action():
            target = self._path(path)
            body = target.read_text(encoding="utf-8")
            if expected_sha256 is not None and digest(body.encode()) != expected_sha256:
                raise ValueError("file changed since it was read")
            if not old_text or body.count(old_text) != 1:
                raise ValueError("edit requires exactly one matching span")
            changed = old_text != new_text
            updated = body.replace(old_text, new_text, 1)
            if changed:
                temporary = target.with_name(target.name + f".{uuid4().hex}.tmp")
                temporary.write_text(updated, encoding="utf-8")
                os.replace(temporary, target)
            return {"status": "ok", "data": {"path": path, "changed": changed,
                "sha256": digest(updated.encode()), "diff": "".join(difflib.unified_diff(
                    body.splitlines(keepends=True), updated.splitlines(keepends=True), fromfile=path, tofile=path))}}
        return self._effect("fs.edit", {"path": path, "old_hash": digest(old_text.encode()),
                             "new_hash": digest(new_text.encode())}, action)

    def _shell(self, command: str, timeout_seconds: int, verify: bool):
        if not 1 <= timeout_seconds <= 600:
            raise ValueError("invalid command timeout")
        def action():
            child = subprocess.Popen(["bash", "-o", "pipefail", "-c", command] if verify else ["bash", "-c", command], cwd=self.runtime.workspace,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
            self.active_shell_pid = child.pid
            try:
                stdout, stderr = child.communicate(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.communicate()
                raise
            finally:
                self.active_shell_pid = None
            output = stdout + (("\n[stderr]\n" + stderr) if stderr else "") + f"\n[exit {child.returncode}]"
            return {"status": "ok" if child.returncode == 0 else "error", "data": {
                "exit_code": child.returncode, "stdout": stdout[:64000],
                "stderr": stderr[:64000], "output": output[:64000], "timed_out": False, "timeout_seconds": timeout_seconds}}
        return self._effect("shell.verify" if verify else "shell.run", {"command": command, "timeout_seconds": timeout_seconds}, action)

    def bash(self, command: str, timeout_seconds: int = 120):
        return self._shell(command, timeout_seconds, False)

    def verify(self, command: str, timeout_seconds: int = 120):
        result = self._shell(command, timeout_seconds, True)
        self.runtime.events.append("verification.observed", {"command": command,
            "exit_code": result["data"]["exit_code"]})
        return result

    def call(self, *_args, **_kwargs):
        raise ValueError("external capability unavailable")

    def parallel(self, operations):
        if any(item.get("operation") != "fs.read" for item in operations):
            raise ValueError("parallel permits independent reads only")
        return [self.read(**item.get("arguments", {})) for item in operations]

    def artifacts_load(self, uri: str, offset: int = 0, limit: int = 16000):
        if not uri.startswith("artifact://sha256/"):
            raise ValueError("invalid artifact URI")
        sha = uri.rsplit("/", 1)[-1]
        if len(sha) != 64 or any(character not in "0123456789abcdef" for character in sha):
            raise ValueError("invalid artifact digest")
        raw = (self.runtime.artifacts / sha).read_bytes()
        if digest(raw) != sha:
            raise ValueError("artifact integrity failure")
        if offset < 0 or not 1 <= limit <= 51200:
            raise ValueError("invalid artifact page")
        page = raw[offset:offset + limit]
        try:
            text = page.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            text = None
            encoding = "base64"
        next_offset = offset + len(page) if offset + len(page) < len(raw) else None
        data = {"uri": uri, "encoding": encoding, "text": text, "offset": offset,
                "returned_bytes": len(page), "total_bytes": len(raw),
                "complete": offset == 0 and next_offset is None, "next_offset": next_offset}
        if encoding == "base64":
            data["base64"] = base64.b64encode(page).decode("ascii")
        return {"status": "ok", "data": data}

    def artifacts_list(self):
        return ["artifact://sha256/" + item.name for item in sorted(self.runtime.artifacts.iterdir())
                if len(item.name) == 64 and item.is_file()][:100]

    def artifacts_publish(self, value, name: str, description: str | None = None):
        body = json.dumps(value, ensure_ascii=False, default=str)
        uri = self.runtime.retain(body)
        return {"uri": uri, "name": name, "description": description}


class SkeinRuntime:
    def __init__(self, workspace: str, state_dir: str, task_id: str, goal: str = "",
                 criteria: list[str] | None = None, constraints: list[str] | None = None,
                 verification_requirements: list[str] | None = None, max_iterations: int = 24,
                 max_verification_attempts: int = 3):
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise ValueError("workspace does not exist")
        if not 1 <= max_iterations <= 500 or not 1 <= max_verification_attempts <= 100:
            raise ValueError("invalid task budgets")
        self.state_root = Path(state_dir).resolve()
        if self.state_root == self.workspace:
            raise ValueError("state root must not be the workspace root")
        self.state_root_inside_workspace = self.state_root.is_relative_to(self.workspace)
        self.state_dir = self.state_root / digest(task_id.encode())
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.lock = (self.state_dir / "owner.lock").open("a+")
        try:
            fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self.lock.close()
            raise RuntimeError("task runtime is already owned by another process") from error
        try:
            self.artifacts = self.state_dir / "artifacts"
            self.artifacts.mkdir(exist_ok=True)
            self.events = EventStore(self.state_dir)
            if not self.events.events:
                self.events.append("task.created", {"task_id": task_id, "goal": goal,
                    "criteria": criteria or [goal], "constraints": constraints or [],
                    "verification_requirements": verification_requirements or [],
                    "max_iterations": max_iterations,
                    "max_verification_attempts": max_verification_attempts}, "task-created")
            else:
                existing = reduce_ledger(self.events.events)
                if existing is None or existing.task_id != task_id:
                    raise ValueError("task identity mismatch")
            self.worker = PersistentPythonWorker(max_output_bytes=51200, state_recovery="snapshot", capture_committed=True)
            self.broker = LocalBroker(self)
            self.checkpoint_path = self.state_dir / "checkpoint.json"
            if self.checkpoint_path.exists():
                checkpoint = json.loads(self.checkpoint_path.read_text())
                if checkpoint["event_sequence"] > len(self.events.events):
                    raise ValueError("checkpoint refers to missing events")
                prefix = self.events.events[:checkpoint["event_sequence"]]
                if checkpoint.get("event_stream_hash") != self.events_hash(prefix):
                    raise ValueError("checkpoint event stream integrity failure")
                if not any(event["kind"] == "checkpoint.created"
                           and event["payload"].get("cell_id") == checkpoint["cell_id"]
                           for event in self.events.events[checkpoint["event_sequence"]:]):
                    raise ValueError("checkpoint publication marker is missing")
                if checkpoint.get("workspace_revision") != self.snapshot_hash(self.snapshot()):
                    raise ValueError("checkpoint workspace revision differs from current workspace")
                self.worker.restore_plain(checkpoint["values"], checkpoint["cell_id"])
        except BaseException:
            if hasattr(self, "worker"):
                self.worker.close()
            fcntl.flock(self.lock.fileno(), fcntl.LOCK_UN)
            self.lock.close()
            raise

    def snapshot(self) -> dict[str, str]:
        result = {}
        for root, directories, files in os.walk(self.workspace):
            base = Path(root)
            directories[:] = [name for name in directories if name != ".git"
                              and not (self.state_root_inside_workspace
                                       and (base / name).resolve().is_relative_to(self.state_root))]
            for name in files:
                path = base / name
                if path.is_symlink() or (self.state_root_inside_workspace
                                         and path.resolve().is_relative_to(self.state_root)):
                    continue
                try:
                    result[str(path.relative_to(self.workspace))] = digest(path.read_bytes())
                except FileNotFoundError:
                    continue
        return result

    @staticmethod
    def snapshot_hash(snapshot: dict[str, str]) -> str:
        return digest(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode())

    @staticmethod
    def events_hash(events: list[dict[str, Any]]) -> str:
        body = "\n".join(json.dumps(event, sort_keys=True, ensure_ascii=False) for event in events)
        return digest(body.encode())

    def retain(self, body: str) -> str:
        raw = body.encode()
        sha = digest(raw)
        path = self.artifacts / sha
        if not path.exists():
            temporary = path.with_name(path.name + f".{uuid4().hex}.tmp")
            with temporary.open("wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        return "artifact://sha256/" + sha

    def page(self, uri: str, offset: int = 0, limit: int = 16000) -> dict[str, Any]:
        requested = uri
        if uri.startswith("r_"):
            retained = [event for event in self.events.events if event["kind"] == "result.retained"][-PTC_RESULT_COUNT:]
            match = next((event["payload"] for event in retained if event["payload"]["result_id"] == uri), None)
            if match is None:
                raise ValueError("unknown or expired result ID")
            uri = match["artifact_uri"]
        if not uri.startswith("artifact://sha256/") or not 0 <= offset or not 1 <= limit <= 51200:
            raise ValueError("invalid artifact request")
        sha = uri.rsplit("/", 1)[-1]
        if len(sha) != 64 or any(character not in "0123456789abcdef" for character in sha):
            raise ValueError("invalid artifact digest")
        raw = (self.artifacts / sha).read_bytes()
        if digest(raw) != sha:
            raise ValueError("artifact integrity failure")
        next_offset = offset + limit if offset + limit < len(raw) else None
        return {"text": raw[offset:offset + limit].decode(errors="replace"),
                "details": {"result_id": requested, "offset": offset, "next_offset": next_offset,
                            "total_bytes": len(raw)}, "total_bytes": len(raw), "next_offset": next_offset}

    def execute(self, code: str, call_id: str) -> dict[str, Any]:
        if len(code.encode()) > 128000:
            raise ValueError("cell exceeds source limit")
        prior = next((event for event in self.events.events if event["kind"] == "cell.completed"
                      and event["payload"]["call_id"] == call_id), None)
        if prior is not None:
            if prior["payload"]["code_hash"] != digest(code.encode()):
                raise ValueError("tool call ID reused for different code")
            return prior["payload"]["response"]
        if any(event["kind"] == "cell.started" and event["payload"]["call_id"] == call_id
               for event in self.events.events):
            raise RuntimeError("interrupted cell requires reconciliation")
        ledger = reduce_ledger(self.events.events)
        if ledger.status != "active":
            raise RuntimeError("task is already terminal")
        if ledger.iteration >= ledger.max_iterations:
            raise RuntimeError("task iteration budget exhausted")
        if ledger.unresolved_effects:
            raise RuntimeError("unresolved execution effect blocks further work")
        cell_id = uuid4().hex
        code_hash = digest(code.encode())
        code_uri = self.retain(code)
        self.events.append("cell.started", {"cell_id": cell_id, "call_id": call_id,
            "code_hash": code_hash, "source_artifact_uri": code_uri})
        self.broker.active_cell_id = cell_id
        self.broker.outcomes = []
        try:
            result = self.worker.execute(code, self.broker, cell_id=cell_id)
        finally:
            self.broker.active_cell_id = None
        prior_completed = next((event for event in reversed(self.events.events[:-1])
                                if event["kind"] == "cell.completed"
                                and event["payload"].get("kernel_epoch") == self.worker.kernel_epoch), None)
        previous_state = prior_completed["payload"].get("state_manifest", []) if prior_completed else []
        completed_reads = [event["payload"]["read_reference"]
                           for event in self.events.events
                           if event["kind"] == "effect.completed"
                           and event["payload"].get("cell_id") == cell_id
                           and isinstance(event["payload"].get("read_reference"), dict)]
        updates = state_updates(previous_state, list(result.state_manifest),
                                completed_reads=completed_reads if result.status != "ok" else [])
        result_id = "r_" + uuid4().hex[:12]
        record = _ptc_record(result, result_id, self.broker.outcomes)
        uri = self.retain(record)
        self.events.append("result.retained", {"result_id": result_id, "artifact_uri": uri, "cell_id": cell_id})
        checkpoint_info = None
        if self.checkpoint_path.exists():
            saved = json.loads(self.checkpoint_path.read_text())
            checkpoint_info = {"source_cell_id": saved["cell_id"], "values": saved["values"],
                               "omitted_names": saved.get("omitted_names", [])}
        projected = _ptc_response(result, result_id, self.broker.outcomes,
                                  {item["name"] for item in previous_state}, checkpoint=checkpoint_info)
        response = {**projected, "status": result.status, "artifact_uri": uri,
                    "effect_unknown": result.effect_unknown, "cell_id": cell_id}
        self.events.append("context.tool_projection_created", {
            "projection_version": "skein-ptc-v4.1-text", "source_cell_id": cell_id,
            "source_artifact_uri": uri, "model_visible_sha256": digest(response["text"].encode()),
            "model_visible_bytes": len(response["text"].encode()),
            "omitted_bytes": max(0, len(record.encode()) - len(response["text"].encode())),
        })
        self.events.append("cell.completed", {"cell_id": cell_id, "call_id": call_id,
            "code_hash": code_hash, "effect_unknown": result.effect_unknown,
            "response": response, "state_count": result.state_count,
            "state_manifest": list(result.state_manifest), "kernel_epoch": self.worker.kernel_epoch,
            "state_updates": updates, "state_update_program": "pi-skein-text-state@1",
            "stdout": result.full_stdout or result.stdout,
            "stderr": result.full_stderr or result.stderr,
            "display": result.display_data,
            "exception": {"ename": result.error_type, "evalue": result.error_message,
                          "traceback": list(result.traceback)} if result.error_type else None})
        if result.status == "ok" and result.checkpoint_values is not None:
            checkpoint = {"cell_id": cell_id, "event_sequence": len(self.events.events),
                          "event_stream_hash": self.events_hash(self.events.events),
                          "workspace_revision": self.snapshot_hash(self.snapshot()),
                          "values": result.checkpoint_values}
            checkpoint["omitted_names"] = list(result.checkpoint_omitted_names)
            atomic_json(self.checkpoint_path, checkpoint)
            self.events.append("checkpoint.created", {"cell_id": cell_id,
                "event_sequence": checkpoint["event_sequence"]})
        self.materialize_notebook()
        return response

    def verify_task(self, commands: list[str]) -> dict[str, Any]:
        ledger = reduce_ledger(self.events.events)
        if ledger is not None and ledger.status == "complete":
            return ledger.latest_verification or {"passed": False, "reason": "missing report"}
        if ledger is not None and ledger.status != "active":
            raise RuntimeError("task is already terminal")
        if ledger is not None and ledger.verification_requirements and commands != ledger.verification_requirements:
            raise ValueError("verification commands differ from task contract")
        if ledger is None or ledger.unresolved_effects:
            report = {"passed": False, "reason": "unresolved execution effects", "commands": []}
        else:
            results = []
            before = self.snapshot_hash(self.snapshot())
            for command in commands:
                result = self.broker.verify(command)
                results.append({"command": command, "exit_code": result["data"]["exit_code"]})
                if result["data"]["exit_code"] != 0 or self.snapshot_hash(self.snapshot()) != before:
                    break
            report = {"passed": bool(commands) and len(results) == len(commands)
                      and all(item["exit_code"] == 0 for item in results)
                      and self.snapshot_hash(self.snapshot()) == before,
                      "commands": results, "workspace_revision": before}
        self.events.append("verification.completed", report)
        if report["passed"]:
            outcome = {"status": "complete", "task_id": ledger.task_id,
                       "changed_paths": ledger.changed_paths, "verification": report}
            self.events.append("task.finished", outcome, "task-finished")
        return report

    def trace(self, after_sequence: int = 0, limit: int = 100, kind: str | None = None) -> dict[str, Any]:
        if after_sequence < 0 or not 1 <= limit <= 1000:
            raise ValueError("invalid trace page")
        events = [event for event in self.events.events
                  if event["sequence"] > after_sequence and (kind is None or event["kind"] == kind)]
        return {"events": events[:limit], "next_sequence": events[limit - 1]["sequence"]
                if len(events) > limit else None}

    def notebook(self) -> list[dict[str, Any]]:
        cells: dict[str, dict[str, Any]] = {}
        for event in self.events.events:
            payload = event["payload"]
            if event["kind"] == "cell.started":
                cells[payload["cell_id"]] = {"cell_id": payload["cell_id"],
                    "source_artifact_uri": payload["source_artifact_uri"], "status": "interrupted"}
            elif event["kind"] == "cell.completed" and payload["cell_id"] in cells:
                cells[payload["cell_id"]].update({"status": payload["response"]["status"],
                    "result_artifact_uri": payload["response"]["artifact_uri"],
                    "kernel_epoch": payload.get("kernel_epoch"),
                    "outputs": [{"output_type": "stream", "name": name, "text": payload[name]}
                                for name in ("stdout", "stderr") if payload.get(name)]
                    + ([{"output_type": "display_data", "data": payload["display"], "metadata": {}}]
                       if payload.get("display") else [])
                    + ([{"output_type": "error", **payload["exception"]}]
                       if payload.get("exception") else [])})
        return list(cells.values())

    def materialize_notebook(self) -> dict[str, Any]:
        cells = [{"cell_type": "markdown", "id": digest(b"task-created")[:32],
                  "metadata": {}, "source": ["# User request\n", "\n", reduce_ledger(self.events.events).goal + "\n"]}]
        for cell in self.notebook():
            source_uri = cell["source_artifact_uri"]
            sha = source_uri.rsplit("/", 1)[-1]
            source = (self.artifacts / sha).read_text(encoding="utf-8")
            cells.append({"cell_type": "code", "id": cell["cell_id"],
                          "execution_count": None,
                          "metadata": {"skein": {"status": cell["status"],
                                                  "kernel_epoch": cell.get("kernel_epoch"),
                                                  "source_artifact_uri": source_uri,
                                                  "result_artifact_uri": cell.get("result_artifact_uri")}},
                          "source": source.splitlines(keepends=True),
                          "outputs": cell.get("outputs", [])})
        watermark = len(self.events.events)
        document = {"nbformat": 4, "nbformat_minor": 5,
                    "metadata": {"skein": {"source_watermark": watermark}}, "cells": cells}
        body = (json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        path = self.state_dir / "notebook.ipynb"
        temporary = path.with_name(path.name + f".{uuid4().hex}.tmp")
        with temporary.open("wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        self.events.append("notebook.materialized", {"path": str(path),
            "source_watermark": watermark, "content_sha256": digest(body)})
        return {"path": str(path), "source_watermark": watermark, "content_sha256": digest(body)}

    def status(self) -> dict[str, Any]:
        ledger = reduce_ledger(self.events.events)
        return {"ledger": asdict(ledger) if ledger else None, "trace_path": str(self.events.path),
                "event_sequence": len(self.events.events)}

    def project_context(self, max_tokens: int = 20000) -> dict[str, Any]:
        if not 1 <= max_tokens <= 200000:
            raise ValueError("invalid context budget")
        ledger = reduce_ledger(self.events.events)
        if ledger is None:
            raise ValueError("task has no ledger")
        watermark = len(self.events.events)
        packet = build_coding_packet(ledger, max_tokens=max_tokens)
        content_hash = digest(packet.encode())
        self.events.append("context.work_packet_created", {
            "program": "pi-skein-ptc-v4.1-packet@1", "source_watermark": watermark,
            "content": packet, "content_hash": content_hash,
            "content_bytes": len(packet.encode()), "max_tokens": max_tokens,
        })
        return {"content": packet, "content_hash": content_hash,
                "source_watermark": watermark}

    def record(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        if kind not in {"pi.agent_started", "pi.message_completed", "pi.agent_settled",
                        "pi.context_prepared"}:
            raise ValueError("unsupported Pi observation")
        event = self.events.append(kind, payload)
        return {"event_id": event["event_id"], "sequence": event["sequence"]}

    def shell(self, command: str) -> dict[str, Any]:
        if reduce_ledger(self.events.events).unresolved_effects:
            raise RuntimeError("unresolved execution effect blocks shell")
        return self.broker.bash(command)

    def finish(self, status: str, reason: str = "") -> dict[str, Any]:
        if status not in {"failed", "cancelled", "blocked"}:
            raise ValueError("unsupported terminal status")
        ledger = reduce_ledger(self.events.events)
        if ledger is None:
            raise ValueError("task was not created")
        if ledger.status == "active":
            self.events.append("task.finished", {"task_id": ledger.task_id,
                "status": status, "reason": reason}, "task-finished")
        return self.status()

    def close(self):
        self.broker.cancel()
        self.worker.close()
        fcntl.flock(self.lock.fileno(), fcntl.LOCK_UN)
        self.lock.close()
