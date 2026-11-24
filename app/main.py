import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.accounts.overseas import cost_groups
from app.api.v1.router import api_router
from app.db.session import SessionLocal
from app.shared.core.config import settings
from app.shared.core.logging_db import request_ctx, setup_db_logging

@asynccontextmanager
async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
    """기동 때 해외연구소 계정 정의(이름·계층·행 순서)를 공통코드에서 읽어 둔다.

    계정 목록의 원본은 common_codes(group_code='OVERSEAS_CATEGORY')다 — 코드에 박아 두면 계정을
    하나 추가·중지할 때마다 배포가 필요하고, 화면(공통코드를 직접 받아 쓴다)과 서버가 서로 다른
    계정 목록으로 집계·검증하게 된다. 못 읽으면 모듈의 초기값으로 계속 돈다(cost_groups 참고).
    """
    with SessionLocal() as db:
        cost_groups.reload_from_db(db)
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

#: 루트 로거에 DB 핸들러를 붙인다 — 이 뒤로는 코드 어디서든 logger.warning()/error()를 부르면
#: error_logs에 행이 하나 남는다(log4j 방식). 핸들러 자체가 DB 쓰기 실패를 삼키므로,
#: DB가 죽어 있어도 API는 정상 동작한다.
setup_db_logging()

logger = logging.getLogger("app.request")

#: 로그로 남길 4xx 상태코드 하한. 404는 양이 많고 대부분 단순 오타·크롤러라 기본적으로 뺀다
#: (남기고 싶으면 이 집합에서 지우면 된다).
_SKIP_4XX = {404}

#: 요청 문맥을 담아 두는 scope 키. 500 핸들러가 미들웨어 바깥에서 돌기 때문에 필요하다.
_LOG_CTX = "_slsi_log_ctx"

#: 로그에 남길 요청 본문 상한(바이트). 파일 업로드가 통째로 들어오는 걸 막는다.
_BODY_MAX = 8192


@app.middleware("http")
async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
    """요청 1건의 문맥(추적 id·IP·경로·쿼리)을 ContextVar에 담아 둔다. 로그를 찍는 쪽이 요청
    정보를 몰라도 DBLogHandler가 같은 행에 채워 넣을 수 있게 하는 장치다.

    같은 dict를 요청 scope에도 걸어 둔다 — **미처리 500은 이 미들웨어 바깥**(Starlette의
    ServerErrorMiddleware)에서 처리되기 때문이다. 그래서 아래 finally가 ContextVar를 되돌린
    뒤에야 핸들러가 돌고, ContextVar만 믿으면 500 로그의 요청 정보가 전부 비어 버린다
    (실제로 그렇게 비어서 찾아낸 자리다). scope는 같은 요청 내내 같은 객체라 살아남는다.

    요청 본문도 여기서 같이 읽어 둔다. 400 하나를 재현하려면 "어떤 값을 보냈나"가 거의 항상
    필요한데, 예외 핸들러에서 읽으려 하면 그때는 receive가 이미 소진돼 있어 500에서는 빈 값이
    나온다. 미들웨어에서 읽는 건 안전하다 — Starlette의 BaseHTTPMiddleware가 dispatch에
    넘기는 request는 _CachedRequest라, 여기서 읽은 본문을 아래 엔드포인트에 그대로 재생해 준다
    (그래서 "미들웨어에서 body를 읽으면 요청이 멈춘다"는 옛 제약은 이제 해당하지 않는다)."""
    started = time.monotonic()
    ctx = {
            "trace_id": uuid.uuid4().hex[:32],
            # SSO 연동 전까지 비어 있다 — 붙으면 여기서 사번·세션을 채우면 된다.
            "user_id": None,
            "session_id": None,
            "client_ip": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
            "api_url": request.url.path,
            "http_method": request.method,
            "params": await _request_params(request),
            # 예외 핸들러가 로그를 찍는 시점은 call_next가 돌아오기 "전"이라, 소요시간을
            # 여기서 계산해 넣으면 로그 행에는 못 들어간다. 시작 시각만 넘기고 계산은
            # DBLogHandler가 로그를 쓰는 순간에 한다.
            "_started": started,
    }
    request.scope[_LOG_CTX] = ctx
    token = request_ctx.set(ctx)
    try:
        return await call_next(request)
    finally:
        request_ctx.reset(token)


def _restore_ctx(request: Request) -> None:
    """예외 핸들러가 로그를 찍기 직전에 요청 문맥을 되살린다 — 500 핸들러는 ContextVar가 이미
    리셋된 뒤에 돌기 때문이다(위 미들웨어 docstring 참고)."""
    request_ctx.set(request.scope.get(_LOG_CTX) or {})


async def _request_params(request: Request) -> str | None:
    """로그의 params 칸에 넣을 값: 쿼리스트링 + 요청 본문. 둘 다 없으면 None.

    본문이 큰 업로드일 수 있으니 상한을 둔다(컬럼은 Text지만, 엑셀 한 장이 통째로 들어오면
    로그 테이블이 업로드 저장소가 된다)."""
    query = str(request.url.query) or None
    try:
        raw = await request.body()
    except Exception:
        return query
    if not raw:
        return query
    body = raw[:_BODY_MAX].decode("utf-8", "replace")
    if len(raw) > _BODY_MAX:
        body += f"... (전체 {len(raw)}바이트 중 앞부분만)"
    return f"{query}\n{body}" if query else body


@app.exception_handler(HTTPException)
async def log_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
    """의도적으로 올린 4xx/5xx(HTTPException)를 WARNING으로 남긴다.

    미처리 500만 기록하면 서버가 또박또박 거절한 400이 로그에 남지 않는다. 그러면 프론트가 잘못된
    값을 보내 저장이 계속 거부되는 상황을 아무도 알아채지 못한다 — 화면은 실패를 조용히 삼키고
    사용자는 저장된 줄 안다."""
    if exc.status_code >= 400 and exc.status_code not in _SKIP_4XX:
        _restore_ctx(request)
        logger.warning(
            "%s %s → %s: %s", request.method, request.url.path, exc.status_code, exc.detail,
            extra={"http_status": exc.status_code, "error_code": type(exc).__name__},
        )
    return await http_exception_handler(request, exc)  # type: ignore[return-value]


@app.exception_handler(Exception)
async def log_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """여기까지 온 건 전부 예상 못 한 500이다(HTTPException은 위 핸들러가 먼저 받는다).
    스택트레이스까지 ERROR로 남기고 500을 반환한다."""
    _restore_ctx(request)
    logger.error(
        "%s %s → 500: %s", request.method, request.url.path, exc,
        exc_info=(type(exc), exc, exc.__traceback__),
        extra={"http_status": 500},
    )
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    # 로컬 개발 중엔 정적 프론트(frontend-html)를 아무 포트로나 띄워서 열어볼 수 있으니
    # localhost/127.0.0.1의 모든 포트를 허용한다. 운영 배포 시엔 settings.cors_origins에
    # 실제 도메인을 명시하고 이 줄은 빼는 것을 권장.
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")
