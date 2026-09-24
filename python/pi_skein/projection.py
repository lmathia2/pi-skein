"""Plain-text Pi projection of Skein's PTC state descriptions."""

from __future__ import annotations

import json
from typing import Any

from .worker import project_live_binding


def state_updates(previous: list[dict[str, Any]], current: list[dict[str, Any]],
                  *, max_bytes: int = 2048,
                  completed_reads: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Return changed described bindings; unchanged and un-described values stay hidden."""
    def key(item: dict[str, Any]) -> str:
        return json.dumps([item.get("name"), item.get("selector", [])], sort_keys=True)

    before = {key(item): item for item in previous if item.get("description") or item.get("read_reference")}
    after = {key(item): item for item in current}
    updates: list[dict[str, Any]] = []
    for identity in sorted(set(before) | set(after)):
        item = after.get(identity)
        if item == before.get(identity):
            continue
        if item is None or not (item.get("description") or item.get("read_reference")):
            if identity not in before:
                continue
            old = before[identity]
            projected = {"name": old.get("name"), "selector": old.get("selector", []),
                         "availability": "association_invalidated"}
        else:
            projected = project_live_binding(item)
        proposed = [*updates, projected]
        if len(updates) < 8 and len(json.dumps(proposed, sort_keys=True, ensure_ascii=False).encode()) <= max_bytes:
            updates.append(projected)
    for reference in reversed(completed_reads or []):
        uri = reference.get("artifact_uri")
        if not isinstance(uri, str) or any(item.get("historical_read", {}).get("artifact_uri") == uri
                                           for item in updates):
            continue
        candidate = {"historical_read": reference, "availability": "historical_read_only"}
        proposed = [*updates, candidate]
        if len(updates) < 8 and len(json.dumps(proposed, sort_keys=True, ensure_ascii=False).encode()) <= max_bytes:
            updates.append(candidate)
    return updates


def state_notice(updates: list[dict[str, Any]], *, state_lost: bool = False) -> str:
    """Render advisory state metadata as Pi-friendly text instead of a JSON envelope."""
    lines: list[str] = []
    if state_lost:
        lines.append("The failed cell's partial assignments were discarded; earlier live bindings may need recovery.")
    if updates:
        lines.append("Python state updates (advisory; saved reads are historical):")
        for item in updates:
            historical = item.get("historical_read")
            if isinstance(historical, dict):
                uri = historical.get("artifact_uri")
                lines.append(f"- Historical read {historical.get('path', '')}: {uri}; "
                             "load with agent.artifacts.load(uri) and check current source before reuse.")
                continue
            name = str(item.get("access_expression") or item.get("name") or "binding")
            if item.get("availability") == "association_invalidated":
                lines.append(f"- {name}: prior description or source association is no longer valid")
                continue
            description = str(item.get("description") or item.get("type") or "changed")
            lines.append(f"- {name}: {description}")
            reference = item.get("read_reference")
            if isinstance(reference, dict) and reference.get("artifact_uri"):
                lines.append(f"  Historical read: {reference['artifact_uri']}; check current source before reuse.")
    return "\n".join(lines)
