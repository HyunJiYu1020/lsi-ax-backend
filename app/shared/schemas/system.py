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
    """컬럼 이름은 사내 표준(sait error_log) 그대로 쓴다 — 로그를 보는 사람이 두 시스템에서
    같은 이름을 보게 하려는 것."""

    log_id: int
    trace_id: str | None = None
    user_id: str | None = None          # SSO 연동 전까지 항상 None
    session_id: str | None = None       # SSO 연동 전까지 항상 None
    occurred_at: datetime
    client_ip: str | None = None
    user_agent: str | None = None
    api_url: str | None = None
    http_method: str | None = None
    params: str | None = None
    log_level: str
    http_status: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    stack_trace: str | None = None
    server_name: str | None = None
    response_time: int | None = None
    process_status: str | None = None
    handler: str | None = None
