"""
Quotas that survive restarts.

The per-IP rate limiter is in memory and resets on every deploy. These limits are
counted from the events table in Supabase instead: messages per session, messages
and sessions per IP per UTC day, and one global daily token budget that caps what
the whole bot can spend in a day. Without Supabase, or when it stops answering,
they fall back to an in-memory count, which is better than nothing and is logged as such.

IPs are stored as a salted hash, never raw.
"""
import hashlib
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from core import telemetry
from core.timeout import safe_timeout

logger = logging.getLogger(__name__)


def hash_ip(ip: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:24]


def _day_start() -> str:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


class QuotaExceeded(Exception):
    def __init__(self, scope: str, message: str):
        super().__init__(message)
        self.scope = scope
        self.message = message


class QuotaService:
    def __init__(self, supabase, *, messages_per_session: int, messages_per_ip_per_day: int,
                 sessions_per_ip_per_day: int, daily_token_budget: int, salt: str,
                 mcp_daily_token_budget: int = 0):
        self.supabase = supabase
        self.messages_per_session = messages_per_session
        self.messages_per_ip_per_day = messages_per_ip_per_day
        self.sessions_per_ip_per_day = sessions_per_ip_per_day
        self.daily_token_budget = daily_token_budget
        self.mcp_daily_token_budget = mcp_daily_token_budget  # 0 = no separate MCP budget
        self.salt = salt
        self._memory = defaultdict(int)  # fallback counters, keyed by (scope, key, day)
        if not (supabase and supabase.enabled):
            logger.warning("Supabase unavailable: quotas are in memory and reset on restart")

    @property
    def persistent(self) -> bool:
        return bool(self.supabase and self.supabase.enabled)

    async def _count(self, scope: str, key: str, *, event_type: Optional[str] = None,
                     session_id: Optional[str] = None, ip_hash: Optional[str] = None,
                     caller: Optional[str] = None) -> int:
        """Count today's events matching the filters, or read the memory fallback"""
        if not self.persistent:
            return self._memory[(scope, key, _day_start())]
        try:
            query = self.supabase.client.table("events").select("id", count="exact").gte("created_at", _day_start())
            if event_type:
                query = query.eq("event_type", event_type)
            if session_id:
                query = query.eq("session_id", session_id)
            if ip_hash:
                query = query.eq("metadata->>ip_hash", ip_hash)
            if caller:
                query = query.eq("metadata->>caller", caller)
            response = await safe_timeout(query.execute(), timeout=4.0, label=f"quota.{scope}")
            if response is None:
                return self._memory[(scope, key, _day_start())]
            return response.count if response.count is not None else len(response.data)
        except Exception as e:
            logger.warning(f"Quota count failed ({scope}), using the in-memory count: {e}")
            return self._memory[(scope, key, _day_start())]

    async def _tokens_today(self, event_type: Optional[str] = None) -> int:
        """Tokens spent today, by everything or by one event type (e.g. "mcp_tokens")"""
        key = ("tokens", event_type or "all", _day_start())
        if not self.persistent:
            return self._memory[key]
        try:
            query = self.supabase.client.table("events").select("tokens_in,tokens_out").gte("created_at", _day_start())
            if event_type:
                query = query.eq("event_type", event_type)
            response = await safe_timeout(query.execute(), timeout=4.0, label="quota.tokens")
            if response is None:
                return self._memory[key]
            return sum((row.get("tokens_in") or 0) + (row.get("tokens_out") or 0) for row in response.data)
        except Exception as e:
            logger.warning(f"Quota token sum failed, using the in-memory count: {e}")
            return self._memory[key]

    def record_memory(self, scope: str, key: str, amount: int = 1):
        """
        Fallback bookkeeping, kept even when Supabase is configured: if the database
        stops answering, the limits fall back to these counts instead of to zero.
        (Oct 2026: the Supabase project disappeared and every count silently read 0.)
        """
        self._memory[(scope, key, _day_start())] += amount

    @staticmethod
    def _reject(scope: str, message: str) -> QuotaExceeded:
        telemetry.record_quota_rejection(scope)
        return QuotaExceeded(scope, message)

    async def check_new_session(self, ip_hash: str):
        if await self._count("sessions_ip", ip_hash, event_type="session_start", ip_hash=ip_hash) >= self.sessions_per_ip_per_day:
            raise self._reject("ip_sessions", "You've started a lot of chats today. Please come back tomorrow, or email rayhanbp@umd.edu.")

    async def check_mcp(self, caller: str, ip_hash: str):
        """The chat limits, plus a separate daily MCP budget so MCP traffic can't use up the chat's"""
        await self.check_message(caller, ip_hash, mcp_caller=caller)
        if self.mcp_daily_token_budget and await self._tokens_today("mcp_tokens") >= self.mcp_daily_token_budget:
            raise self._reject("mcp_budget", "Today's budget for MCP calls is used up. Try again tomorrow, or use the chat at chat.rayhanpatel.com.")

    async def check_message(self, session_id: str, ip_hash: str, *, mcp_caller: Optional[str] = None):
        # An MCP caller has no chat session row; its events carry the caller in metadata
        per_caller = (await self._count("messages_session", mcp_caller, event_type="mcp", caller=mcp_caller) if mcp_caller
                      else await self._count("messages_session", session_id, session_id=session_id))
        if per_caller >= self.messages_per_session:
            raise self._reject("session", "This chat has reached its message limit. Start a new chat, or email rayhanbp@umd.edu.")
        if await self._count("messages_ip", ip_hash, ip_hash=ip_hash) >= self.messages_per_ip_per_day:
            raise self._reject("ip", "You've reached today's message limit. Please come back tomorrow, or email rayhanbp@umd.edu.")
        if await self._tokens_today() >= self.daily_token_budget:
            logger.error("Daily token budget reached; refusing new messages until tomorrow UTC")
            raise self._reject("budget", "The agent has reached its daily budget. Please try again tomorrow, or email rayhanbp@umd.edu.")
