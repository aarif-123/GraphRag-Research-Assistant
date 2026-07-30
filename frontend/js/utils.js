/**
 * utils.js — Helper and utility functions
 */

import { els, state } from './state.js';

export function escapeHtml(str) {
    if (!str) return '';
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

export async function copyTextToClipboard(text) {
    if (!text) return false;
    try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(text);
            return true;
        }
        // Fallback for older browsers
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        const successful = document.execCommand('copy');
        document.body.removeChild(textarea);
        return successful;
    } catch (e) {
        console.error("Clipboard copy failed:", e);
        return false;
    }
}

export function showToast(message, type = 'info', duration = 3000) {
    const existing = document.querySelector('.aether-toast');
    if (existing) existing.remove();

    const toast = document.createElement('div');
    toast.className = `aether-toast toast-${type}`;
    let iconSvg = '';
    if (type === 'success') {
        iconSvg = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>`;
    } else if (type === 'error') {
        iconSvg = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
    } else {
        iconSvg = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>`;
    }

    toast.innerHTML = `${iconSvg}<span>${escapeHtml(message)}</span>`;
    toast.style.cssText = `
        position: fixed;
        bottom: 24px;
        right: 24px;
        z-index: 10000;
        background: var(--bg-elevated, #1e1e2d);
        color: var(--text-primary, #fff);
        border: 1px solid var(--glass-border, rgba(255,255,255,0.1));
        border-radius: 8px;
        padding: 12px 18px;
        font-size: 13px;
        display: flex;
        align-items: center;
        gap: 10px;
        box-shadow: 0 10px 25px rgba(0,0,0,0.3);
        animation: toastIn 0.3s cubic-bezier(0.16, 1, 0.3, 1);
    `;

    document.body.appendChild(toast);

    setTimeout(() => {
        toast.style.animation = 'toastOut 0.3s cubic-bezier(0.16, 1, 0.3, 1) forwards';
        setTimeout(() => toast.remove(), 300);
    }, duration);
}

export function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

export function formatTimestamp(isoStr) {
    if (!isoStr) return '';
    try {
        const d = new Date(isoStr);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch (e) {
        return '';
    }
}

export function isMobileViewport() {
    return window.innerWidth <= 768;
}

export function toggleSidebar() {
    if (isMobileViewport()) {
        if (els.sidebar?.classList.contains('mobile-open')) {
            closeMobileSidebar();
        } else {
            openMobileSidebar();
        }
    } else {
        els.sidebar?.classList.toggle('collapsed');
    }
}

export function openMobileSidebar() {
    els.sidebar?.classList.remove('collapsed');
    els.sidebar?.classList.add('mobile-open');
    document.body.classList.add('sidebar-mobile-open');
}

export function closeMobileSidebar() {
    els.sidebar?.classList.remove('mobile-open');
    document.body.classList.remove('sidebar-mobile-open');
}

export function initMobileSidebar() {
    if (isMobileViewport()) {
        els.sidebar?.classList.remove('collapsed');
    }

    window.addEventListener('resize', () => {
        if (!isMobileViewport()) {
            closeMobileSidebar();
        }
    });

    document.addEventListener('click', (e) => {
        if (!isMobileViewport()) return;
        const historyItem = e.target.closest('.history-item');
        if (historyItem && els.sidebar?.classList.contains('mobile-open')) {
            closeMobileSidebar();
        }
    });

    if (els.sourcesPanel) {
        document.addEventListener('touchstart', (e) => {
            if (!isMobileViewport()) return;
            if (state.sourcesOpen && !els.sourcesPanel.contains(e.target) && e.target !== els.sourcePanelToggle) {
                if (window.setSourcesPanelOpen) window.setSourcesPanelOpen(false);
            }
        }, { passive: true });
    }
}

export function initMermaidModal() {
    const modal = document.getElementById('mermaidModal');
    const container = document.getElementById('mermaidModalContainer');
    const closeBtn = document.getElementById('mermaidModalClose');
    const zoomIn = document.getElementById('mermaidZoomIn');
    const zoomOut = document.getElementById('mermaidZoomOut');
    const resetZoom = document.getElementById('mermaidResetZoom');
    const titleEl = document.getElementById('mermaidModalTitle');

    if (!modal || !container) return;

    let zoomLevel = 1;

    const setZoom = (level) => {
        zoomLevel = Math.max(0.5, Math.min(3, level));
        const svg = container.querySelector('svg');
        if (svg) {
            svg.style.transform = `scale(${zoomLevel})`;
            svg.style.transformOrigin = 'center center';
            svg.style.transition = 'transform 0.2s ease';
        }
    };

    if (zoomIn) zoomIn.addEventListener('click', () => setZoom(zoomLevel + 0.2));
    if (zoomOut) zoomOut.addEventListener('click', () => setZoom(zoomLevel - 0.2));
    if (resetZoom) resetZoom.addEventListener('click', () => setZoom(1));

    if (closeBtn) {
        closeBtn.addEventListener('click', () => {
            modal.classList.remove('visible');
        });
    }

    modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.classList.remove('visible');
    });
}
