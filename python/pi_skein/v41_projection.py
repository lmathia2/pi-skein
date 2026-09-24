"""Pi-facing PTC v4.1 result projection copied from Skein benchmark adapter."""

from __future__ import annotations

import json
import re

PTC_OBSERVATION_BYTES = 50 * 1024
PTC_RESULT_BYTES = 256_000
PTC_RESULT_COUNT = 32

def _bounded_text(value: str, limit: int) -> tuple[str, bool]:
    encoded = value.encode('utf-8')
    if len(encoded) <= limit:
        return value, False
    return encoded[:limit].decode('utf-8', errors='ignore'), True


def _bounded_head_tail(value: str, limit: int) -> tuple[str, bool]:
    encoded = value.encode('utf-8')
    if len(encoded) <= limit:
        return value, False
    marker = b'\n...[middle omitted]...\n'
    available = max(limit - len(marker), 0)
    head = encoded[:available * 2 // 3].decode('utf-8', errors='ignore')
    tail = encoded[-(available // 3):].decode('utf-8', errors='ignore') if available else ''
    return head + marker.decode() + tail, True


def _fence(text: str) -> str:
    fence = '`' * max(3, max((len(x) for x in re.findall(r'`+', text)), default=0) + 1)
    return f'{fence}text\n{text}\n{fence}'


def _ptc_record(result, result_id: str, outcomes: list[dict] = ()) -> str:
    parts = [f'Result {result_id}: {result.status}']
    for label, text in [('stdout', result.full_stdout or result.stdout),
                        ('value', result.full_value_repr or result.value_repr),
                        ('stderr', result.full_stderr or result.stderr),
                        ('error', result.error_message)]:
        if text:
            parts.append(f'{label}:\n{text}')
    for outcome in outcomes:
        parts.append(json.dumps(outcome, ensure_ascii=False))
    record = '\n\n'.join(parts)
    clipped, truncated = _bounded_text(record, PTC_RESULT_BYTES - 160)
    return clipped + (f'\n[retention truncated: original {len(record.encode())} bytes; discarded suffix unavailable]' if truncated else '')


def _ptc_response(result, result_id: str, outcomes: list[dict] = (),
                  prior_names: set[str] = frozenset(), reuse: list[str] = (),
                  checkpoint: dict | None = None) -> dict:
    stdout = result.full_stdout or result.stdout
    value = result.full_value_repr or result.value_repr
    stderr = result.full_stderr or result.stderr
    selected = '\n'.join(part for part in (stdout, f'=> {value}' if value else '',
                                            f'[stderr]\n{stderr}' if stderr else '') if part)
    notices, telemetry = [], []
    omitted = result.output_truncated
    for index, outcome in enumerate(outcomes):
        data = outcome.get('data', {})
        operation = outcome['operation']
        notice = None
        if operation in {'bash', 'verify'}:
            stderr = data.get('stderr', '')
            code = data.get('exit_code')
            # Conservative: do not infer visibility from a coincidental substring.
            if stderr or code != 0 or outcome['status'] != 'ok':
                diagnostic, clipped = _bounded_text('\n'.join(stderr.splitlines()[:max(1, 700 // max(1, len(outcomes)))]), min(2000, 18000 // max(1, len(outcomes))))
                clipped |= diagnostic != stderr
                notice = f"Shell call {index + 1}: {outcome['status']} [exit {code}]\nCommand (data):\n{_fence(outcome.get('command', '')[:120])}"
                if diagnostic:
                    notice += f'\nstderr (data):\n{_fence(diagnostic)}'
                if outcome.get('error'):
                    notice += '\n' + outcome['error']
                omitted |= clipped
            telemetry.append({'call': index + 1, 'operation': operation, 'status': outcome['status'],
                'command': outcome.get('command', ''),
                'exit_code': code, 'timeout': data.get('timed_out', False),
                'stdout_bytes': outcome.get('original_stream_bytes', {}).get('stdout', len(data.get('stdout', '').encode())), 'stderr_bytes': outcome.get('original_stream_bytes', {}).get('stderr', len(stderr.encode())),
                'selected_omitted_stderr': bool(stderr and stderr not in selected),
                'selected_omitted_exit': code != 0 and f'[exit {code}]' not in selected,
                'notice_supplied': notice is not None, 'duration_ms': outcome.get('duration_ms')})
        elif operation in {'write', 'edit'}:
            diff = data.get('diff', '') or ''
            # Exact matching only; arbitrary dictionaries are left untouched.
            envelope = repr({'status': outcome['status'], 'data': data})
            receipt = f"{operation}: {json.dumps(data.get('path'))} — {'changed' if data.get('changed') else 'no change'}; +{sum(x.startswith('+') and not x.startswith('+++') for x in diff.splitlines())}/-{sum(x.startswith('-') and not x.startswith('---') for x in diff.splitlines())} lines."
            if envelope in selected:
                selected = selected.replace(envelope, receipt)
                omitted |= bool(diff)
            if outcome['status'] != 'ok':
                notice = f"{operation} failed: {outcome.get('error', '')}"
            elif result.status != 'ok':
                notice = receipt + ' Filesystem effects were not rolled back.'
        elif operation == 'read' and not data.get('complete', True):
            notice = f"Partial read (path data): {json.dumps(data.get('path'))}; lines {data.get('offset')}-{data.get('offset', 1) + data.get('returned_lines', 0) - 1} of {data.get('total_lines')}. Next offset: {data.get('next_offset')}; None means end of file, not full acquisition. Do not overwrite a file from a partial read."
        if operation not in {'bash', 'verify'}:
            telemetry.append({'call': index + 1, 'operation': operation, 'status': outcome['status'],
                              'complete': data.get('complete'), 'changed': data.get('changed'),
                              'duration_ms': outcome.get('duration_ms')})
        if outcome['status'] == 'error' and not notice:
            notice = f"{operation} failed: {outcome.get('error', '')}"
        if notice:
            notice, notice_cut = _bounded_text('\n'.join(notice.splitlines()[:20]), max(200, 22000 // max(1, len(outcomes))))
            omitted |= notice_cut
            notices.append(notice)
    if result.error_message:
        notices.append(_bounded_text(f'{result.error_type}: {result.error_message}', 2000)[0])
    if result.status == 'timeout' or result.failure_stage == 'transport':
        if checkpoint is not None:
            omitted_names = checkpoint.get('omitted_names', [])
            notices.append('Worker discarded; the last committed plain-data checkpoint will be restored on the next cell.' +
                (f" Opaque bindings lost: {', '.join(omitted_names[:12])}." if omitted_names else ''))
        else:
            notices.append('Worker discarded with no committed checkpoint. External effects may be unknown; reconcile before retrying.')
    elif result.status == 'error':
        notices.append('Plain-data namespace rolled back; external effects are not rolled back.' if result.state_preserved else 'Cell failed; inspect state before continuing.')
    if result.state_deleted:
        notices.append('Deleted or unavailable variables: ' + ', '.join(result.state_deleted[:12]))
    new = sorted(set(result.state_delta) - prior_names)
    # Reserve half the cap for operational evidence. Bound each record at capture.
    mandatory, notice_clipped = _bounded_text('\n\n'.join(dict.fromkeys(notices)), PTC_OBSERVATION_BYTES // 2)
    line_budget = max(1, 1990 - len(mandatory.splitlines()))
    selected_lines = selected.splitlines(keepends=True)
    if len(selected_lines) > line_budget:
        head_count = line_budget * 2 // 3
        selected = ''.join(selected_lines[:head_count]) + '\n...[middle lines omitted]...\n' + ''.join(selected_lines[-(line_budget - head_count):])
    body, clipped = _bounded_head_tail(selected, PTC_OBSERVATION_BYTES - len(mandatory.encode()) - 500)
    omitted |= clipped or notice_clipped or len(selected_lines) > line_budget
    text = '\n\n'.join(x for x in (body, mandatory) if x) or '(no output)'
    if omitted:
        text += f"\nDetails omitted: code(more='{result_id}', offset=0). Retention: 256 KB, last 32 cells; discarded suffixes are unavailable."
    return {'text': text, 'details': {
        'result_id': result_id, 'status': result.status, 'state_count': result.state_count,
        'state_delta': new, 'state_deleted': list(result.state_deleted), 'state_preserved': result.state_preserved,
        'failure_stage': result.failure_stage, 'duration_ms': result.duration_ms,
        'output_truncated': omitted, 'broker_outcomes': telemetry,
        'prior_bindings_read_before_assignment': list(reuse),
        'checkpoint': ({'source_cell_id': checkpoint['source_cell_id'],
                        'value_count': len(checkpoint['values']),
                        'omitted_names': checkpoint.get('omitted_names', [])}
                       if checkpoint is not None else None),
    }}


