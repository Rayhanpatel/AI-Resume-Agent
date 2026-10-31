from fastapi import APIRouter
from api.routes import health, session, chat

api_router = APIRouter()

api_router.include_router(health.router, tags=["health"])
api_router.include_router(session.router, tags=["session"])
api_router.include_router(chat.router, tags=["chat"])
