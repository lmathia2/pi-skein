import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from pi_skein.runtime import SkeinRuntime
from pi_skein.output import project_output


class RuntimeIntegrationTest(unittest.TestCase):
    def test_large_output_is_plain_text_and_exactly_pageable(self):
        full = "é\n" * 5000
        uri = "artifact://sha256/" + "a" * 64
        visible, omitted = project_output(full, uri)
        self.assertLessEqual(len(visible.encode()), 16000)
        self.assertIn("code(more=...)", visible)
        self.assertGreater(omitted, 0)
        self.assertFalse(visible.startswith("�"))

    def test_model_context_is_text_and_traceable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = SkeinRuntime(str(root), str(root / "state"), "context-task",
                                   "Change greeting", ["Output says hello"])
            try:
                first = runtime.project_context(max_tokens=400)
                self.assertIn("## TASK\n\nGoal: Change greeting", first["content"])
                self.assertIn("- Output says hello", first["content"])
                self.assertEqual(first["content_hash"], hashlib.sha256(first["content"].encode()).hexdigest())
                self.assertEqual(first["source_watermark"], 1)
                runtime.execute("print('hello')", "context-cell")
                projection = runtime.trace(kind="context.tool_projection_created")["events"][0]
                cell = runtime.trace(kind="cell.completed")["events"][0]
                self.assertEqual(projection["payload"]["model_visible_sha256"],
                                 hashlib.sha256(cell["payload"]["response"]["text"].encode()).hexdigest())
                self.assertEqual(runtime.notebook()[0]["outputs"][0]["text"], "hello\n")
                materialized = runtime.trace(kind="notebook.materialized")["events"][0]["payload"]
                notebook_bytes = Path(materialized["path"]).read_bytes()
                self.assertEqual(hashlib.sha256(notebook_bytes).hexdigest(), materialized["content_sha256"])
                notebook = json.loads(notebook_bytes)
                self.assertEqual(notebook["nbformat"], 4)
                self.assertEqual(notebook["cells"][1]["source"], ["print('hello')"])
                second = runtime.project_context(max_tokens=400)
                self.assertGreater(second["source_watermark"], first["source_watermark"])
                with self.assertRaisesRegex(ValueError, "budget"):
                    runtime.project_context(max_tokens=1)
            finally:
                runtime.close()

    def test_v41_result_projection_and_paging(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = SkeinRuntime(str(root), str(root / "state"), "projection-task", "Inspect command")
            try:
                output = runtime.execute("print(bash('printf ok; printf warning >&2; exit 3'))", "projection-cell")
                self.assertEqual(output["status"], "ok")
                self.assertIn("Shell call 1: error [exit 3]", output["text"])
                self.assertEqual(output["details"]["broker_outcomes"][0]["exit_code"], 3)
                result_id = output["details"]["result_id"]
                page = runtime.page(result_id)
                self.assertIn("Result " + result_id, page["text"])
                self.assertEqual(page["details"]["result_id"], result_id)
                self.assertEqual(runtime.trace(kind="result.retained")["events"][0]["payload"]["result_id"], result_id)
            finally:
                runtime.close()

    def test_read_attestation_reaches_ptc_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "source.txt").write_text("first\nsecond\n")
            runtime = SkeinRuntime(str(workspace), str(root / "state"), "read-task", "Inspect source")
            try:
                output = runtime.execute("source = agent.fs.read('source.txt')\n"
                    "print(source['read_reference']['source_coverage']['whole_file'])\n"
                    "print(agent.state.reads())", "read-cell")
                self.assertEqual(output["status"], "ok")
                self.assertIn("True", output["text"])
                self.assertIn("read:1", output["text"])
                effect = runtime.trace(kind="effect.completed")["events"][0]["payload"]
                self.assertEqual(effect["read_evidence"]["returned_lines"], 2)
                self.assertTrue(effect["result_artifact_uri"].startswith("artifact://sha256/"))
                page = runtime.broker.artifacts_load(effect["result_artifact_uri"])
                self.assertEqual(json.loads(page["data"]["text"])["data"]["text"], "first\nsecond\n")
            finally:
                runtime.close()

    def test_described_state_is_retained_without_extra_model_packet(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = SkeinRuntime(str(root), str(root / "state"), "notice-task", "Compute value")
            try:
                output = runtime.execute("value = 42\nagent.state.annotate('value', 'answer to the calculation')", "notice-cell")
                self.assertEqual(output["status"], "ok")
                self.assertIn("answer to the calculation", output["text"])
                self.assertNotIn("Python state updates", output["text"])
                terminal = runtime.trace(kind="cell.completed")["events"][0]["payload"]
                self.assertEqual(terminal["state_updates"][0]["description"], "answer to the calculation")
            finally:
                runtime.close()

    def test_repeated_read_reuses_captured_source_in_model_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            content = "repeated source line\n" * 30
            (workspace / "source.txt").write_text(content)
            runtime = SkeinRuntime(str(workspace), str(root / "state"), "reuse-task", "Inspect source")
            try:
                first = runtime.execute("source = read('source.txt')\nprint(source[:25])", "reuse-first")
                self.assertEqual(first["status"], "ok")
                second = runtime.execute("source2 = read('source.txt')\nprint(source2)", "reuse-second")
                self.assertEqual(second["status"], "ok")
                self.assertIn("source already retained", second["text"])
                self.assertNotIn(content, second["text"])
                read_events = runtime.trace(kind="effect.completed")["events"]
                self.assertEqual(len(read_events), 2)
            finally:
                runtime.close()

    def test_failed_cell_retains_completed_read_as_historical_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "source.txt").write_text("one\ntwo\n")
            runtime = SkeinRuntime(str(workspace), str(root / "state"), "failed-read", "Inspect source")
            try:
                result = runtime.execute("captured = read('source.txt')\nraise ValueError('stop')", "failed-cell")
                self.assertEqual(result["status"], "error")
                self.assertIn("Plain-data namespace rolled back", result["text"])
                terminal = runtime.trace(kind="cell.completed")["events"][0]["payload"]
                self.assertEqual(terminal["state_updates"][0]["availability"], "historical_read_only")
            finally:
                runtime.close()

    def test_cells_effects_trace_and_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            runtime = SkeinRuntime(str(workspace), str(root / "state"), "task-1", "Change greeting",
                                   verification_requirements=["test -f greet.txt"])
            try:
                first = runtime.execute("n = 7\nprint(write('greet.txt', 'hello'))", "call-1")
                self.assertEqual(first["status"], "ok")
                self.assertEqual((workspace / "greet.txt").read_text(), "hello")
                self.assertEqual(runtime.execute("print(n * 6)", "call-2")["text"], "42\n")
                self.assertEqual(runtime.execute("n = 7\nprint(write('greet.txt', 'hello'))", "call-1"), first)
                with self.assertRaisesRegex(ValueError, "different code"):
                    runtime.execute("write('greet.txt', 'bad')", "call-1")
                self.assertEqual(runtime.execute("print(n * 6)", "call-2")["text"], "42\n")
                self.assertEqual(runtime.status()["ledger"]["changed_paths"], ["greet.txt"])
                self.assertEqual(len(runtime.notebook()), 2)
                self.assertEqual(runtime.notebook()[0]["status"], "ok")
                self.assertTrue(runtime.trace(kind="cell.completed")["events"])
                with self.assertRaisesRegex(ValueError, "task contract"):
                    runtime.verify_task(["true"])
                self.assertTrue(runtime.verify_task(["test -f greet.txt"])["passed"])
                self.assertEqual(runtime.status()["ledger"]["status"], "complete")
                with self.assertRaisesRegex(RuntimeError, "terminal"):
                    runtime.execute("print('late')", "call-3")
            finally:
                runtime.close()

            reopened = SkeinRuntime(str(workspace), str(root / "state"), "task-1")
            try:
                self.assertEqual(reopened.status()["ledger"]["status"], "complete")
                self.assertEqual(reopened.worker.execute("print(n)", reopened.broker).stdout, "7\n")
                events = [json.loads(line) for line in Path(reopened.status()["trace_path"]).read_text().splitlines()]
                self.assertEqual([item["sequence"] for item in events], list(range(1, len(events) + 1)))
            finally:
                reopened.close()

    def test_interrupted_effect_blocks_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = SkeinRuntime(str(root), str(root / "state"), "task-2", "Unknown effect")
            try:
                runtime.events.append("effect.started", {"operation_id": "lost", "operation": "shell.run"})
                self.assertIn("lost", runtime.status()["ledger"]["unresolved_effects"])
                with self.assertRaisesRegex(RuntimeError, "unresolved"):
                    runtime.execute("print(1)", "call-1")
                self.assertFalse(runtime.verify_task(["true"])["passed"])
            finally:
                runtime.close()

    def test_mutating_verifier_cannot_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            runtime = SkeinRuntime(str(workspace), str(root / "state"), "task-3", "Check without mutation")
            try:
                report = runtime.verify_task(["touch generated.txt"])
                self.assertFalse(report["passed"])
                self.assertEqual(runtime.status()["ledger"]["status"], "active")
                self.assertIn("generated.txt", runtime.status()["ledger"]["changed_paths"])
                with self.assertRaisesRegex(ValueError, "escapes workspace"):
                    runtime.broker.read("../outside.txt")
            finally:
                runtime.close()

    def test_resume_rejects_workspace_divergence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            runtime = SkeinRuntime(str(workspace), str(root / "state"), "task-4", "Preserve checkpoint")
            try:
                self.assertEqual(runtime.execute("value = 5\nwrite('saved.txt', 'known')", "cell-1")["status"], "ok")
            finally:
                runtime.close()
            (workspace / "saved.txt").write_text("outside change")
            with self.assertRaisesRegex(ValueError, "workspace revision"):
                SkeinRuntime(str(workspace), str(root / "state"), "task-4")

    def test_pi_trace_inside_state_root_does_not_invalidate_ptc_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            state_root = workspace / ".skein-state"
            runtime = SkeinRuntime(str(workspace), str(state_root), "task-with-pi-trace", "Compute")
            try:
                self.assertEqual(runtime.execute("value = 5", "cell-1")["status"], "ok")
            finally:
                runtime.close()
            trace_dir = state_root / "pi-trace" / "sample"
            trace_dir.mkdir(parents=True)
            (trace_dir / "events.jsonl").write_text('{"sequence":1}\n')
            reopened = SkeinRuntime(str(workspace), str(state_root), "task-with-pi-trace")
            try:
                self.assertEqual(reopened.execute("print(value)", "cell-2")["text"], "5\n")
            finally:
                reopened.close()

    def test_provider_error_is_terminal_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = SkeinRuntime(str(root), str(root / "state"), "task-5", "Provider failure")
            try:
                status = runtime.finish("failed", "Pi agent error")
                self.assertEqual(status["ledger"]["status"], "failed")
                self.assertFalse(status["ledger"]["outcome"].get("verification"))
                with self.assertRaisesRegex(RuntimeError, "terminal"):
                    runtime.execute("print('should not run')", "call-1")
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
