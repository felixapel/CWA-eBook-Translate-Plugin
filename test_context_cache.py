"""Cache/context contracts at the translator and HTTP orchestration boundary."""

from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("BT_AUTH_MODE", "disabled")
os.environ.setdefault("BT_ALLOW_INSECURE_AUTH", "true")

import server
import translator
from work_budget import WorkBudget


def budget() -> WorkBudget:
    return WorkBudget(
        max_attempts=20,
        max_input_bytes=1_000_000,
        max_output_tokens=100_000,
        deadline_seconds=30,
    )


class TranslationContractTests(unittest.TestCase):
    def test_groups_are_stable_over_empty_slots(self) -> None:
        self.assertEqual(
            translator.translation_groups(
                ["a", "", "b", "c", " ", "d"], batch_size=2
            ),
            [[0, 2], [3, 5]],
        )

    def test_single_item_batch_without_context_uses_single_contract(self) -> None:
        with mock.patch.object(translator, "BT_CONTEXT_WINDOW", 0):
            single = translator.single_cache_contract("English", "Spanish")
            grouped = translator.batch_cache_contract(
                ["hello"], [0], "English", "Spanish"
            )
        self.assertEqual(grouped, single)

    def test_contract_changes_with_prompt_language_protocol_and_context(self) -> None:
        with mock.patch.object(translator, "BT_CONTEXT_WINDOW", 1):
            baseline = translator.batch_cache_contract(
                ["before", "one", "two", "after"],
                [1, 2],
                "English",
                "Spanish",
            )
            changed_context = translator.batch_cache_contract(
                ["different", "one", "two", "after"],
                [1, 2],
                "English",
                "Spanish",
            )
            changed_language = translator.batch_cache_contract(
                ["before", "one", "two", "after"],
                [1, 2],
                "English",
                "French",
            )

        self.assertNotEqual(baseline.context_hash, changed_context.context_hash)
        self.assertNotEqual(baseline.prompt_hash, changed_language.prompt_hash)
        self.assertEqual(baseline.protocol_version, translator.SEGMENT_PROTOCOL)


class ServerGroupCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.namespace = {
            "tenant": "subject-42",
            "book_id": "book-7",
            "chapter_id": "chapter-3",
        }

    def test_partial_group_hit_retranslates_the_whole_original_group(self) -> None:
        def partial_hit(text, _source, _target, *, scope, record_hit):
            self.assertFalse(record_hit)
            return "old-a" if text == "a" and scope.provider == "local" else None

        fresh = [("new-a", "local"), ("new-b", "local")]
        with (
            mock.patch.object(server, "get_cached", side_effect=partial_hit),
            mock.patch.object(server, "put_cache") as put_cache,
            mock.patch.object(server, "translate_batch", return_value=fresh) as translate,
            mock.patch.object(
                server,
                "cache_lookup_backends",
                return_value=[("local", "gemma4-12b")],
            ),
        ):
            result = server._translate_paragraphs(
                ["a", "b"],
                "English",
                "Spanish",
                budget(),
                **self.namespace,
            )

        self.assertEqual(result["translations"], ["new-a", "new-b"])
        self.assertEqual(result["cached_count"], 0)
        self.assertEqual(result["fresh_count"], 2)
        translate.assert_called_once()
        self.assertEqual(translate.call_args.kwargs["selected_groups"], [[0, 1]])
        self.assertEqual(put_cache.call_count, 2)
        for call in put_cache.call_args_list:
            cache_scope = call.kwargs["scope"]
            self.assertEqual(cache_scope.tenant, "subject-42")
            self.assertEqual(cache_scope.book_id, "book-7")
            self.assertEqual(cache_scope.chapter_id, "chapter-3")

    def test_complete_group_hit_avoids_provider_work(self) -> None:
        hits = {"a": "cached-a", "b": "cached-b"}

        def complete_hit(text, _source, _target, *, scope, record_hit):
            self.assertEqual(scope.provider, "local")
            self.assertFalse(record_hit)
            return hits[text]

        with (
            mock.patch.object(server, "get_cached", side_effect=complete_hit),
            mock.patch.object(server, "translate_batch") as translate,
            mock.patch.object(server, "put_cache") as put_cache,
            mock.patch.object(server, "record_cache_hit") as record_hit,
            mock.patch.object(
                server,
                "cache_lookup_backends",
                return_value=[("local", "gemma4-12b")],
            ),
        ):
            result = server._translate_paragraphs(
                ["a", "b"],
                "English",
                "Spanish",
                budget(),
                **self.namespace,
            )

        self.assertEqual(result["translations"], ["cached-a", "cached-b"])
        self.assertEqual(result["cached_count"], 2)
        self.assertEqual(result["fresh_count"], 0)
        translate.assert_not_called()
        put_cache.assert_not_called()
        self.assertEqual(record_hit.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
