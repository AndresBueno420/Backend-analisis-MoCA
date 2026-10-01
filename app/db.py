import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jobs.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    payload     TEXT NOT NULL,
    result      TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(_SCHEMA)


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def insert_pending_job(job_id: str, raw_payload: str) -> None:
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, status, payload, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (job_id, "pendiente", raw_payload, now, now),
        )
        conn.commit()


def reset_processing_to_pending() -> int:
    """
    Crash recovery: si el proceso murió con jobs en 'procesando', los dejamos
    'pendiente' para que el worker los retome. Devuelve cuántos se reseteó.
    """
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status = 'pendiente', updated_at = ?"
            " WHERE status = 'procesando'",
            (_now(),),
        )
        conn.commit()
        return cur.rowcount


def fetch_pending_job_ids() -> list[str]:
    with _connect() as conn:
        return [
            row[0]
            for row in conn.execute(
                "SELECT id FROM jobs WHERE status = 'pendiente' ORDER BY created_at"
            )
        ]


def get_payload(job_id: str) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT payload FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return row[0] if row else None


def get_status(job_id: str) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT status FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return row[0] if row else None


def get_job(job_id: str) -> dict | None:
    """Devuelve {status, result (str JSON | None), error (str | None)} o None si no existe."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT status, result, error FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return None
        return {"status": row[0], "result": row[1], "error": row[2]}


def mark_processing(job_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'procesando', updated_at = ? WHERE id = ?",
            (_now(), job_id),
        )
        conn.commit()


def mark_ready(job_id: str, result_json: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'listo', result = ?, error = NULL,"
            " updated_at = ? WHERE id = ?",
            (result_json, _now(), job_id),
        )
        conn.commit()


def mark_error(job_id: str, error_message: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'error', error = ?, updated_at = ? WHERE id = ?",
            (error_message, _now(), job_id),
        )
        conn.commit()
