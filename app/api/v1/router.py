from fastapi import APIRouter

from app.api.v1.endpoints import chat, fx, health, overseas, plan, system

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(fx.router, tags=["fx"])
api_router.include_router(plan.router, tags=["management-plan"])
api_router.include_router(overseas.router, tags=["overseas"])
api_router.include_router(system.router, tags=["system"])
api_router.include_router(chat.router, tags=["chat"])
