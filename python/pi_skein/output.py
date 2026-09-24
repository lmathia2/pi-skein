"""PTC result paging with a small plain-text model projection."""

from __future__ import annotations


def project_output(full: str, artifact_uri: str, *, max_bytes: int = 16000,
                   max_lines: int = 400) -> tuple[str, int]:
    """Bound visible UTF-8 without splitting a character; retain full text by URI."""
    raw = full.encode("utf-8")
    selected = "".join(full.splitlines(keepends=True)[:max_lines])
    if selected == full and len(raw) <= max_bytes:
        return full, 0
    notice = f"\n[complete output: {artifact_uri}; use code(more=...) to page]"
    available = max_bytes - len(notice.encode())
    if available < 0:
        raise ValueError("output projection budget is smaller than paging notice")
    selected_bytes = selected.encode()[:available]
    selected = selected_bytes.decode("utf-8", errors="ignore")
    return selected + notice, max(0, len(raw) - len(selected.encode()))
