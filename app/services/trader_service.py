from app.database.repository import TraderRepository


class TraderService:
    def __init__(self, repository: TraderRepository) -> None:
        self.repository = repository

    async def list(self, limit: int = 50, offset: int = 0):
        return await self.repository.list_traders(limit, offset)
