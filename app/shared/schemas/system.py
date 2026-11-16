from datetime import datetime

from pydantic import BaseModel


class CommonCodeIn(BaseModel):
    group_code: str
    code: str
    name: str
    parent_code: str | None = None
    sort_order: int = 0
    is_active: bool = True
    description: str | None = None


class CommonCodeOut(CommonCodeIn):
    pass


class ErrorLogOut(BaseModel):
    occurred_at: datetime
    method: str
    path: str
    status_code: int
    error_type: str
    message: str
    traceback: str
