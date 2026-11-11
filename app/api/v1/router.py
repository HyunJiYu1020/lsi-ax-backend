from fastapi import APIRouter

from app.api.v1.endpoints import account, chat, cost, fx, health, overseas, plan, system, version

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(account.router, tags=["accounts"])
api_router.include_router(fx.router, tags=["fx"])
api_router.include_router(plan.router, tags=["management-plan"])
api_router.include_router(version.router, tags=["version"])
api_router.include_router(cost.router, tags=["cost-entries"])
api_router.include_router(overseas.router, tags=["overseas"])
api_router.include_router(system.router, tags=["system"])
api_router.include_router(chat.router, tags=["chat"])
