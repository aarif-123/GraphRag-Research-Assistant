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

let modalState = {
    zoom: 1,
    panX: 0,
    panY: 0,
    isDragging: false,
    startX: 0,
    startY: 0
};

export function openMermaidModal(svgContent, title = 'Interactive Diagram Viewer') {
    const modal = document.getElementById('mermaid-modal');
    const canvas = document.getElementById('mermaidModalCanvas');
    const titleEl = document.querySelector('#mermaid-modal .mermaid-modal-title');

    if (!modal || !canvas) return;

    if (titleEl) {
        titleEl.innerHTML = `
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="color: var(--accent-cyan, #22d3ee);">
                <polygon points="12 2 2 22 22 22"></polygon>
            </svg>
            ${escapeHtml(title)}
        `;
    }

    canvas.innerHTML = svgContent;

    // Reset pan & zoom
    modalState.zoom = 1;
    modalState.panX = 0;
    modalState.panY = 0;
    updateModalCanvasTransform();

    modal.style.display = 'flex';
    requestAnimationFrame(() => {
        modal.classList.add('active');
    });
}

export function closeMermaidModal() {
    const modal = document.getElementById('mermaid-modal');
    if (!modal) return;

    modal.classList.remove('active');
    setTimeout(() => {
        modal.style.display = 'none';
    }, 300);
}

function updateModalCanvasTransform() {
    const canvas = document.getElementById('mermaidModalCanvas');
    if (canvas) {
        canvas.style.transform = `translate(${modalState.panX}px, ${modalState.panY}px) scale(${modalState.zoom})`;
    }
}

export function initMermaidModal() {
    const modal = document.getElementById('mermaid-modal');
    const viewport = document.getElementById('mermaidModalViewport');
    const closeBtn = document.getElementById('mermaidCloseBtn');
    const zoomIn = document.getElementById('btnMermaidZoomIn');
    const zoomOut = document.getElementById('btnMermaidZoomOut');
    const resetBtn = document.getElementById('btnMermaidReset');

    if (!modal) return;

    if (zoomIn) {
        zoomIn.addEventListener('click', () => {
            modalState.zoom = Math.min(4, modalState.zoom + 0.25);
            updateModalCanvasTransform();
        });
    }

    if (zoomOut) {
        zoomOut.addEventListener('click', () => {
            modalState.zoom = Math.max(0.25, modalState.zoom - 0.25);
            updateModalCanvasTransform();
        });
    }

    if (resetBtn) {
        resetBtn.addEventListener('click', () => {
            modalState.zoom = 1;
            modalState.panX = 0;
            modalState.panY = 0;
            updateModalCanvasTransform();
        });
    }

    if (closeBtn) {
        closeBtn.addEventListener('click', closeMermaidModal);
    }

    modal.addEventListener('click', (e) => {
        if (e.target === modal) closeMermaidModal();
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && modal.classList.contains('active')) {
            closeMermaidModal();
        }
    });

    // Mouse Dragging (Panning)
    if (viewport) {
        viewport.addEventListener('mousedown', (e) => {
            modalState.isDragging = true;
            modalState.startX = e.clientX - modalState.panX;
            modalState.startY = e.clientY - modalState.panY;
        });

        window.addEventListener('mousemove', (e) => {
            if (!modalState.isDragging) return;
            modalState.panX = e.clientX - modalState.startX;
            modalState.panY = e.clientY - modalState.startY;
            updateModalCanvasTransform();
        });

        window.addEventListener('mouseup', () => {
            modalState.isDragging = false;
        });

        // Wheel Zoom
        viewport.addEventListener('wheel', (e) => {
            e.preventDefault();
            const delta = e.deltaY < 0 ? 0.15 : -0.15;
            modalState.zoom = Math.max(0.25, Math.min(4, modalState.zoom + delta));
            updateModalCanvasTransform();
        }, { passive: false });
    }
}

