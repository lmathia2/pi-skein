"""Bounded, deterministic model-facing PTC task packet."""

from __future__ import annotations

from typing import Any


class ContextBudgetExceeded(ValueError):
    pass


def _estimate(text: str) -> int:
    return (len(text) + 3) // 4


def build_coding_packet(ledger: Any, *, max_tokens: int = 20000) -> str:
    """Keep required control whole, as Skein's v4.1 packet does."""
    criteria = "\n".join(f"- {item}" for item in ledger.criteria) or "- Complete the requested outcome."
    constraints = "\n".join(f"- {item}" for item in ledger.constraints) or "- None."
    changed = "\n".join(f"- `{path}`" for path in ledger.changed_paths) or "- None yet."
    verification = ledger.latest_verification
    if verification is None:
        latest = ""
    else:
        mark = "PASSED" if verification.get("passed") else "FAILED"
        commands = ", ".join(item.get("command", "") for item in verification.get("commands", []))
        latest = f"**{mark}** — {commands or verification.get('reason', 'verification')}"
    sections = [
        ("TASK", f"Goal: {ledger.goal}\n\nAcceptance criteria:\n{criteria}\n\nConstraints:\n{constraints}"),
        ("CONTINUATION", f"Workspace changes:\n{changed}\n\nNext action: "
         + ("Resolve the recorded execution uncertainty before more work." if ledger.unresolved_effects
            else "Fix the verification failure and rerun the required checks." if verification and not verification.get("passed")
            else "Continue the task.")),
        ("LATEST VERIFICATION", latest),
        ("EVIDENCE NAVIGATION", "Page a retained result with code(more=...). "
         "Inspect live Python values with agent.state.list() or agent.state.describe(name) "
         "inside a cell. Prior reads are historical observations; check current source before reuse."),
    ]
    packet = "\n\n".join(f"## {heading}\n\n{body}" for heading, body in sections if body)
    required = _estimate(packet)
    if required > max_tokens:
        raise ContextBudgetExceeded(f"Required PTC context needs {required} estimated tokens; budget is {max_tokens}")
    return packet
