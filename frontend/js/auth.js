/**
 * auth.js — Authentication, user profiles, credits, and payment integration
 */

import { API_BASE, els, state } from './state.js';

export function getAuthHeader() {
    const token = localStorage.getItem('aether_token');
    return token ? { 'Authorization': `Bearer ${token}` } : {};
}

export function updateUserUI(user) {
    if (!user) return;
    const email = user.email || '';
    state.userEmail = email;
    const metadata = user.user_metadata || {};
    const displayName = metadata.full_name || email;
    const emailText = document.getElementById('userEmailText');
    const avatarIcon = document.getElementById('userAvatarIcon');
    if (emailText) emailText.textContent = displayName;
    if (avatarIcon) {
        const initial = (metadata.full_name || email).charAt(0).toUpperCase();
        avatarIcon.textContent = initial;
    }
}

export function updateCreditPill(credits) {
    const pill = document.getElementById('creditPill');
    const text = document.getElementById('creditText');
    const badge = document.getElementById('planBadge');
    const icon = document.getElementById('creditIcon');
    if (!pill || !text) return;

    if (!credits) {
        pill.style.display = 'none';
        return;
    }

    if (credits.is_unlimited || credits.plan === 'pro') {
        pill.style.display = 'none';
        if (els.upgradeDropdownBtn) els.upgradeDropdownBtn.style.display = 'none';
    } else {
        pill.style.display = 'flex';
        const remaining = credits.credits_remaining ?? 0;
        const limit = credits.credits_limit ?? 20;
        const pct = remaining / limit;

        text.textContent = `${remaining} / ${limit}`;
        if (badge) badge.style.display = 'none';
        if (els.upgradeDropdownBtn) els.upgradeDropdownBtn.style.display = 'flex';

        if (pct > 0.5) {
            pill.style.borderColor = 'rgba(99,102,241,0.3)';
            pill.style.background = 'rgba(99,102,241,0.1)';
            pill.style.color = 'var(--primary-light)';
            if (icon) icon.setAttribute('stroke', 'var(--primary-light)');
        } else if (pct > 0.2) {
            pill.style.borderColor = 'rgba(245,158,11,0.4)';
            pill.style.background = 'rgba(245,158,11,0.08)';
            pill.style.color = '#f59e0b';
            if (icon) icon.setAttribute('stroke', '#f59e0b');
        } else {
            pill.style.borderColor = 'rgba(239,68,68,0.4)';
            pill.style.background = 'rgba(239,68,68,0.08)';
            pill.style.color = '#ef4444';
            if (icon) icon.setAttribute('stroke', '#ef4444');
        }

        if (remaining <= 3 && remaining > 0) {
            pill.style.animation = 'pulse 1.5s ease-in-out infinite';
        } else {
            pill.style.animation = 'none';
        }
    }
}

export async function initAuth() {
    try {
        const token = localStorage.getItem('aether_token');
        if (!token) {
            window.location.href = '/';
            return;
        }

        const res = await fetch('/api/auth/me', {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (!res.ok) {
            localStorage.removeItem('aether_token');
            window.location.href = '/';
            return;
        }

        const user = await res.json();
        updateUserUI(user);
        if (window.loadHistory) await window.loadHistory();

        try {
            const token = localStorage.getItem('aether_token');
            const cRes = await fetch(`${API_BASE}/api/credits`, {
                headers: { 'Authorization': `Bearer ${token}` }
            });
            if (cRes.ok) {
                const cData = await cRes.json();
                updateCreditPill(cData);

                if (localStorage.getItem('trigger_upgrade') === 'true') {
                    localStorage.removeItem('trigger_upgrade');
                    if (cData.plan !== 'pro') {
                        if (els.paymentModal) {
                            els.paymentModal.classList.add('visible');
                        }
                    }
                }
            }
        } catch (_) { /* silent */ }
    } catch (e) {
        console.error("Failed to initialize Auth:", e);
        window.location.href = '/';
    }
}

export async function startRazorpayCheckout() {
    const token = localStorage.getItem('aether_token');
    if (!token) {
        alert("Please log in to upgrade your plan.");
        window.location.href = '/';
        return;
    }

    try {
        const res = await fetch('/api/auth/razorpay/create-order', {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (!res.ok) {
            const errData = await res.json();
            throw new Error(errData.detail || "Failed to initiate checkout");
        }

        const data = await res.json();

        if (typeof Razorpay === 'undefined') {
            throw new Error("Razorpay Checkout SDK not loaded. Please check your internet connection.");
        }

        const options = {
            "key": data.key_id,
            "amount": data.amount,
            "currency": data.currency,
            "name": "Aether",
            "description": "Pro Plan Upgrade",
            "order_id": data.order_id,
            "handler": async function (response) {
                try {
                    const verifyRes = await fetch('/api/auth/razorpay/verify-payment', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'Authorization': `Bearer ${token}`
                        },
                        body: JSON.stringify({
                            razorpay_order_id: response.razorpay_order_id,
                            razorpay_payment_id: response.razorpay_payment_id,
                            razorpay_signature: response.razorpay_signature
                        })
                    });

                    if (verifyRes.ok) {
                        if (els.paymentModal) els.paymentModal.classList.remove('visible');
                        alert("Upgrade successful! Welcome to Aether Pro.");
                        window.location.reload();
                    } else {
                        const verifyErr = await verifyRes.json();
                        alert("Payment verification failed: " + (verifyErr.detail || "Invalid transaction signature"));
                    }
                } catch (err) {
                    console.error("Signature verification failed:", err);
                    alert("Error verifying payment signature. Please contact support.");
                }
            },
            "prefill": {
                "email": "",
                "contact": ""
            },
            "theme": {
                "color": "#6366f1"
            }
        };
        const rzp = new Razorpay(options);
        rzp.open();
    } catch (e) {
        console.error("Razorpay checkout error:", e);
        alert(e.message || "Failed to start checkout process.");
    }
}