export function cleanAndSanitizeMermaid(code) {
    if (!code) return '';

    // 1. Remove Markdown code block syntax if present inside text
    let clean = code.replace(/```mermaid/gi, '').replace(/```/g, '').trim();

    // 2. Normalize line breaks
    clean = clean.replace(/\r\n/g, '\n');

    // 3. Remove unneeded markdown formatting inside node text (e.g., **bold**, *italic*)
    clean = clean.replace(/\*\*([^*]+)\*\*/g, '$1').replace(/\*([^*]+)\*/g, '$1');

    // 4. Wrap unquoted node labels in [...] with double quotes
    // Handles nodeId[Label Text (with parens, colons: etc)] -> nodeId["Label Text (with parens, colons: etc)"]
    clean = clean.replace(/([\w-]+)\[\s*([^"\]\n][^\]\n]*?)\s*\]/g, (match, id, label) => {
        const safeLabel = label.replace(/"/g, "'");
        return `${id}["${safeLabel}"]`;
    });

    // 5. Wrap unquoted node labels in ((...))
    clean = clean.replace(/([\w-]+)\(\(\s*([^"\)\n][^\)\n]*?)\s*\)\)/g, (match, id, label) => {
        const safeLabel = label.replace(/"/g, "'");
        return `${id}(("${safeLabel}"))`;
    });

    // 6. Wrap unquoted node labels in (...)
    clean = clean.replace(/([\w-]+)\(\s*([^"\)\n][^\)\n]*?)\s*\)/g, (match, id, label) => {
        if (id.toLowerCase() === 'subgraph') return match;
        const safeLabel = label.replace(/"/g, "'");
        return `${id}("${safeLabel}")`;
    });

    // 7. Ensure valid graph directive
    const validDirectives = [
        'graph', 'flowchart', 'sequenceDiagram', 'classDiagram',
        'stateDiagram', 'erDiagram', 'gantt', 'pie', 'gitGraph', 'mindmap', 'timeline'
    ];

    const lines = clean.split('\n');
    let firstLine = lines[0].trim();
    const hasDirective = validDirectives.some(dir => firstLine.toLowerCase().startsWith(dir.toLowerCase()));

    if (!hasDirective) {
        clean = 'graph TD\n' + clean;
    }

    return clean;
}

export async function postProcessResponse(container) {
    if (!container) return;

    const codeBlocks = container.querySelectorAll('pre code.language-mermaid, pre.mermaid, code.language-mermaid');

    if (!codeBlocks || codeBlocks.length === 0) return;

    if (!window.mermaid) {
        console.warn('Mermaid library not loaded');
        return;
    }

    for (let i = 0; i < codeBlocks.length; i++) {
        const codeBlock = codeBlocks[i];
        const preElement = codeBlock.closest('pre') || codeBlock;

        if (preElement.getAttribute('data-mermaid-processed')) continue;
        preElement.setAttribute('data-mermaid-processed', 'true');

        let rawMermaid = codeBlock.textContent || codeBlock.innerText || '';

        // Decode HTML entities safely
        const doc = new DOMParser().parseFromString(rawMermaid, 'text/html');
        rawMermaid = doc.documentElement.textContent || rawMermaid;

        if (!rawMermaid.trim()) continue;

        const sanitizedCode = cleanAndSanitizeMermaid(rawMermaid);
        const uniqueId = 'mermaid-' + Date.now() + '-' + Math.floor(Math.random() * 10000);

        try {
            const { svg } = await window.mermaid.render(uniqueId, sanitizedCode);

            const wrapper = document.createElement('div');
            wrapper.className = 'mermaid-container';
            wrapper.style.cssText = `
                position: relative;
                margin: 1.25rem 0;
                padding: 1.25rem;
                background: rgba(15, 23, 42, 0.65);
                border: 1px solid var(--glass-border, rgba(255, 255, 255, 0.1));
                border-radius: 12px;
                overflow-x: auto;
                cursor: pointer;
            `;

            wrapper.innerHTML = `
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; font-size: 12px; color: var(--accent-cyan, #22d3ee); font-weight: 500;">
                    <span style="display: flex; align-items: center; gap: 6px;">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 2 22 22 22"/></svg>
                        Taxonomy / Architecture Flowchart
                    </span>
                    <button class="btn-expand-mermaid" style="background: rgba(30, 41, 59, 0.8); border: 1px solid var(--glass-border, rgba(255, 255, 255, 0.15)); color: var(--text-primary, #fff); padding: 4px 10px; border-radius: 6px; font-size: 11px; cursor: pointer; display: flex; align-items: center; gap: 4px; transition: all 0.2s ease;">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>
                        Expand Diagram
                    </button>
                </div>
                <div class="mermaid" style="display: flex; justify-content: center;">${svg}</div>
            `;

            const openHandler = (e) => {
                e.stopPropagation();
                openMermaidModal(svg, 'Taxonomy / Architecture Flowchart');
            };

            wrapper.addEventListener('click', openHandler);

            preElement.parentNode.replaceChild(wrapper, preElement);
        } catch (err) {
            console.warn('First render attempt failed, trying fallback sanitization:', err);

            // Clean up any error DOM nodes inserted by Mermaid
            document.querySelectorAll(`[id*="${uniqueId}"]`).forEach(el => el.remove());

            try {
                // Secondary fallback: strip all non-alphanumeric chars from labels
                const fallbackCode = sanitizedCode.replace(/\[\s*"([^"]+)"\s*\]/g, (m, lbl) => {
                    const cleanLbl = lbl.replace(/[^a-zA-Z0-9\s-_]/g, '');
                    return `["${cleanLbl}"]`;
                });

                const fallbackId = uniqueId + '-fallback';
                const { svg } = await window.mermaid.render(fallbackId, fallbackCode);

                const wrapper = document.createElement('div');
                wrapper.className = 'mermaid-container';
                wrapper.style.cssText = `
                    position: relative;
                    margin: 1.25rem 0;
                    padding: 1.25rem;
                    background: rgba(15, 23, 42, 0.65);
                    border: 1px solid var(--glass-border, rgba(255, 255, 255, 0.1));
                    border-radius: 12px;
                    overflow-x: auto;
                    cursor: pointer;
                `;
                wrapper.innerHTML = `
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; font-size: 12px; color: var(--accent-cyan, #22d3ee); font-weight: 500;">
                        <span style="display: flex; align-items: center; gap: 6px;">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 2 22 22 22"/></svg>
                            Taxonomy / Architecture Flowchart
                        </span>
                        <button class="btn-expand-mermaid" style="background: rgba(30, 41, 59, 0.8); border: 1px solid var(--glass-border, rgba(255, 255, 255, 0.15)); color: var(--text-primary, #fff); padding: 4px 10px; border-radius: 6px; font-size: 11px; cursor: pointer; display: flex; align-items: center; gap: 4px; transition: all 0.2s ease;">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>
                            Expand Diagram
                        </button>
                    </div>
                    <div class="mermaid" style="display: flex; justify-content: center;">${svg}</div>
                `;
                wrapper.addEventListener('click', (e) => {
                    e.stopPropagation();
                    openMermaidModal(svg, 'Taxonomy / Architecture Flowchart');
                });
                preElement.parentNode.replaceChild(wrapper, preElement);
            } catch (fallbackErr) {
                console.error('All Mermaid rendering attempts failed:', fallbackErr);
                document.querySelectorAll(`[id*="${uniqueId}"]`).forEach(el => el.remove());
                preElement.removeAttribute('data-mermaid-processed');
            }
        }
    }
}

// Global exposure
window.cleanAndSanitizeMermaid = cleanAndSanitizeMermaid;
window.postProcessResponse = postProcessResponse;
window.openMermaidModal = openMermaidModal;
window.closeMermaidModal = closeMermaidModal;
