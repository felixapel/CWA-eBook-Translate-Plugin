"""Boundary-schema tests that must reject invalid data before side effects."""
import unittest
from unittest import mock

import server


class APISchemaTests(unittest.TestCase):
    def setUp(self):
        server._rate_limit_store.clear()
        self.client = server.app.test_client()

    def tearDown(self):
        server._rate_limit_store.clear()

    def test_unpaired_unicode_surrogate_never_reaches_provider(self):
        with mock.patch.object(server, "translate_text") as translate_text:
            response = self.client.post(
                "/translate",
                data='{"text":"\\ud800"}',
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertIsInstance(response.get_json().get("error"), str)
        translate_text.assert_not_called()

    def test_batch_unpaired_unicode_surrogate_never_reaches_provider(self):
        with mock.patch.object(server, "translate_batch") as translate_batch:
            response = self.client.post(
                "/translate/batch",
                data='{"paragraphs":["valid","\\udfff"]}',
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertIsInstance(response.get_json().get("error"), str)
        translate_batch.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
