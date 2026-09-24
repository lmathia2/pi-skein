// Static model instructions from Skein's measured Pi PTC v4.1 adapter.
export const promptComponents = {
  runtime: 'Run persistent CPython with workspace helpers available as plain functions. Variables/functions persist while the worker lives; committed plain-data variables recover after a restart. json, math, and re are preloaded.',
  workflow: 'Prefer one code call for multi-step work: call helpers, loop/filter/compute, reuse useful variables, and print only what the parent needs.',
  contract: 'Call helpers without await. stdout is returned; the final expression is shown as =>. Contract errors are Python exceptions; bash returns its exit status and verify raises on failure. Imports that access the host are restricted; use the helpers. Paths stay in the workspace; scratch files go in .ptc-scratch/.',
  verification: 'Use verify(...) for the final required check after changes; bash(...) never counts as final verification. Custom probes must assert or exit nonzero on mismatch.',
  example: "source = read('src/app.py')\nprint([line for line in source.splitlines() if 'timeout' in line][:20])\nprint(verify('pytest -q', timeout_seconds=120))",
};

export function assemblePrompt(signatures: string): string {
  return [promptComponents.runtime, promptComponents.workflow, signatures,
    promptComponents.contract, promptComponents.verification, promptComponents.example].join('\n\n');
}

export const helperSignatures = [
  'read(path: str, offset: int = 1, limit: int = 400) -> text (attributes: path, text, sha256, offset, returned_lines, total_lines, complete, next_offset)',
  'write(path: str, content: str, expected_sha256: str | None = None, expected_absent: bool = False) -> receipt (attributes: path, changed, sha256, diff)',
  'edit(path: str, old_text: str, new_text: str, expected_sha256: str | None = None) -> receipt (attributes: path, changed, sha256, diff)',
  'bash(command: str, timeout_seconds: int = 120) -> shell text (attributes: exit_code, stdout, stderr, output, timed_out, timeout_seconds, effects_unknown)',
  'verify(command: str, timeout_seconds: int = 120) -> shell text (raises on failure) (attributes: exit_code, stdout, stderr, output, timed_out, timeout_seconds, effects_unknown)',
].join('\n');

export function needsEvidenceReview(finalText: string, mutationGeneration: number, verifiedGeneration: number): boolean {
  const unresolved = finalText.replace(/\bno known gaps?\b/gi, '');
  return mutationGeneration > verifiedGeneration || /known gaps?|remaining (?:gap|issue)|unimplemented|failing probe/i.test(unresolved);
}

export function advanceEvidence(outcomes: Array<{ operation: string; status: string; changed?: boolean }>, mutationGeneration: number, verifiedGeneration: number): [number, number] {
  for (const outcome of outcomes) {
    if ((outcome.operation === 'write' || outcome.operation === 'edit') && outcome.status === 'ok' && outcome.changed !== false) mutationGeneration++;
    if (outcome.operation === 'bash' || outcome.operation === 'verify') mutationGeneration++;
    if (outcome.operation === 'verify' && outcome.status === 'ok') verifiedGeneration = mutationGeneration;
  }
  return [mutationGeneration, verifiedGeneration];
}

export const evidenceReviewPrompt = 'Before finalizing, compare every requirement with concrete evidence. Run the required final check with verify(...). Do not finish while required behavior remains a known gap.';
