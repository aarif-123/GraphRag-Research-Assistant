"""
routes/auth.py — Authentication, user profile, chat history,
credits/plan, and payment endpoints.
"""

import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import (
    CREDIT_COSTS,
    FREE_CREDITS_PER_DAY,
    PRO_TOP_K_MAX,
    FREE_TOP_K_MAX,
    RAZORPAY_KEY_ID,
    RAZORPAY_KEY_SECRET,
    REQUIRE_EMAIL_VERIFICATION,
    STRIPE_WEBHOOK_SECRET,
    log,
)
from app.clients.pool import pool
from app.utils.auth import (
    create_access_token,
    decode_access_token,
    get_authenticated_user,
    get_token_from_request,
    hash_password,
    verify_password,
)
from app.utils.credits import get_user_plan
from app.utils.email import (
    send_reset_email,
    send_verification_email,
    validate_email_mailboxlayer,
)
from app.models.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    PasswordUpdateRequest,
    ProfileUpdateRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    SignUpRequest,
    VerifyEmailRequest,
    RazorpayVerifyRequest,
)

router = APIRouter(prefix="/api/auth")

# ─────────────────────── AUTH ───────────────────────────────────


@router.post("/signup")
async def auth_signup(req: SignUpRequest, request: Request):
    email = req.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")

    is_valid, err_msg = await validate_email_mailboxlayer(email)
    if not is_valid:
        raise HTTPException(status_code=400, detail=err_msg)
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters long")

    existing = await asyncio.to_thread(pool.db.users.find_one, {"email": email})
    if existing:
        raise HTTPException(status_code=400, detail="An account with this email already exists")

    from datetime import timedelta
    uid = str(uuid.uuid4())
    password_hash = hash_password(req.password)
    now = datetime.now(timezone.utc)
    credits_reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    is_verified_init = not REQUIRE_EMAIL_VERIFICATION
    user_doc = {
        "_id": uid,
        "email": email,
        "password_hash": password_hash,
        "user_metadata": {
            "full_name": email.split("@")[0].capitalize(),
            "institution": "",
            "role": "",
        },
        "plan": "free",
        "credits_used": 0,
        "credits_reset_at": credits_reset,
        "stripe_customer_id": None,
        "stripe_subscription_id": None,
        "is_verified": is_verified_init,
        "created_at": now,
        "updated_at": now,
    }
    await asyncio.to_thread(pool.db.users.insert_one, user_doc)

    if REQUIRE_EMAIL_VERIFICATION:
        await send_verification_email(email, uid, request)
        return {
            "status": "verification_pending",
            "email": email,
            "msg": "Please verify your email address via the link sent to you.",
        }

    token = create_access_token(uid, email)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {"id": uid, "email": email, "user_metadata": user_doc["user_metadata"]},
    }


@router.post("/login")
async def auth_login(req: LoginRequest, request: Request):
    email = req.email.strip().lower()
    user = await asyncio.to_thread(pool.db.users.find_one, {"email": email})
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Invalid email or password")

    if REQUIRE_EMAIL_VERIFICATION and not user.get("is_verified", False):
        await send_verification_email(email, user["_id"], request)
        return {
            "status": "verification_pending",
            "email": email,
            "msg": "Please verify your email address to log in.",
        }

    token = create_access_token(user["_id"], user["email"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user["_id"],
            "email": user["email"],
            "user_metadata": user.get("user_metadata", {}),
        },
    }


