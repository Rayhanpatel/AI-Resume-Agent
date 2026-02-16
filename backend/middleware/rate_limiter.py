"""
Rate Limiter Middleware
Prevents abuse with per-IP rate limiting
"""
from fastapi import Request, HTTPException
from collections import defaultdict
import time
from typing import Dict, List


class RateLimiter:
    """Simple in-memory rate limiter"""
    
    def __init__(self, requests_per_minute: int = 10):
        self.rpm = requests_per_minute
        self.requests: Dict[str, List[float]] = defaultdict(list)
    
    def check(self, client_ip: str) -> bool:
        """
        Check if request should be allowed.
        Returns True if allowed, raises HTTPException if rate limited.
        """
        now = time.time()
        minute_ago = now - 60
        
        # Clean old requests
        self.requests[client_ip] = [
            t for t in self.requests[client_ip] if t > minute_ago
        ]
        
        # Periodic cleanup of stale IPs (every 100 checks)
        if not hasattr(self, '_check_count'):
            self._check_count = 0
        self._check_count += 1
            
        if self._check_count >= 100:
            self._check_count = 0
            # Create list of IPs to remove to avoid runtime error during iteration
            empty_ips = [ip for ip, times in self.requests.items() if not times]
            for ip in empty_ips:
                del self.requests[ip]
        
        # Check rate limit
        if len(self.requests[client_ip]) >= self.rpm:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "Rate limit exceeded",
                    "message": "Too many requests. Please wait a minute and try again.",
                    "retry_after_seconds": 60
                }
            )
        
        # Record this request
        self.requests[client_ip].append(now)
        return True
    
    def get_client_ip(self, request: Request) -> str:
        """Extract client IP from request"""
        # Check Cloudflare header FIRST (most reliable when behind CF)
        real_ip = request.headers.get("cf-connecting-ip")
        if real_ip:
            return real_ip
        
        # Check for forwarded IP (behind other proxies/CDN)
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        
        # Fallback to direct client
        return request.client.host if request.client else "unknown"

# Lazy-initialized global instance (uses config)
_rate_limiter = None

def get_rate_limiter() -> RateLimiter:
    """Get rate limiter instance, lazily initialized with config"""
    global _rate_limiter
    if _rate_limiter is None:
        from core.config import get_settings
        settings = get_settings()
        _rate_limiter = RateLimiter(requests_per_minute=settings.rate_limit_rpm)
    return _rate_limiter
