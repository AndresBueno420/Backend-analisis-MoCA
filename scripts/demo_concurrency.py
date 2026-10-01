"""
Demostración visible del límite de concurrencia del worker.

Encola 6 jobs con un analizador falso que tarda 0.5s. Con N=3, deberían
procesarse en dos oleadas (~1.0s totales), y el pico de simultaneidad debe
ser exactamente 3.
"""
import asyncio
import json
import tempfile
import threading
import time
import uuid
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, worker  # noqa: E402

N = 3
JOBS = 6
JOB_DURATION_SEC = 0.5

lock = threading.Lock()
state = {"in_flight": 0, "max_in_flight": 0}
events: list[tuple[float, str, int]] = []  # (t_rel, descr, in_flight_tras_evento)
t_start = 0.0


def slow_analyzer(strokes, *, pressure_supported):
    job_label = threading.current_thread().name
    with lock:
        state["in_flight"] += 1
        if state["in_flight"] > state["max_in_flight"]:
            state["max_in_flight"] = state["in_flight"]
        events.append((time.monotonic() - t_start, f"START   {job_label}", state["in_flight"]))
    time.sleep(JOB_DURATION_SEC)
    with lock:
        state["in_flight"] -= 1
        events.append((time.monotonic() - t_start, f"END     {job_label}", state["in_flight"]))
    return {"ok": True}


def _payload() -> str:
    return json.dumps({"strokes": [{"points": []}], "device": {"pressureSupported": False}})


async def main() -> None:
    global t_start

    # DB temporal para no ensuciar data/jobs.db
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db.DB_PATH = Path(tmp.name)
    db.init_db()

    worker.set_analyzer(slow_analyzer)
    await worker.start(concurrency=N)

    job_ids = []
    for i in range(JOBS):
        jid = str(uuid.uuid4())
        db.insert_pending_job(jid, _payload())
        job_ids.append(jid)

    t_start = time.monotonic()
    print(f"Encolando {JOBS} jobs con N={N}, duración por job = {JOB_DURATION_SEC}s\n")

    for jid in job_ids:
        await worker.enqueue(jid)

    # Esperar a que todos lleguen a 'listo'
    deadline = t_start + 10.0
    while time.monotonic() < deadline:
        if all(db.get_status(jid) == "listo" for jid in job_ids):
            break
        await asyncio.sleep(0.02)
    elapsed = time.monotonic() - t_start

    await worker.stop()

    print(f"{'t (s)':>8}  evento                                in_flight")
    print(f"{'-----':>8}  ------------------------------------  ---------")
    for t, descr, infl in events:
        marker = " <-- pico" if infl == state["max_in_flight"] and "START" in descr else ""
        print(f"{t:>8.3f}  {descr:<38}  {infl}{marker}")

    print()
    print(f"total transcurrido : {elapsed:.3f}s")
    print(f"pico concurrencia  : {state['max_in_flight']}  (N configurado = {N})")
    print(f"jobs completados   : {sum(1 for jid in job_ids if db.get_status(jid) == 'listo')}/{JOBS}")

    # Oleadas esperadas: ceil(JOBS/N) × duración
    import math
    waves = math.ceil(JOBS / N)
    print(f"oleadas esperadas  : {waves} ({JOBS} jobs / {N} concurrentes)")


if __name__ == "__main__":
    asyncio.run(main())
