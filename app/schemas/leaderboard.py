from pydantic import BaseModel


class LeaderboardResponse(BaseModel):
    traders: list[dict]
    total: int
