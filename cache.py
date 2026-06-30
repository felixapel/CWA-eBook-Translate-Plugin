"""
Translation cache backed by SQLite. Content-addressed (SHA-256 hash of
text + language pair), never re-translates the same paragraph.
"""
import os
import sqlite3
import hashlib
import logging
import threading
from pathlib import Path
from datetime import datetime, timezone, timedelta

log = logging.getLogger("book-translator.cache")

DB_PATH = Path(os.getenv("DB_PATH", "translations.db"))

# ── Thread-local connection pool (M1) ──────────────────────────────────────
_thread_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Get a thread-local connection, creating one if needed (M1: connection pool)."""
    conn = getattr(_thread_local, "conn", None)
    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _thread_local.conn = conn
    return conn


def init_db():
    """Create the translation cache table if it doesn't exist."""
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS translations (
                cache_key TEXT PRIMARY KEY,
                source_text TEXT NOT NULL,
                source_lang TEXT NOT NULL,
                target_lang TEXT NOT NULL,
                translated_text TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT 'MiniMax-M3',
                created_at TEXT NOT NULL,
                hit_count INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_translations_langs
            ON translations(source_lang, target_lang)
        """)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    log.info("Translation cache initialized at %s", DB_PATH)


def compute_cache_key(text: str, source_lang: str, target_lang: str) -> str:
    """SHA-256 hash of (text + source + target)."""
    content = f"{text}|{source_lang}|{target_lang}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def get_cached(text: str, source_lang: str, target_lang: str) -> str | None:
    """Look up a translation in the cache. Returns None if not found."""
    cache_key = compute_cache_key(text, source_lang, target_lang)
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT translated_text FROM translations WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if row:
            log.debug("Cache HIT for %d chars %s→%s", len(text), source_lang, target_lang)
            return row[0]
        return None
    except Exception:
        # Read-only SELECT: nothing to roll back, just surface the error.
        raise


def put_cache(
    text: str,
    source_lang: str,
    target_lang: str,
    translated_text: str,
    model: str = "MiniMax-M3",
):
    """Store a translation in the cache."""
    cache_key = compute_cache_key(text, source_lang, target_lang)
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO translations
               (cache_key, source_text, source_lang, target_lang,
                translated_text, model, created_at, hit_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
            (cache_key, text, source_lang, target_lang, translated_text, model, now),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    log.debug("Cache PUT: %d chars %s→%s", len(text), source_lang, target_lang)


def get_cache_stats() -> dict:
    """Return cache statistics."""
    conn = _get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM translations").fetchone()[0]
        total_hits = conn.execute(
            "SELECT COALESCE(SUM(hit_count), 0) FROM translations"
        ).fetchone()[0]
        # Unique language pairs
        pairs = conn.execute(
            "SELECT source_lang, target_lang, COUNT(*) as cnt "
            "FROM translations GROUP BY source_lang, target_lang"
        ).fetchall()
    except Exception:
        raise
    return {
        "total_entries": total,
        "total_hits": total_hits,
        "db_size_mb": round(os.path.getsize(str(DB_PATH)) / (1024 * 1024), 2),
        "language_pairs": {
            f"{s}→{t}": c for s, t, c in pairs
        },
    }


def cleanup_old_entries(days: int = 30) -> int:
    """Evict cached translations older than N days. Returns count of deleted rows."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "DELETE FROM translations WHERE created_at < ?", (cutoff,)
        )
        deleted = cursor.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    log.info("Cache cleanup: evicted %d entries older than %d days", deleted, days)
    return deleted


# Initialize on import
init_db()