@router.post("/verify-email")
async def verify_email(req: VerifyEmailRequest):
    email = req.email.strip().lower()
    user = await asyncio.to_thread(pool.db.users.find_one, {"email": email})
    if not user:
        raise HTTPException(status_code=400, detail="User not found")

    stored_code = user.get("verification_code")
    expires_at = user.get("verification_expires_at")

    if not stored_code or stored_code != req.code.strip():
        raise HTTPException(status_code=400, detail="Invalid verification code")
    if expires_at and datetime.utcnow() > expires_at:
        raise HTTPException(status_code=400, detail="Verification code has expired")

    await asyncio.to_thread(
        pool.db.users.update_one,
        {"_id": user["_id"]},
        {"$set": {"is_verified": True}, "$unset": {"verification_code": "", "verification_expires_at": ""}},
    )

    token = create_access_token(user["_id"], user["email"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {"id": user["_id"], "email": user["email"], "user_metadata": user.get("user_metadata", {})},
    }


@router.get("/verify-link")
async def verify_email_link(email: str, code: str):
    email = email.strip().lower()
    user = await asyncio.to_thread(pool.db.users.find_one, {"email": email})
    if not user:
        return HTMLResponse(status_code=400, content="""
            <html><body style="font-family: Arial, sans-serif; background-color: #0b0e14; color: #f8fafc; padding: 40px; text-align: center;">
                <h2 style="color: #ef4444;">User Not Found</h2>
                <p style="color: #94a3b8;">The requested user account was not found.</p>
                <p><a href="/" style="color: #6366f1; text-decoration: none; font-weight: bold;">Return to Landing Page</a></p>
            </body></html>""")

    stored_code = user.get("verification_code")
    expires_at = user.get("verification_expires_at")

    if not stored_code or stored_code != code.strip():
        return HTMLResponse(status_code=400, content="""
            <html><body style="font-family: Arial, sans-serif; background-color: #0b0e14; color: #f8fafc; padding: 40px; text-align: center;">
                <h2 style="color: #ef4444;">Invalid Verification Link</h2>
                <p style="color: #94a3b8;">This verification link is invalid or has already been used.</p>
                <p><a href="/" style="color: #6366f1; text-decoration: none; font-weight: bold;">Return to Landing Page</a></p>
            </body></html>""")

    if expires_at and datetime.utcnow() > expires_at:
        return HTMLResponse(status_code=400, content="""
            <html><body style="font-family: Arial, sans-serif; background-color: #0b0e14; color: #f8fafc; padding: 40px; text-align: center;">
                <h2 style="color: #ef4444;">Verification Link Expired</h2>
                <p style="color: #94a3b8;">This verification link has expired. Please log in to request a new link.</p>
                <p><a href="/" style="color: #6366f1; text-decoration: none; font-weight: bold;">Return to Landing Page</a></p>
            </body></html>""")

    await asyncio.to_thread(
        pool.db.users.update_one,
        {"_id": user["_id"]},
        {"$set": {"is_verified": True}, "$unset": {"verification_code": "", "verification_expires_at": ""}},
    )
    return RedirectResponse(url="/?verified=true")


@router.post("/resend-verification")
async def resend_verification(req: ResendVerificationRequest, request: Request):
    email = req.email.strip().lower()
    user = await asyncio.to_thread(pool.db.users.find_one, {"email": email})
    if not user:
        raise HTTPException(status_code=400, detail="User not found")
    await send_verification_email(email, user["_id"], request)
    return {"msg": "Verification link resent successfully"}


@router.post("/forgot-password")
async def forgot_password(req: ForgotPasswordRequest):
    email = req.email.strip().lower()
    user = await asyncio.to_thread(pool.db.users.find_one, {"email": email})
    if not user:
        return {"msg": "If this email exists, a reset code has been sent."}
    await send_reset_email(email, user["_id"])
    return {"msg": "Password reset code sent successfully"}


@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest):
    email = req.email.strip().lower()
    user = await asyncio.to_thread(pool.db.users.find_one, {"email": email})
    if not user:
        raise HTTPException(status_code=400, detail="User not found")

    stored_token = user.get("password_reset_token")
    expires_at = user.get("password_reset_expires_at")

    if not stored_token or stored_token != req.token.strip():
        raise HTTPException(status_code=400, detail="Invalid or missing reset token")
    if expires_at and datetime.utcnow() > expires_at:
        raise HTTPException(status_code=400, detail="Reset token has expired")
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters long")

    password_hash = hash_password(req.new_password)
    await asyncio.to_thread(
        pool.db.users.update_one,
        {"_id": user["_id"]},
        {"$set": {"password_hash": password_hash}, "$unset": {"password_reset_token": "", "password_reset_expires_at": ""}},
    )
    return {"msg": "Password reset successful"}


