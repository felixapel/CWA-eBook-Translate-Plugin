"""Security contracts for the destructive cache-cleanup credential."""
import os
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import server


class CleanupTokenTests(unittest.TestCase):
    def setUp(self):
        self.original_api_token = server.API_TOKEN
        self.original_path = server._CLEANUP_TOKEN_PATH
        self.original_cache = server._cleanup_token_cache
        self.tempdir = tempfile.TemporaryDirectory()
        server.API_TOKEN = ""
        server._CLEANUP_TOKEN_PATH = Path(self.tempdir.name) / "cleanup_token"
        server._cleanup_token_cache = None

    def tearDown(self):
        server.API_TOKEN = self.original_api_token
        server._CLEANUP_TOKEN_PATH = self.original_path
        server._cleanup_token_cache = self.original_cache
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
