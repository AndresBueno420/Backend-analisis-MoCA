from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class _Base(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class CanvasCssSize(_Base):
    width: float
    height: float


class Device(_Base):
    pointer_type: Literal["stylus", "finger"]
    pressure_supported: bool
    sample_rate_hz: float
    canvas_css_size: CanvasCssSize


Point = tuple[float, float, float, float, float, float]


class Stroke(_Base):
    points: list[Point]


class Integrity(_Base):
    point_count: int
    stroke_count: int


class TracePayload(_Base):
    subject: str
    task: Literal["cubo", "reloj"]
    captured_at: datetime
    device: Device
    strokes: list[Stroke]
    integrity: Integrity
