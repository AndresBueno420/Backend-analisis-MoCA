"""
Enforcement del contrato de respuesta documentado en
.claude/analisis-cinematico/SKILL.md § Formato de respuesta.

Las invariantes:
- Envoltura siempre con exactamente {jobId, status, result, error} (ni más, ni menos).
- status in {pendiente, procesando, listo, error}.
- result != null si y solo si status == listo.
- error  != null si y solo si status == error.

Si estos tests fallan, o el código divergió del skill o el skill divergió del
código — en cualquier caso, hay que decidir cuál es la fuente de verdad y
realinear al otro.
"""
import json
import tempfile
import time
from pathlib import Path

import pytest

import app.db as db
from app import worker

EXPECTED_TOP_LEVEL_KEYS = {"jobId", "status", "result", "error"}
VALID_STATUSES = {"pendiente", "procesando", "listo", "error"}


@pytest.fixture
def client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setattr(db, "DB_PATH", Path(tmp.name))
    worker.reset_analyzer()

    # Importar main DESPUÉS del monkey-patch para que lifespan use la DB temp.
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def _valid_payload() -> dict:
    pts = [[float(i), 0.0, 0.5, 0.0, 0.0, float(i * 16)] for i in range(10)]
    return {
        "subject": "P001",
        "task": "cubo",
        "capturedAt": "2026-10-01T12:00:00Z",
        "device": {
            "pointerType": "stylus",
            "pressureSupported": False,
            "sampleRateHz": 60,
            "canvasCssSize": {"width": 800, "height": 600},
        },
        "strokes": [{"points": pts}],
        "integrity": {"pointCount": 10, "strokeCount": 1},
    }


def _assert_envelope(body: dict, expected_status: str) -> None:
    assert set(body.keys()) == EXPECTED_TOP_LEVEL_KEYS, (
        f"envoltura inesperada: {sorted(body.keys())}. "
        f"Esperado: {sorted(EXPECTED_TOP_LEVEL_KEYS)}."
    )
    assert body["status"] in VALID_STATUSES, f"status inválido: {body['status']}"
    assert body["status"] == expected_status

    if expected_status == "listo":
        assert body["result"] is not None
        assert body["error"] is None
    elif expected_status == "error":
        assert body["result"] is None
        assert body["error"] is not None
    else:  # pendiente | procesando
        assert body["result"] is None
        assert body["error"] is None


def test_upload_response_matches_envelope(client):
    """POST /upload/trace devuelve la envoltura completa, no un dict reducido."""
    r = client.post("/upload/trace", json=_valid_payload())
    assert r.status_code == 202
    body = r.json()
    assert body["status"] in {"pendiente", "procesando", "listo"}
    _assert_envelope(body, expected_status=body["status"])


def test_status_listo_envelope_and_result_fields(client):
    """
    Un job que completa cae en status=listo con el result lleno de los 13
    campos que el skill lista.
    """
    r = client.post("/upload/trace", json=_valid_payload())
    job_id = r.json()["jobId"]

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        r = client.get(f"/status/{job_id}")
        if r.json()["status"] == "listo":
            break
        time.sleep(0.02)

    body = r.json()
    _assert_envelope(body, expected_status="listo")

    expected_result_keys = {
        "totalPoints", "strokeCount", "durationSec",
        "velocityMeanPxS", "velocityMaxPxS", "normalizedJerk",
        "pauseCount", "pauseTotalSec", "airTimeSec", "tremorIndex",
        "pressureMean", "pressureVariance", "pressureVelocityCorr",
        "thresholdsCalibrated",
    }
    assert set(body["result"].keys()) == expected_result_keys, (
        f"campos de result: {sorted(body['result'].keys())}, "
        f"esperados: {sorted(expected_result_keys)}"
    )

    # Mientras los umbrales de pausa/FFT sean provisionales (ver SKILL.md
    # § thresholdsCalibrated), este flag debe salir False — es la señal al
    # cliente de que pauseCount/tremorIndex no son clínicamente definitivos.
    assert body["result"]["thresholdsCalibrated"] is False


def test_status_error_envelope(client):
    """
    Un job cuyo analizador falla cae en status=error con error poblado y
    result null — misma envoltura que los demás estados.
    """
    def exploding_analyzer(strokes, *, pressure_supported):
        raise RuntimeError("boom de test")

    worker.set_analyzer(exploding_analyzer)
    r = client.post("/upload/trace", json=_valid_payload())
    job_id = r.json()["jobId"]

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        r = client.get(f"/status/{job_id}")
        if r.json()["status"] == "error":
            break
        time.sleep(0.02)

    body = r.json()
    _assert_envelope(body, expected_status="error")
    assert "boom de test" in body["error"]


def test_status_404_uses_detail_shape_not_envelope(client):
    """
    404 es un error HTTP, no un estado de job — usa la forma {detail:...}
    de FastAPI, no la envoltura. El skill explícitamente separa los dos.
    """
    r = client.get("/status/no-existe-123")
    assert r.status_code == 404
    body = r.json()
    assert "detail" in body
    # La envoltura NO debe aparecer en errores HTTP
    assert "jobId" not in body
    assert "status" not in body
