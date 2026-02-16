"""
Configuration management for Rayhan's AI Resume Chatbot
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # Pydantic v2 config
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"
    )
    
    # Gemini API (AI Studio fallback)
    google_api_key: str
    
    # Vertex AI (Production)
    google_cloud_project: Optional[str] = None
    google_cloud_location: str = "us-central1"
    google_application_credentials_json: Optional[str] = None
    
    # Supabase
    supabase_url: Optional[str] = None
    supabase_key: Optional[str] = None
    
    # Mem0 (optional — memory disabled if absent)
    mem0_api_key: Optional[str] = None
    admin_api_key: Optional[str] = None
    
    # Langfuse (Optional)
    langfuse_public_key: Optional[str] = None
    langfuse_secret_key: Optional[str] = None
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com", 
        validation_alias="LANGFUSE_HOST"
    )
    
    # Integrations
    google_sheet_webhook: Optional[str] = None
    turnstile_secret_key: Optional[str] = None
    
    # App Config
    environment: str = "development"
    frontend_url: str = "http://localhost:5173"
    rate_limit_rpm: int = 10


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    try:
        return Settings()
    except Exception as e:
        if "google_api_key" in str(e):
            raise SystemExit(
                "\n❌ GOOGLE_API_KEY is required!\n"
                "   1. Get a free key at: https://aistudio.google.com/apikey\n"
                "   2. Copy backend/.env.example to backend/.env\n"
                "   3. Paste your key after GOOGLE_API_KEY=\n"
            )
        raise
