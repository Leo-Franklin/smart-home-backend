import pytest
from unittest.mock import MagicMock, patch
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.config import get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """Clear lru_cache before and after each test to ensure monkeypatch env vars take effect."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def test_env(monkeypatch):
    """Set required env vars for tests and clear cache."""
    monkeypatch.setenv("JWT_SECRET_KEY", "test_secret_key_that_is_at_least_32_characters_long")
    monkeypatch.setenv("ADMIN_PASSWORD", "testpassword_for_ci_only")
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "http://localhost:5173")
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_health():
    mock_nas = MagicMock()
    mock_nas.check_writable.return_value = True
    app.state.nas_syncer = mock_nas
    with patch("app.routers.system._check_ffmpeg", return_value=True):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "uptime_seconds" in data


@pytest.mark.asyncio
async def test_login_success(test_env):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/auth/login",
            data={"username": "admin", "password": "testpassword_for_ci_only"},
        )
    assert resp.status_code == 200
    assert "access_token" in resp.json()


@pytest.mark.asyncio
async def test_login_fail():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/auth/login",
            data={"username": "admin", "password": "wrong"},
        )
    assert resp.status_code == 401
