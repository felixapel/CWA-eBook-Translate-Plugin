"""Security contracts for the destructive cache-cleanup credential."""
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("BT_AUTH_MODE", "disabled")
os.environ.setdefault("BT_ALLOW_INSECURE_AUTH", "true")

import server


class CleanupTokenTests(unittest.TestCase):
    def setUp(self):
        self.original_api_token = server.API_TOKEN
        self.original_path = server._CLEANUP_TOKEN_PATH
        self.original_cache = server._cleanup_token_cache
        self.original_file_mode = server._CLEANUP_FILE_MODE
        self.tempdir = tempfile.TemporaryDirectory()
        server.API_TOKEN = ""
        server._CLEANUP_TOKEN_PATH = Path(self.tempdir.name) / "cleanup_token"
        server._cleanup_token_cache = None

    def tearDown(self):
        server.API_TOKEN = self.original_api_token
        server._CLEANUP_TOKEN_PATH = self.original_path
        server._cleanup_token_cache = self.original_cache
        server._CLEANUP_FILE_MODE = self.original_file_mode
        self.tempdir.cleanup()

    def test_concurrent_first_use_creates_one_consistent_secret(self):
        workers = 12
        start = threading.Barrier(workers + 1)
        generated = []
        generated_lock = threading.Lock()

        def fake_token_urlsafe(_size):
            with generated_lock:
                token = f"generated-token-{len(generated)}"
                generated.append(token)
            # Keep the creation window open so an unlocked implementation
            # deterministically lets the other callers generate too.
            time.sleep(0.03)
            return token

        results = []
        errors = []

        def get_token():
            start.wait()
            try:
                results.append(server._get_cleanup_token())
            except Exception as exc:  # pragma: no cover - assertion evidence
                errors.append(exc)

        with mock.patch.object(
            server._secrets, "token_urlsafe", side_effect=fake_token_urlsafe
        ):
            threads = [threading.Thread(target=get_token) for _ in range(workers)]
            for thread in threads:
                thread.start()
            start.wait()
            for thread in threads:
                thread.join(timeout=3)

        self.assertFalse(errors)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(generated), 1)
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(server._CLEANUP_TOKEN_PATH.read_text(), results[0])
        mode = stat.S_IMODE(server._CLEANUP_TOKEN_PATH.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_existing_token_permissions_are_repaired(self):
        server._CLEANUP_TOKEN_PATH.write_text("persisted-token")
        server._CLEANUP_TOKEN_PATH.chmod(0o644)

        self.assertEqual(server._get_cleanup_token(), "persisted-token")
        mode = stat.S_IMODE(server._CLEANUP_TOKEN_PATH.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_operator_group_mode_keeps_persisted_credential_group_readable(self):
        server._CLEANUP_FILE_MODE = 0o640

        token = server._get_cleanup_token()

        self.assertTrue(token)
        mode = stat.S_IMODE(server._CLEANUP_TOKEN_PATH.stat().st_mode)
        self.assertEqual(mode, 0o640)
        lock = server._CLEANUP_TOKEN_PATH.with_name("cleanup_token.lock")
        self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o640)

    def test_concurrent_processes_share_the_same_persisted_secret(self):
        start_file = Path(self.tempdir.name) / "start"
        child_code = (
            "import os,sys,time; "
            "start=sys.argv[1]; "
            "exec('while not os.path.exists(start):\\n time.sleep(0.001)'); "
            "import server; print(server._get_cleanup_token())"
        )
        processes = []
        for index in range(8):
            env = os.environ.copy()
            env["BT_API_TOKEN"] = ""
            env["BT_CACHE_DIR"] = self.tempdir.name
            env["DB_PATH"] = str(Path(self.tempdir.name) / f"child-{index}.db")
            processes.append(subprocess.Popen(
                [sys.executable, "-c", child_code, str(start_file)],
                cwd=Path(__file__).parent,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ))

        start_file.write_text("go")
        outputs = [process.communicate(timeout=10) for process in processes]
        for process, (_stdout, stderr) in zip(processes, outputs):
            self.assertEqual(process.returncode, 0, stderr)

        tokens = [stdout.strip() for stdout, _stderr in outputs]
        self.assertEqual(len(set(tokens)), 1)
        self.assertEqual(server._CLEANUP_TOKEN_PATH.read_text(), tokens[0])
        mode = stat.S_IMODE(server._CLEANUP_TOKEN_PATH.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_cleanup_rejects_non_object_json(self):
        server.API_TOKEN = "operator-token"
        response = server.app.test_client().post(
            "/cache/cleanup",
            json=["not", "an", "object"],
            headers={"X-BT-Token": "operator-token"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(), {"error": "Request body must be a JSON object"}
        )

    def test_cleanup_rejects_malformed_json_without_deleting(self):
        server.API_TOKEN = "operator-token"
        with mock.patch.object(server, "cleanup_old_entries") as cleanup:
            response = server.app.test_client().post(
                "/cache/cleanup",
                data=b'{"days":',
                content_type="application/json",
                headers={"X-BT-Token": "operator-token"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIsInstance(response.get_json().get("error"), str)
        cleanup.assert_not_called()

    def test_persistence_failure_is_fail_closed_and_stable(self):
        with mock.patch.object(
            server,
            "_persist_cleanup_token",
            side_effect=OSError("synthetic persistence failure"),
        ):
            response = server.app.test_client().post(
                "/cache/cleanup",
                json={"days": 30},
                headers={"X-BT-Token": "any-value"},
            )

        body = response.get_json()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(body["error"], "cleanup_credential_unavailable")
        self.assertIsNone(server._cleanup_token_cache)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX FIFO support")
    def test_non_regular_token_path_fails_closed_without_blocking(self):
        os.mkfifo(server._CLEANUP_TOKEN_PATH, 0o600)
        env = os.environ.copy()
        env["BT_API_TOKEN"] = ""
        env["BT_CACHE_DIR"] = self.tempdir.name
        env["DB_PATH"] = str(Path(self.tempdir.name) / "fifo-child.db")
        child_code = (
            "import server; "
            "\ntry: server._get_cleanup_token()"
            "\nexcept server.CleanupCredentialUnavailable: print('disabled')"
            "\nelse: raise SystemExit('credential unexpectedly available')"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", child_code],
            cwd=Path(__file__).parent,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            self.fail("cleanup token read blocked on a FIFO")

        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(stdout.strip(), "disabled")


if __name__ == "__main__":
    unittest.main(verbosity=2)
