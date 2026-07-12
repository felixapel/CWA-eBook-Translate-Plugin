"""Deterministic contracts for the namespaced, bounded cache schema v2."""

from __future__ import annotations

import os
import sqlite3
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cache import CACHE_SCHEMA_VERSION, CacheScope, CacheStore


class MutableClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: int) -> None:
        self.now += timedelta(**kwargs)


def scope(**overrides: str) -> CacheScope:
    values = {
        "tenant": "user:42",
        "book_id": "book:7",
        "chapter_id": "chapter:3",
        "context_hash": "ctx-a",
        "provider": "local",
        "model": "gemma4-12b",
        "prompt_hash": "prompt-a",
        "protocol_version": "cwa-translate-segments/v1",
    }
    values.update(overrides)
    return CacheScope(**values)


class CacheV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.tmp.name) / "private-cache"
        self.db_path = self.cache_dir / "translations.db"
        self.clock = MutableClock()
        self.store = CacheStore(
            self.db_path,
            ttl_days=30,
            max_entries=3,
            hit_flush_threshold=100,
            now=self.clock,
        )

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_scope_dimensions_and_content_are_all_part_of_key(self) -> None:
        baseline = self.store.compute_key("same text", "English", "Spanish", scope())
        variants = [
            scope(tenant="user:99"),
            scope(book_id="book:8"),
            scope(chapter_id="chapter:4"),
            scope(context_hash="ctx-b"),
            scope(provider="minimax"),
            scope(model="MiniMax-M3"),
            scope(prompt_hash="prompt-b"),
            scope(protocol_version="cwa-translate-segments/v2"),
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertNotEqual(
                    baseline,
                    self.store.compute_key(
                        "same text", "English", "Spanish", variant
                    ),
                )

        self.assertNotEqual(
            baseline,
            self.store.compute_key("other text", "English", "Spanish", scope()),
        )
        self.assertNotEqual(
            baseline,
            self.store.compute_key("same text", "French", "Spanish", scope()),
        )

    def test_ttl_is_enforced_on_read_and_stale_rows_are_removed(self) -> None:
        self.store.put("hello", "English", "Spanish", "hola", scope())
        self.assertEqual(
            self.store.get("hello", "English", "Spanish", scope()), "hola"
        )

        self.clock.advance(days=31)
        self.assertIsNone(
            self.store.get("hello", "English", "Spanish", scope())
        )
        self.assertEqual(self.store.stats()["total_entries"], 0)

    def test_hard_cap_is_never_exceeded(self) -> None:
        for index in range(6):
            self.clock.advance(seconds=1)
            self.store.put(
                f"source-{index}",
                "English",
                "Spanish",
                f"target-{index}",
                scope(context_hash=f"ctx-{index}"),
            )

        stats = self.store.stats()
        self.assertEqual(stats["total_entries"], 3)
        self.assertIsNone(
            self.store.get(
                "source-0", "English", "Spanish", scope(context_hash="ctx-0")
            )
        )
        self.assertEqual(
            self.store.get(
                "source-5", "English", "Spanish", scope(context_hash="ctx-5")
            ),
            "target-5",
        )

    def test_cache_hit_does_not_write_until_counters_are_flushed(self) -> None:
        self.store.put("hello", "English", "Spanish", "hola", scope())
        connection = self.store.connection()
        changes_before = connection.total_changes

        self.assertEqual(
            self.store.get("hello", "English", "Spanish", scope()), "hola"
        )
        self.assertEqual(connection.total_changes, changes_before)

        stats = self.store.stats()
        self.assertEqual(stats["total_hits"], 1)
        self.assertGreater(connection.total_changes, changes_before)

    def test_source_text_is_not_persisted_and_identifiers_are_hashed(self) -> None:
        private_source = "private source text that must not be stored"
        private_scope = scope(
            tenant="private-user@example.invalid",
            book_id="private-book-title",
            chapter_id="private-chapter-title",
        )
        self.store.put(
            private_source, "English", "Spanish", "texto traducido", private_scope
        )
        self.store.checkpoint()

        columns = {
            row[1]
            for row in self.store.connection().execute(
                "PRAGMA table_info(translations)"
            )
        }
        self.assertNotIn("source_text", columns)
        db_bytes = self.db_path.read_bytes()
        self.assertNotIn(private_source.encode(), db_bytes)
        self.assertNotIn(private_scope.tenant.encode(), db_bytes)
        self.assertNotIn(private_scope.book_id.encode(), db_bytes)
        self.assertNotIn(private_scope.chapter_id.encode(), db_bytes)

    def test_new_cache_paths_are_private(self) -> None:
        self.assertEqual(
            stat.S_IMODE(self.cache_dir.stat().st_mode), 0o700
        )
        self.assertEqual(stat.S_IMODE(self.db_path.stat().st_mode), 0o600)
        for suffix in ("-wal", "-shm"):
            path = Path(str(self.db_path) + suffix)
            if path.exists():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_v1_table_is_preserved_but_never_served_as_v2(self) -> None:
        self.store.close()
        self.tmp.cleanup()

        self.tmp = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.tmp.name) / "migration-cache"
        self.cache_dir.mkdir(mode=0o700)
        self.db_path = self.cache_dir / "translations.db"
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """CREATE TABLE translations (
                cache_key TEXT PRIMARY KEY,
                source_text TEXT NOT NULL,
                source_lang TEXT NOT NULL,
                target_lang TEXT NOT NULL,
                translated_text TEXT NOT NULL,
                model TEXT NOT NULL,
                created_at TEXT NOT NULL,
                hit_count INTEGER NOT NULL DEFAULT 1
            )"""
        )
        conn.execute(
            "INSERT INTO translations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-key",
                "legacy source",
                "English",
                "Spanish",
                "legacy target",
                "legacy-model",
                self.clock().isoformat(),
                7,
            ),
        )
        conn.commit()
        conn.close()

        self.store = CacheStore(
            self.db_path,
            ttl_days=30,
            max_entries=3,
            now=self.clock,
        )
        names = {
            row[0]
            for row in self.store.connection().execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertIn("translations_v1", names)
        self.assertIn("translations", names)
        legacy_count = self.store.connection().execute(
            "SELECT COUNT(*) FROM translations_v1"
        ).fetchone()[0]
        self.assertEqual(legacy_count, 1)
        self.assertIsNone(
            self.store.get(
                "legacy source", "English", "Spanish", scope(model="legacy-model")
            )
        )
        self.assertEqual(
            self.store.connection().execute("PRAGMA user_version").fetchone()[0],
            CACHE_SCHEMA_VERSION,
        )

    def test_invalid_retention_configuration_fails_closed(self) -> None:
        bad_db = Path(self.tmp.name) / "bad" / "cache.db"
        for ttl_days, max_entries in ((0, 1), (-1, 1), (1, 0), (1, -1)):
            with self.subTest(ttl_days=ttl_days, max_entries=max_entries):
                with self.assertRaises(ValueError):
                    CacheStore(
                        bad_db,
                        ttl_days=ttl_days,
                        max_entries=max_entries,
                        now=self.clock,
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
