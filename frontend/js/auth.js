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

export function initProfileSettings() {
    // 1. Open/Close Modal Listeners
    if (els.profileSettingsBtn && els.profileModal) {
        els.profileSettingsBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const dropdown = document.getElementById('userDropdown');
            if (dropdown) dropdown.style.display = 'none'; // Close dropdown

            // Load user data
            await loadProfileData();

            // Open Modal
            els.profileModal.classList.add('visible');
            switchTab('details');
        });
    }

    if (els.profileModalClose && els.profileModal) {
        els.profileModalClose.addEventListener('click', () => {
            els.profileModal.classList.remove('visible');
        });
    }

    // Close payment modal
    if (els.paymentModalClose && els.paymentModal) {
        els.paymentModalClose.addEventListener('click', () => {
            els.paymentModal.classList.remove('visible');
        });
    }

    // Toggle payment modal from profile modal upgrade button
    if (els.profileUpgradeBtn && els.paymentModal) {
        els.profileUpgradeBtn.addEventListener('click', () => {
            if (els.profileModal) els.profileModal.classList.remove('visible');
            els.paymentModal.classList.add('visible');
        });
    }

    // Toggle payment modal from dropdown upgrade button
    if (els.upgradeDropdownBtn && els.paymentModal) {
        els.upgradeDropdownBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const dropdown = document.getElementById('userDropdown');
            if (dropdown) dropdown.style.display = 'none'; // Close dropdown
            els.paymentModal.classList.add('visible');
        });
    }

    // 2. Tab Switching Listeners
    if (els.profileTabDetails) {
        els.profileTabDetails.addEventListener('click', () => switchTab('details'));
    }
    if (els.profileTabSecurity) {
        els.profileTabSecurity.addEventListener('click', () => switchTab('security'));
    }
    if (els.profileTabBilling) {
        els.profileTabBilling.addEventListener('click', () => switchTab('billing'));
    }

    // 3. Form Submission Listeners
    if (els.profileForm) {
        els.profileForm.addEventListener('submit', handleProfileUpdate);
    }
    if (els.passwordForm) {
        els.passwordForm.addEventListener('submit', handlePasswordUpdate);
    }
}

// Helper to switch tabs
function switchTab(tabName) {
    // Reset alerts
    if (els.profileError) { els.profileError.style.display = 'none'; els.profileError.textContent = ''; }
    if (els.profileSuccess) { els.profileSuccess.style.display = 'none'; els.profileSuccess.textContent = ''; }

    // Update active tab button classes
    const tabs = [els.profileTabDetails, els.profileTabSecurity, els.profileTabBilling];
    tabs.forEach(t => {
        if (t) t.classList.remove('active');
    });

    // Update active content visibility
    if (els.profileDetailsTabContent) els.profileDetailsTabContent.style.display = 'none';
    if (els.profileSecurityTabContent) els.profileSecurityTabContent.style.display = 'none';
    if (els.profileBillingTabContent) els.profileBillingTabContent.style.display = 'none';

    if (tabName === 'details') {
        if (els.profileTabDetails) els.profileTabDetails.classList.add('active');
        if (els.profileDetailsTabContent) els.profileDetailsTabContent.style.display = 'flex';
    } else if (tabName === 'security') {
        if (els.profileTabSecurity) els.profileTabSecurity.classList.add('active');
        if (els.profileSecurityTabContent) els.profileSecurityTabContent.style.display = 'block';
    } else if (tabName === 'billing') {
        if (els.profileTabBilling) els.profileTabBilling.classList.add('active');
        if (els.profileBillingTabContent) els.profileBillingTabContent.style.display = 'block';
        loadBillingData();
    }
}

// Load account profile details
async function loadProfileData() {
    try {
        const token = localStorage.getItem('aether_token');
        if (!token) return;

        const res = await fetch('/api/auth/me', {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (!res.ok) throw new Error();

        const user = await res.json();
        const metadata = user.user_metadata || {};

        if (els.profileEmail) els.profileEmail.value = user.email || '';
        if (els.profileFullName) els.profileFullName.value = metadata.full_name || '';
        if (els.profileInstitution) els.profileInstitution.value = metadata.institution || '';
        if (els.profileRole) els.profileRole.value = metadata.role || '';

        // Populate hidden username field for password form
        const pwdUser = document.getElementById('passwordFormUsername');
        if (pwdUser) pwdUser.value = user.email || '';
    } catch (e) {
        console.error("Failed to load user profile data:", e);
    }
}

// Update profile details
async function handleProfileUpdate(e) {
    e.preventDefault();
    if (els.profileError) els.profileError.style.display = 'none';
    if (els.profileSuccess) els.profileSuccess.style.display = 'none';

    const saveBtn = document.getElementById('saveProfileBtn');
    const originalText = saveBtn ? saveBtn.textContent : 'Update Profile Details';
    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.textContent = 'Updating...';
    }

    try {
        const token = localStorage.getItem('aether_token');
        const res = await fetch('/api/auth/profile', {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({
                full_name: els.profileFullName ? els.profileFullName.value.trim() : '',
                institution: els.profileInstitution ? els.profileInstitution.value.trim() : '',
                role: els.profileRole ? els.profileRole.value : ''
            })
        });

        if (!res.ok) {
            const data = await res.json();
            throw new Error(data.detail || "Failed to update profile details");
        }

        const data = await res.json();
        // Update user header / top UI
        updateUserUI(data);

        if (els.profileSuccess) {
            els.profileSuccess.textContent = "Profile details updated successfully!";
            els.profileSuccess.style.display = 'block';
        }
    } catch (err) {
        if (els.profileError) {
            els.profileError.textContent = err.message || "An unexpected error occurred.";
            els.profileError.style.display = 'block';
        }
    } finally {
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.textContent = originalText;
        }
    }
}

