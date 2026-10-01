"""
Motor de análisis cinemático para trazos MoCA.

Funciones puras: reciben una lista de trazos (cada trazo es una secuencia de
puntos [x, y, pressure, tiltX, tiltY, timestampMs]) y devuelven métricas.

Fórmulas definidas en .claude/analisis-cinematico/SKILL.md — cualquier cambio
en el cálculo tiene que reflejarse ahí también.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

# ---------------------------------------------------------------------------
# Umbrales provisionales (pendientes de calibrar con datos reales — por eso
# viven acá arriba y no hardcodeados dentro de las funciones).
# ---------------------------------------------------------------------------

INTRA_STROKE_PAUSE_VELOCITY_THRESHOLD_PX_S: float = 5.0
INTRA_STROKE_PAUSE_MIN_DURATION_SEC: float = 0.1  # 100 ms

TREMOR_BAND_HZ: tuple[float, float] = (3.0, 18.0)
FFT_INTERPOLATION_RATE_HZ: float = 100.0

# Flaguea en el resultado si pauseCount/pauseTotalSec/tremorIndex se calcularon
# con umbrales ya validados contra datos reales de pacientes. Mientras los
# cuatro valores de arriba sean provisionales, esto queda en False. Flipear a
# True cuando se calibre con una cohorte real (ver SKILL.md § thresholdsCalibrated).
THRESHOLDS_CALIBRATED: bool = False

# Índices dentro del tuple de punto
_IDX_X = 0
_IDX_Y = 1
_IDX_PRESSURE = 2
_IDX_T_MS = 5

Point = Sequence[float]
Stroke = Sequence[Point]


def _as_arrays(stroke: Stroke) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    arr = np.asarray(stroke, dtype=float)
    return arr[:, _IDX_X], arr[:, _IDX_Y], arr[:, _IDX_PRESSURE], arr[:, _IDX_T_MS] / 1000.0


# ---------------------------------------------------------------------------
# Derivadas por trazo
# ---------------------------------------------------------------------------

def compute_velocities(stroke: Stroke) -> tuple[np.ndarray, np.ndarray]:
    """
    v_i = distancia(p_i, p_{i+1}) / (t_{i+1} - t_i)  [px/s]
    Devuelve (velocities, dts_sec), ambos de longitud n-1.
    """
    x, y, _, t = _as_arrays(stroke)
    if len(x) < 2:
        return np.array([], dtype=float), np.array([], dtype=float)
    dist = np.hypot(np.diff(x), np.diff(y))
    dts = np.diff(t)
    with np.errstate(divide="ignore", invalid="ignore"):
        v = np.where(dts > 0, dist / dts, 0.0)
    return v, dts


def compute_accelerations(velocities: np.ndarray, dts_sec: np.ndarray) -> np.ndarray:
    """a_i = (v_{i+1} - v_i) / dt_i. Longitud: len(velocities) - 1."""
    if len(velocities) < 2:
        return np.array([], dtype=float)
    dv = np.diff(velocities)
    dt = dts_sec[: len(dv)]
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(dt > 0, dv / dt, 0.0)


def compute_jerks(accelerations: np.ndarray, dts_sec: np.ndarray) -> np.ndarray:
    """j_i = (a_{i+1} - a_i) / dt_i. Longitud: len(accelerations) - 1."""
    if len(accelerations) < 2:
        return np.array([], dtype=float)
    da = np.diff(accelerations)
    dt = dts_sec[: len(da)]
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(dt > 0, da / dt, 0.0)


def normalized_jerk_for_stroke(stroke: Stroke) -> float | None:
    """
    NJ = sqrt(0.5 * sum(j_i^2 * dt_i) * T^5 / L^2)

    Devuelve None si el trazo tiene <4 puntos o L==0/T==0 (trazo degenerado).
    """
    x, y, _, t = _as_arrays(stroke)
    if len(x) < 4:
        return None
    v, dts = compute_velocities(stroke)
    a = compute_accelerations(v, dts)
    j = compute_jerks(a, dts)
    if len(j) == 0:
        return None
    L = float(np.sum(np.hypot(np.diff(x), np.diff(y))))
    T = float(t[-1] - t[0])
    if L <= 0 or T <= 0:
        return None
    dt_for_j = dts[: len(j)]
    jerk_integral = float(np.sum((j ** 2) * dt_for_j))
    return float(np.sqrt(0.5 * jerk_integral * (T ** 5) / (L ** 2)))


# ---------------------------------------------------------------------------
# Pausas intra-trazo y tiempo en el aire
# ---------------------------------------------------------------------------

def intra_stroke_pauses(
    velocities: np.ndarray,
    dts_sec: np.ndarray,
    *,
    velocity_threshold: float = INTRA_STROKE_PAUSE_VELOCITY_THRESHOLD_PX_S,
    min_duration: float = INTRA_STROKE_PAUSE_MIN_DURATION_SEC,
) -> tuple[int, float]:
    """
    Tramos continuos con v_i < threshold cuya duración acumulada > min_duration
    cuentan como una pausa. Devuelve (count, total_duration_sec).
    """
    if len(velocities) == 0:
        return 0, 0.0
    below = velocities < velocity_threshold
    dt_v = dts_sec[: len(velocities)]
    count = 0
    total = 0.0
    i = 0
    n = len(below)
    while i < n:
        if below[i]:
            j = i
            run_dt = 0.0
            while j < n and below[j]:
                run_dt += float(dt_v[j])
                j += 1
            if run_dt > min_duration:
                count += 1
                total += run_dt
            i = j
        else:
            i += 1
    return count, total


def air_time(strokes: Sequence[Stroke]) -> float:
    """
    Tiempo total entre pen-up de un trazo y pen-down del siguiente (segundos).
    No se calcula con velocidad — es diferencia directa de timestamps.
    """
    if len(strokes) < 2:
        return 0.0
    total_ms = 0.0
    for i in range(len(strokes) - 1):
        last_t = float(strokes[i][-1][_IDX_T_MS])
        first_t = float(strokes[i + 1][0][_IDX_T_MS])
        gap = first_t - last_t
        if gap > 0:
            total_ms += gap
    return total_ms / 1000.0


# ---------------------------------------------------------------------------
# Presión (con doble validación de presión real)
# ---------------------------------------------------------------------------

_NULL_PRESSURE = {
    "pressureMean": None,
    "pressureVariance": None,
    "pressureVelocityCorr": None,
}


def pressure_features_for_stroke(
    stroke: Stroke,
    velocities: np.ndarray,
    *,
    pressure_supported: bool,
) -> dict:
    """
    Features de presión por trazo. Devuelve dict con los tres campos en None
    si (a) el dispositivo no soporta presión, o (b) la presión es constante en
    el trazo (no es real, viene inventada).
    """
    if not pressure_supported:
        return dict(_NULL_PRESSURE)
    _, _, pressure, _ = _as_arrays(stroke)
    if len(pressure) < 2 or np.all(pressure == pressure[0]):
        return dict(_NULL_PRESSURE)

    corr: float | None = None
    # Longitudes: presión = n, velocidad = n-1. Alineo presión[1:] con v
    # (velocidad del tramo que termina en ese punto).
    if len(velocities) >= 2:
        p_aligned = pressure[1:]
        if np.std(p_aligned) > 0 and np.std(velocities) > 0:
            corr = float(np.corrcoef(p_aligned, velocities)[0, 1])

    return {
        "pressureMean": float(np.mean(pressure)),
        "pressureVariance": float(np.var(pressure, ddof=0)),
        "pressureVelocityCorr": corr,
    }


# ---------------------------------------------------------------------------
# Temblor espectral (FFT sobre la serie de velocidad)
# ---------------------------------------------------------------------------

def tremor_index_for_stroke(
    stroke: Stroke,
    *,
    band_hz: tuple[float, float] = TREMOR_BAND_HZ,
    interp_rate_hz: float = FFT_INTERPOLATION_RATE_HZ,
) -> float | None:
    """
    tremorIndex = potencia(banda 3-18Hz) / potencia_total,
    calculado sobre la velocidad re-muestreada a tasa fija (eventos táctiles no
    son uniformes). Devuelve None si el trazo es muy corto.
    """
    v, _ = compute_velocities(stroke)
    if len(v) < 8:
        return None
    _, _, _, t = _as_arrays(stroke)
    t_centers = (t[:-1] + t[1:]) / 2.0
    duration = float(t_centers[-1] - t_centers[0])
    if duration <= 0:
        return None
    n_samples = max(8, int(duration * interp_rate_hz) + 1)
    t_uniform = np.linspace(t_centers[0], t_centers[-1], n_samples)
    v_uniform = np.interp(t_uniform, t_centers, v)
    v_uniform = v_uniform - np.mean(v_uniform)  # fuera DC
    spectrum = np.fft.rfft(v_uniform)
    power = np.abs(spectrum) ** 2
    freqs = np.fft.rfftfreq(n_samples, d=1.0 / interp_rate_hz)
    total = float(np.sum(power))
    if total <= 0:
        return None
    band_mask = (freqs >= band_hz[0]) & (freqs <= band_hz[1])
    return float(np.sum(power[band_mask])) / total


# ---------------------------------------------------------------------------
# Agregado top-level
# ---------------------------------------------------------------------------

def analyze_strokes(strokes: Sequence[Stroke], *, pressure_supported: bool) -> dict:
    """
    Agrega todas las métricas en un dict plano con los nombres de campo que
    espera consumir la app (ver ejemplo en SKILL.md § Formato de salida).
    """
    pooled_v: list[np.ndarray] = []
    nj_values: list[float] = []
    tremor_values: list[float] = []
    pressure_rows: list[dict] = []
    pause_count = 0
    pause_total = 0.0
    total_points = 0
    first_t_ms: float | None = None
    last_t_ms: float | None = None

    for stroke in strokes:
        total_points += len(stroke)
        if len(stroke) >= 1:
            s_first = float(stroke[0][_IDX_T_MS])
            s_last = float(stroke[-1][_IDX_T_MS])
            first_t_ms = s_first if first_t_ms is None else min(first_t_ms, s_first)
            last_t_ms = s_last if last_t_ms is None else max(last_t_ms, s_last)
        if len(stroke) < 2:
            continue
        v, dts = compute_velocities(stroke)
        pooled_v.append(v)

        nj = normalized_jerk_for_stroke(stroke)
        if nj is not None:
            nj_values.append(nj)

        c, d = intra_stroke_pauses(v, dts)
        pause_count += c
        pause_total += d

        ti = tremor_index_for_stroke(stroke)
        if ti is not None:
            tremor_values.append(ti)

        pressure_rows.append(
            pressure_features_for_stroke(stroke, v, pressure_supported=pressure_supported)
        )

    v_all = np.concatenate(pooled_v) if pooled_v else np.array([])
    duration_sec = (
        (last_t_ms - first_t_ms) / 1000.0
        if first_t_ms is not None and last_t_ms is not None
        else 0.0
    )

    def _mean_or_none(key: str) -> float | None:
        vals = [r[key] for r in pressure_rows if r[key] is not None]
        return float(np.mean(vals)) if vals else None

    return {
        "totalPoints": total_points,
        "strokeCount": len(strokes),
        "durationSec": duration_sec,
        "velocityMeanPxS": float(np.mean(v_all)) if len(v_all) else 0.0,
        "velocityMaxPxS": float(np.max(v_all)) if len(v_all) else 0.0,
        "normalizedJerk": float(np.mean(nj_values)) if nj_values else None,
        "pauseCount": pause_count,
        "pauseTotalSec": pause_total,
        "airTimeSec": air_time(strokes),
        "tremorIndex": float(np.mean(tremor_values)) if tremor_values else None,
        "pressureMean": _mean_or_none("pressureMean"),
        "pressureVariance": _mean_or_none("pressureVariance"),
        "pressureVelocityCorr": _mean_or_none("pressureVelocityCorr"),
        "thresholdsCalibrated": THRESHOLDS_CALIBRATED,
    }
