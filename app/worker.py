"""
Worker in-process para procesar jobs 'pendiente' con límite de concurrencia.

- `asyncio.Queue` en memoria recibe los job_ids desde el endpoint de subida.
- En el arranque se recuperan los jobs 'pendiente' de SQLite (y los que quedaron
  'procesando' por una caída previa se resetean a 'pendiente').
- Cada job corre en una task propia que primero adquiere un semáforo
  (concurrencia real); el análisis en sí va a un thread (CPU-bound).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable, Optional

from . import db
from .analysis import analyze_strokes

log = logging.getLogger(__name__)

CONCURRENCY_LIMIT = 3

# Estado del worker (vive por la duración del proceso). Se re-crea en start()
# para que los tests que corren en event loops distintos no choquen.
_queue: Optional[asyncio.Queue] = None
_semaphore: Optional[asyncio.Semaphore] = None
_run_task: Optional[asyncio.Task] = None

# Hook de testing: función que hace el análisis. Firma:
#   (strokes: list, *, pressure_supported: bool) -> dict
_analyzer: Callable = analyze_strokes


def set_analyzer(fn: Callable) -> None:
    """Reemplaza el analizador. Solo para tests."""
    global _analyzer
    _analyzer = fn


def reset_analyzer() -> None:
    global _analyzer
    _analyzer = analyze_strokes


async def start(concurrency: int = CONCURRENCY_LIMIT) -> asyncio.Task:
    """
    Arranca el worker. Crea queue/semaphore atados al event loop actual,
    recupera jobs pendientes desde SQLite, y lanza el loop forever.
    Devuelve la task del loop para que el llamador la cancele al apagar.
    """
    global _queue, _semaphore, _run_task

    _queue = asyncio.Queue()
    _semaphore = asyncio.Semaphore(concurrency)

    reset = db.reset_processing_to_pending()
    if reset:
        log.info("recovery: %d job(s) 'procesando' reseteados a 'pendiente'", reset)

    pending = db.fetch_pending_job_ids()
    for jid in pending:
        _queue.put_nowait(jid)
    if pending:
        log.info("recovery: %d job(s) 'pendiente' re-encolados", len(pending))

    _run_task = asyncio.create_task(_run_forever(), name="moca-worker")
    return _run_task


async def stop() -> None:
    """Cancela el loop del worker (los jobs en curso terminan solos)."""
    global _run_task
    if _run_task is None:
        return
    _run_task.cancel()
    try:
        await _run_task
    except asyncio.CancelledError:
        pass
    _run_task = None


async def enqueue(job_id: str) -> None:
    if _queue is None:
        raise RuntimeError("worker no está iniciado")
    await _queue.put(job_id)


async def _run_forever() -> None:
    assert _queue is not None
    while True:
        job_id = await _queue.get()
        # Fire-and-forget: la task adquiere el semáforo adentro.
        asyncio.create_task(_process_one(job_id), name=f"job-{job_id[:8]}")


async def _process_one(job_id: str) -> None:
    assert _semaphore is not None
    async with _semaphore:
        try:
            db.mark_processing(job_id)
            raw = db.get_payload(job_id)
            if raw is None:
                db.mark_error(job_id, "payload no encontrado en DB")
                return
            payload = json.loads(raw)
            strokes = [stroke["points"] for stroke in payload.get("strokes", [])]
            pressure_supported = bool(
                payload.get("device", {}).get("pressureSupported", False)
            )
            result = await asyncio.to_thread(
                _analyzer, strokes, pressure_supported=pressure_supported
            )
            db.mark_ready(job_id, json.dumps(result))
        except Exception as e:  # noqa: BLE001
            log.exception("job %s falló durante el análisis", job_id)
            db.mark_error(job_id, f"{type(e).__name__}: {e}")
