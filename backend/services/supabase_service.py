"""
Supabase Service
Persistent storage for sessions and events using async supabase-py client.
"""
import asyncio
import logging
from core.timeout import safe_timeout
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from supabase import acreate_client, AsyncClient

logger = logging.getLogger(__name__)

class SupabaseService:
    """Async Service Wrapper for Sync Supabase Client"""
    
    client: AsyncClient | None = None
    enabled: bool = False
    
    @classmethod
    async def create(cls, url: str, key: str) -> "SupabaseService":
        """Async Factory Pattern"""
        instance = cls()
        instance.enabled = bool(url and key)
        if not instance.enabled:
            logger.warning("Supabase credentials not provided - running in memory-only mode")
            return instance

        try:
            instance.client = await acreate_client(url, key)
            logger.info("Supabase Async Service initialized")
        except Exception as e:
            logger.error(f"Failed to init Supabase client: {e}")
            instance.enabled = False
            instance.client = None
        
        return instance

    async def close(self):
        """No-op for sync client cleanup"""
        pass

    # =========================================================================
    # Session Management
    # =========================================================================
    
    async def create_session(
        self, 
        session_id: str, 
        user_name: str, 
        company: Optional[str] = None
    ) -> Optional[Dict]:
        """Create a new chat session"""
        if not self.enabled:
            return {"id": session_id, "user_name": user_name, "company": company}
        
        try:
            payload = {
                "id": session_id,
                "user_name": user_name,
                "company": company,
                "preferences": {},
                "created_at": datetime.now(timezone.utc).isoformat(),
                "last_active": datetime.now(timezone.utc).isoformat()
            }
            response = await safe_timeout(
                self.client.table("sessions").insert(payload).execute(),
                timeout=5.0, label="supabase.create_session"
            )
            if response is None:
                return None
            data = response.data
            logger.info(f"Session created: {session_id}")
            return data[0] if data else None
        except Exception as e:
            logger.warning(f"Failed to create session: {e}")
            return None
    
    async def get_session(self, session_id: str) -> Optional[Dict]:
        """Get session by ID"""
        if not self.enabled:
            return None
        
        try:
            response = await safe_timeout(
                self.client.table("sessions").select("*").eq("id", session_id).execute(),
                timeout=5.0, label="supabase.get_session"
            )
            if response is None:
                return None
            data = response.data
            return data[0] if data else None
        except Exception as e:
            logger.warning(f"Failed to get session: {e}")
            return None
    
    async def update_session_activity(self, session_id: str) -> bool:
        """Update the last_active timestamp"""
        if not self.enabled:
            return True
            
        try:
            await safe_timeout(
                self.client.table("sessions").update(
                    {"last_active": datetime.now(timezone.utc).isoformat()}
                ).eq("id", session_id).execute(),
                timeout=5.0, label="supabase.update_activity"
            )
            return True
        except Exception as e:
            logger.warning(f"Failed to update session activity: {e}")
            return False
    
    async def update_preferences(self, session_id: str, preferences: Dict) -> bool:
        """Update user preferences"""
        if not self.enabled:
            return True
            
        try:
            # Fetch current
            response = await safe_timeout(
                self.client.table("sessions").select("preferences").eq("id", session_id).execute(),
                timeout=5.0, label="supabase.get_preferences"
            )
            if not response or not response.data:
                return False
            
            current_prefs = response.data[0].get("preferences", {}) or {}
            current_prefs.update(preferences)
            
            await safe_timeout(
                self.client.table("sessions").update(
                    {"preferences": current_prefs}
                ).eq("id", session_id).execute(),
                timeout=5.0, label="supabase.update_preferences"
            )
            return True
        except Exception as e:
            logger.warning(f"Failed to update preferences: {e}")
            return False
    
    # =========================================================================
    # Event Logging
    # =========================================================================
    
    async def log_event(
        self,
        session_id: str,
        event_type: str,
        intent: Optional[str] = None,
        tokens_in: Optional[int] = None,
        tokens_out: Optional[int] = None,
        latency_ms: Optional[int] = None,
        metadata: Optional[Dict] = None
    ) -> bool:
        """Log an event for analytics"""
        if not self.enabled:
            return True
        
        try:
            payload = {
                "session_id": session_id,
                "event_type": event_type,
                "intent": intent,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "latency_ms": latency_ms,
                "metadata": metadata or {},
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            await safe_timeout(
                self.client.table("events").insert(payload).execute(),
                timeout=5.0, label="supabase.log_event"
            )
            return True
        except Exception as e:
            logger.warning(f"Failed to log event: {e}")
            return False

    # =========================================================================
    # Session Queries
    # =========================================================================
    
    async def get_recent_sessions(self, limit: int = 50) -> List[Dict]:
        """Get recent sessions ordered by last activity"""
        if not self.enabled:
            return []
        
        try:
            response = await safe_timeout(
                self.client.table("sessions").select("*").order(
                    "last_active", desc=True
                ).limit(limit).execute(),
                timeout=10.0, label="supabase.get_recent_sessions"
            )
            if response is None:
                return []
            return response.data if response.data else []
        except Exception as e:
            logger.warning(f"Failed to get recent sessions: {e}")
            return []

    # =========================================================================
    # Analytics
    # =========================================================================
    
    async def get_analytics(self, days: int = 30) -> Dict[str, Any]:
        """Get analytics"""
        if not self.enabled:
            return {"error": "Database not available", "enabled": False}
        
        try:
            since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            
            # Count sessions
            sess_resp = await safe_timeout(
                self.client.table("sessions").select("id", count="exact").gte("created_at", since).execute(),
                timeout=10.0, label="supabase.analytics_sessions"
            )
            if sess_resp is None:
                return {"error": "Query timed out", "enabled": True}
            total_sessions = sess_resp.count if sess_resp.count is not None else len(sess_resp.data)

            # Get events
            events_resp = await safe_timeout(
                self.client.table("events").select("*").gte("created_at", since).execute(),
                timeout=10.0, label="supabase.analytics_events"
            )
            if events_resp is None:
                return {"error": "Query timed out", "enabled": True}
            events_data = events_resp.data
            
            total_events = len(events_data)
            total_tokens_in = sum(e.get("tokens_in") or 0 for e in events_data)
            total_tokens_out = sum(e.get("tokens_out") or 0 for e in events_data)
            latencies = [e.get("latency_ms") for e in events_data if e.get("latency_ms")]
            avg_latency = sum(latencies) / len(latencies) if latencies else 0
            
            intents = {}
            for e in events_data:
                intent = e.get("intent")
                if intent:
                    intents[intent] = intents.get(intent, 0) + 1
            
            return {
                "period_days": days,
                "total_sessions": total_sessions,
                "total_events": total_events,
                "total_tokens_in": total_tokens_in,
                "total_tokens_out": total_tokens_out,
                "total_tokens": total_tokens_in + total_tokens_out,
                "avg_latency_ms": round(avg_latency, 2),
                "intent_breakdown": intents,
                "enabled": True
            }
        except Exception as e:
            logger.error(f"Failed to get analytics: {e}")
            return {"error": str(e), "enabled": True}
