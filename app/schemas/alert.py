from datetime import datetime

from pydantic import BaseModel


class AlertResponse(BaseModel):
    alert_type: str
    payload: dict
    created_at: datetime
