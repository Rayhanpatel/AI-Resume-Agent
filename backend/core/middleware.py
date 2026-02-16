import uuid
from starlette.types import ASGIApp, Receive, Scope, Send


class RequestIDMiddleware:
    """Pure ASGI middleware for request ID injection.
    
    Avoids BaseHTTPMiddleware which causes POST request body hangs
    in newer Starlette versions.
    """
    
    def __init__(self, app: ASGIApp):
        self.app = app
    
    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        # Extract or generate request ID
        headers = dict(scope.get("headers", []))
        request_id = headers.get(b"x-request-id", b"").decode() or str(uuid.uuid4())
        
        # Store in scope state for access in endpoints
        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["request_id"] = request_id
        
        # Intercept response to add header
        async def send_with_request_id(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message["headers"] = headers
            await send(message)
        
        await self.app(scope, receive, send_with_request_id)
