from datetime import date

from pydantic import BaseModel


class PlanVersionIn(BaseModel):
    year: int
    plan_no: str


class PlanVersionUpdate(BaseModel):
    is_confirmed: bool = False
    is_final: bool = False


class PlanVersionOut(BaseModel):
    year: int
    plan_no: str
    is_confirmed: bool
    is_final: bool
    finalized_at: date | None = None


class PlanEntryIn(BaseModel):
    account_key: str
    scope_key: str
    year: int
    plan_no: str
    category_key: str
    month: int
    amount: float


class PlanEntryOut(PlanEntryIn):
    pass
