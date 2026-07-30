"""
utils/credits.py — Credit/plan management: daily credit enforcement,
rate limiting, and credit snapshot helpers.
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import HTTPException, Request

from app.config import (
    CREDIT_COSTS,
    FREE_CREDITS_PER_DAY,
    RATE_LIMIT,
    log,
)
from app.utils.auth import decode_access_token

# ──────────────────────────────────────────────────────────────────────────────
# RATE LIMITER
# ──────────────────────────────────────────────────────────────────────────────

_rate_store: Dict[str, List[float]] = {}
_last_cleanup = time.time()


async def check_rate_limit(client_ip: str) -> None:
    """Raise HTTP 429 if the client has exceeded RATE_LIMIT requests per minute."""
    global _last_cleanup, _rate_store
    now = time.time()
    if now - _last_cleanup > 300:
        cutoff = now - 60
        _rate_store = {
            k: [t for t in v if t > cutoff]
            for k, v in _rate_store.items()
            if any(t > cutoff for t in v)
        }
        _last_cleanup = now
    hits = [t for t in _rate_store.get(client_ip, []) if now - t < 60.0]
    if len(hits) >= RATE_LIMIT:
        raise HTTPException(429, f"Rate limit: max {RATE_LIMIT}/min.")
    hits.append(now)
    _rate_store[client_ip] = hits


# ──────────────────────────────────────────────────────────────────────────────
# PLAN / CREDIT SYSTEM
# ──────────────────────────────────────────────────────────────────────────────


async def get_user_plan(request: Request) -> Dict[str, Any]:
    """Return {plan, credits_used, credits_reset_at} for the authenticated user.
    Falls back to {'plan': 'free', ...} for unauthenticated requests.
    """
    from app.clients.pool import pool  # lazy import to avoid circular deps

    try:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return {"plan": "free", "credits_used": 0, "credits_reset_at": None}
        token = auth_header.split(" ")[1]
        payload = decode_access_token(token)
        if not payload:
            return {"plan": "free", "credits_used": 0, "credits_reset_at": None}
        uid = payload.get("sub")
        user = await asyncio.to_thread(pool.db.users.find_one, {"_id": uid})
        if not user:
            return {"plan": "free", "credits_used": 0, "credits_reset_at": None}

        now = datetime.now(timezone.utc)
        now_naive = now.replace(tzinfo=None)
        reset_at = user.get("credits_reset_at")
        credits_used = user.get("credits_used", 0)

        reset_at_naive = reset_at
        if isinstance(reset_at, datetime) and reset_at.tzinfo is not None:
            reset_at_naive = reset_at.astimezone(timezone.utc).replace(tzinfo=None)

        # Reset daily credits if the reset time has passed
        if reset_at is None or (
            isinstance(reset_at_naive, datetime) and now_naive >= reset_at_naive
        ):
            new_reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            new_reset_naive = new_reset.replace(tzinfo=None)
            await asyncio.to_thread(
                pool.db.users.update_one,
                {"_id": uid},
                {"$set": {"credits_used": 0, "credits_reset_at": new_reset_naive}},
            )
            credits_used = 0
            reset_at = new_reset_naive

        return {
            "plan": user.get("plan", "free"),
            "credits_used": credits_used,
            "credits_reset_at": reset_at.isoformat() if isinstance(reset_at, datetime) else None,
            "user_id": uid,
        }
    except Exception as e:
        log.warning(f"get_user_plan error: {e}")
        return {"plan": "free", "credits_used": 0, "credits_reset_at": None}


async def check_and_deduct_credit(request: Request, action: str) -> None:
    """Check if user has credits remaining and deduct one.
    Raises HTTP 402 if exhausted. Pro users bypass the credit system entirely.
    """
    from app.clients.pool import pool  # lazy import

    plan_info = await get_user_plan(request)
    plan = plan_info.get("plan", "free")
    if plan == "pro":
        return  # Pro users: unlimited

    cost = CREDIT_COSTS.get(action, 1)
    credits_used = plan_info.get("credits_used", 0)

    if credits_used + cost > FREE_CREDITS_PER_DAY:
        reset_at = plan_info.get("credits_reset_at", "tomorrow")
        raise HTTPException(
            status_code=402,
            detail={
                "error": "credit_exhausted",
                "message": f"You have used all {FREE_CREDITS_PER_DAY} daily credits. "
                f"Upgrade to Pro for unlimited access, or wait until {reset_at}.",
                "credits_used": credits_used,
                "credits_limit": FREE_CREDITS_PER_DAY,
                "reset_at": reset_at,
                "upgrade_url": "/upgrade",
            },
        )

    uid = plan_info.get("user_id")
    if uid:
        await asyncio.to_thread(
            pool.db.users.update_one,
            {"_id": uid},
            {"$inc": {"credits_used": cost}},
        )


async def append_credits_snapshot(res: Any, request: Request) -> Any:
    """Append the updated credit snapshot to any API response dict."""
    if isinstance(res, dict):
        try:
            post_plan = await get_user_plan(request)
            _plan = post_plan.get("plan", "free")
            _used = post_plan.get("credits_used", 0)
            res["credits"] = {
                "plan": _plan,
                "credits_used": _used,
                "credits_remaining": None
                if _plan == "pro"
                else max(0, FREE_CREDITS_PER_DAY - _used),
                "credits_limit": None if _plan == "pro" else FREE_CREDITS_PER_DAY,
                "is_unlimited": _plan == "pro",
            }
        except Exception as e:
            log.warning(f"Failed to append credits snapshot: {e}")
    return res


async def require_pro(request: Request, feature_name: str = "This feature") -> None:
    """Raise HTTP 403 with upgrade prompt if user is not on Pro plan."""
    plan_info = await get_user_plan(request)
    if plan_info.get("plan") != "pro":
        raise HTTPException(
            status_code=403,
            detail={
                "error": "pro_required",
                "message": f"{feature_name} is available on the Pro plan. "
                "Upgrade to unlock unlimited surveys, bulk research, heavy models, and more.",
                "upgrade_url": "/upgrade",
            },
        )
