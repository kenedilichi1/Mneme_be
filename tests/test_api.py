import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_health_check():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"success": True, "message": "OK", "data": {"status": "ok"}}


@pytest.mark.asyncio
async def test_root():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/")
        assert response.status_code == 200
        assert response.json()["success"] is True


@pytest.mark.asyncio
async def test_request_otp_validation_error(client: AsyncClient):
    response = await client.post("/api/v1/auth/request-otp", json={"email": "not-an-email"})
    assert response.status_code == 422
