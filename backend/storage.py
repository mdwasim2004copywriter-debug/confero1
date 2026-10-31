"""
Persistent storage, users and their meetings, Postgres (Supabase), via a
single DATABASE_URL connection string. Previously a local meetings.db
SQLite file living in backend/ -- moved to Postgres because the backend
now deploys to a host with no guaranteed persistent local disk, and a
hosted database survives independently of whichever compute host runs
the app. DATABASE_URL is read lazily (inside each function, not at
import time) because api.py imports this module before it calls
load_dotenv() -- reading the env var at import time would see it unset.

Connections are pooled (see _acquire/_release/_run below) rather than
opened fresh per call -- opening a brand-new TCP+TLS connection to
Supabase for every single storage call was measured at ~2-4.5 seconds
each (connection setup, not query time), which on a 10-chunk streaming
recording alone added tens of seconds of pure overhead. Reusing a small
set of already-open connections removes that per-call cost while every
function below keeps exactly the same name, signature and behavior.

Meetings are scoped per-user: every meeting is tied to the user_id that
recorded it, and every read is filtered by user_id too, so one person
can never list or open another person's meetings, even by guessing an id.
"""

import json
import os
import queue
import threading
import uuid
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

# Small bounded pool of already-open connections, reused across calls
# instead of paying a fresh TCP+TLS handshake to Supabase every time.
# Bounded (not unlimited) so a burst of concurrent requests can't open
# unbounded connections against Supabase's own connection limit; once
# at capacity, a caller simply waits for one to free up, same as any
# normal connection pool (this is the same shape as psycopg_pool /
# pgbouncer, just hand-rolled with only the standard library so no new
# dependency is needed for this fix).
_POOL_MAX_SIZE = 5
_pool: "queue.Queue[psycopg.Connection]" = queue.Queue(maxsize=_POOL_MAX_SIZE)
_pool_lock = threading.Lock()
_pool_created = 0


def _new_connection():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Set it in backend/.env (local) or the "
            "host's environment variables (deployed) to a Postgres connection string."
        )
    # prepare_threshold=None: Supabase's connection pooler (Supavisor, used
    # instead of the IPv6-only direct connection) runs in transaction mode,
    # where a server-side prepared statement from one query can end up
    # bound to a different backend connection on the next -- disabling
    # psycopg's automatic statement preparation avoids that class of bug
    # entirely, as Supabase's own client docs recommend for pooled connections.
    return psycopg.connect(database_url, row_factory=dict_row, prepare_threshold=None)


def _acquire() -> psycopg.Connection:
    """Hands back an already-open connection if one's idle in the pool;
    otherwise opens a new one (up to _POOL_MAX_SIZE total), or waits for
    one to free up if already at capacity. Mirrors a normal connection
    pool's behavior -- never silently exceeds the configured max."""
    global _pool_created
    try:
        return _pool.get_nowait()
    except queue.Empty:
        pass

    with _pool_lock:
        if _pool_created < _POOL_MAX_SIZE:
            _pool_created += 1
            try:
                return _new_connection()
            except Exception:
                _pool_created -= 1
                raise

    # Pool is at capacity and every connection is currently checked out --
    # wait for one to be returned rather than opening an unbounded extra.
    return _pool.get()


def _release(conn: psycopg.Connection, broken: bool = False) -> None:
    """Returns a connection to the pool for reuse, or closes and discards
    it if it's known to be broken (so a fresh one gets created on the
    next acquire instead of handing out something dead)."""
    global _pool_created
    if broken or conn.closed:
        try:
            conn.close()
        except Exception:
            pass
        with _pool_lock:
            _pool_created -= 1
        return
    try:
        _pool.put_nowait(conn)
    except queue.Full:
        # Shouldn't happen (_pool_created never exceeds _POOL_MAX_SIZE),
        # but if it ever did, closing rather than leaking is the safe choice.
        try:
            conn.close()
        except Exception:
            pass
        with _pool_lock:
            _pool_created -= 1


