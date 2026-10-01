---
description: Audita el motor de análisis cinemático contra el ejemplo numérico de referencia y el contrato de la API antes de confiar en los resultados.
---

Audita el estado actual del servicio contra esta checklist. Responde cada punto con evidencia concreta (qué archivo, qué línea, qué resultado obtuviste al correrlo) — no "sí, está bien" sin mostrar el cálculo.

1. **Verificación numérica del jerk:** corre el motor de análisis contra el ejemplo del skill `analisis-cinematico` (los 4 puntos p0-p3). ¿Da exactamente v1=100, v2=100, v3=200, a1=0, a2=1000, j1=10000? Si no, muestra qué valores dio y dónde está el desvío.

2. **Verificación de presión:** confirma que existe la doble validación (pressureSupported==false, y valores todos iguales) antes de calcular cualquier estadística de presión. Muestra el fragmento de código que la implementa.

3. **Contrato accept-fast/process-slow:** confirma que `POST /upload/trace` responde antes de que el análisis termine — no que "es rápido", sino que estructuralmente no espera el resultado. ¿Dónde está la frontera entre "guardar y responder" y "analizar"?

4. **Separación 400 vs 500:** busca todos los `raise HTTPException` o equivalente. Clasifica cada uno: ¿es un error de payload (400) o una falla interna (500)? Señala cualquiera que esté mal clasificado.

5. **Límite de concurrencia:** confirma que existe un límite real (semáforo, cola con tamaño fijo, pool de workers) y no solo una función async sin control — una función `async def` sin un límite explícito no limita nada por sí sola.

6. **Validación de integridad:** confirma que `integrity.pointCount`/`strokeCount` se comparan contra los datos reales recibidos antes de aceptar el job, y que un payload que no cuadra responde 400, no se acepta silenciosamente.

7. **Segmentación por trazo:** confirma que ninguna derivada (velocidad/aceleración/jerk) se calcula saltando de un trazo a otro — revisa que el cálculo itere dentro de cada `stroke` por separado.

8. **Estabilidad del JSON de resultado:** compara el JSON real que devuelve `GET /status/{job_id}` contra el formato de ejemplo del skill. ¿Los nombres de campo coinciden? Esto importa porque otro repo (IncognitusApp) va a parsear esto más adelante.

Termina con un resumen de 3-5 líneas: qué está verificado y listo, y qué necesita corregirse antes de conectar esto a la tablet real.