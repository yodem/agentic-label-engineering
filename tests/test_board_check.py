import os
"""Real-browser layout gate tests for scripts/board-check.mjs.

These are negative-control tests: each static fixture page in
tests/fixtures/board/check/ is designed to trip exactly one of the four
checks (overlap, overflow, icons, console), plus one clean page that must
pass all four. They run against a tiny http.server-served fixture via
--url, so they do not depend on `ale board` or the Python status pipeline.

Skips (rather than fails) when Chrome or node is unavailable, since this
gate requires a real local Chrome binary that CI/sandbox environments may
not have.
"""
import http.server
import json
import shutil
import subprocess
import sys
import threading
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "board-check.mjs"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "board" / "check"

if os.environ.get("CODEX_SANDBOX"):
    # Chrome is killed at launch inside the Codex sandbox and each kill pops a macOS crash dialog.
    raise unittest.SkipTest("Chrome cannot run inside the Codex sandbox")
CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

NODE_BIN = shutil.which("node")
CHROME_AVAILABLE = Path(CHROME_BIN).exists()


def _skip_reason():
    if NODE_BIN is None:
        return "node is not installed"
    if not CHROME_AVAILABLE:
        return "Chrome binary not found at %s" % CHROME_BIN
    return None


class _FixtureServer:
    """Serves tests/fixtures/board/check/ over plain HTTP on localhost."""

    def __init__(self, directory):
        handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(
            *a, directory=str(directory), **kw
        )
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def url(self, name):
        port = self.httpd.server_address[1]
        return "http://127.0.0.1:%d/%s" % (port, name)

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def run_board_check(url, out_dir):
    proc = subprocess.run(
        [NODE_BIN, str(SCRIPT), "--out", str(out_dir), "--url", url],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=90,
    )
    return proc


@unittest.skipIf(_skip_reason(), _skip_reason() or "")
class TestBoardCheck(unittest.TestCase):
    def setUp(self):
        self.server = _FixtureServer(FIXTURES)
        self.server.__enter__()
        self.addCleanup(self.server.__exit__)

    def _run(self, fixture_name, tmp_path):
        url = self.server.url(fixture_name)
        out_dir = tmp_path
        proc = run_board_check(url, out_dir)
        return proc

    def test_overlap_fixture_fails_overlap_only(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run("overlap.html", tmp)
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            lines = [l for l in proc.stdout.splitlines() if l.startswith("FAIL")]
            self.assertEqual(len(lines), 4, proc.stdout)
            for line in lines:
                self.assertIn("[overlap", line, proc.stdout)
                self.assertNotIn("overflow", line.split("::")[0], proc.stdout)
                self.assertNotIn("icons", line.split("::")[0], proc.stdout)
                self.assertNotIn("console", line.split("::")[0], proc.stdout)
                self.assertNotIn("render-timeout", line, proc.stdout)

    def test_overflow_fixture_fails_overflow_only(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run("overflow.html", tmp)
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            lines = [l for l in proc.stdout.splitlines() if l.startswith("FAIL")]
            self.assertEqual(len(lines), 4, proc.stdout)
            for line in lines:
                self.assertIn("[overflow", line, proc.stdout)
                self.assertNotIn("overlap]", line.split("::")[0], proc.stdout)
                self.assertNotIn("icons", line.split("::")[0], proc.stdout)
                self.assertNotIn("console", line.split("::")[0], proc.stdout)
                self.assertNotIn("render-timeout", line, proc.stdout)

    def test_icon_fixture_fails_icons_only(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run("icon.html", tmp)
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            lines = [l for l in proc.stdout.splitlines() if l.startswith("FAIL")]
            self.assertEqual(len(lines), 4, proc.stdout)
            for line in lines:
                self.assertIn("[icons", line, proc.stdout)
                self.assertNotIn("overlap]", line.split("::")[0], proc.stdout)
                self.assertNotIn("overflow", line.split("::")[0], proc.stdout)
                self.assertNotIn("console", line.split("::")[0], proc.stdout)
                self.assertNotIn("render-timeout", line, proc.stdout)

    def test_clean_fixture_passes_all_cases(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run("clean.html", tmp)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            lines = [l for l in proc.stdout.splitlines() if l.strip()]
            pass_lines = [l for l in lines if l.startswith("PASS")]
            self.assertEqual(len(pass_lines), 4, proc.stdout)
            screenshots = sorted(p.name for p in Path(tmp).glob("*.png"))
            self.assertEqual(
                screenshots,
                [
                    "desktop-dark.png",
                    "desktop-light.png",
                    "mobile-dark.png",
                    "mobile-light.png",
                ],
                proc.stdout,
            )


    def test_empty_render_fails_not_ready(self):
        """A run with real tasks that renders zero rows must never pass.

        Static stand-in for the exact bug this gate exists to catch: the
        page never gets a `.task` element (blocked EventSource, wrong run
        dir, or a status subprocess that failed silently), so the ready
        wait must time out and the case must fail loudly rather than
        report "0 tasks" as a clean pass.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run("empty-render.html", tmp)
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            lines = [l for l in proc.stdout.splitlines() if l.startswith("FAIL")]
            self.assertEqual(len(lines), 4, proc.stdout)
            for line in lines:
                self.assertTrue(
                    "[not-ready" in line or "[tasks-missing" in line,
                    "expected [not-ready] or [tasks-missing], got: %s" % line,
                )

    def test_hidden_element_displayed_by_a_class_rule_fails(self):
        """`.btn{display:inline-flex}` beats `[hidden]{display:none}`; the gate must catch it."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run("hidden-shown.html", tmp)
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            lines = [l for l in proc.stdout.splitlines() if l.startswith("FAIL")]
            self.assertEqual(len(lines), 4, proc.stdout)
            for line in lines:
                self.assertIn("[hidden-shown", line)


if __name__ == "__main__":
    unittest.main()

