"""
Verifica el motor de análisis contra el ejemplo numérico de referencia del
skill (.claude/analisis-cinematico/SKILL.md § 3).

Si estos tests fallan, hay un bug en las derivadas — no se puede confiar en
ninguna métrica (NJ, pausas, FFT) hasta que esto cuadre.
"""
import numpy as np
from numpy.testing import assert_allclose

from app.analysis import (
    compute_accelerations,
    compute_jerks,
    compute_velocities,
)

# 4 puntos de un trazo recto, 100ms entre cada uno (y constante).
# Formato de punto: [x, y, pressure, tiltX, tiltY, timestampMs]
EXAMPLE_STROKE = [
    [0.0,  0.0, 0.0, 0.0, 0.0,   0.0],
    [10.0, 0.0, 0.0, 0.0, 0.0, 100.0],
    [20.0, 0.0, 0.0, 0.0, 0.0, 200.0],
    [40.0, 0.0, 0.0, 0.0, 0.0, 300.0],
]


def test_example_velocities():
    v, _ = compute_velocities(EXAMPLE_STROKE)
    print(f"\n  velocidades (px/s): {v.tolist()}")
    assert_allclose(v, [100.0, 100.0, 200.0])


def test_example_accelerations():
    v, dts = compute_velocities(EXAMPLE_STROKE)
    a = compute_accelerations(v, dts)
    print(f"\n  aceleraciones (px/s^2): {a.tolist()}")
    assert_allclose(a, [0.0, 1000.0])


def test_example_jerks():
    v, dts = compute_velocities(EXAMPLE_STROKE)
    a = compute_accelerations(v, dts)
    j = compute_jerks(a, dts)
    print(f"\n  jerks (px/s^3): {j.tolist()}")
    assert_allclose(j, [10000.0])
