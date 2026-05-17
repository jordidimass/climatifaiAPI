from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from db import config
from db.session import session_scope


async def get_optional_db() -> AsyncIterator[AsyncSession | None]:
    if not config.is_database_configured():
        yield None
        return
    async with session_scope() as sess:
        yield sess


OptionalDbSession = Annotated[AsyncSession | None, Depends(get_optional_db)]
