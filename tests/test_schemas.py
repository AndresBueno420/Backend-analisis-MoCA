import pytest
from pydantic import ValidationError

from app.integrity import IntegrityMismatchError, verify_integrity
from app.schemas import TracePayload


def _valid_payload() -> dict:
    return {
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
            {
                "points": [
                    [0.0, 0.0, 0.5, 0.0, 0.0, 0.0],
                    [10.0, 0.0, 0.5, 0.0, 0.0, 100.0],
                    [20.0, 0.0, 0.5, 0.0, 0.0, 200.0],
                ]
            },
            {
                "points": [
                    [30.0, 30.0, 0.5, 0.0, 0.0, 500.0],
                    [40.0, 40.0, 0.5, 0.0, 0.0, 600.0],
                ]
            },
        ],
        "integrity": {"pointCount": 5, "strokeCount": 2},
    }


def test_valid_payload_parses_and_passes_integrity():
    payload = TracePayload.model_validate(_valid_payload())
    verify_integrity(payload)

    assert payload.subject == "P001"
    assert payload.task == "cubo"
    assert len(payload.strokes) == 2
    assert payload.device.pointer_type == "stylus"
    assert payload.device.sample_rate_hz == 60.0


def test_integrity_mismatch_point_count_raises_structured_error():
    data = _valid_payload()
    data["integrity"]["pointCount"] = 99

    payload = TracePayload.model_validate(data)

    with pytest.raises(IntegrityMismatchError) as exc_info:
        verify_integrity(payload)

    err = exc_info.value
    assert err.field == "pointCount"
    assert err.declared == 99
    assert err.actual == 5
    assert "pointCount" in str(err)


def test_missing_required_field_raises_validation_error():
    data = _valid_payload()
    del data["task"]

    with pytest.raises(ValidationError) as exc_info:
        TracePayload.model_validate(data)

    errors = exc_info.value.errors()
    assert any(
        e["loc"] == ("task",) and e["type"] == "missing" for e in errors
    ), f"se esperaba un error 'missing' en 'task', se obtuvo: {errors}"
