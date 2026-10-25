"""
Origin secret check (pure ASGI, like RequestIDMiddleware).

The API lives on Railway and used to be reachable directly, so Cloudflare's rate
limits and bot checks in front of the frontend never applied to it. Once the API
hostname is proxied through Cloudflare with a transform rule that adds
X-Origin-Secret, this refuses anything that didn't come through Cloudflare.
Health endpoints stay open for the uptime check. Off until ORIGIN_SECRET is set.
"""
import hmac
import json


class OriginSecretMiddleware:
    def __init__(self, app, secret: str | None, open_prefixes=("/api/v1/health",)):
        self.app = app
        self.secret = (secret or "").encode()
        self.open_prefixes = open_prefixes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.secret or scope["method"] == "OPTIONS":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if any(path.startswith(p) for p in self.open_prefixes):
            return await self.app(scope, receive, send)

        sent = b""
        for name, value in scope.get("headers", []):
            if name == b"x-origin-secret":
                sent = value
                break
        if hmac.compare_digest(sent, self.secret):
            return await self.app(scope, receive, send)

        body = json.dumps({"error": "Requests must come through chat.rayhanpatel.com", "code": "HTTP_403"}).encode()
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
