"""
Vertex AI Authentication Service
Handles OAuth2 token generation and refresh for Vertex AI API calls
"""
import json
import logging
import time
import asyncio
from typing import Optional
from google.oauth2 import service_account
import google.auth.transport.requests

logger = logging.getLogger(__name__)

# Vertex AI API scopes
VERTEX_AI_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


class VertexAuthService:
    """Manages Vertex AI authentication with automatic token refresh"""
    
    def __init__(
        self,
        credentials_json: Optional[str] = None,
        project_id: Optional[str] = None,
        location: str = "us-central1"
    ):
        self.project_id = project_id
        self.location = location
        self.credentials = None
        self._token: Optional[str] = None
        self._token_expiry: float = 0
        self.enabled = False
        
        if credentials_json and project_id:
            try:
                # Parse JSON credentials
                creds_dict = json.loads(credentials_json)
                self.credentials = service_account.Credentials.from_service_account_info(
                    creds_dict,
                    scopes=VERTEX_AI_SCOPES
                )
                self.enabled = True
                logger.info(f"Vertex AI auth initialized for project: {project_id}")
            except Exception as e:
                logger.warning(f"Vertex AI auth init failed: {e}")
                self.enabled = False
        else:
            logger.info("Vertex AI credentials not provided - using AI Studio fallback")
    
    def get_base_url(self, model: str = "gemini-2.0-flash") -> str:
        """Get the Vertex AI API base URL for a model"""
        return (
            f"https://{self.location}-aiplatform.googleapis.com/v1/"
            f"projects/{self.project_id}/locations/{self.location}/"
            f"publishers/google/models/{model}"
        )
    
    def get_streaming_url(self, model: str = "gemini-2.0-flash") -> str:
        """Get the streaming endpoint URL"""
        return f"{self.get_base_url(model)}:streamGenerateContent?alt=sse"
    
    def get_generate_url(self, model: str = "gemini-2.0-flash") -> str:
        """Get the non-streaming endpoint URL"""
        return f"{self.get_base_url(model)}:generateContent"
    
    async def get_access_token(self) -> Optional[str]:
        """Get a valid access token, refreshing if needed"""
        if not self.enabled:
            return None
        
        # Check if token is still valid (with 5 min buffer)
        if self._token and time.time() < (self._token_expiry - 300):
            return self._token
        
        try:
            # Refresh the token (blocking call - run in thread to avoid blocking event loop)
            request = google.auth.transport.requests.Request()
            await asyncio.to_thread(self.credentials.refresh, request)
            
            self._token = self.credentials.token
            # Token expires in ~1 hour, store expiry time
            if self.credentials.expiry:
                self._token_expiry = self.credentials.expiry.timestamp()
            else:
                # Default to 55 minutes from now
                self._token_expiry = time.time() + 3300
            
            logger.debug("Vertex AI access token refreshed")
            return self._token
            
        except Exception as e:
            logger.error(f"Failed to refresh Vertex AI token: {e}")
            return None
    
    async def get_headers(self) -> dict:
        """Get headers for Vertex AI API requests"""
        token = await self.get_access_token()
        if token:
            return {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
        return {}


# Global instance (initialized in main.py)
_vertex_auth: Optional[VertexAuthService] = None


def init_vertex_auth(
    credentials_json: Optional[str],
    project_id: Optional[str],
    location: str = "us-central1"
) -> VertexAuthService:
    """Initialize the global Vertex AI auth service"""
    global _vertex_auth
    _vertex_auth = VertexAuthService(credentials_json, project_id, location)
    return _vertex_auth


def get_vertex_auth() -> Optional[VertexAuthService]:
    """Get the global Vertex AI auth service"""
    return _vertex_auth
