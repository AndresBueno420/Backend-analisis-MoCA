from .schemas import TracePayload


class IntegrityMismatchError(ValueError):
    def __init__(self, field: str, declared: int, actual: int):
        self.field = field
        self.declared = declared
        self.actual = actual
        super().__init__(
            f"integrity.{field} declara {declared} pero el payload contiene {actual}"
        )


def verify_integrity(payload: TracePayload) -> None:
    actual_stroke_count = len(payload.strokes)
    if payload.integrity.stroke_count != actual_stroke_count:
        raise IntegrityMismatchError(
            field="strokeCount",
            declared=payload.integrity.stroke_count,
            actual=actual_stroke_count,
        )

    actual_point_count = sum(len(s.points) for s in payload.strokes)
    if payload.integrity.point_count != actual_point_count:
        raise IntegrityMismatchError(
            field="pointCount",
            declared=payload.integrity.point_count,
            actual=actual_point_count,
        )
