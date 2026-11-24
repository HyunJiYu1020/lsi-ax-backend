"""API 라우터 등록 — 공통 엔드포인트와 계정별 엔드포인트를 한곳에서 모은다.

import 경로가 그 자체로 "공통인가 계정 전용인가"를 말해 준다(app/shared/ vs app/accounts/<계정>/).
계정을 추가할 때는 app/accounts/<계정>/endpoints/를 만들고 여기에 한 블록을 더하면 된다.
"""

from fastapi import APIRouter

from app.accounts.overseas.endpoints import aggregate as overseas_aggregate
from app.accounts.overseas.endpoints import close as overseas_close
from app.accounts.overseas.endpoints import collect as overseas_collect
from app.accounts.overseas.endpoints import variance as overseas_variance
from app.shared.endpoints import (
    account,
    chat,
    cost,
    fx,
    guideline,
    health,
    plan,
    reason,
    reference,
    sheet,
    system,
    version,
)

api_router = APIRouter()

# ---------- 전 계정 공용 ----------
api_router.include_router(health.router, tags=["health"])
api_router.include_router(account.router, tags=["accounts"])
api_router.include_router(fx.router, tags=["fx"])
api_router.include_router(plan.router, tags=["management-plan"])
api_router.include_router(version.router, tags=["version"])
api_router.include_router(reference.router, tags=["reference"])
api_router.include_router(sheet.router, tags=["sheet-rows"])
api_router.include_router(reason.router, tags=["variance-reasons"])
api_router.include_router(cost.router, tags=["cost-entries"])
api_router.include_router(system.router, tags=["system"])
api_router.include_router(guideline.router, tags=["guidelines"])
api_router.include_router(chat.router, tags=["chat"])

# ---------- 해외연구소 계정 ----------
# 네 모듈이 같은 "/overseas/..." 경로 공간을 쓰면서 화면 단위로만 파일을 가른다.
api_router.include_router(overseas_collect.router, tags=["overseas"])     # 취합·기준정보·행/열
api_router.include_router(overseas_variance.router, tags=["overseas"])    # 증감분석
api_router.include_router(overseas_aggregate.router, tags=["overseas"])   # 집계·확정 사전점검
# 실적결산 · 전표매핑만 "/close/..."로 경로 공간을 따로 쓴다 — 다루는 자료가 ERP 전표이고 19개
# 계정이 같이 쓸 자리라서, 해외연구소 전용 경로 아래에 두면 다음 계정을 붙일 때 경로를 옮겨야 한다.
api_router.include_router(overseas_close.router, tags=["close"])
