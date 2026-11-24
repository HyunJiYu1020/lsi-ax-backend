"""로그를 DB(error_logs)에 적는 log4j 방식 핸들러.

사내 표준(sait)과 같은 구조다 — 전역 예외 핸들러만 쓰는 게 아니라 표준 logging에 핸들러를
하나 붙여서, 코드 어디서든 `logger.warning()`/`logger.error()`를 부르면 그게 DB에 남는다.
그래서 "미처리 500"뿐 아니라 **의도적으로 거절한 4xx**도 기록된다.

500만 적으면 4xx 거부가 아무 흔적도 남기지 않는다 — 프론트가 0-based 단계를 보내 저장이 400으로
계속 거부되던 사고가 그렇게 묻혔다.

요청 정보(경로·IP·본문 등)는 로그 호출부가 매번 넘기지 않는다 — 미들웨어가 ContextVar에 담아
두고 핸들러가 꺼내 쓴다. 로깅 호출 한 줄이 요청 문맥을 몰라도 되게 하려는 것.
"""

import logging
import socket
import time
import traceback as tb_module
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

# 요청 1건의 문맥. 미들웨어가 채우고 핸들러가 읽는다. 요청 밖(스크립트·스타트업)에서 찍힌
# 로그는 빈 dict를 보게 되고, 그때는 요청 관련 컬럼이 전부 NULL로 남는다.
request_ctx: ContextVar[dict[str, Any]] = ContextVar("request_ctx", default={})

# 재진입 방지. DB 쓰기가 실패하면서 또 로그를 찍으면 무한 재귀가 된다
# (SQLAlchemy·psycopg이 WARNING을 내는 경로가 실제로 있다).
_writing: ContextVar[bool] = ContextVar("_writing", default=False)

_SERVER_NAME = socket.gethostname()[:100]

#: error_message 컬럼이 String(1000)이라 넘치면 자른다 — 표준 DDL을 그대로 쓰기 때문에
#: 컬럼을 늘리는 대신 여기서 맞춘다. 전문은 stack_trace에 남는다.
_MESSAGE_MAX = 1000
_PARAMS_MAX = 20000


def _clip(s: str | None, n: int) -> str | None:
    if s is None:
        return None
    return s if len(s) <= n else s[: n - 3] + "..."


def _elapsed_ms(ctx: dict[str, Any]) -> int | None:
    """요청 시작부터 지금까지의 ms. 미들웨어 밖에서 찍힌 로그는 None."""
    started = ctx.get("_started")
    return None if started is None else int((time.monotonic() - started) * 1000)


class DBLogHandler(logging.Handler):
    """WARNING 이상의 로그 레코드를 error_logs에 한 행씩 적는다.

    DB 쓰기가 실패해도 **절대 예외를 올리지 않는다** — 로그를 못 남기는 것 때문에 정상 응답이
    깨지면 안 되기 때문이다. 실패는 조용히 버린다(stderr 기본 핸들러에는 그대로 남는다)."""

    def emit(self, record: logging.LogRecord) -> None:
        if _writing.get():
            return
        token = _writing.set(True)
        try:
            self._write(record)
        except Exception:
            pass
        finally:
            _writing.reset(token)

    def _write(self, record: logging.LogRecord) -> None:
        # 지연 import — 이 모듈이 세션/모델보다 먼저 로드될 수 있다(설정 시점 순환 참조 방지).
        from app.db.session import SessionLocal
        from app.shared.models.system import ErrorLog

        ctx = request_ctx.get()
        stack = (
            "".join(tb_module.format_exception(*record.exc_info))
            if record.exc_info
            else (record.stack_info or None)
        )
        exc_type = record.exc_info[0].__name__ if record.exc_info and record.exc_info[0] else None

        db = SessionLocal()
        try:
            db.add(
                ErrorLog(
                    trace_id=ctx.get("trace_id"),
                    user_id=ctx.get("user_id"),       # SSO 연동 전까지 None
                    session_id=ctx.get("session_id"),  # SSO 연동 전까지 None
                    occurred_at=datetime.fromtimestamp(record.created, tz=timezone.utc),
                    client_ip=ctx.get("client_ip"),
                    user_agent=_clip(ctx.get("user_agent"), 500),
                    api_url=_clip(ctx.get("api_url"), 500),
                    http_method=ctx.get("http_method"),
                    params=_clip(ctx.get("params"), _PARAMS_MAX),
                    log_level=record.levelname,
                    http_status=getattr(record, "http_status", None) or ctx.get("http_status"),
                    # 표준의 error_code 자리에 지금은 예외 클래스명을 넣는다. 사내 코드 체계가
                    # 생기면 extra={"error_code": ...}로 넘기는 값이 우선한다.
                    error_code=getattr(record, "error_code", None) or exc_type,
                    error_message=_clip(record.getMessage(), _MESSAGE_MAX),
                    stack_trace=stack,
                    server_name=_SERVER_NAME,
                    response_time=_elapsed_ms(ctx),
                    handler=_clip(record.name, 50),
                )
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def setup_db_logging(level: int = logging.WARNING) -> DBLogHandler:
    """루트 로거에 DBLogHandler를 붙인다. 두 번 불러도 핸들러가 중복으로 달리지 않는다
    (테스트에서 앱을 여러 번 만들면 같은 로그가 여러 행으로 들어가기 때문)."""
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, DBLogHandler):
            return h
    handler = DBLogHandler()
    handler.setLevel(level)
    root.addHandler(handler)
    # 루트 로거 자체가 WARNING보다 높게 잡혀 있으면 핸들러까지 레코드가 오지 않는다.
    if root.level > level or root.level == logging.NOTSET:
        root.setLevel(level)
    return handler
