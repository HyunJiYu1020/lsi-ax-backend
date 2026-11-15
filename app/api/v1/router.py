from fastapi import APIRouter

from app.api.v1.endpoints import (
    account,
    chat,
    cost,
    fx,
    health,
    overseas,
    overseas_aggregate,
    overseas_close,
    overseas_variance,
    plan,
    system,
    version,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(account.router, tags=["accounts"])
api_router.include_router(fx.router, tags=["fx"])
api_router.include_router(plan.router, tags=["management-plan"])
api_router.include_router(version.router, tags=["version"])
api_router.include_router(cost.router, tags=["cost-entries"])
api_router.include_router(overseas.router, tags=["overseas"])
# 증감분석 화면 전용(비교 기준·증감 표·증감사유·증감 동인·실적 1차/2차). overseas.router 뒤에
# 붙여 같은 "/overseas/..." 경로 공간을 쓰면서 파일만 분리한다.
api_router.include_router(overseas_variance.router, tags=["overseas"])
# 집계 화면 전용(CP 계약 조회 · 확정 사전점검 · 인건비율). 같은 "/overseas/..." 경로 공간이고
# 파일만 화면 단위로 가른다 — overseas.py는 이 모듈을 모른다(의존 방향 한쪽).
api_router.include_router(overseas_aggregate.router, tags=["overseas"])
# 실적결산 · 전표매핑("/close/..."). 편성 쪽과 경로 공간을 따로 쓴다 — 다루는 자료가 ERP 전표이고
# 19개 계정이 같이 쓸 자리라서, 해외연구소 전용 경로("/overseas/...") 아래에 두면 다음 계정을
# 붙일 때 경로를 옮겨야 한다.
api_router.include_router(overseas_close.router, tags=["close"])
api_router.include_router(system.router, tags=["system"])
api_router.include_router(chat.router, tags=["chat"])
