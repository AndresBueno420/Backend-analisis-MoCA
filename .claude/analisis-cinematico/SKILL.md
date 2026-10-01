---
name: analisis-cinematico
description: Fórmulas exactas para el motor de análisis cinemático del backend de trazabilidad (velocidad, jerk normalizado, temblor espectral, presión, pausas). Úsalo al implementar cualquier función de cálculo de métricas sobre los datos de trazo.
---

# Análisis cinemático — fórmulas de referencia

Todas las fórmulas de abajo operan **por trazo** (`stroke`), no sobre el dibujo completo de una sola vez. Un trazo es la secuencia de puntos entre un pen-down y el siguiente pen-up. Las métricas globales del dibujo (ej. jerk normalizado promedio) se agregan después, promediando o sumando lo calculado por trazo — nunca calcules la derivada saltando de un trazo a otro, el lápiz estuvo en el aire entremedio y esa transición no es un movimiento real a medir.

## 1. Velocidad

Entre dos puntos consecutivos `(x_i, y_i, t_i)` y `(x_{i+1}, y_{i+1}, t_{i+1})` del mismo trazo:

```
distancia_i = sqrt((x_{i+1} - x_i)^2 + (y_{i+1} - y_i)^2)
dt_i = t_{i+1} - t_i   (en segundos, no en ms — convierte antes de dividir)
v_i = distancia_i / dt_i
```

Guarda `velocityMeanPxS` (media de todos los `v_i` del trazo) y `velocityMaxPxS` (máximo).

## 2. Aceleración y jerk

```
a_i = (v_{i+1} - v_i) / dt_i
j_i = (a_{i+1} - a_i) / dt_i
```

## 3. Jerk normalizado (la métrica que falta hoy)

```
NJ = sqrt(0.5 * integral(jerk(t)^2 dt) * T^5 / L^2)
```

Donde:
- `integral(jerk(t)^2 dt)` ≈ `sum(j_i^2 * dt_i)` (suma discreta sobre el trazo)
- `T` = duración total del trazo (segundos)
- `L` = longitud total del trazo (suma de todas las `distancia_i`)

Calcula un `NJ` por trazo; el agregado del dibujo es el promedio de los `NJ` de todos sus trazos (`normalizedJerk` en el resultado final).

### Ejemplo numérico para verificar tu implementación

Antes de conectar esto a nada más, corre este caso a mano y confirma que tu código da el mismo resultado. Puntos de un trazo recto (4 puntos, en píxeles y milisegundos):

```
p0 = (x=0,  t=0)
p1 = (x=10, t=100)
p2 = (x=20, t=200)
p3 = (x=40, t=300)
```

(y constante, no afecta el cálculo en este ejemplo)

Valores esperados:
- `v1` (p0→p1) = 10 px / 0.1s = **100 px/s**
- `v2` (p1→p2) = 10 px / 0.1s = **100 px/s**
- `v3` (p2→p3) = 20 px / 0.1s = **200 px/s**
- `a1` (v1→v2) = (100-100)/0.1 = **0 px/s²**
- `a2` (v2→v3) = (200-100)/0.1 = **1000 px/s²**
- `j1` (a1→a2) = (1000-0)/0.1 = **10000 px/s³**

Si tu implementación no da estos valores exactos para este caso, hay un bug en la derivada antes de seguir con el jerk normalizado — no avances hasta que esto cuadre.

## 4. Features de presión

**Primero, verifica que la presión es real, no inventada:**
```
if device.pressureSupported == false: no calcules nada de presión, deja los campos en null
if todos los valores de presión del trazo son iguales (ej. siempre 0.5): tampoco son reales, deja los campos en null
```

Si pasa esa verificación:
```
pressureMean = media de los valores de presión del trazo
pressureVariance = varianza de esos valores
pressureVelocityCorr = correlación de Pearson entre presión y velocidad (muestra a muestra)
```

## 5. Pausas inter-trazo

Dos tipos de pausa, no los mezcles:
- **Pausa dentro de un trazo:** tramo donde `v_i` cae por debajo de un umbral (provisional: 5 px/s) durante más de un tiempo mínimo (provisional: 100ms). Cuenta y suma duración.
- **Tiempo en el aire (in-air time):** el hueco de tiempo entre el pen-up de un trazo y el pen-down del siguiente. Esto no se calcula con velocidad (no hay puntos ahí) — es directamente `timestamp del primer punto del siguiente trazo - timestamp del último punto del trazo anterior`.

Estos dos umbrales son provisionales — déjalos como constantes configurables al inicio del archivo, no hardcodeados dentro de la función, porque van a necesitar calibrarse con datos reales más adelante.

## 6. Temblor espectral (FFT)

Sobre la serie de velocidad (no de posición) de un trazo:
1. Si el muestreo no es uniforme (lo normal con eventos táctiles), interpola a una tasa fija antes de aplicar FFT.
2. Aplica FFT, obtén el espectro de potencia.
3. Calcula la potencia relativa en la banda 3–18 Hz (temblor clínico) frente a la potencia total:
   ```
   tremorIndex = potencia_en_banda_3_18Hz / potencia_total
   ```
