"""
Pydantic Request/Response Schemas
Validates and sanitizes all API input/output
"""
from pydantic import BaseModel, field_validator, Field
from typing import Optional, List, Dict, Any
import re
import uuid


def _strip_html(text: str) -> str:
    """Remove HTML tags from text"""
    return re.sub(r'<[^>]+>', '', text)


class SessionRequest(BaseModel):
    """Request to create a new session"""
    user_name: str = Field(..., min_length=1, max_length=100)
    company: Optional[str] = Field(None, max_length=100)
    job_posting: Optional[str] = Field(None, max_length=15000)  # Increased for extracted content
    job_url: Optional[str] = Field(None, max_length=2000)  # NEW: job posting URL
    turnstile_token: Optional[str] = Field(None, max_length=4096)  # Cloudflare Turnstile
    
    @field_validator('user_name')
    @classmethod
    def sanitize_name(cls, v: str) -> str:
        v = _strip_html(v)
        v = ' '.join(v.split())
        if not v:
            raise ValueError('Name cannot be empty')
        return v
    
    @field_validator('company')
    @classmethod
    def sanitize_company(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = _strip_html(v)
        v = ' '.join(v.split())
        return v if v else None
    
    @field_validator('job_posting')
    @classmethod
    def sanitize_job_posting(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = _strip_html(v)
        v = v.strip()
        return v if v else None
    
    @field_validator('job_url')
    @classmethod
    def validate_job_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if v and not v.lower().startswith(('http://', 'https://')):
            raise ValueError('Job URL must start with http:// or https://')
        return v if v else None


class ChatRequest(BaseModel):
    """Request to send a chat message"""
    session_id: str = Field(..., min_length=36, max_length=36)
    query: str = Field(..., min_length=1, max_length=4000)
    user_name: Optional[str] = Field(None, min_length=1, max_length=100)
    company: Optional[str] = Field(None, max_length=100)
    job_posting: Optional[str] = Field(None, max_length=10000)

    @field_validator('session_id')
    @classmethod
    def validate_session_id(cls, v: str) -> str:
        try:
            uuid.UUID(v)
        except ValueError:
            raise ValueError('Invalid session ID format')
        return v
    
    @field_validator('query')
    @classmethod
    def sanitize_query(cls, v: str) -> str:
        v = _strip_html(v)
        v = v.strip()
        if not v:
            raise ValueError('Query cannot be empty')
        return v
    
    @field_validator('user_name')
    @classmethod
    def sanitize_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = _strip_html(v)
        v = ' '.join(v.split())
        return v if v else None
    
    @field_validator('company')
    @classmethod
    def sanitize_company(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = _strip_html(v)
        v = ' '.join(v.split())
        return v if v else None
    
    @field_validator('job_posting')
    @classmethod
    def sanitize_job_posting(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = _strip_html(v)
        v = v.strip()
        return v if v else None


class ChatResponse(BaseModel):
    """Response from chat endpoint"""
    response: str
    intent: str
    session_id: str
    latency_ms: Optional[int] = None


class SessionResponse(BaseModel):
    """Response from session creation"""
    session_id: str
    welcome_message: str
    suggested_prompts: Optional[List[str]] = None  # Dynamic prompts based on job
    job_info: Optional[Dict[str, Any]] = None  # Extracted company/role/skills
    extraction_error: Optional[str] = None  # Error message if URL extraction failed

