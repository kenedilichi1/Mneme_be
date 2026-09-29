"""Contract tests for the uniform HTTP response envelope."""

from httpx import AsyncClient


async def test_success_envelope_shape(client: AsyncClient):
    res = await client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["message"] == "OK"
    assert body["data"] == {"status": "ok"}
    assert set(body) == {"success", "message", "data"}


async def test_not_found_route_uses_error_envelope(client: AsyncClient):
    res = await client.get("/api/v1/no-such-route")
    assert res.status_code == 404
    body = res.json()
    assert body["success"] is False
    assert set(body) == {"success", "error"}
    assert body["error"]["code"] == 404
    assert isinstance(body["error"]["message"], str)
    assert "details" in body["error"]


async def test_validation_error_uses_error_envelope(client: AsyncClient):
    res = await client.post(
        "/api/v1/auth/request-otp", json={"email": "not-an-email"}
    )
    assert res.status_code == 422
    body = res.json()
    assert body["success"] is False
    assert body["error"]["code"] == 422
    assert body["error"]["message"] == "Request validation failed"
    details = body["error"]["details"]
    assert isinstance(details, list) and details
    assert {"loc", "msg", "type"} <= set(details[0])


async def test_http_exception_uses_error_envelope(client: AsyncClient):
    res = await client.get("/api/v1/auth/me")  # no token
    assert res.status_code in (401, 403)
    body = res.json()
    assert body["success"] is False
    assert body["error"]["code"] == res.status_code
    assert isinstance(body["error"]["message"], str)
