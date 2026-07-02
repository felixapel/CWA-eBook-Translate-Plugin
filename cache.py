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


# Optional hard cap on cache rows (0 = unlimited). Checked cheaply on writes:
# when exceeded, the oldest rows are evicted down to the cap.
CACHE_MAX_ENTRIES = int(os.getenv("BT_CACHE_MAX_ENTRIES", "0"))
_put_counter = 0
_ENFORCE_EVERY = 200  # only run the COUNT/DELETE housekeeping every N writes


def compute_cache_key(
    text: str, source_lang: str, target_lang: str, model: str = ""
) -> str:
    """SHA-256 hash of (normalized text + source + target + model).

    Model is part of the key so that switching providers/models never serves
    a stale translation from the previous backend. Before this change a
    row cached by ``gemma4-12b`` would silently be served after the operator
    switched to ``MiniMax-M3``, with no signal that the user was getting the
    cheaper/worse translation. Including model also lets the operator run
    a parallel ``gemma4-12b`` + ``MiniMax-M3`` comparison on the same book.

    Existing rows (cached before this change) used a model-less key and will
    simply miss once the new key is in effect — the cache re-warms gradually
    on first request, no destructive migration needed.
    """
    content = f"{model}|{text.strip()}|{source_lang}|{target_lang}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def get_cached(
    text: str, source_lang: str, target_lang: str, model: str = ""
) -> str | None:
    """Look up a translation in the cache. Returns None if not found.
    A hit increments hit_count so /stats reflects real cache usage."""
    cache_key = compute_cache_key(text, source_lang, target_lang, model=model)
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT translated_text FROM translations WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if row:
            log.debug("Cache HIT for %d chars %s→%s (model=%s)", len(text), source_lang, target_lang, model or "?")
            try:
                conn.execute(
                    "UPDATE translations SET hit_count = hit_count + 1 WHERE cache_key = ?",
                    (cache_key,),
                )
                conn.commit()
            except Exception:
                conn.rollback()  # stats bookkeeping must never break a read
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
    model: str,
):
    """Store a translation in the cache (upsert; re-puts keep the hit count).

    ``model`` is required (no default) so that callers always tag which
    backend produced the translation. Mixing backends under the same key
    was the root cause of cross-provider cache poisoning.
    """
    if not model:
        raise ValueError("put_cache requires a non-empty model name")
    global _put_counter
    cache_key = compute_cache_key(text, source_lang, target_lang, model=model)
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT INTO translations
               (cache_key, source_text, source_lang, target_lang,
                translated_text, model, created_at, hit_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0)
               ON CONFLICT(cache_key) DO UPDATE SET
                 translated_text = excluded.translated_text,
                 model = excluded.model,
                 created_at = excluded.created_at""",
            (cache_key, text.strip(), source_lang, target_lang, translated_text, model, now),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    log.debug("Cache PUT: %d chars %s→%s", len(text), source_lang, target_lang)

    if CACHE_MAX_ENTRIES > 0:
        _put_counter += 1
        if _put_counter >= _ENFORCE_EVERY:
            _put_counter = 0
            _enforce_max_entries(conn)


def _enforce_max_entries(conn: sqlite3.Connection):
    """Evict oldest rows beyond CACHE_MAX_ENTRIES (best-effort housekeeping)."""
    try:
        total = conn.execute("SELECT COUNT(*) FROM translations").fetchone()[0]
        excess = total - CACHE_MAX_ENTRIES
        if excess > 0:
            conn.execute(
                """DELETE FROM translations WHERE cache_key IN (
                       SELECT cache_key FROM translations
                       ORDER BY created_at ASC LIMIT ?)""",
                (excess,),
            )
            conn.commit()
            log.info("Cache cap: evicted %d oldest entries (cap %d)", excess, CACHE_MAX_ENTRIES)
    except Exception:
        conn.rollback()
        log.exception("Cache cap enforcement failed (non-fatal)")


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
        "db_size_mb": _db_size_mb(),
        "language_pairs": {
            f"{s}→{t}": c for s, t, c in pairs
        },
    }


def _db_size_mb() -> float:
    """Total bytes occupied by the SQLite file plus its WAL/SHM siblings.

    With ``journal_mode=WAL``, the main ``.db`` file stays small (often empty)
    while the actual pages live in ``-wal``. Reports that only inspect the
    main file under-report the real on-disk footprint by an order of
    magnitude — operators relying on this for backup sizing or cleanup
    triggers would make decisions on stale data. Sum all three files.
    """
    total = 0
    for suffix in ("", "-wal", "-shm"):
        path = str(DB_PATH) + suffix
        try:
            total += os.path.getsize(path)
        except OSError:
            # -shm/-wal may not exist yet on a brand-new database
            continue
    return round(total / (1024 * 1024), 2)


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
