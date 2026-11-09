"""Offline acceptance checks using synthetic transcripts and a temporary database."""

import json
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from chronicle import core, db


class ChronicleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="chronicle-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.projects = self.root / "claude" / "projects"
        self.projects.mkdir(parents=True)
        self.data = self.root / "data"
        for target, values in (
            (db, {"HOME": self.data, "DB_PATH": self.data / "chronicle.db"}),
            (core, {"PROJECTS": self.projects, "JOBS": self.root / "claude" / "jobs",
                    "REPORTS": self.data / "sessions", "WORK_REPORTS": self.data / "reports"}),
        ):
            for name, value in values.items():
                patcher = patch.object(target, name, value)
                patcher.start()
                self.addCleanup(patcher.stop)
        self.no_model = patch.object(core.subprocess, "run", side_effect=AssertionError("Model must not run"))
        self.no_model.start()
        self.addCleanup(self.no_model.stop)
        self.transcript = self.projects / "demo" / "demo-session.jsonl"
        self.transcript.parent.mkdir()
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def append(self, record):
        with self.transcript.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def seed(self):
        self.append({"type": "user", "timestamp": self.timestamp,
                     "cwd": str(self.root / "example-project"),
                     "message": {"content": "Собери демонстрационный сайт"}})
        self.append({"type": "assistant", "timestamp": self.timestamp,
                     "message": {"content": [{"type": "text", "text": "Сайт собран, проверка пройдена."}]}})

    def test_partial_json_line_is_read_only_after_completion(self):
        self.transcript.write_bytes(b'{"type":"user"}\n{"type":')
        records, offset = core._read_new(self.transcript, 0)
        self.assertEqual(records, [{"type": "user"}])
        self.assertEqual(offset, len(b'{"type":"user"}\n'))
        with self.transcript.open("ab") as stream:
            stream.write(b'"assistant"}\n')
        records, final = core._read_new(self.transcript, offset)
        self.assertEqual(records, [{"type": "assistant"}])
        self.assertEqual(final, self.transcript.stat().st_size)

    def test_scan_is_incremental_and_does_not_duplicate_records(self):
        self.seed()
        with core.connect() as connection:
            self.assertEqual(core.scan(con=connection), 1)
            self.assertEqual(core.scan(con=connection), 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM cc_log").fetchone()[0], 2)
            self.append({"type": "user", "timestamp": self.timestamp,
                         "message": {"content": "Добавь страницу контактов"}})
            self.assertEqual(core.scan(con=connection), 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM cc_log").fetchone()[0], 3)
            session = core.sessions(con=connection)[0]
            self.assertEqual(len(session["prompts"]), 2)

    def test_first_run_produces_sessions_and_report_without_model(self):
        self.seed()
        stats = core.run(llm=False)
        self.assertEqual(stats["changed"], 1)
        self.assertEqual(stats["calls"], 0)
        self.assertEqual(stats["pending"], 2)
        sessions = core.sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["status"], "идёт")
        self.assertIn("Собери демонстрационный сайт", core.render(sessions))
        report = core.report(llm=False)
        self.assertTrue(report.is_relative_to(self.data))
        self.assertIn("1 сессий", report.read_text())
        self.assertIn("demo-session", report.read_text())

    def test_truncated_transcript_resets_session_metadata_and_keeps_extracted_work(self):
        old_timestamp = datetime.fromtimestamp(time.time() + 10, timezone.utc).isoformat()
        new_timestamp = datetime.fromtimestamp(time.time() - 10, timezone.utc).isoformat()
        self.append({"type": "user", "timestamp": old_timestamp,
                     "cwd": str(self.root / "old-project"), "entrypoint": "sdk-cli",
                     "message": {"content": "Собери демонстрационный сайт"}})
        self.append({"type": "ai-title", "timestamp": old_timestamp, "aiTitle": "Старое название"})
        self.append({"type": "assistant", "timestamp": old_timestamp,
                     "message": {"content": [
                         {"type": "tool_use", "name": "Write", "input": {"file_path": str(self.root / "old.py")}},
                         {"type": "text", "text": "Старый результат"}]}})
        with core.connect() as connection:
            core.scan(con=connection)
            answer = {"summary": {"task": "Сайт", "done": "Собран", "now": "Готово", "owner": ""},
                      "items": [{"kind": "result", "text": "Сохранённый результат", "metric": ""}]}
            with patch.object(core, "_ask", return_value=json.dumps(answer)):
                core.summarize(con=connection)
            saved_work = [tuple(row) for row in connection.execute("SELECT * FROM cc_work")]
            self.assertEqual(len(saved_work), 1)
            old_size = self.transcript.stat().st_size
            self.transcript.write_text(json.dumps({"type": "user", "timestamp": new_timestamp,
                                                  "message": {"content": "Новый запрос"}},
                                                 ensure_ascii=False) + "\n", encoding="utf-8")
            self.assertLess(self.transcript.stat().st_size, old_size)
            self.assertEqual(core.scan(con=connection), 1)
            row = connection.execute("SELECT * FROM cc_sessions WHERE id = 'demo-session'").fetchone()
            self.assertEqual([item["text"] for item in json.loads(row["prompts"])], ["Новый запрос"])
            self.assertEqual(json.loads(row["files"]), [])
            self.assertEqual(row["tools"], 0)
            self.assertIsNone(row["last_reply"])
            self.assertIsNone(row["title"])
            self.assertIsNone(row["entry"])
            self.assertIsNone(row["cwd"])
            self.assertIsNone(row["cwd_last"])
            self.assertEqual(row["started_ts"], core._ts(new_timestamp))
            self.assertEqual(row["last_ts"], core._ts(new_timestamp))
            self.assertEqual([tuple(row) for row in connection.execute("SELECT * FROM cc_work")], saved_work)
            self.assertEqual(core.pending(con=connection), 0)
            self.assertEqual(core.scan(con=connection), 0)

    def test_model_failure_preserves_pending_records_for_retry(self):
        self.seed()
        with core.connect() as connection:
            core.scan(con=connection)
            before = core.pending(con=connection)
            with patch.object(core, "_ask", side_effect=db.ChronicleError("Synthetic unavailable model")):
                self.assertEqual(core.summarize(con=connection), 1)
            self.assertEqual(core.pending(con=connection), before)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM cc_work").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM errors").fetchone()[0], 1)
            answer = {"summary": {"task": "Сайт", "done": "Собран", "now": "Готово", "owner": ""},
                      "items": [{"kind": "result", "text": "Собран демонстрационный сайт", "metric": ""}]}
            with patch.object(core, "_ask", return_value=json.dumps(answer)):
                self.assertEqual(core.summarize(con=connection), 1)
                self.assertEqual(core.summarize(con=connection), 0)
            self.assertEqual(core.pending(con=connection), 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM cc_work").fetchone()[0], 1)
        report = core.work_report(time.time() - 3600, time.time() + 3600, llm=False)
        self.assertIn("Собран демонстрационный сайт", report.read_text())

    def test_model_timeout_does_not_discard_pending_records(self):
        self.seed()
        with core.connect() as connection:
            core.scan(con=connection)
            with patch.object(core, "_ask", side_effect=subprocess.TimeoutExpired("synthetic-model", 1)):
                self.assertEqual(core.summarize(con=connection), 1)
            self.assertEqual(core.pending(con=connection), 2)

    def test_malformed_cli_output_is_a_readable_error(self):
        response = subprocess.CompletedProcess(["synthetic-model"], 1, stdout="", stderr="Synthetic CLI error")
        with patch.object(core.subprocess, "run", return_value=response):
            with self.assertRaisesRegex(db.ChronicleError, "Synthetic CLI error"):
                core._ask("Only summarize", "Synthetic data")

    def test_non_object_cli_envelope_is_a_readable_error(self):
        for payload in (None, []):
            with self.subTest(payload=payload):
                response = subprocess.CompletedProcess(["synthetic-model"], 0,
                                                       stdout=json.dumps(payload), stderr="")
                with patch.object(core.subprocess, "run", return_value=response):
                    with self.assertRaises(db.ChronicleError):
                        core._ask("Only summarize", "Synthetic data")

    def test_model_invocation_disables_builtin_tools_and_mcp(self):
        response = subprocess.CompletedProcess(["synthetic-model"], 0,
                                               stdout=json.dumps({"result": "Synthetic summary"}), stderr="")
        with patch.object(core.subprocess, "run", return_value=response) as run:
            self.assertEqual(core._ask("Only summarize", "Synthetic data"), "Synthetic summary")
        arguments = run.call_args.args[0]
        self.assertEqual(arguments[arguments.index("--tools") + 1], "")
        self.assertEqual(arguments[arguments.index("--disallowedTools") + 1], "mcp__*")
        self.assertEqual(arguments[arguments.index("--setting-sources") + 1], "")


if __name__ == "__main__":
    unittest.main()
