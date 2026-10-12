from __future__ import annotations

import asyncio
import base64

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from wb_control_center.dashboard_auth import DashboardBasicAuthMiddleware, valid_basic_authorization


def test_basic_authorization_requires_exact_configured_credentials():
    encoded = base64.b64encode("seller:secret".encode()).decode()
    assert valid_basic_authorization(f"Basic {encoded}", "seller", "secret")
    assert not valid_basic_authorization(f"Basic {encoded}", "seller", "wrong")
    assert not valid_basic_authorization(f"Basic {encoded}", "", "secret")
    assert not valid_basic_authorization("Bearer token", "seller", "secret")


def test_public_dashboard_gate_challenges_bad_credentials_and_keeps_health_available():
    app = FastAPI()
    app.add_middleware(DashboardBasicAuthMiddleware, required=True, username="seller", password="secret")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/private")
    async def private():
        return {"data": "private"}

    async def check():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            anonymous = await client.get("/private")
            healthy = await client.get("/health")
            encoded = base64.b64encode("seller:secret".encode()).decode()
            authenticated = await client.get("/private", headers={"Authorization": f"Basic {encoded}"})
        return anonymous, healthy, authenticated

    anonymous, healthy, authenticated = asyncio.run(check())
    assert anonymous.status_code == 401
    assert "Basic" in anonymous.headers["www-authenticate"]
    assert healthy.status_code == 200
    assert authenticated.status_code == 200


def test_required_gate_fails_closed_if_credentials_are_missing():
    app = FastAPI()
    app.add_middleware(DashboardBasicAuthMiddleware, required=True, username="", password="")

    @app.get("/private")
    async def private():
        return {"data": "private"}

    async def check():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/private")

    response = asyncio.run(check())
    assert response.status_code == 503
