"""
TTL Cache for Session Storage
Prevents memory leaks by expiring old sessions
"""
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import threading


class TTLCache(OrderedDict):
    """
    Thread-safe TTL (Time-To-Live) cache with max size limit.
    
    Sessions expire after `ttl_seconds` and the cache is bounded
    to `max_size` entries to prevent memory leaks.
    
    IMPORTANT: All internal methods use super() calls to avoid
    deadlocks and infinite recursion from overridden dunder methods.
    """
    
    def __init__(self, ttl_seconds: int = 86400, max_size: int = 10000):
        """
        Args:
            ttl_seconds: Time-to-live for entries (default: 24 hours)
            max_size: Maximum number of entries (default: 10,000)
        """
        super().__init__()
        self.ttl = timedelta(seconds=ttl_seconds)
        self.max_size = max_size
        self._lock = threading.Lock()
    
    def _is_expired(self, item: dict) -> bool:
        """Check if a raw cache item is expired"""
        return datetime.now(timezone.utc) - item["created"] > self.ttl
    
    def __setitem__(self, key: str, value: Any) -> None:
        """Store a value with automatic timestamp"""
        with self._lock:
            # Remove if exists to update position
            if super().__contains__(key):
                super().__delitem__(key)
            
            # Store with timestamp
            super().__setitem__(key, {
                "value": value,
                "created": datetime.now(timezone.utc)
            })
            
            # Evict oldest if over capacity
            while len(self) > self.max_size:
                self.popitem(last=False)
    
    def __getitem__(self, key: str) -> Any:
        """Get value, checking expiration"""
        with self._lock:
            if not super().__contains__(key):
                raise KeyError(key)
            
            item = super().__getitem__(key)
            
            # Check if expired
            if self._is_expired(item):
                super().__delitem__(key)
                raise KeyError(key)
            
            return item["value"]
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get value with default, checking expiration"""
        try:
            return self[key]
        except KeyError:
            return default
    
    def __contains__(self, key: str) -> bool:
        """Check if key exists and is not expired"""
        with self._lock:
            if not super().__contains__(key):
                return False
            item = super().__getitem__(key)
            if self._is_expired(item):
                super().__delitem__(key)
                return False
            return True
    
    def cleanup(self) -> int:
        """
        Remove all expired entries.
        
        Returns:
            Number of entries removed
        """
        with self._lock:
            now = datetime.now(timezone.utc)
            expired = [
                key for key, item in list(super().items())
                if now - item["created"] > self.ttl
            ]
            for key in expired:
                super().__delitem__(key)
            return len(expired)
    
    def stats(self) -> dict:
        """Get cache statistics"""
        with self._lock:
            return {
                "size": len(self),
                "max_size": self.max_size,
                "ttl_seconds": self.ttl.total_seconds()
            }