@router.put("/profile")
async def auth_update_profile(req: ProfileUpdateRequest, request: Request):
    token = get_token_from_request(request)
    user = await get_authenticated_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    uid = user["id"]

    user_db = await asyncio.to_thread(pool.db.users.find_one, {"_id": uid})
    if not user_db:
        raise HTTPException(status_code=404, detail="User not found")

    meta = user_db.get("user_metadata", {})
    if req.full_name is not None:
        meta["full_name"] = req.full_name
    if req.institution is not None:
        meta["institution"] = req.institution
    if req.role is not None:
        meta["role"] = req.role

    await asyncio.to_thread(
        pool.db.users.update_one,
        {"_id": uid},
        {"$set": {"user_metadata": meta, "updated_at": datetime.now(timezone.utc)}},
    )
    return {"id": uid, "email": user_db["email"], "user_metadata": meta}


@router.put("/password")
async def auth_update_password(req: PasswordUpdateRequest, request: Request):
    token = get_token_from_request(request)
    user = await get_authenticated_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    uid = user["id"]

    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters long")

    user_db = await asyncio.to_thread(pool.db.users.find_one, {"_id": uid})
    if not user_db:
        raise HTTPException(status_code=404, detail="User not found")

    password_hash = hash_password(req.password)
    await asyncio.to_thread(
        pool.db.users.update_one,
        {"_id": uid},
        {"$set": {"password_hash": password_hash, "updated_at": datetime.now(timezone.utc)}},
    )
    return {"status": "success"}


@router.get("/me")
async def auth_me(request: Request):
    token = get_token_from_request(request)
    user = await get_authenticated_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    return {
        "id": user.get("id"),
        "email": user.get("email"),
        "user_metadata": user.get("user_metadata", {}),
    }


@router.get("/plan")
async def get_plan(request: Request):
    token = get_token_from_request(request)
    user = await get_authenticated_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    plan_info = await get_user_plan(request)
    plan = plan_info.get("plan", "free")
    credits_used = plan_info.get("credits_used", 0)
    return {
        "plan": plan,
        "is_pro": plan == "pro",
        "credits_used": credits_used,
        "credits_limit": None if plan == "pro" else FREE_CREDITS_PER_DAY,
        "credits_remaining": None if plan == "pro" else max(0, FREE_CREDITS_PER_DAY - credits_used),
        "credits_reset_at": plan_info.get("credits_reset_at"),
        "features": {
            "survey": plan == "pro",
            "bulk_research": plan == "pro",
            "heavy_model": plan == "pro",
            "api_access": plan == "pro",
            "top_k_max": PRO_TOP_K_MAX if plan == "pro" else FREE_TOP_K_MAX,
            "citation_network_full": plan == "pro",
        },
    }


# ─────────────────────── PAYMENT ───────────────────────────────────

