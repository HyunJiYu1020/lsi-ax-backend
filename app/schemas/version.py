from datetime import date

from pydantic import BaseModel


class VersionIn(BaseModel):
    year: int
    month: int
    kind: str  # forecast | actual


class VersionStatusUpdate(BaseModel):
    status: str  # drafting | done


class VersionOut(BaseModel):
    year: int
    month: int
    kind: str
    status: str
    confirmed_at: date | None = None
