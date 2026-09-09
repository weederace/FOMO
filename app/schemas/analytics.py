from pydantic import BaseModel


class MetricResult(BaseModel):
    value: float | None
    confidence: float
    sufficient: bool
    reason: str
