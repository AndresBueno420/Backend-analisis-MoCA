"""Smoke test del endpoint /upload/trace. Se corre a mano, no es parte de pytest."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tempfile

import app.db as db

# Redirige la DB a un archivo temporal para no ensuciar data/jobs.db
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
db.DB_PATH = Path(_tmp.name)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)

VALID = {
    "subject": "P001",
    "task": "cubo",
    "capturedAt": "2026-10-01T12:00:00Z",
    "device": {
        "pointerType": "stylus",
        "pressureSupported": True,
        "sampleRateHz": 60,
        "canvasCssSize": {"width": 800, "height": 600},
    },
    "strokes": [
        {"points": [[0.0, 0.0, 0.5, 0.0, 0.0, 0.0], [10.0, 0.0, 0.5, 0.0, 0.0, 100.0]]},
    ],
    "integrity": {"pointCount": 2, "strokeCount": 1},
}


def main() -> int:
    with client:
        # 1. válido -> 202
        r = client.post("/upload/trace", json=VALID)
        assert r.status_code == 202, (r.status_code, r.text)
        body = r.json()
        assert "jobId" in body and body["status"] == "pendiente"
        print(f"[OK] 202 válido -> jobId={body['jobId']}")

        # 2. JSON malformado -> 400 json_invalido
        r = client.post(
            "/upload/trace",
            content=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400, (r.status_code, r.text)
        assert r.json()["detail"]["code"] == "json_invalido"
        print(f"[OK] 400 json_invalido -> {r.json()['detail']['message']}")

        # 3. Esquema inválido (falta task) -> 400 esquema_invalido
        bad_schema = {**VALID}
        bad_schema.pop("task")
        r = client.post("/upload/trace", json=bad_schema)
        assert r.status_code == 400, (r.status_code, r.text)
        assert r.json()["detail"]["code"] == "esquema_invalido"
        print(f"[OK] 400 esquema_invalido -> {len(r.json()['detail']['errors'])} error(es)")

        # 4. Integridad mala (pointCount miente) -> 400 integridad_invalida
        bad_integrity = {**VALID, "integrity": {"pointCount": 99, "strokeCount": 1}}
        r = client.post("/upload/trace", json=bad_integrity)
        assert r.status_code == 400, (r.status_code, r.text)
        d = r.json()["detail"]
        assert d["code"] == "integridad_invalida"
        assert d["field"] == "pointCount"
        assert d["declared"] == 99 and d["actual"] == 2
        print(f"[OK] 400 integridad_invalida -> field={d['field']} declared={d['declared']} actual={d['actual']}")

        # 5. Confirmar que el válido se persistió como 'pendiente'
        import sqlite3

        conn = sqlite3.connect(db.DB_PATH)
        row = conn.execute(
            "SELECT id, status FROM jobs WHERE id = ?", (body["jobId"],)
        ).fetchone()
        conn.close()
        assert row is not None, "job no se persistió"
        # El worker arranca con la app (lifespan) y procesa inmediatamente,
        # así que cualquiera de estos tres estados es válido.
        assert row[1] in {"pendiente", "procesando", "listo"}, (
            f"status inesperado: {row[1]}"
        )
        print(f"[OK] persistencia -> id={row[0][:8]}... status={row[1]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
