from fastapi import APIRouter
from api.routes import health, session, chat, admin

api_router = APIRouter()

api_router.include_router(health.router, tags=["health"])
api_router.include_router(session.router, tags=["session"])
api_router.include_router(chat.router, tags=["chat"])
api_router.include_router(admin.router, tags=["admin"])