def _run(fn):
    """Runs fn(conn) using a pooled connection: commits on success, rolls
    back on failure (same per-call atomicity the old fresh-connection-
    per-call code had), and always returns the connection to the pool --
    except when the connection itself turned out to be dead (Supabase's
    pooler can close an idle connection from its side), in which case it's
    discarded and the call is retried once on a fresh connection instead
    of surfacing an error the caller did nothing to cause."""
    last_err = None
    for _ in range(2):
        conn = _acquire()
        try:
            result = fn(conn)
            conn.commit()
        except psycopg.OperationalError as e:
            try:
                conn.rollback()
            except Exception:
                pass
            _release(conn, broken=True)
            last_err = e
            continue
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            _release(conn)
            raise
        else:
            _release(conn)
            return result
    raise last_err


def init_db():
    def _op(conn):
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meetings (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT,
                date TEXT,
                duration_seconds INTEGER,
                speaker_count INTEGER,
                data TEXT,          -- full result JSON (meeting/speakers/transcript/notes/metrics)
                created_at TEXT
            )
            """
        )
        # Dedicated columns (not just the `data` blob) for the Dashboard's
        # aggregate charts -- lets it query across every meeting without
        # parsing a full transcript JSON blob per row. ADD COLUMN IF NOT
        # EXISTS is safe to run on every startup, including against an
        # already-populated table from before this feature existed; those
        # older rows simply get NULLs here, which the dashboard treats as
        # "uncategorized" rather than a fake category.
        conn.execute("ALTER TABLE meetings ADD COLUMN IF NOT EXISTS source_category TEXT")
        conn.execute("ALTER TABLE meetings ADD COLUMN IF NOT EXISTS media_type TEXT")
        conn.execute("ALTER TABLE meetings ADD COLUMN IF NOT EXISTS file_size_bytes BIGINT")
        conn.execute("ALTER TABLE meetings ADD COLUMN IF NOT EXISTS total_seconds REAL")
        # Phase B (streaming): an in-progress recording's chunk-by-chunk state.
        # Deliberately separate from `meetings` -- a stream session is
        # throwaway bookkeeping for one recording in progress, never a thing
        # that shows up in "My Meetings"; only a successful finalize ever
        # writes a `meetings` row, via the exact same save_meeting() below
        # that the old single-upload path has always used.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stream_sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT,                 -- NULL for a guest session, same guest model as /api/process-meeting
                status TEXT NOT NULL,         -- 'recording' | 'finalizing' | 'finalized' | 'failed'
                title TEXT,
                meeting_type TEXT,
                multilingual INTEGER NOT NULL DEFAULT 0,
                finalized_meeting_id TEXT,
                created_at TEXT
            )
            """
        )
        # 'mic' | 'tab' -- which capture source started this session, so
        # stream_finalize can tag the resulting meeting's metrics the same
        # way /api/process-meeting already does for an upload.
        conn.execute("ALTER TABLE stream_sessions ADD COLUMN IF NOT EXISTS source TEXT")
        conn.execute("ALTER TABLE stream_sessions ADD COLUMN IF NOT EXISTS include_video BOOLEAN")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stream_chunks (
                stream_id TEXT NOT NULL,
                sequence_number INTEGER NOT NULL,
                status TEXT NOT NULL,         -- 'ok' | 'failed'
                start_time REAL,
                end_time REAL,
                audio_path TEXT,
                transcript_json TEXT,         -- JSON [[start, end, text, low_confidence], ...]
                error TEXT,
                created_at TEXT,
                PRIMARY KEY (stream_id, sequence_number)
            )
            """
        )
        # Per-chunk timing, summed at finalize into the finished meeting's
        # metrics.timingSeconds -- lets a live (streamed) recording report
        # the same decode/transcription breakdown a whole-file upload does,
        # without a separate aggregation pass over stream_metrics.jsonl.
        conn.execute("ALTER TABLE stream_chunks ADD COLUMN IF NOT EXISTS decode_seconds REAL")
        conn.execute("ALTER TABLE stream_chunks ADD COLUMN IF NOT EXISTS transcribe_seconds REAL")

    _run(_op)


# ---------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------

def create_user(email: str, password_hash: str) -> str:
    """Creates a new user, returns their id. Caller must already have
    checked the email isn't taken (get_user_by_email first)."""
    user_id = str(uuid.uuid4())

    def _op(conn):
        conn.execute(
            "INSERT INTO users (id, email, password_hash, created_at) VALUES (%s, %s, %s, %s)",
            (user_id, email.strip().lower(), password_hash, datetime.now(timezone.utc).isoformat()),
        )

    _run(_op)
    return user_id


