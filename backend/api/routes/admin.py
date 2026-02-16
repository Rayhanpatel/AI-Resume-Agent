from fastapi import APIRouter, Query, Depends
from api.deps import get_supabase_service, verify_admin
from services.supabase_service import SupabaseService

router = APIRouter(prefix="/admin", dependencies=[Depends(verify_admin)])

@router.get("/analytics")
async def get_analytics(
    days: int = Query(default=30, ge=1, le=365), 
    db: SupabaseService = Depends(get_supabase_service)
):
    """Get analytics data for the specified period"""
    return await db.get_analytics(days)

@router.get("/sessions")
async def get_recent_sessions(
    limit: int = Query(default=50, ge=1, le=500), 
    db: SupabaseService = Depends(get_supabase_service)
):
    """Get recent sessions"""
    sessions = await db.get_recent_sessions(limit)
    return {"sessions": sessions, "source": "supabase"}
