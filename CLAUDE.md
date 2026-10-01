# Backend de Análisis Cinemático — Trazabilidad MoCA

## Qué es este repositorio

Servicio independiente que recibe los datos crudos de trazo (capturados en IncognitusApp durante Copia del Cubo y Dibujo del Reloj) y calcula las métricas cinemáticas que tienen valor clínico: velocidad, jerk normalizado, temblor (espectral), pausas, y features de presión.

**No es parte de IncognitusApp ni del backend de visión.** Es un tercer servicio, separado del endpoint `/evaluate` que ya existe para el módulo de visión — decisión ya confirmada por el equipo, no está abierta a discusión en este repo.

## Por qué existe (contexto, no lo repitas en el código, pero entiéndelo)

El análisis que ya existe en el proyecto corre de forma síncrona en un servidor compartido y solo calcula velocidad, FFT y pausas — le falta jerk normalizado y presión. En vez de parchar ese servicio compartido, este repo construye el análisis completo desde cero, como servicio propio, para poder:
1. Desarrollarlo y probarlo sin depender de dónde se despliegue al final.
2. Probar de verdad la cola de sincronización offline de la tablet contra un servidor real, no un mock.
3. Cerrar la brecha de jerk/presión con una implementación nueva y verificada, no un parche.

## Contrato no negociable: accept-fast / process-slow

Este es el punto más importante de todo el servicio, no un detalle de implementación:

- `POST /upload/trace` **nunca** debe esperar a que termine el análisis. Valida el payload, lo guarda, responde `202 Accepted` + un `job_id`, y termina ahí. El análisis corre después, en background.
- Si esto se viola (si el endpoint de subida corre el análisis inline y responde cuando termina), se pierde la razón de ser de este servicio: evitar que varias tablets sincronizando a la vez saturen el análisis.
- `GET /status/{job_id}` es la única forma de saber si el análisis ya terminó y cuál fue el resultado.

## Códigos de error: esto es lo que el cliente usa para decidir si reintenta

- **400 (payload inválido):** el JSON no cumple el esquema, o `integrity.pointCount`/`strokeCount` no coincide con los datos reales enviados. Esto es un error permanente — reintentar exactamente lo mismo nunca va a funcionar. El cliente (la tablet) lo trata distinto a un error 5xx.
- **500 (falla interna real):** algo se rompió procesando una solicitud válida. Esto sí vale la pena reintentar.
- No mezclar estos dos casos bajo el mismo código de respuesta. Esta distinción es la que la tablet necesita para no reintentar infinito algo que nunca se va a arreglar solo.

## Concurrencia

El análisis debe tener un límite de cuántos jobs corren a la vez (ej. `asyncio.Semaphore(N)` con N pequeño, 2-4 para empezar). Si llegan 10 trazos de golpe (varias tablets sincronizando al final del día), se procesan en orden, no todos a la vez.

## Esquema de entrada: `moca-trace/1`

```json
{
  "subject": "string (código de participante, nunca nombre real)",
  "task": "cubo | reloj",
  "capturedAt": "ISO 8601",
  "device": {
    "pointerType": "stylus | finger",
    "pressureSupported": true,
    "sampleRateHz": 60,
    "canvasCssSize": {"width": 0, "height": 0}
  },
  "strokes": [
    {
      "points": [[x, y, pressure, tiltX, tiltY, timestampMs], ...]
    }
  ],
  "integrity": {"pointCount": 0, "strokeCount": 0}
}
```

Valida `integrity` contra los datos reales **antes** de aceptar el job. Si no coincide, es un 400, no un intento de análisis con datos posiblemente corruptos.

## Stack

Python + FastAPI + SQLite (simple, un archivo — no se necesita un motor externo para esto) + numpy/scipy para la parte espectral. Sin autenticación compleja por ahora (un token compartido simple basta); no es el foco de este servicio.

## Dónde están las fórmulas

En el skill `analisis-cinematico` (`.claude/skills/analisis-cinematico/SKILL.md`). No las rederives — ya están definidas, con un ejemplo numérico para verificar que la implementación las calculó bien.

## El resultado tiene que poder mostrarse en la app

El JSON que devuelve `GET /status/{job_id}` cuando el job está listo es lo que, más adelante, IncognitusApp va a consultar y mostrar en pantalla. Que sea estable y completo (no solo "ok": true) — debe incluir todas las métricas calculadas con nombres de campo claros, porque alguien del otro lado lo va a parsear.