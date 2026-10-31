from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.routes import auth
from app.utils import credits


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "headers": [],
            "client": ("127.0.0.1", 54321),
            "scheme": "http",
            "server": ("testserver", 80),
            "path": "/",
        }
    )


@pytest.mark.asyncio
async def test_signup_returns_token_without_email_verification(monkeypatch):
    inserted_users = []

    class Users:
        def find_one(self, query):
            return None

        def insert_one(self, document):
            inserted_users.append(document)

    monkeypatch.setattr(auth.pool, "db", SimpleNamespace(users=Users()))
    monkeypatch.setattr(auth, "REQUIRE_EMAIL_VERIFICATION", False)
    monkeypatch.setattr(auth, "hash_password", lambda password: "hashed-password")
    monkeypatch.setattr(auth, "create_access_token", lambda user_id, email: "access-token")

    response = await auth.auth_signup(
        auth.SignUpRequest(email="Researcher@example.com", password="password123"),
        _request(),
    )

    assert response["access_token"] == "access-token"
    assert response["user"]["email"] == "researcher@example.com"
    assert inserted_users[0]["is_verified"] is True
    assert inserted_users[0]["password_hash"] == "hashed-password"


@pytest.mark.asyncio
async def test_disabled_credit_system_skips_lookup_and_deduction(monkeypatch):
    monkeypatch.setattr(credits, "CREDIT_SYSTEM_ENABLED", False)
    get_user_plan = AsyncMock(side_effect=AssertionError("unexpected plan lookup"))
    monkeypatch.setattr(credits, "get_user_plan", get_user_plan)

    await credits.check_and_deduct_credit(_request(), "chat")

    get_user_plan.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_credit_snapshot_reports_unlimited(monkeypatch):
    monkeypatch.setattr(credits, "CREDIT_SYSTEM_ENABLED", False)
    monkeypatch.setattr(
        credits,
        "get_user_plan",
        AsyncMock(return_value={"plan": "free", "credits_used": 20}),
    )

    result = await credits.append_credits_snapshot({"answer": "grounded"}, _request())

    assert result["credits"]["is_unlimited"] is True
    assert result["credits"]["credits_remaining"] is None
    assert result["credits"]["credits_limit"] is None
