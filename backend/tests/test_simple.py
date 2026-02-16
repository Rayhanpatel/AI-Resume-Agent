"""
Health check tests.
Uses an isolated FastAPI app with just the health router — no lifespan,
no env vars, no external dependencies needed.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.routes.health import router


@pytest.fixture()
def client():
    """Minimal test app with just the health router."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    with TestClient(app) as c:
        yield c


def test_health_returns_200(client):
    """Health endpoint returns 200 with status and services dict."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "services" in data


def test_health_services_default_false(client):
    """With no services initialized, all should report False."""
    data = client.get("/api/v1/health").json()
    for service, status in data["services"].items():
        assert status is False, f"{service} should be False when not initialized"
