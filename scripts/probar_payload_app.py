"""
Simula el payload que BackendAnalisisClient.kt genera, y verifica contra el
backend real que la transformación es aceptada por el schema moca-trace/1.
Se corre a mano con el servidor arriba en 127.0.0.1:8000.
"""
import json
import math
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

BASE = "http://127.0.0.1:8000"


def build_payload():
    """Equivalente mínimo del buildPayload() en Kotlin.

    Simula una captura con 2 trazos de 5 muestras cada uno, con el formato
    que SesionCaptura genera: timestamps en ns (API 34+), tilt polar
    (ángulo + orientación), que se convierten a ms y tilt cartesiano.
    """
    # Datos de la "SesionCaptura" simulada
    resolucion = "ns"  # API 34+
    canvas_w, canvas_h = 1200, 900
    tasa_hz = 180.0
    presiones_distintas = 15
    divisor = 1_000_000.0 if resolucion == "ns" else 1.0

    trazos_raw = [
        # Trazo 1: línea recta de 5 puntos (en ns)
        [
            (100.0, 200.0, 0.3, 0.1, 0.5, 1_000_000_000),
            (120.0, 200.0, 0.4, 0.1, 0.5, 1_008_000_000),
            (140.0, 200.0, 0.5, 0.1, 0.5, 1_016_000_000),
            (160.0, 200.0, 0.6, 0.1, 0.5, 1_024_000_000),
            (180.0, 200.0, 0.5, 0.1, 0.5, 1_032_000_000),
        ],
        # Trazo 2: línea después de 300 ms en el aire
        [
            (200.0, 300.0, 0.4, 0.2, 0.6, 1_332_000_000),
            (220.0, 300.0, 0.5, 0.2, 0.6, 1_340_000_000),
            (240.0, 300.0, 0.6, 0.2, 0.6, 1_348_000_000),
            (260.0, 300.0, 0.5, 0.2, 0.6, 1_356_000_000),
            (280.0, 300.0, 0.4, 0.2, 0.6, 1_364_000_000),
        ],
    ]

    t_zero = None
    strokes = []
    point_count = 0
    for muestras in trazos_raw:
        pts = []
        for (x, y, pres, tilt, orient, t) in muestras:
            t_ms = t / divisor
            if t_zero is None:
                t_zero = t_ms
            pts.append([
                x,
                y,
                pres,
                tilt * math.cos(orient),
                tilt * math.sin(orient),
                t_ms - t_zero,
            ])
            point_count += 1
        strokes.append({"points": pts})

    return {
        "subject": "TEST_PRUEBA",
        "task": "cubo",
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "device": {
            "pointerType": "stylus",
            "pressureSupported": presiones_distintas > 3,
            "sampleRateHz": tasa_hz,
            "canvasCssSize": {"width": canvas_w, "height": canvas_h},
        },
        "strokes": strokes,
        "integrity": {"pointCount": point_count, "strokeCount": len(strokes)},
    }


def http_post(url, body_dict):
    data = json.dumps(body_dict).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def http_get(url):
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def main():
    payload = build_payload()

    print("── POST /upload/trace ──")
    status, body = http_post(f"{BASE}/upload/trace", payload)
    print(f"HTTP {status}")
    print(json.dumps(body, indent=2, ensure_ascii=False))
    assert status == 202, "upload falló"
    job_id = body["jobId"]

    print()
    print(f"── Polling /status/{job_id[:8]}… ──")
    for i in range(30):
        time.sleep(0.5)
        status, body = http_get(f"{BASE}/status/{job_id}")
        s = body.get("status")
        print(f"  [{i:02d}] {s}")
        if s in ("listo", "error"):
            break

    print()
    print("── Resultado final ──")
    print(json.dumps(body, indent=2, ensure_ascii=False))

    if body.get("status") == "listo":
        r = body["result"]
        print()
        print(f"thresholdsCalibrated = {r.get('thresholdsCalibrated')}")
        print(f"normalizedJerk       = {r.get('normalizedJerk')}")
        print(f"tremorIndex          = {r.get('tremorIndex')}")
        print(f"pauseCount           = {r.get('pauseCount')}")
        print(f"airTimeSec           = {r.get('airTimeSec')}")


if __name__ == "__main__":
    main()
