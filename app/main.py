import traceback as tb_module

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import settings
from app.db.session import SessionLocal
from app.models import ErrorLog

app = FastAPI(title=settings.app_name)


@app.exception_handler(Exception)
async def log_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """HTTPException(의도적으로 4xx 등을 raise한 것)은 FastAPI 기본 핸들러가 먼저 처리하므로
    여기까지 오는 건 전부 예상 못 한 500이다. DB에 기록하고 500을 반환한다."""
    db = SessionLocal()
    try:
        db.add(
            ErrorLog(
                method=request.method,
                path=request.url.path,
                status_code=500,
                error_type=type(exc).__name__,
                message=str(exc),
                traceback="".join(tb_module.format_exception(type(exc), exc, exc.__traceback__)),
            )
        )
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
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
