"""
utils/email.py — Transactional email: verification and password reset emails
sent via SMTP, with a mock/dev fallback when SMTP is not configured.
"""

import asyncio
import os
import random
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import httpx

from app.config import (
    MAILBOXLAYER_API_KEY,
    REQUIRE_EMAIL_VERIFICATION,
    SMTP_FROM,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USER,
    log,
)


async def send_auth_email(
    to_email: str, subject: str, text_content: str, html_content: str
) -> bool:
    """Send a transactional email via SMTP.

    Falls back to a log-only mock when SMTP credentials are not configured,
    which is convenient for local development.
    """
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    if not SMTP_HOST or not SMTP_USER or not SMTP_PASSWORD:
        # Development / local mock mode
        log.info(
            f"\n=================== [MOCK EMAIL] ==================="
            f"\nTO: {to_email}"
            f"\nFROM: {SMTP_FROM}"
            f"\nSUBJECT: {subject}"
            f"\nCONTENT: {text_content}"
            f"\n====================================================\n"
        )
        return True

    try:

        def send_sync() -> None:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = SMTP_FROM
            msg["To"] = to_email

            part1 = MIMEText(text_content, "plain")
            part2 = MIMEText(html_content, "html")
            msg.attach(part1)
            msg.attach(part2)

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_FROM, to_email, msg.as_string())

        await asyncio.to_thread(send_sync)
        log.info(f"Email sent successfully to {to_email}")
        return True
    except Exception as e:
        log.error(f"Failed to send email to {to_email} via SMTP: {e}")
        return False


async def validate_email_mailboxlayer(email: str) -> Tuple[bool, Optional[str]]:
    """Validate an email address using the Mailboxlayer API.
    Returns (True, None) when valid or when the API key is not set.
    Returns (False, error_message) for invalid/disposable addresses.
    """
    api_key = MAILBOXLAYER_API_KEY
    if not api_key:
        return True, None

    try:
        url = "http://apilayer.net/api/check"
        params = {"access_key": api_key, "email": email}
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(url, params=params)
            if res.status_code != 200:
                log.warning(f"Mailboxlayer API returned status code {res.status_code}")
                return True, None

            data = res.json()
            if "error" in data:
                log.warning(f"Mailboxlayer API error: {data['error']}")
                return True, None

            if not data.get("format_valid", True):
                return False, "Invalid email address format."
            if not data.get("mx_found", True):
                return False, "This email domain does not exist or cannot receive emails."
            if data.get("disposable", False):
                return False, "Disposable or temporary email addresses are not allowed."

            return True, None
    except Exception as e:
        log.error(f"Error calling Mailboxlayer API: {e}")
        return True, None


async def send_verification_email(email: str, user_id: str, request=None) -> None:
    """Generate a 6-digit verification code, persist it in MongoDB, and send
    the verification link to the user via email.
    """
    from app.clients.pool import pool  # lazy import

    code = f"{random.randint(100000, 999999)}"
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    expires_at_naive = expires_at.replace(tzinfo=None)

    # Save verification code to MongoDB
    await asyncio.to_thread(
        pool.db.users.update_one,
        {"_id": user_id},
        {"$set": {"verification_code": code, "verification_expires_at": expires_at_naive}},
    )

    base_url = "http://localhost:8000/"
    if request:
        base_url = str(request.base_url)

    verify_link = f"{base_url}api/auth/verify-link?email={email}&code={code}"

    subject = "Verify your Aether account"
    text_content = (
        f"Welcome to Aether! Please verify your email by clicking the following link:\n"
        f"{verify_link}\nThis link is valid for 24 hours."
    )

    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background-color: #0b0e14; color: #f8fafc; padding: 40px; text-align: center;">
        <div style="max-width: 500px; margin: 0 auto; background-color: #111118; border: 1px solid rgba(255, 255, 255, 0.08); padding: 30px; border-radius: 12px; box-shadow: 0 10px 30px rgba(0,0,0,0.5);">
            <h2 style="color: #6366f1; margin-bottom: 20px;">Welcome to Aether</h2>
            <p style="color: #94a3b8; font-size: 16px; line-height: 1.5;">Thank you for registering. Please click the button below to verify your email address and activate your account:</p>
            <div style="margin: 30px 0;">
                <a href="{verify_link}" style="background-color: #6366f1; color: white; padding: 12px 28px; border-radius: 8px; font-weight: bold; text-decoration: none; display: inline-block; font-size: 16px; box-shadow: 0 4px 15px rgba(99, 102, 241, 0.4);">
                    Verify Email Address
                </a>
            </div>
            <p style="color: #64748b; font-size: 12px; margin-top: 20px;">Or copy and paste this link in your browser:<br><a href="{verify_link}" style="color: #818cf8; word-break: break-all;">{verify_link}</a></p>
            <p style="color: #64748b; font-size: 12px;">This link is valid for 24 hours. If you did not sign up for Aether, please ignore this email.</p>
        </div>
    </body>
    </html>
    """
    await send_auth_email(email, subject, text_content, html_content)


async def send_reset_email(email: str, user_id: str) -> None:
    """Generate a 6-digit reset token, persist it in MongoDB, and send the
    password-reset code to the user via email.
    """
    from app.clients.pool import pool  # lazy import

    token = f"{random.randint(100000, 999999)}"
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    expires_at_naive = expires_at.replace(tzinfo=None)

    # Save reset token to MongoDB
    await asyncio.to_thread(
        pool.db.users.update_one,
        {"_id": user_id},
        {"$set": {"password_reset_token": token, "password_reset_expires_at": expires_at_naive}},
    )

    subject = "Reset your Aether password"
    text_content = (
        f"We received a request to reset your Aether password.\n"
        f"Your reset code is: {token}\nThis code is valid for 1 hour."
    )

    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background-color: #0b0e14; color: #f8fafc; padding: 40px; text-align: center;">
        <div style="max-width: 500px; margin: 0 auto; background-color: #111118; border: 1px solid rgba(255, 255, 255, 0.08); padding: 30px; border-radius: 12px; box-shadow: 0 10px 30px rgba(0,0,0,0.5);">
            <h2 style="color: #ef4444; margin-bottom: 20px;">Reset Password Request</h2>
            <p style="color: #94a3b8; font-size: 16px; line-height: 1.5;">We received a request to reset your Aether account password. Please enter the following 6-digit code on the reset password screen to proceed:</p>
            <div style="background-color: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); padding: 15px; border-radius: 8px; font-size: 32px; font-weight: bold; letter-spacing: 6px; color: #f87171; margin: 30px 0;">
                {token}
            </div>
            <p style="color: #64748b; font-size: 12px;">This code is valid for 1 hour. If you did not request a password reset, please ignore this email or contact support if you have concerns.</p>
        </div>
    </body>
    </html>
    """
    await send_auth_email(email, subject, text_content, html_content)