@router.post("/upgrade")
async def stripe_webhook(request: Request):
    """Stripe webhook — flips plan to pro on payment, back to free on cancellation."""
    import hashlib
    import hmac
    import json as json_module

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if STRIPE_WEBHOOK_SECRET:
        try:
            parts = {p.split("=")[0]: p.split("=")[1] for p in sig_header.split(",") if "=" in p}
            ts = parts.get("t", "")
            v1 = parts.get("v1", "")
            signed_payload = f"{ts}.{payload.decode()}"
            expected = hmac.new(
                STRIPE_WEBHOOK_SECRET.encode(),
                signed_payload.encode(),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(expected, v1):
                raise HTTPException(400, "Invalid Stripe signature")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(400, "Could not verify Stripe signature")

    try:
        event = json_module.loads(payload)
    except Exception:
        raise HTTPException(400, "Invalid JSON payload")

    event_type = event.get("type", "")
    data = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        customer_id = data.get("customer")
        subscription_id = data.get("subscription")
        customer_email = data.get("customer_details", {}).get("email") or data.get("customer_email")
        if customer_email:
            await asyncio.to_thread(
                pool.db.users.update_one,
                {"email": customer_email.lower()},
                {"$set": {"plan": "pro", "stripe_customer_id": customer_id, "stripe_subscription_id": subscription_id}},
            )
            log.info(f"Stripe: upgraded {customer_email} to pro (sub={subscription_id})")

    elif event_type == "customer.subscription.deleted":
        customer_id = data.get("customer")
        if customer_id:
            await asyncio.to_thread(
                pool.db.users.update_one,
                {"stripe_customer_id": customer_id},
                {"$set": {"plan": "free", "stripe_subscription_id": None}},
            )
            log.info(f"Stripe: downgraded customer {customer_id} to free (subscription cancelled)")

    return {"received": True}


router_payments = APIRouter(prefix="/api")


@router_payments.post("/razorpay/create-order")
async def razorpay_create_order(request: Request):
    import json as json_module
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")

    amount = body.get("amount", 0)
    currency = body.get("currency", "INR")

    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        raise HTTPException(503, "Razorpay not configured on this server.")

    try:
        import razorpay
        client_rp = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
        order = await asyncio.to_thread(
            client_rp.order.create,
            {"amount": amount, "currency": currency, "payment_capture": 1},
        )
        return {"order_id": order["id"], "amount": order["amount"], "currency": order["currency"]}
    except Exception as e:
        log.error(f"Razorpay order creation failed: {e}")
        raise HTTPException(500, f"Razorpay error: {str(e)}")


@router_payments.post("/razorpay/verify")
async def razorpay_verify_payment(req: RazorpayVerifyRequest, request: Request):
    import hashlib
    import hmac
    from app.utils.auth import get_token_from_request, get_authenticated_user

    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        raise HTTPException(503, "Razorpay not configured on this server.")

    try:
        token = get_token_from_request(request)
        user = await get_authenticated_user(token)
        if not user:
            raise HTTPException(401, "Invalid token")

        expected_signature = hmac.new(
            RAZORPAY_KEY_SECRET.encode("utf-8"),
            f"{req.razorpay_order_id}|{req.razorpay_payment_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected_signature, req.razorpay_signature):
            raise HTTPException(400, "Invalid payment signature — possible tampering detected.")

        await asyncio.to_thread(
            pool.db.users.update_one,
            {"_id": user["id"]},
            {"$set": {"plan": "pro"}},
        )
        log.info(f"Razorpay: upgraded user {user['id']} to pro")
        return {"status": "success", "plan": "pro"}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Razorpay verification error: {e}")
        raise HTTPException(500, str(e))


@router_payments.get("/razorpay/payments")
async def get_payment_history(request: Request):
    from app.utils.auth import get_token_from_request, get_authenticated_user
    token = get_token_from_request(request)
    user = await get_authenticated_user(token)
    if not user:
        raise HTTPException(401, "Invalid token")
    try:
        history = await asyncio.to_thread(
            lambda: list(pool.db.payments.find({"user_id": user["id"]}).sort("created_at", -1).limit(20))
        )
        for h in history:
            h["id"] = str(h.pop("_id"))
            if isinstance(h.get("created_at"), datetime):
                h["created_at"] = h["created_at"].isoformat()
        return history
    except Exception as e:
        log.error(f"Error fetching payment history: {e}")
        raise HTTPException(500, str(e))


# ─────────────────────── HISTORY ───────────────────────────────────

router_history = APIRouter(prefix="/api")


@router_history.get("/history")
async def list_history(request: Request):
    from app.utils.auth import get_token_from_request, get_authenticated_user
    token = get_token_from_request(request)
    user = await get_authenticated_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        sessions = await asyncio.to_thread(
            lambda: list(pool.db.chat_sessions.find({"user_id": user["id"]}).sort("updated_at", -1))
        )
        for s in sessions:
            s["id"] = s.pop("_id")
            if isinstance(s.get("updated_at"), datetime):
                s["updated_at"] = s["updated_at"].isoformat()
            if isinstance(s.get("created_at"), datetime):
                s["created_at"] = s["created_at"].isoformat()
        return sessions
    except Exception as e:
        log.error(f"Error fetching history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router_history.post("/history")
async def create_history(request: Request):
    from app.utils.auth import get_token_from_request, get_authenticated_user
    token = get_token_from_request(request)
    user = await get_authenticated_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        body = await request.json()
    except Exception:
        body = {}

    title = body.get("title", "New Chat")
    messages = body.get("messages", [])

    try:
        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        session_doc = {
            "_id": session_id,
            "user_id": user["id"],
            "title": title,
            "messages": messages,
            "created_at": now,
            "updated_at": now,
        }
        await asyncio.to_thread(pool.db.chat_sessions.insert_one, session_doc)
        session_doc["id"] = session_doc.pop("_id")
        session_doc["created_at"] = session_doc["created_at"].isoformat()
        session_doc["updated_at"] = session_doc["updated_at"].isoformat()
        return session_doc
    except Exception as e:
        log.error(f"Error creating history session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router_history.put("/history/{session_id}")
async def update_history(session_id: str, request: Request):
    from app.utils.auth import get_token_from_request, get_authenticated_user
    from pymongo import ReturnDocument

    token = get_token_from_request(request)
    user = await get_authenticated_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    update_data = {}
    if "title" in body:
        update_data["title"] = body["title"]
    if "messages" in body:
        update_data["messages"] = body["messages"]
    update_data["updated_at"] = datetime.now(timezone.utc)

    try:
        res = await asyncio.to_thread(
            pool.db.chat_sessions.find_one_and_update,
            {"_id": session_id, "user_id": user["id"]},
            {"$set": update_data},
            return_document=ReturnDocument.AFTER,
        )
        if not res:
            raise HTTPException(status_code=404, detail="Chat session not found")
        res["id"] = res.pop("_id")
        if isinstance(res.get("created_at"), datetime):
            res["created_at"] = res["created_at"].isoformat()
        if isinstance(res.get("updated_at"), datetime):
            res["updated_at"] = res["updated_at"].isoformat()
        return res
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error updating history session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router_history.delete("/history")
async def delete_all_history(request: Request):
    from app.utils.auth import get_token_from_request, get_authenticated_user
    token = get_token_from_request(request)
    user = await get_authenticated_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        await asyncio.to_thread(pool.db.chat_sessions.delete_many, {"user_id": user["id"]})
        return {"status": "success"}
    except Exception as e:
        log.error(f"Error deleting all history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router_history.delete("/history/{session_id}")
async def delete_history(session_id: str, request: Request):
    from app.utils.auth import get_token_from_request, get_authenticated_user
    token = get_token_from_request(request)
    user = await get_authenticated_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        res = await asyncio.to_thread(
            pool.db.chat_sessions.delete_one, {"_id": session_id, "user_id": user["id"]}
        )
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Chat session not found")
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error deleting history session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router_history.get("/credits")
async def get_credits(request: Request):
    plan_info = await get_user_plan(request)
    plan = plan_info.get("plan", "free")
    credits_used = plan_info.get("credits_used", 0)
    credits_remaining = None if plan == "pro" else max(0, FREE_CREDITS_PER_DAY - credits_used)
    return {
        "plan": plan,
        "credits_used": credits_used,
        "credits_limit": None if plan == "pro" else FREE_CREDITS_PER_DAY,
        "credits_remaining": credits_remaining,
        "credits_reset_at": plan_info.get("credits_reset_at"),
        "is_unlimited": plan == "pro",
        "credit_costs": CREDIT_COSTS,
    }
