# Backend de Análisis Cinemático — MoCA

Servicio HTTP independiente que recibe trazos capturados en IncognitusApp durante las tareas de **Copia del Cubo** y **Dibujo del Reloj** del test MoCA, y calcula métricas cinemáticas con valor clínico: velocidad, jerk normalizado, temblor espectral (FFT), pausas, tiempo en el aire y features de presión.

**Contrato de operación**: _accept-fast / process-slow_. `POST /upload/trace` **no espera** al análisis — valida, persiste y responde 202 inmediatamente. El análisis corre en background con un límite de concurrencia (3 jobs simultáneos). El resultado se consulta por polling a `GET /status/{job_id}`.

## Documentación de diseño

Antes de tocar nada, leer:

- **[`CLAUDE.md`](./CLAUDE.md)** — por qué existe este servicio (vs parchar el existente), el contrato accept-fast/process-slow, la separación 400 vs 500, el esquema de entrada `moca-trace/1`.
- **[`.claude/analisis-cinematico/SKILL.md`](./.claude/analisis-cinematico/SKILL.md)** — fórmulas exactas de cada métrica, ejemplo numérico de referencia (p0–p3), y **el contrato de respuesta** (envoltura `{jobId, status, result, error}` con sus 4 estados posibles). Esta es la fuente única de verdad para IncognitusApp.

Si lo que vas a cambiar afecta la salida JSON, actualizá primero el skill. El código debe seguir al skill, no al revés.

## Requisitos

- Python 3.12+
- Windows / Linux / macOS

## Levantar localmente

```bash
# 1. Entorno virtual
python -m venv .venv
# Windows (bash):
source .venv/Scripts/activate
# Linux/macOS:
# source .venv/bin/activate

# 2. Dependencias (runtime)
pip install -r requirements.txt
# Para correr tests también:
pip install -r requirements-dev.txt

# 3. Levantar el servidor
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Verificar:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

Documentación interactiva (Swagger UI) en `http://127.0.0.1:8000/docs`.

## Endpoints

| Método | Ruta | Qué hace |
|---|---|---|
| `GET`  | `/health` | Liveness. Devuelve `{"status": "ok"}`. |
| `POST` | `/upload/trace` | Recibe un payload `moca-trace/1`, valida, encola. Responde 202 con `jobId`. Nunca espera al análisis. |
| `GET`  | `/status/{job_id}` | Devuelve el estado actual del job con la envoltura documentada en `SKILL.md`. 404 si no existe. |
| `POST` | `/debug/mode` | **Solo testing.** Fuerza `/upload/trace` a responder 400/500 (para probar la cola offline de la tablet). Body: `{"mode": "off" \| "fail_400" \| "fail_500"}`. |
| `GET`  | `/debug/mode` | Devuelve el modo actual. |

**Shape de respuesta de `/upload/trace` y `/status/{job_id}`** (envoltura idéntica en los 4 estados — detalle en el skill):

```json
{
  "jobId": "uuid",
  "status": "pendiente | procesando | listo | error",
  "result": null | { ...13 métricas + thresholdsCalibrated... },
  "error":  null | "descripción del fallo"
}
```

**Códigos de error** (CLAUDE.md detalla la razón de separarlos):
- `400` — payload inválido (JSON malformado, esquema, o integridad). Reintentar lo mismo nunca va a funcionar.
- `404` — `job_id` no existe.
- `500` — falla interna del servicio. Vale la pena reintentar.

## Pruebas

```bash
pytest tests/ -v
```

Qué cubre:

- `test_schemas.py` — validación Pydantic + chequeo de integridad (`pointCount`/`strokeCount` reales vs declarados).
- `test_analysis_example.py` — el motor cumple el ejemplo numérico del skill (p0–p3 → v=[100,100,200], a=[0,1000], j=[10000]).
- `test_worker_concurrency.py` — el semáforo del worker limita de verdad: `max_in_flight == 3` exacto, no más.
- `test_response_envelope.py` — enforcement del contrato de respuesta documentado en `SKILL.md`. Guardia contra campos no documentados.

## Scripts de verificación manual

Herramientas puntuales, no parte del suite de pytest:

- `scripts/smoke_upload.py` — ejercita los 4 caminos de `/upload/trace` (válido + 3 variantes de 400) y chequea persistencia en SQLite.
- `scripts/demo_concurrency.py` — imprime un timeline visible del batching del worker (encola 6 jobs con analizador lento, muestra pico de simultaneidad y oleadas).
- `scripts/e2e_demo.py` — flujo completo: POST → polling → listo. Imprime el JSON final.

Correrlos: `.venv/Scripts/python.exe scripts/<nombre>.py` (en Windows; usar `PYTHONIOENCODING=utf-8` si la consola es cp1252).

## Estructura del repo

```
app/
  main.py          FastAPI app, lifespan, endpoints (/health, /upload, /status, /debug)
  schemas.py       Modelos Pydantic del esquema moca-trace/1 (camelCase vía alias)
  integrity.py     verify_integrity() + IntegrityMismatchError (chequeo cross-field)
  analysis.py      Motor cinemático (funciones puras, numpy) + constantes de umbral
  worker.py        Worker in-process: asyncio.Queue + Semaphore(3), crash recovery
  db.py            SQLite stdlib: tabla jobs, estados pendiente/procesando/listo/error
tests/             Tests pytest (6 archivos, 11 tests)
scripts/           Herramientas de verificación manual
data/              DB SQLite (gitignored)
.claude/
  analisis-cinematico/SKILL.md   Contrato de respuesta + fórmulas
  commands/                      Comandos slash de Claude Code
CLAUDE.md          Contrato del servicio y decisiones de diseño
```

## Estado actual y limitaciones conocidas

- **Umbrales sin calibrar clínicamente.** `pauseCount`, `pauseTotalSec` y `tremorIndex` se calculan con umbrales provisionales (5 px/s, 100 ms, 100 Hz de interpolación FFT). El flag `result.thresholdsCalibrated: false` lo señaliza explícitamente. El cliente no debe mostrar estos valores como diagnósticos mientras el flag esté en `false` — ver `SKILL.md § thresholdsCalibrated`.
- **Sin autenticación.** CLAUDE.md define esto como aceptable para esta fase; el despliegue real requiere al menos un token compartido.
- **Modo debug activo.** `/debug/mode` fuerza respuestas 400/500 para testear la cola offline de la tablet. **No desplegar a producción con los endpoints `/debug/*` expuestos** — cualquiera puede dejar al servicio rechazando todo tráfico.
- **Load test real pendiente.** El test de concurrencia usa `time.sleep` como analizador falso; una ráfaga real de payloads de tamaño clínico contra numpy en thread pool no se midió todavía.