def get_user_by_email(email: str):
    def _op(conn):
        row = conn.execute("SELECT * FROM users WHERE email = %s", (email.strip().lower(),)).fetchone()
        return dict(row) if row else None

    return _run(_op)


# ---------------------------------------------------------------------
# Meetings, every function below requires a user_id and is scoped to it
# ---------------------------------------------------------------------

def save_meeting(result: dict, user_id: str):
    """Saves (or overwrites, if the same id is reused) one processed
    meeting, tied to the user who recorded it. The four metrics columns
    are read from result["metrics"] when present (added by
    api.py::_finalize_result) -- a result with no metrics (shouldn't
    happen for a new recording, but kept safe for any caller that lacks
    it) just stores NULLs rather than raising."""
    m = result["meeting"]
    metrics = result.get("metrics") or {}
    timing = metrics.get("timingSeconds") or {}

    def _op(conn):
        conn.execute(
            """
            INSERT INTO meetings (
                id, user_id, title, date, duration_seconds, speaker_count, data,
                source_category, media_type, file_size_bytes, total_seconds, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                title = EXCLUDED.title,
                date = EXCLUDED.date,
                duration_seconds = EXCLUDED.duration_seconds,
                speaker_count = EXCLUDED.speaker_count,
                data = EXCLUDED.data,
                source_category = EXCLUDED.source_category,
                media_type = EXCLUDED.media_type,
                file_size_bytes = EXCLUDED.file_size_bytes,
                total_seconds = EXCLUDED.total_seconds,
                created_at = EXCLUDED.created_at
            """,
            (
                m["id"],
                user_id,
                m["title"],
                m["date"],
                m["durationSeconds"],
                m["speakerCount"],
                json.dumps(result),
                metrics.get("source"),
                metrics.get("mediaType"),
                metrics.get("fileSizeBytes"),
                timing.get("total"),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    _run(_op)


def list_meetings(user_id: str):
    """Lightweight summaries for this user's 'recent meetings' list,
    not the full transcript, and never another user's meetings."""
    def _op(conn):
        rows = conn.execute(
            "SELECT id, title, date, duration_seconds, speaker_count "
            "FROM meetings WHERE user_id = %s ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "title": r["title"],
                "date": r["date"],
                "durationSeconds": r["duration_seconds"],
                "speakerCount": r["speaker_count"],
            }
            for r in rows
        ]

    return _run(_op)


def list_meeting_metrics(user_id: str):
    """Lightweight per-recording metrics for the Dashboard's aggregate
    charts -- reads only the dedicated columns below (never the full
    `data` blob), so this stays fast regardless of how long any one
    transcript is. Oldest first, so trend-over-time charts don't need to
    reverse the list."""
    def _op(conn):
        rows = conn.execute(
            "SELECT id, title, date, speaker_count, duration_seconds, "
            "source_category, media_type, file_size_bytes, total_seconds, created_at "
            "FROM meetings WHERE user_id = %s ORDER BY created_at ASC",
            (user_id,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "title": r["title"],
                "date": r["date"],
                "speakerCount": r["speaker_count"],
                "durationSeconds": r["duration_seconds"],
                "sourceCategory": r["source_category"],
                "mediaType": r["media_type"],
                "fileSizeBytes": r["file_size_bytes"],
                "totalSeconds": r["total_seconds"],
            }
            for r in rows
        ]

    return _run(_op)


def delete_meeting(meeting_id: str, user_id: str) -> bool:
    """Deletes one meeting, scoped to user_id same as get_meeting, so a
    user can never delete (or even discover the existence of) someone
    else's meeting by passing its id. Returns True if a row was actually
    deleted, False if no such meeting existed for this user."""
    def _op(conn):
        cursor = conn.execute(
            "DELETE FROM meetings WHERE id = %s AND user_id = %s", (meeting_id, user_id)
        )
        return cursor.rowcount > 0

    return _run(_op)


def get_meeting(meeting_id: str, user_id: str):
    """Full detail for one meeting, returns None if it doesn't exist OR
    if it belongs to a different user, so there's no way to view someone
    else's meeting by guessing/passing their id."""
    def _op(conn):
        row = conn.execute(
            "SELECT data FROM meetings WHERE id = %s AND user_id = %s", (meeting_id, user_id)
        ).fetchone()
        return json.loads(row["data"]) if row is not None else None

    return _run(_op)


# ---------------------------------------------------------------------
# Streaming sessions (Phase B) -- an in-progress recording's chunk state.
# A session belongs to user_id, or to no one (guest, same as a guest's
# /api/process-meeting result: never written to `meetings`, returned to
# whoever holds the response). The session id itself is an unguessable
# uuid4 and doubles as the guest's capability token, exactly as a guest's
# finished meeting today is protected by nothing but holding the response.
# ---------------------------------------------------------------------

def create_stream_session(
    user_id: str | None, title: str, meeting_type: str, multilingual: bool, source: str = "mic", include_video: bool = False
) -> str:
    session_id = str(uuid.uuid4())

    def _op(conn):
        conn.execute(
            """
            INSERT INTO stream_sessions (id, user_id, status, title, meeting_type, multilingual, source, include_video, created_at)
            VALUES (%s, %s, 'recording', %s, %s, %s, %s, %s, %s)
            """,
            (
                session_id,
                user_id,
                title,
                meeting_type,
                1 if multilingual else 0,
                source,
                include_video,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    _run(_op)
    return session_id


def get_stream_session(stream_id: str):
    def _op(conn):
        row = conn.execute("SELECT * FROM stream_sessions WHERE id = %s", (stream_id,)).fetchone()
        return dict(row) if row else None

    return _run(_op)


def set_stream_session_status(stream_id: str, status: str, finalized_meeting_id: str | None = None) -> None:
    def _op(conn):
        conn.execute(
            "UPDATE stream_sessions SET status = %s, finalized_meeting_id = COALESCE(%s, finalized_meeting_id) WHERE id = %s",
            (status, finalized_meeting_id, stream_id),
        )

    _run(_op)


def upsert_stream_chunk(
    stream_id: str,
    sequence_number: int,
    status: str,
    start_time: float | None,
    end_time: float | None,
    audio_path: str | None,
    transcript_raw: list | None,
    error: str | None = None,
    decode_seconds: float | None = None,
    transcribe_seconds: float | None = None,
) -> None:
    """Upsert -- a retried chunk overwrites its own prior row (same
    primary key), it never creates a second row for the same
    sequence_number. The idempotency guarantee callers rely on (don't
    re-transcribe) comes from get_stream_chunk() being checked *before*
    this is called, not from this being an upsert."""
    def _op(conn):
        conn.execute(
            """
            INSERT INTO stream_chunks
                (stream_id, sequence_number, status, start_time, end_time, audio_path, transcript_json, error,
                 decode_seconds, transcribe_seconds, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (stream_id, sequence_number) DO UPDATE SET
                status = EXCLUDED.status,
                start_time = EXCLUDED.start_time,
                end_time = EXCLUDED.end_time,
                audio_path = EXCLUDED.audio_path,
                transcript_json = EXCLUDED.transcript_json,
                error = EXCLUDED.error,
                decode_seconds = EXCLUDED.decode_seconds,
                transcribe_seconds = EXCLUDED.transcribe_seconds,
                created_at = EXCLUDED.created_at
            """,
            (
                stream_id,
                sequence_number,
                status,
                start_time,
                end_time,
                audio_path,
                json.dumps(transcript_raw) if transcript_raw is not None else None,
                error,
                decode_seconds,
                transcribe_seconds,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    _run(_op)


def get_stream_chunk(stream_id: str, sequence_number: int):
    def _op(conn):
        row = conn.execute(
            "SELECT * FROM stream_chunks WHERE stream_id = %s AND sequence_number = %s",
            (stream_id, sequence_number),
        ).fetchone()
        return dict(row) if row else None

    return _run(_op)


def list_stream_chunks(stream_id: str):
    """Ordered by sequence_number -- callers must never rely on insertion/
    arrival order, only this."""
    def _op(conn):
        rows = conn.execute(
            "SELECT * FROM stream_chunks WHERE stream_id = %s ORDER BY sequence_number ASC",
            (stream_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    return _run(_op)


def delete_stream_session(stream_id: str) -> None:
    """Row cleanup only -- the caller (api.py) is responsible for deleting
    any audio files on disk first, storage.py doesn't do filesystem I/O
    anywhere else and shouldn't start here."""
    def _op(conn):
        conn.execute("DELETE FROM stream_chunks WHERE stream_id = %s", (stream_id,))
        conn.execute("DELETE FROM stream_sessions WHERE id = %s", (stream_id,))

    _run(_op)
