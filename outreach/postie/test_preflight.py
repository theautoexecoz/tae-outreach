import subprocess
import tempfile
import unittest
from pathlib import Path

import preflight


class LoadBatchTests(unittest.TestCase):
    def _batch(self, content: str) -> str:
        handle = tempfile.NamedTemporaryFile("w", delete=False)
        handle.write(content)
        handle.close()
        self.addCleanup(Path(handle.name).unlink, missing_ok=True)
        return handle.name

    def test_loads_unique_tab_separated_rows(self):
        path = self._batch("12\tPerson@Example.com\n13\tother@example.com\n")
        self.assertEqual(
            preflight.load_batch(path),
            [(12, "person@example.com"), (13, "other@example.com")],
        )

    def test_rejects_duplicate_recipient(self):
        path = self._batch("12\tperson@example.com\n13\tperson@example.com\n")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            preflight.load_batch(path)

    def test_rejects_malformed_row(self):
        path = self._batch("not-a-row\n")
        with self.assertRaisesRegex(ValueError, "tab-separated"):
            preflight.load_batch(path)


class ArchiveQueryTests(unittest.TestCase):
    def test_passes_sql_over_stdin_and_parses_matches(self):
        seen = {}

        def runner(command, **kwargs):
            seen["command"] = command
            seen["input"] = kwargs["input"]
            return subprocess.CompletedProcess(
                command, 0,
                stdout="12\tperson@example.com\t2026-08-04 12:15:31\tSubject\n",
                stderr="",
            )

        rows = preflight.query_archive([(12, "person@example.com")], runner=runner)
        self.assertEqual(rows[0][1], "person@example.com")
        self.assertIn("tae-mailarchive-db", seen["command"])
        self.assertIn(preflight.POSTIE_BODY_MARKER, seen["input"])

    def test_fails_closed_when_archive_query_fails(self):
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 2, stdout="", stderr="database unavailable")

        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            preflight.query_archive([(12, "person@example.com")], runner=runner)


if __name__ == "__main__":
    unittest.main()