4. Si quieres desagregar, las sub-bandas de referencia son: 3–7 Hz (Parkinsoniano), 4–12 Hz (temblor esencial), 6–12 Hz (fisiológico aumentado), 13–18 Hz (ortostático) — opcional para una primera versión, el índice agregado es suficiente para empezar.

## Formato de respuesta del servicio

Esta sección es el **contrato con el cliente** (IncognitusApp). Lo que no esté acá no va en la respuesta; lo que esté acá tiene que salir siempre con estos nombres exactos.

Aplica a los dos endpoints que devuelven estado de job:
- `POST /upload/trace` — devuelve el estado inicial del job que se acaba de aceptar.
- `GET /status/{job_id}` — devuelve el estado actual del job.

Los dos endpoints usan **la misma envoltura**. El cliente puede parsear la respuesta sin ramificar por endpoint — solo mira `status` para decidir qué hacer.

### Envoltura (idéntica en los 4 estados posibles)

```json
{
  "jobId": "uuid",
  "status": "pendiente | procesando | listo | error",
  "result": null | { ...métricas... },
  "error": null | "descripción del fallo"
}
```

- `jobId` — siempre string (uuid4).
- `status` — uno de los cuatro literales. Transiciones válidas: `pendiente → procesando → (listo | error)`. Nunca retrocede.
- `result` — dict de métricas **sólo** cuando `status == "listo"`. En los otros tres estados vale `null`.
- `error` — string descriptivo **sólo** cuando `status == "error"`. En los otros tres estados vale `null`.

Los cuatro campos están siempre presentes. No hay respuestas con menos campos ni con campos extra.

### Caso `status: "listo"` — forma de `result`

```json
{
  "jobId": "...",
  "status": "listo",
  "result": {
    "totalPoints": 842,
    "strokeCount": 3,
    "durationSec": 12.4,
    "velocityMeanPxS": 134.2,
    "velocityMaxPxS": 610.8,
    "normalizedJerk": 48.3,
    "pauseCount": 4,
    "pauseTotalSec": 1.1,
    "airTimeSec": 0.6,
    "tremorIndex": 0.08,
    "pressureMean": null,
    "pressureVariance": null,
    "pressureVelocityCorr": null,
    "thresholdsCalibrated": false
  },
  "error": null
}
```

Campos del `result` que pueden salir `null` sin que sea un fallo:
- `pressureMean` / `pressureVariance` / `pressureVelocityCorr` — cuando el dispositivo no soporta presión o la presión fue constante en el trazo (ver § 4).
- `normalizedJerk` — cuando todos los trazos tenían menos de 4 puntos.
- `tremorIndex` — cuando todos los trazos tenían menos de 8 puntos.

El resto de los campos (contadores, duraciones, velocidades, pausas) siempre son numéricos.

#### `thresholdsCalibrated` — por qué está acá

Dos métricas del `result` dependen de umbrales numéricos que **todavía no están calibrados contra datos reales de pacientes**:

- `pauseCount` y `pauseTotalSec` — usan un umbral de velocidad (5 px/s provisional) y una duración mínima (100 ms provisional) para decidir qué cuenta como pausa (§ 5).
- `tremorIndex` — usa una tasa de interpolación de 100 Hz provisional antes de la FFT (§ 6).

Mientras no haya calibración clínica, `thresholdsCalibrated` sale **siempre `false`**. Esto no significa que las métricas estén mal calculadas — significa que los números crudos existen pero su umbral de "normal vs patológico" aún no fue validado con una cohorte real. El cliente no debe mostrarlas al clínico como diagnósticas mientras este flag esté en `false`; mostrarlas como "exploratorias" o detrás de un disclaimer está bien.

Las demás métricas (`velocityMean/Max`, `normalizedJerk`, `durationSec`, `airTimeSec`, `pressure*`) no dependen de umbrales calibrables — son cálculos directos sobre los datos del trazo, su valor no cambia con calibración.

Cuando se calibre, este flag pasa a `true` en una línea del motor; los nombres de los demás campos no cambian.

### Caso `status: "error"`

Algo falló procesando un payload que ya había pasado la validación de entrada (típicamente un bug del motor, no del cliente). El cliente puede reintentar.

```json
{
  "jobId": "...",
  "status": "error",
  "result": null,
  "error": "NombreDeExcepcion: mensaje descriptivo"
}
```

### Casos `status: "pendiente"` y `status: "procesando"`

El análisis aún no terminó. El cliente debe seguir haciendo polling a `GET /status/{job_id}`.

```json
{
  "jobId": "...",
  "status": "pendiente",
  "result": null,
  "error": null
}
```

(La forma para `"procesando"` es idéntica, solo cambia el valor de `status`.)

### Nota sobre errores HTTP

Los códigos 400/404/5xx **no usan esta envoltura** — son errores de transporte, no estados de job. Siguen la convención FastAPI (`{"detail": {...}}`). Un `status: "error"` es diferente de un HTTP 500: el primero significa "el job existe en la DB y su análisis falló"; el segundo, "la request ni siquiera llegó a crear un job".
