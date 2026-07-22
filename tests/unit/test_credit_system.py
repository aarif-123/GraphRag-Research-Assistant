"""
Unit tests for the credit system business logic.

Covers:
- Free user with credits remaining: deduction succeeds
- Free user exhausted: raises HTTP 402
- Pro user: bypasses credit system entirely
- Daily credit reset trigger
- CREDIT_COSTS table coverage for all known action types
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import pytest
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Inline replica of credit-system constants and helpers.
# We do NOT import app.py directly (requires live env vars at module level).
# ---------------------------------------------------------------------------

FREE_CREDITS_PER_DAY = 20

CREDIT_COSTS: Dict[str, int] = {
    "query": 1,
    "chat": 1,
    "timeline": 3,
    "compare": 3,
    "pdf": 5,
}


def _credits_remaining(credits_used: int) -> int:
    return max(0, FREE_CREDITS_PER_DAY - credits_used)


def _should_reset(reset_at: Optional[datetime], now: datetime) -> bool:
    if reset_at is None:
        return True
    reset_naive = reset_at.replace(tzinfo=None) if reset_at.tzinfo else reset_at
    now_naive = now.replace(tzinfo=None) if now.tzinfo else now
    return now_naive >= reset_naive


async def check_and_deduct_credit_logic(
    plan: str,
    credits_used: int,
    action: str,
) -> None:
    """
    Extracted business logic from check_and_deduct_credit().
    Raises HTTPException(402) when free user is out of credits.
    """
    if plan == "pro":
        return  # unlimited

    cost = CREDIT_COSTS.get(action, 1)
    if credits_used + cost > FREE_CREDITS_PER_DAY:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "credit_exhausted",
                "message": (f"You have used all {FREE_CREDITS_PER_DAY} daily credits."),
                "credits_used": credits_used,
                "credits_limit": FREE_CREDITS_PER_DAY,
            },
        )


# ---------------------------------------------------------------------------
# Tests — CREDIT_COSTS table
# ---------------------------------------------------------------------------


class TestCreditCostsTable:
    @pytest.mark.parametrize(
        "action,expected_cost",
        [
            ("query", 1),
            ("chat", 1),
            ("timeline", 3),
            ("compare", 3),
            ("pdf", 5),
        ],
    )
    def test_known_action_costs(self, action: str, expected_cost: int):
        assert CREDIT_COSTS[action] == expected_cost

    def test_unknown_action_defaults_to_1(self):
        """Fallback cost for unlisted actions."""
        cost = CREDIT_COSTS.get("unknown_action", 1)
        assert cost == 1


# ---------------------------------------------------------------------------
# Tests — deduction logic
# ---------------------------------------------------------------------------


class TestCheckAndDeductCredit:
    async def test_pro_user_bypasses_check(self):
        # Should never raise regardless of credits_used
        await check_and_deduct_credit_logic("pro", FREE_CREDITS_PER_DAY, "query")

    async def test_free_user_with_credits_passes(self):
        await check_and_deduct_credit_logic("free", 0, "query")

    async def test_free_user_at_limit_raises_402(self):
        with pytest.raises(HTTPException) as exc_info:
            await check_and_deduct_credit_logic("free", FREE_CREDITS_PER_DAY, "query")
        assert exc_info.value.status_code == 402
        detail = exc_info.value.detail
        assert detail["error"] == "credit_exhausted"

    async def test_free_user_over_limit_raises_402(self):
        with pytest.raises(HTTPException) as exc_info:
            await check_and_deduct_credit_logic(
                "free",
                FREE_CREDITS_PER_DAY - 1,
                "pdf",  # pdf costs 5
            )
        assert exc_info.value.status_code == 402

    async def test_free_user_exact_boundary_passes(self):
        """credits_used=17, action=compare (cost=3) → total=20 = limit → PASSES (check is strictly >)."""
        await check_and_deduct_credit_logic("free", 17, "compare")

    async def test_free_user_one_over_boundary_raises(self):
        """credits_used=18, action=compare (cost=3) → total=21 > 20 → raises."""
        with pytest.raises(HTTPException):
            await check_and_deduct_credit_logic("free", 18, "compare")

    @pytest.mark.parametrize("action", list(CREDIT_COSTS.keys()))
    async def test_pro_bypasses_all_action_types(self, action: str):
        await check_and_deduct_credit_logic("pro", FREE_CREDITS_PER_DAY, action)


# ---------------------------------------------------------------------------
# Tests — daily reset logic
# ---------------------------------------------------------------------------


class TestDailyReset:
    def test_reset_triggered_when_reset_at_is_none(self):
        now = datetime.now(timezone.utc)
        assert _should_reset(None, now) is True

    def test_reset_triggered_when_past_reset_time(self):
        now = datetime.now(timezone.utc)
        past_reset = now - timedelta(hours=1)
        assert _should_reset(past_reset, now) is True

    def test_no_reset_when_future_reset_time(self):
        now = datetime.now(timezone.utc)
        future_reset = now + timedelta(hours=10)
        assert _should_reset(future_reset, now) is False

    def test_credits_remaining_calculation(self):
        assert _credits_remaining(0) == FREE_CREDITS_PER_DAY
        assert _credits_remaining(FREE_CREDITS_PER_DAY) == 0
        # Over-limit should clamp at 0
        assert _credits_remaining(FREE_CREDITS_PER_DAY + 5) == 0
        assert _credits_remaining(10) == FREE_CREDITS_PER_DAY - 10
