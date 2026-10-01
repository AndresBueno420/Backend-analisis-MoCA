import asyncio
import json
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import ValidationError

from . import worker
from .db import get_job, init_db, insert_pending_job
from .integrity import IntegrityMismatchError, verify_integrity
from .schemas import TracePayload


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await worker.start()
    try:
        yield
    finally:
        await worker.stop()


app = FastAPI(title="Backend de Análisis Cinemático — MoCA", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Modo de prueba — fuerza respuestas 400/500 en /upload/trace para validar la
# cola del cliente. Vive en memoria del proceso; se resetea al reiniciar.
# No usar en producción.
# ---------------------------------------------------------------------------
_debug_fail_mode: str = "off"  # off | fail_400 | fail_500


@app.post("/debug/mode")
async def debug_set_mode(request: Request):
    global _debug_fail_mode
    body = await request.json()
    mode = body.get("mode", "off")
    if mode not in {"off", "fail_400", "fail_500"}:
        raise HTTPException(400, detail={"code": "modo_invalido", "message": f"modo desconocido: {mode}"})
    _debug_fail_mode = mode
    return {"mode": _debug_fail_mode}


@app.get("/debug/mode")
def debug_get_mode():
    return {"mode": _debug_fail_mode}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/upload/trace", status_code=status.HTTP_202_ACCEPTED)
async def upload_trace(request: Request):
    if _debug_fail_mode == "fail_400":
        raise HTTPException(
            status_code=400,
            detail={"code": "debug_forzado", "message": "backend en modo fail_400 (test)"},
        )
    if _debug_fail_mode == "fail_500":
        raise HTTPException(
            status_code=500,
            detail={"code": "debug_forzado", "message": "backend en modo fail_500 (test)"},
        )

    raw_body = await request.body()

    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "json_invalido",
                "message": f"El cuerpo no es JSON válido: {e.msg}",
            },
        )

    try:
        payload = TracePayload.model_validate(data)
    except ValidationError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "esquema_invalido",
                "message": "El payload no cumple el esquema moca-trace/1.",
                "errors": e.errors(include_url=False),
            },
        )

    try:
        verify_integrity(payload)
    except IntegrityMismatchError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "integridad_invalida",
                "message": str(e),
                "field": e.field,
                "declared": e.declared,
                "actual": e.actual,
            },
        )

    job_id = str(uuid.uuid4())
    await asyncio.to_thread(insert_pending_job, job_id, raw_body.decode("utf-8"))
    await worker.enqueue(job_id)

    return {"jobId": job_id, "status": "pendiente", "result": None, "error": None}


@app.get("/status/{job_id}")
async def get_status(job_id: str):
    row = await asyncio.to_thread(get_job, job_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "job_no_encontrado",
                "message": f"No existe un job con id {job_id}.",
            },
        )
    result = json.loads(row["result"]) if row["result"] is not None else None
    return {
        "jobId": job_id,
        "status": row["status"],
        "result": result,
        "error": row["error"],
    }