// Update password
async function handlePasswordUpdate(e) {
    e.preventDefault();
    if (els.profileError) els.profileError.style.display = 'none';
    if (els.profileSuccess) els.profileSuccess.style.display = 'none';

    const newPwd = els.profilePassword ? els.profilePassword.value : '';
    const confirmPwd = els.profileConfirmPassword ? els.profileConfirmPassword.value : '';

    if (newPwd.length < 6) {
        if (els.profileError) {
            els.profileError.textContent = "Password must be at least 6 characters long.";
            els.profileError.style.display = 'block';
        }
        return;
    }

    if (newPwd !== confirmPwd) {
        if (els.profileError) {
            els.profileError.textContent = "Passwords do not match.";
            els.profileError.style.display = 'block';
        }
        return;
    }

    const saveBtn = document.getElementById('savePasswordBtn');
    const originalText = saveBtn ? saveBtn.textContent : 'Update Password';
    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.textContent = 'Updating...';
    }

    try {
        const token = localStorage.getItem('aether_token');
        const res = await fetch('/api/auth/password', {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({
                password: newPwd
            })
        });

        if (!res.ok) {
            const data = await res.json();
            throw new Error(data.detail || "Failed to update password");
        }

        if (els.profilePassword) els.profilePassword.value = '';
        if (els.profileConfirmPassword) els.profileConfirmPassword.value = '';

        if (els.profileSuccess) {
            els.profileSuccess.textContent = "Password updated successfully!";
            els.profileSuccess.style.display = 'block';
        }
    } catch (err) {
        if (els.profileError) {
            els.profileError.textContent = err.message || "An unexpected error occurred.";
            els.profileError.style.display = 'block';
        }
    } finally {
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.textContent = originalText;
        }
    }
}

// Load billing & subscription data
async function loadBillingData() {
    try {
        const token = localStorage.getItem('aether_token');
        const res = await fetch(`${API_BASE}/api/credits`, {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (!res.ok) throw new Error();
        const credits = await res.json();

        if (els.profilePlanName) {
            els.profilePlanName.textContent = credits.plan === 'pro' || credits.is_unlimited ? 'Pro Plan' : 'Free Trial';
        }

        if (credits.plan === 'pro' || credits.is_unlimited) {
            if (els.profilePlanCredits) els.profilePlanCredits.style.display = 'none';
            if (els.profileUpgradeBtn) els.profileUpgradeBtn.style.display = 'none';
            if (els.profileProBadge) els.profileProBadge.style.display = 'inline-block';
        } else {
            const remaining = credits.credits_remaining ?? 0;
            const limit = credits.credits_limit ?? 20;
            if (els.profilePlanCredits) {
                els.profilePlanCredits.textContent = `${remaining} / ${limit} credits remaining`;
                els.profilePlanCredits.style.display = 'block';
            }
            if (els.profileUpgradeBtn) els.profileUpgradeBtn.style.display = 'inline-block';
            if (els.profileProBadge) els.profileProBadge.style.display = 'none';
        }
    } catch (e) {
        console.error("Failed to load billing summary:", e);
    }

    // Load payment/billing history list
    await loadPaymentHistory();
}

// Fetch billing history from razorpay
async function loadPaymentHistory() {
    const list = els.billingHistoryList;
    if (!list) return;

    try {
        const token = localStorage.getItem('aether_token');
        const res = await fetch('/api/razorpay/payments', {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (!res.ok) throw new Error();
        const data = await res.json();

        list.innerHTML = '';
        if (data.length === 0) {
            list.innerHTML = `<div style="font-size: 13px; color: var(--text-secondary); text-align: center; padding: 12px 0;">No billing history found.</div>`;
            return;
        }

        data.forEach(item => {
            const dateStr = new Date(item.created_at).toLocaleDateString(undefined, {
                year: 'numeric',
                month: 'short',
                day: 'numeric'
            });
            const amtStr = (item.amount / 100).toFixed(2);
            const row = document.createElement('div');
            row.style.cssText = 'display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; background: rgba(255,255,255,0.02); border-radius: var(--radius-sm); border: 1px solid var(--surface-glass-border); font-size: 13px; gap: 12px;';
            row.innerHTML = `
                <div style="flex: 1; min-width: 0;">
                    <div style="font-weight: 500; color: var(--text-primary); text-overflow: ellipsis; overflow: hidden; white-space: nowrap;">${item.plan === 'pro' ? 'Pro Plan Upgrade' : 'Credit Topup'}</div>
                    <div style="font-size: 11px; color: var(--text-secondary); margin-top: 2px;">${dateStr} • Ref: ${item.razorpay_payment_id || 'N/A'}</div>
                </div>
                <div style="text-align: right; flex-shrink: 0;">
                    <div style="font-weight: 600; color: var(--primary-light);">${item.currency === 'INR' ? '₹' : '$'}${amtStr}</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--accent-emerald); font-weight: 600; margin-top: 2px;">${item.status || 'success'}</div>
                </div>
            `;
            list.appendChild(row);
        });
    } catch (e) {
        list.innerHTML = `<div style="font-size: 13px; color: #ef4444; text-align: center; padding: 12px 0;">Failed to load billing history.</div>`;
    }
}
