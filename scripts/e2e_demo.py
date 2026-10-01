"""
End-to-end: POST /upload/trace -> poll /status/{job_id} -> listo.

Usa TestClient (su portal mantiene vivo el event loop entre requests, así que
el worker procesa en background mientras hacemos polling).
"""
import json
import math
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.db as db  # noqa: E402

# DB temporal para no ensuciar data/jobs.db
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
db.DB_PATH = Path(_tmp.name)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def _gen_stroke(start_t_ms: float, n: int = 20, dt_ms: float = 16.0) -> list[list[float]]:
    """Trazo sintético con oscilación suave y presión variable."""
    pts = []
    for i in range(n):
        t = start_t_ms + i * dt_ms
        x = i * 5.0 + math.sin(i * 0.5) * 2.0
        y = i * 3.0 + math.cos(i * 0.7) * 1.5
        pressure = 0.40 + 0.10 * math.sin(i * 0.3)
        pts.append([x, y, pressure, 0.0, 0.0, float(t)])
    return pts


def _build_payload() -> dict:
    stroke1 = _gen_stroke(start_t_ms=0.0)
    air_gap_ms = 400.0
    stroke2 = _gen_stroke(start_t_ms=stroke1[-1][5] + air_gap_ms)
    strokes = [{"points": stroke1}, {"points": stroke2}]
    return {
        "subject": "DEMO-001",
        "task": "cubo",
        "capturedAt": "2026-10-01T12:00:00Z",
        "device": {
            "pointerType": "stylus",
            "pressureSupported": True,
            "sampleRateHz": 62.5,
            "canvasCssSize": {"width": 800, "height": 600},
        },
        "strokes": strokes,
        "integrity": {
            "pointCount": sum(len(s["points"]) for s in strokes),
            "strokeCount": len(strokes),
        },
    }


def main() -> int:
    payload = _build_payload()
    print(f"Payload: {len(payload['strokes'])} trazo(s), "
          f"{payload['integrity']['pointCount']} punto(s) totales\n")

    with TestClient(app) as client:
        t0 = time.monotonic()
        r = client.post("/upload/trace", json=payload)
        assert r.status_code == 202, (r.status_code, r.text)
        up = r.json()
        job_id = up["jobId"]
        print(f"[POST /upload/trace] -> 202  jobId={job_id}  status={up['status']}")

        # Polling
        poll_count = 0
        last_status = None
        deadline = t0 + 10.0
        while time.monotonic() < deadline:
            poll_count += 1
            r = client.get(f"/status/{job_id}")
            assert r.status_code == 200, (r.status_code, r.text)
            body = r.json()
            if body["status"] != last_status:
                dt = time.monotonic() - t0
                print(f"[GET  /status]  t={dt*1000:>5.0f}ms  status={body['status']}")
                last_status = body["status"]
            if body["status"] in ("listo", "error"):
                break
            time.sleep(0.02)

        elapsed_ms = (time.monotonic() - t0) * 1000
        print(f"\nFlujo end-to-end completado en {elapsed_ms:.0f}ms "
              f"con {poll_count} polling(s).\n")

        # Chequeo 404
        r404 = client.get("/status/no-existe-123")
        assert r404.status_code == 404, (r404.status_code, r404.text)
        print(f"[GET  /status/no-existe-123] -> 404  "
              f"code={r404.json()['detail']['code']}\n")

        # JSON final
        print("=" * 70)
        print("Respuesta final de GET /status/{job_id}:")
        print("=" * 70)
        print(json.dumps(body, indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
