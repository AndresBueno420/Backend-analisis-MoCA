"""
Verifica que el semáforo del worker limita la concurrencia real.

Dos aserciones separadas, porque fallan por razones distintas:
  (a) nunca se observan más de N análisis simultáneos  → semáforo roto.
  (b) el tiempo total es ~ ceil(JOBS/N) × duración     → se procesaron en
       oleadas, no todos de una ni todos en serie.
"""
import asyncio
import json
import threading
import time
import uuid

import pytest

from app import db, worker


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_jobs.db")
    db.init_db()
    yield
    # El worker se para dentro del test (necesita el mismo event loop).
    worker.reset_analyzer()


def _minimal_payload() -> str:
    return json.dumps(
        {
            "strokes": [{"points": []}],
            "device": {"pressureSupported": False},
        }
    )


def test_semaphore_limits_concurrent_analyses(temp_db):
    N = 3
    JOBS = 6
    JOB_DURATION_SEC = 0.5

    # Instrumentación compartida entre threads: el analizador corre vía
    # asyncio.to_thread, así que necesitamos un Lock de threading, no asyncio.
    lock = threading.Lock()
    stats = {"in_flight": 0, "max_in_flight": 0, "runs": 0}

    def slow_analyzer(strokes, *, pressure_supported):
        with lock:
            stats["in_flight"] += 1
            stats["runs"] += 1
            if stats["in_flight"] > stats["max_in_flight"]:
                stats["max_in_flight"] = stats["in_flight"]
        time.sleep(JOB_DURATION_SEC)
        with lock:
            stats["in_flight"] -= 1
        return {"ok": True}

    worker.set_analyzer(slow_analyzer)

    async def run_scenario():
        await worker.start(concurrency=N)

        job_ids = []
        for _ in range(JOBS):
            jid = str(uuid.uuid4())
            db.insert_pending_job(jid, _minimal_payload())
            await worker.enqueue(jid)
            job_ids.append(jid)

        t0 = time.monotonic()
        deadline = t0 + 10.0
        while time.monotonic() < deadline:
            if all(db.get_status(jid) == "listo" for jid in job_ids):
                break
            await asyncio.sleep(0.02)
        elapsed = time.monotonic() - t0

        await worker.stop()
        return elapsed, job_ids

    elapsed, job_ids = asyncio.run(run_scenario())

    # Todos corrieron
    assert stats["runs"] == JOBS, f"se esperaban {JOBS} runs, hubo {stats['runs']}"

    # Pico de concurrencia = N exactamente
    assert stats["max_in_flight"] == N, (
        f"pico observado = {stats['max_in_flight']}, esperado = {N}. "
        "Si es > N, el semáforo no está gateando. Si es < N, "
        "la cola no está entregando trabajo lo suficientemente rápido."
    )

    # Oleadas: ceil(JOBS/N) × duración, con holgura por overhead
    expected = (JOBS / N) * JOB_DURATION_SEC  # 2 * 0.5 = 1.0s
    assert expected * 0.8 < elapsed < expected * 2.5, (
        f"elapsed={elapsed:.2f}s, esperado ≈{expected:.2f}s. "
        "Mucho menos → sin límite. Mucho más → se serializó."
    )

    # Todos llegaron a 'listo'
    for jid in job_ids:
        assert db.get_status(jid) == "listo"
