/**
 * chat.js — Streaming chat engine, markdown/math parsing, attachment tray & audio recorder
 */

import { API_BASE, els, state } from './state.js';
import { getAuthHeader, updateCreditPill } from './auth.js';
import { escapeHtml, copyTextToClipboard } from './utils.js';

export async function apiCall(endpoint, body, method = 'POST') {
    const headers = {
        'Content-Type': 'application/json',
        ...getAuthHeader()
    };

    const res = await fetch(`${API_BASE}${endpoint}`, {
        method: method,
        headers: headers,
        body: body ? JSON.stringify(body) : undefined,
    });

    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        if (res.status === 402 && err.detail && err.detail.error === 'credit_exhausted') {
            const e = new Error(err.detail.message || 'Credit limit reached');
            e.isCreditError = true;
            e.creditDetail = err.detail;
            throw e;
        }
        if (res.status === 403 && err.detail && err.detail.error === 'pro_required') {
            const e = new Error(err.detail.message || 'Pro plan required');
            e.isProError = true;
            e.creditDetail = err.detail;
            throw e;
        }
        throw new Error(
            typeof err.detail === 'string' ? err.detail :
                (err.detail?.message || `HTTP ${res.status}`)
        );
    }

    return res.json();
}

export function updateSendButtonState() {
    if (!els.sendBtn || !els.queryInput) return;
    const val = els.queryInput.value;
    const hasUploading = state.pendingAttachments.some(att => att.isLoading);
    els.sendBtn.disabled = val.trim().length === 0 || state.isLoading || hasUploading;
}

export function handleInputChange() {
    if (!els.queryInput) return;
    const val = els.queryInput.value;
    if (els.charCount) {
        els.charCount.textContent = `${val.length}/2000`;
    }
    updateSendButtonState();

    els.queryInput.style.height = 'auto';
    els.queryInput.style.height = Math.min(els.queryInput.scrollHeight, 150) + 'px';
}

export function updateQueryInputPlaceholder() {
    if (els.queryInput) {
        if (state.wikipediaMode) {
            els.queryInput.placeholder = "Search Wikipedia for datasets or topics...";
        } else {
            els.queryInput.placeholder = "Ask anything";
        }
    }
}

export function handleInputKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (els.sendBtn && !els.sendBtn.disabled) sendQuery();
    }
}

export function setAttachMenuOpen(isOpen) {
    state.attachMenuOpen = isOpen;
    if (els.attachMenu) {
        els.attachMenu.classList.toggle('open', isOpen);
        els.attachMenu.setAttribute('aria-hidden', String(!isOpen));
    }
    if (els.attachMenuBtn) {
        els.attachMenuBtn.setAttribute('aria-expanded', String(isOpen));
        els.attachMenuBtn.classList.toggle('active', isOpen);
    }
}

export function handleAttachAction(action) {
    switch (action) {
        case 'files':
            els.attachmentFileInput?.click();
            break;
        case 'pdf':
            els.pdfFileInput?.click();
            break;
        case 'paste-link':
            if (els.linkModal) {
                els.linkModal.classList.add('visible');
                setTimeout(() => els.paperUrlInput?.focus(), 100);
            }
            break;
        case 'video':
            els.videoFileInput?.click();
            break;
        case 'deep-research':
            state.deepResearchMode = true;
            if (els.modelSelect) els.modelSelect.value = 'heavy';
            if (els.verifyToggle) els.verifyToggle.checked = true;
            if (els.groundedStudyToggle) {
                els.groundedStudyToggle.checked = true;
                if (window.syncStudyGuardrails) window.syncStudyGuardrails();
            }
            renderAttachmentTray();
            break;
        case 'wikipedia':
            state.wikipediaMode = true;
            renderAttachmentTray();
            updateQueryInputPlaceholder();
            break;
        default:
            break;
    }
    setAttachMenuOpen(false);
}

export async function stagePdfFiles(pdfFiles) {
    const files = Array.from(pdfFiles || []);
    if (!files.length) return;

    for (const file of files) {
        const id = `att-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
        state.pendingAttachments.push({
            id: id,
            name: file.name,
            size: file.size,
            mime: 'application/pdf',
            isLoading: true
        });
        renderAttachmentTray();

        const formData = new FormData();
        formData.append('file', file);

        try {
            const token = localStorage.getItem('aether_token');
            const headers = {};
            if (token) {
                headers['Authorization'] = `Bearer ${token}`;
            }

            const res = await fetch('/api/upload/pdf', {
                method: 'POST',
                headers: headers,
                body: formData
            });

            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                throw new Error(errData.detail || 'Upload failed');
            }

            const data = await res.json();

            const idx = state.pendingAttachments.findIndex(item => item.id === id);
            if (idx !== -1) {
                state.pendingAttachments[idx].isLoading = false;
                state.pendingAttachments[idx].url = window.location.origin + data.url;
                state.pendingAttachments[idx].mime = 'text/url';
            }
            renderAttachmentTray();
        } catch (e) {
            console.error('Failed to upload PDF:', e);
            alert(`Failed to upload ${file.name}: ${e.message}`);
            state.pendingAttachments = state.pendingAttachments.filter(item => item.id !== id);
            renderAttachmentTray();
        }
    }
}

export function processSelectedFiles(fileList, opts = {}) {
    const files = Array.from(fileList || []);
    if (!files.length) return;

    const pdfFiles = opts.onlyVideo
        ? []
        : files.filter(file => file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf'));

    const attachmentFiles = opts.onlyPdf
        ? []
        : files.filter(file => !pdfFiles.includes(file));

    if (pdfFiles.length) {
        stagePdfFiles(pdfFiles);
    }
    if (attachmentFiles.length) {
        addPendingAttachments(attachmentFiles);
    }
}

export function addPendingAttachments(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;

    const known = new Set(state.pendingAttachments.map(item => `${item.name}-${item.size}-${item.mime}`));
    files.forEach(file => {
        const key = `${file.name}-${file.size}-${file.type}`;
        if (known.has(key)) return;
        state.pendingAttachments.push({
            id: `att-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
            name: file.name,
            size: file.size,
            mime: file.type || 'application/octet-stream',
        });
    });

    renderAttachmentTray();
}

export function renderAttachmentTray() {
    if (!els.attachmentTray) return;

    if (!state.pendingAttachments.length && !state.wikipediaMode && !state.deepResearchMode) {
        els.attachmentTray.classList.remove('visible');
        els.attachmentTray.innerHTML = '';
        return;
    }

    els.attachmentTray.classList.add('visible');

    let modeHtml = '';
    if (state.wikipediaMode) {
        modeHtml += `
            <div class="attachment-card wikipedia-mode-card" style="border-color: rgba(99, 102, 241, 0.4); background: rgba(99, 102, 241, 0.05);">
                <div class="attachment-icon-box" style="background: var(--primary); display: flex; align-items: center; justify-content: center; font-size: 18px;">🌐</div>
                <div class="attachment-info">
                    <span class="attachment-name">Wikipedia Mode Active</span>
                    <span class="attachment-type">Direct Wikipedia Search</span>
                </div>
                <button class="attachment-remove" id="disableWikiModeBtn" aria-label="Disable Wikipedia Mode">×</button>
            </div>
        `;
    }

    if (state.deepResearchMode) {
        modeHtml += `
            <div class="attachment-card deep-research-mode-card" style="border-color: rgba(167, 139, 250, 0.4); background: rgba(167, 139, 250, 0.05);">
                <div class="attachment-icon-box" style="background: var(--accent-purple); display: flex; align-items: center; justify-content: center; font-size: 18px; color: white;">🔍</div>
                <div class="attachment-info">
                    <span class="attachment-name">Deep Research Mode Active</span>
                    <span class="attachment-type">Using Heavy Reasoning Model</span>
                </div>
                <button class="attachment-remove" id="disableDeepResearchBtn" aria-label="Disable Deep Research Mode">×</button>
            </div>
        `;
    }

    const attachmentsHtml = state.pendingAttachments.map(file => {
        let kind = 'file';
        let iconSvg = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"></path><polyline points="13 2 13 9 20 9"></polyline></svg>`;
        let iconBg = '';

        if (file.isLoading) {
            kind = 'Uploading...';
            iconBg = 'background: rgba(167, 139, 250, 0.15); color: var(--accent-purple);';
            iconSvg = `<svg class="spinner" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" style="width: 18px; height: 18px; animation: spin 0.8s linear infinite;"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-dasharray="31.4" stroke-dashoffset="10"></circle></svg>`;
        } else if (file.mime === 'text/url') {
            kind = 'Link';
            iconBg = 'background: rgba(34, 211, 238, 0.15); color: var(--accent-cyan); border: 1px solid rgba(34, 211, 238, 0.3);';
            iconSvg = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width: 18px; height: 18px;"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" /><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" /></svg>`;
        } else {
            if (file.mime.startsWith('image/')) kind = 'image';
            if (file.mime.startsWith('video/')) kind = 'video';
            if (file.mime === 'application/pdf' || file.name.endsWith('.pdf')) {
                kind = 'PDF document';
                iconBg = 'background: rgba(239, 68, 68, 0.15); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.3);';
                iconSvg = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width: 18px; height: 18px;"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>`;
            }
            if (file.name.endsWith('.docx') || file.name.endsWith('.doc')) kind = 'Word document';
        }

        return `
            <div class="attachment-card">
                <div class="attachment-icon-box" style="${iconBg}">
                    ${iconSvg}
                </div>
                <div class="attachment-info">
                    <span class="attachment-name" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</span>
                    <span class="attachment-type">${kind}</span>
                </div>
                <button class="attachment-remove" data-attachment-id="${file.id}" aria-label="Remove attachment">×</button>
            </div>
        `;
    }).join('');

    els.attachmentTray.innerHTML = modeHtml + attachmentsHtml;

    els.attachmentTray.querySelectorAll('.attachment-remove').forEach(button => {
        button.addEventListener('click', () => {
            if (button.id === 'disableWikiModeBtn') {
                state.wikipediaMode = false;
                renderAttachmentTray();
                updateQueryInputPlaceholder();
            } else if (button.id === 'disableDeepResearchBtn') {
                state.deepResearchMode = false;
                if (els.modelSelect) els.modelSelect.value = 'light';
                renderAttachmentTray();
            } else {
                const attId = button.dataset.attachmentId;
                state.pendingAttachments = state.pendingAttachments.filter(item => item.id !== attId);
                renderAttachmentTray();
            }
        });
    });

    updateSendButtonState();
}

export function formatMarkdown(text) {
    if (!text) return '';
    text = text.replace(/\[(\d+)\](?!\()/g, '<span class="citation">[$1]</span>');
    text = text.replace(/【(.*?)】/g, '<span class="source-tag">$1</span>');

    text = text.replace(/\u2022/g, '-');
    text = text.replace(/([^\n])\s+-\s+/g, '$1\n\n- ');
    text = text.replace(/([a-zA-Z0-9\):])(\s*)\n-\s+/g, '$1\n\n- ');

    text = text.replace(/-\s+([^\n]+?)\s+\((\d{4})\)\s*(—|-|–)\s*([^\n]+)/g, '- **$1** <span class="paper-year">($2)</span> &mdash; <span class="paper-author">$4</span>');
    text = text.replace(/-\s+([^\n]+?)\s+\((\d{4})\)\s*(\[\d+\]|\[N\])/g, '- **$1** <span class="paper-year">($2)</span> $3');

    const mathBlocks = [];
    let processedText = text;

    processedText = processedText.replace(/\\begin\{([a-zA-Z\*]+)\}([\s\S]*?)\\end\{\1\}/g, (match) => {
        const id = `MATHDISPLAYPLACEHOLDER${mathBlocks.length}`;
        mathBlocks.push({ id, raw: match });
        return id;
    });

    processedText = processedText.replace(/\\\[([\s\S]*?)\\\]/g, (match) => {
        const id = `MATHDISPLAYPLACEHOLDER${mathBlocks.length}`;
        mathBlocks.push({ id, raw: match });
        return id;
    });

    processedText = processedText.replace(/\$\$([\s\S]*?)\$\$/g, (match) => {
        const id = `MATHDISPLAYPLACEHOLDER${mathBlocks.length}`;
        mathBlocks.push({ id, raw: match });
        return id;
    });

    processedText = processedText.replace(/\\\(([\s\S]*?)\\\)/g, (match) => {
        const id = `MATHINLINEPLACEHOLDER${mathBlocks.length}`;
        mathBlocks.push({ id, raw: match });
        return id;
    });

    processedText = processedText.replace(/\$([^\$\s](?:[^\$]*?[^\$\s])?)\$/g, (match, p1) => {
        if (p1.includes('\n')) return match;
        const id = `MATHINLINEPLACEHOLDER${mathBlocks.length}`;
        mathBlocks.push({ id, raw: match });
        return id;
    });

    if (window.marked && window.marked.parse) {
        processedText = marked.parse(processedText);
    } else {
        processedText = processedText.replace(/\n\n/g, '</p><p>').replace(/\n/g, '<br>');
    }

    mathBlocks.forEach(block => {
        processedText = processedText.replace(block.id, () => block.raw);
    });

    return processedText;
}

export function typesetMath(elements) {
    if (!elements || elements.length === 0) return;
    const runTypeset = () => {
        if (window.MathJax && window.MathJax.typesetPromise) {
            window.MathJax.typesetPromise(elements).catch(err => console.warn('MathJax error:', err));
        }
    };
    if (window.MathJax && window.MathJax.typesetPromise) {
        runTypeset();
    } else {
        const script = document.getElementById('MathJax-script');
        if (script) {
            script.addEventListener('load', runTypeset, { once: true });
        }
    }
}

export function updatePipelineStep(step) {
    if (els.pipelineStep) {
        els.pipelineStep.textContent = step ? `• ${step}` : '';
        els.pipelineStep.style.opacity = step ? '1' : '0';
    }
}

export function scrollToBottom() {
    if (!els.chatContainer) return;
    els.chatContainer.scrollTo({
        top: els.chatContainer.scrollHeight,
        behavior: 'smooth',
    });
}

export function addMessage(role, content, opts = {}) {
    const id = 'msg-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
    const div = document.createElement('div');
    div.className = `message ${role}`;
    div.id = id;

    const avatar = role === 'user' ? '👤' : '🔬';
    const attachments = Array.isArray(opts.attachments) ? opts.attachments : [];
    const attachmentsHtml = attachments.length
        ? `
            <div class="message-attachments">
                ${attachments.map(file => `
                    <span class="message-attachment-chip" title="${escapeHtml(file.name)}">
                        <span>${escapeHtml(file.name)}</span>
                    </span>
                `).join('')}
            </div>
        `
        : '';

    const copyBtnHtml = role === 'user' ? `
        <div class="message-footer" style="margin-top: 8px;">
            <div class="message-actions-group">
                <span class="message-stat btn-copy" data-copy-target=".message-content">
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/></svg>
                    Copy
                </span>
            </div>
        </div>
    ` : '';

    div.innerHTML = `
        <div class="message-avatar">${avatar}</div>
        <div class="message-body">
            <div class="message-header">
                <span class="message-sender">${role === 'user' ? 'You' : 'Aether'}</span>
                <span class="message-meta">${new Date().toLocaleTimeString()}</span>
            </div>
            <div class="message-content">${opts.isError ? content : formatMarkdown(content)}</div>
            ${attachmentsHtml}
            ${copyBtnHtml}
        </div>
    `;

    if (els.chatMessages) els.chatMessages.appendChild(div);
    scrollToBottom();

    if (!opts.isError) {
        const contentDiv = div.querySelector('.message-content');
        if (contentDiv) {
            typesetMath([contentDiv]);
            if (window.postProcessResponse) window.postProcessResponse(contentDiv);
        }
    }

    return id;
}

export function addAssistantMessage(data, stream = true) {
    const id = 'msg-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
    const div = document.createElement('div');
    div.className = 'message assistant';
    div.id = id;

    let verifyBadge = '';
    let flaggedHtml = '';
    if (data.verification) {
        const v = data.verification;
        const verdict = (v.verdict || 'unknown').toLowerCase();
        let badgeClass = 'unknown';
        let badgeIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>';
        if (verdict === 'pass') {
            badgeClass = 'pass';
            badgeIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>';
        } else if (verdict === 'partial') {
            badgeClass = 'partial';
            badgeIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';
        } else if (verdict === 'fail') {
            badgeClass = 'fail';
            badgeIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>';
        }

        const confText = v.confidence != null ? `${(v.confidence * 100).toFixed(0)}%` : '&mdash;';
        verifyBadge = `<span class="verification-badge ${badgeClass}">${badgeIcon} ${verdict.toUpperCase()} &bull; ${confText} confidence</span>`;

        if (v.flagged_claims && v.flagged_claims.length > 0) {
            flaggedHtml = `
                <div class="verification-checks">
                    <h4>Verification Checks</h4>
                    <ul>
                        ${v.flagged_claims.map(c => {
                const isVerified = c.toUpperCase().includes('VERIFIED') && !c.toUpperCase().includes('UNVERIFIED');
                const cls = isVerified ? 'verified' : 'unverified';
                const icon = isVerified
                    ? '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>'
                    : '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/></svg>';
                const cleanText = c.replace(/^[^a-zA-Z0-9("]*/, '');
                return `<li class="verif-item ${cls}"><span>${icon}</span> <span>${escapeHtml(cleanText)}</span></li>`;
            }).join('')}
                    </ul>
                </div>
            `;
        }
    }

    let warningHtml = '';
    if (data.warning) {
        warningHtml = `<div class="message-warning" style="display:flex;align-items:flex-start;gap:6px"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="flex-shrink:0;margin-top:1px"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>${escapeHtml(data.warning)}</div>`;
    }

    const stats = [];
    if (data.intent) stats.push(`<span class="message-stat">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 11-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
        Aether Optimized
    </span>`);

    if (data.datasets && data.datasets.length > 0) {
        stats.push(`<span class="message-stat clickable" style="border-color: rgba(34, 211, 238, 0.25); color: var(--accent-cyan);" onclick="openSourcesPanel(); switchSourceTab('datasets')">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle; margin-right:2px;"><path d="M12 22c5.523 0 10-2.239 10-5V7c0-2.761-4.477-5-10-5S2 4.239 2 7v10c0 2.761 4.477 5 10 5z"/><path d="M2 7c0 2.76 4.477 5 10 5s10-2.24 10-5"/><path d="M2 12c0 2.76 4.477 5 10 5s10-2.24 10-5"/></svg>
            ${data.datasets.length} Datasets
        </span>`);
    }

    const copyBtnHtml = `<span class="message-stat btn-copy" data-copy-target=".message-content">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/></svg>
        Copy
    </span>`;

    let matrixHtml = '';
    if (data.papers && data.papers.length > 0) {
        const topTitles = data.papers.slice(0, 4).map(p => p.title).join(' | ');
        matrixHtml = `<span class="message-stat btn-copy" style="color: var(--accent-emerald);" onclick="document.getElementById('queryInput').value = 'Generate a tight markdown comparison matrix table for these papers: ${topTitles.replace(/'/g, "\\'")} (Compare Methodology, Datasets, and Accuracy)'; document.getElementById('queryInput').focus(); document.getElementById('sendBtn').click();">Matrix Summary</span>`;
    }

    div.innerHTML = `
        <div class="message-avatar">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="color: var(--primary-light)">
                <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>
            </svg>
        </div>
        <div class="message-body">
            <div class="message-header" style="justify-content: flex-start; margin-bottom: 4px;">
                <span class="message-sender" style="color: var(--primary-light); font-weight: 600;">Aether</span>
            </div>
            <div class="message-content"></div>
            ${verifyBadge}
            ${flaggedHtml}
            ${warningHtml}
            <div class="message-footer" style="opacity: 0; transition: opacity 0.5s;">
                <div class="message-stats-group">${stats.join('')}</div>
                <div class="message-actions-group">
                    ${copyBtnHtml} ${matrixHtml}
                </div>
            </div>
        </div>
    `;

    if (els.chatMessages) els.chatMessages.appendChild(div);
    const contentDiv = div.querySelector('.message-content');
    const footerDiv = div.querySelector('.message-footer');

    const textRaw = data.answer || '';

    if (!stream) {
        contentDiv.innerHTML = formatMarkdown(textRaw);
        typesetMath([contentDiv]);
        if (window.postProcessResponse) window.postProcessResponse(contentDiv);
        footerDiv.style.opacity = '1';
    } else {
        let currIdx = 0;
        const streamInterval = setInterval(() => {
            currIdx += Math.floor(Math.random() * 35) + 25;
            const finished = currIdx >= textRaw.length;
            if (finished) {
                currIdx = textRaw.length;
                clearInterval(streamInterval);
            }

            contentDiv.innerHTML = formatMarkdown(textRaw.substring(0, currIdx));

            if (finished) {
                footerDiv.style.opacity = '1';
                typesetMath([contentDiv]);
                if (window.postProcessResponse) window.postProcessResponse(contentDiv);
            }

            if (els.chatContainer && (els.chatContainer.scrollHeight - els.chatContainer.scrollTop - els.chatContainer.clientHeight < 150)) {
                scrollToBottom();
            }
        }, 12);
    }

    scrollToBottom();
    return id;
}

export function addLoadingMessage() {
    const id = 'loading-' + Date.now();
    const div = document.createElement('div');
    div.className = 'message assistant';
    div.id = id;

    div.innerHTML = `
        <div class="message-avatar" style="font-size:24px; color: var(--primary-light);">✨</div>
        <div class="message-body">
            <div class="message-loading">
                <div class="typing-dots">
                    <span></span><span></span><span></span>
                </div>
                <span>Reasoning and verifying sources...</span>
            </div>
        </div>
    `;

    if (els.chatMessages) els.chatMessages.appendChild(div);
    scrollToBottom();
    return id;
}

export function removeMessage(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

export async function sendQuery() {
    if (!els.queryInput) return;
    const query = els.queryInput.value.trim();
    if (!query || state.isLoading) return;

    setAttachMenuOpen(false);
    state.isLoading = true;
    if (els.sendBtn) els.sendBtn.disabled = true;

    if (els.welcomeScreen) {
        els.welcomeScreen.style.display = 'none';
    }

    const outgoingAttachments = [...state.pendingAttachments];
    const isWikipediaQuery = state.wikipediaMode;

    const msgAttachments = [...outgoingAttachments];
    if (isWikipediaQuery) {
        msgAttachments.push({ name: 'Wikipedia Mode', size: 0, mime: 'text/wikipedia' });
    }

    const urlAttachments = outgoingAttachments.filter(att => att.mime === 'text/url');
    const urlString = urlAttachments.map(att => att.url).join(' ');
    const fullQuery = urlString ? `${query} ${urlString}`.trim() : query;

    addMessage('user', query, { attachments: msgAttachments });
    state.messages.push({ role: 'user', content: fullQuery });

    state.pendingAttachments = [];
    state.wikipediaMode = false;
    renderAttachmentTray();
    updateQueryInputPlaceholder();

    els.queryInput.value = '';
    handleInputChange();

    const loadingId = addLoadingMessage();
    if (window.setSourcesLoading) window.setSourcesLoading();

    const steps = isWikipediaQuery
        ? ["Searching Wikipedia", "Fetching Article Details", "Synthesizing Summary"]
        : ["Planning Strategy", "Searching Knowledge Graph", "Retrieving Papers", "Semantic Vector Search", "Applying MMR Reranking", "Reasoning & Synthesis", "Verifying for Hallucinations"];
    let stepIdx = 0;
    updatePipelineStep(steps[stepIdx]);
    const stepInterval = setInterval(() => {
        if (stepIdx < steps.length - 1) {
            stepIdx++;
            updatePipelineStep(steps[stepIdx]);
        }
    }, 2000);

    const useChat = state.messages.length > 2;

    try {
        let data;
        const requestData = {
            top_k: els.topK ? parseInt(els.topK.value) : 5,
            min_similarity: els.minSim ? parseFloat(els.minSim.value) / 100 : 0.1,
            temperature: els.temperature ? parseFloat(els.temperature.value) : 0.0,
            use_heavy: els.modelSelect ? els.modelSelect.value === 'heavy' : false,
            verify: els.verifyToggle ? els.verifyToggle.checked : true,
            mode: isWikipediaQuery ? 'wikipedia' : 'research',
        };

        if (useChat) {
            const cleanMessages = state.messages
                .filter(m => m && (m.role === 'user' || m.role === 'assistant') && m.content)
                .map(m => ({
                    role: String(m.role),
                    content: typeof m.content === 'string' ? m.content : String(m.content)
                }));
            data = await apiCall('/api/chat', {
                ...requestData,
                messages: cleanMessages,
            });
        } else {
            data = await apiCall('/api/research', {
                ...requestData,
                query: fullQuery,
            });
        }

        clearInterval(stepInterval);
        updatePipelineStep("Complete");
        setTimeout(() => updatePipelineStep(null), 2000);

        removeMessage(loadingId);

        state.messages.push({ role: 'assistant', content: data.answer, data: data });
        const assistantMsgId = addAssistantMessage(data);
        state.messageData.set(assistantMsgId, data);

        state.lastResponse = data;
        if (window.updateSourcesPanel) window.updateSourcesPanel(data);

        if (data.credits) {
            updateCreditPill(data.credits);
        }

        if ((data.papers && data.papers.length > 0) || (data.chunks && data.chunks.length > 0)) {
            if (!state.sourcesOpen && window.toggleSourcesPanel) {
                window.toggleSourcesPanel();
            }
        }

        if (window.saveToHistory) window.saveToHistory(query, data);

    } catch (err) {
        clearInterval(stepInterval);
        updatePipelineStep(null);
        removeMessage(loadingId);

        if (err.isCreditError && err.creditDetail) {
            const d = err.creditDetail;
            const resetAt = d.reset_at ? new Date(d.reset_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'midnight';
            addMessage('assistant',
                `<div style="display:flex;flex-direction:column;gap:10px;padding:14px 16px;border-radius:12px;background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.3);">
                  <div style="display:flex;align-items:center;gap:8px;font-weight:700;color:#ef4444;font-size:14px;">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                    Daily Credit Limit Reached
                  </div>
                  <div style="color:var(--text-secondary);font-size:13px;line-height:1.5;">
                    You've used all <strong>${d.credits_limit || 20} daily credits</strong>. Credits reset at <strong>${resetAt}</strong>.
                  </div>
                  <div style="display:flex;gap:8px;flex-wrap:wrap;">
                    <a href="/upgrade" style="display:inline-flex;align-items:center;gap:6px;padding:7px 14px;border-radius:8px;background:linear-gradient(135deg,#6366f1,#a78bfa);color:white;font-size:12px;font-weight:600;text-decoration:none;">
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>
                      Upgrade to Pro
                    </a>
                    <span style="display:inline-flex;align-items:center;padding:7px 14px;border-radius:8px;background:rgba(99,102,241,0.1);border:1px solid rgba(99,102,241,0.2);color:var(--primary-light);font-size:12px;">Resets ${resetAt} UTC</span>
                  </div>
                </div>`,
                { isError: false }
            );
            updateCreditPill({ plan: 'free', credits_remaining: 0, credits_limit: d.credits_limit || 20, is_unlimited: false });
        } else if (err.isProError) {
            addMessage('assistant',
                `<div style="display:flex;flex-direction:column;gap:10px;padding:14px 16px;border-radius:12px;background:rgba(167,139,250,0.08);border:1px solid rgba(167,139,250,0.3);">
                  <div style="display:flex;align-items:center;gap:8px;font-weight:700;color:#a78bfa;font-size:14px;">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>
                    Pro Feature
                  </div>
                  <div style="color:var(--text-secondary);font-size:13px;line-height:1.5;">${err.message}</div>
                  <a href="/upgrade" style="display:inline-flex;align-items:center;gap:6px;padding:7px 14px;width:fit-content;border-radius:8px;background:linear-gradient(135deg,#6366f1,#a78bfa);color:white;font-size:12px;font-weight:600;text-decoration:none;">Upgrade to Pro →</a>
                </div>`,
                { isError: false }
            );
        } else {
            addMessage('assistant', `(!) Error: ${err.message}`, { isError: true });
        }
    } finally {
        state.isLoading = false;
        updateSendButtonState();
    }
}

export function toggleSpeechToText() {
    if (state.audioRecording) {
        stopSpeechToText(false);
    } else {
        startSpeechToText();
    }
}

export async function startSpeechToText() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        state.audioStream = stream;
        state.audioChunks = [];
        state.discardRecording = false;

        const mediaRecorder = new MediaRecorder(stream);
        state.mediaRecorder = mediaRecorder;

        mediaRecorder.ondataavailable = (event) => {
            if (event.data && event.data.size > 0) {
                state.audioChunks.push(event.data);
            }
        };

        mediaRecorder.onstop = async () => {
            if (state.animationFrameId) cancelAnimationFrame(state.animationFrameId);
            if (state.audioContext) {
                try { await state.audioContext.close(); } catch (_) { }
            }
            if (state.audioStream) {
                state.audioStream.getTracks().forEach(track => track.stop());
            }

            if (state.discardRecording) {
                if (els.voiceRecordingOverlay) {
                    els.voiceRecordingOverlay.classList.remove('visible');
                    els.voiceRecordingOverlay.classList.remove('transcribing');
                }
                if (els.composerMain) els.composerMain.classList.remove('recording');
                if (els.micBtn) els.micBtn.classList.remove('active');
                state.audioRecording = false;
                return;
            }

            // Enter transcribing state
            if (els.voiceRecordingOverlay) {
                els.voiceRecordingOverlay.classList.add('transcribing');
                if (els.voicePreviewText) {
                    els.voicePreviewText.textContent = "Transcribing voice input...";
                }
            }

            const audioBlob = new Blob(state.audioChunks, { type: 'audio/webm' });
            state.audioRecording = false;

            if (audioBlob.size === 0) {
                // Exit transcribing state and hide
                if (els.voiceRecordingOverlay) {
                    els.voiceRecordingOverlay.classList.remove('visible');
                    els.voiceRecordingOverlay.classList.remove('transcribing');
                }
                if (els.composerMain) els.composerMain.classList.remove('recording');
                if (els.micBtn) els.micBtn.classList.remove('active');
                return;
            }

            const formData = new FormData();
            formData.append('file', audioBlob, 'speech.webm');

            try {
                if (els.queryInput) els.queryInput.placeholder = "Transcribing voice input...";
                const res = await fetch(`${API_BASE}/api/voice/transcribe`, {
                    method: 'POST',
                    headers: getAuthHeader(),
                    body: formData
                });
                if (res.ok) {
                    const data = await res.json();
                    if (data.text && data.text.trim()) {
                        if (els.queryInput) {
                            els.queryInput.value = (els.queryInput.value ? els.queryInput.value + ' ' : '') + data.text.trim();
                            handleInputChange();
                            els.queryInput.focus();
                        }
                    }
                }
            } catch (e) {
                console.error("Transcription failed:", e);
            } finally {
                // Exit transcribing state and hide overlay
                if (els.voiceRecordingOverlay) {
                    els.voiceRecordingOverlay.classList.remove('visible');
                    els.voiceRecordingOverlay.classList.remove('transcribing');
                    if (els.voicePreviewText) {
                        els.voicePreviewText.textContent = "Listening...";
                    }
                }
                if (els.composerMain) els.composerMain.classList.remove('recording');
                if (els.micBtn) els.micBtn.classList.remove('active');
                updateQueryInputPlaceholder();
            }
        };

        mediaRecorder.start(100);
        state.audioRecording = true;

        if (els.micBtn) els.micBtn.classList.add('active');
        if (els.composerMain) els.composerMain.classList.add('recording');
        if (els.voiceRecordingOverlay) els.voiceRecordingOverlay.classList.add('visible');

        initVoiceWaveVisualizer(stream);

    } catch (err) {
        console.error("Microphone access denied:", err);
        alert("Microphone access is required for voice input.");
    }
}

export function stopSpeechToText(discard = false) {
    state.discardRecording = discard;
    if (state.mediaRecorder && state.mediaRecorder.state !== 'inactive') {
        state.mediaRecorder.stop();
    }
}

function initVoiceWaveVisualizer(stream) {
    const waveContainer = els.voiceWaveContainer;
    if (!waveContainer) return;

    waveContainer.innerHTML = '';
    const numBars = 16;
    const bars = [];
    for (let i = 0; i < numBars; i++) {
        const bar = document.createElement('div');
        bar.className = 'voice-wave-bar';
        bar.style.setProperty('--i', i);
        waveContainer.appendChild(bar);
        bars.push(bar);
    }

    try {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        const audioCtx = new AudioContext();
        state.audioContext = audioCtx;
        const analyser = audioCtx.createAnalyser();
        analyser.fftSize = 64;
        state.audioAnalyser = analyser;

        const source = audioCtx.createMediaStreamSource(stream);
        source.connect(analyser);

        const dataArray = new Uint8Array(analyser.frequencyBinCount);
        waveContainer.classList.remove('fallback-animated');

        const updateWave = () => {
            if (!state.audioRecording) return;
            analyser.getByteFrequencyData(dataArray);

            for (let i = 0; i < numBars; i++) {
                const val = dataArray[i % dataArray.length];
                const h = Math.max(6, Math.min(36, (val / 255) * 40));
                if (bars[i]) bars[i].style.height = `${h}px`;
            }

            state.animationFrameId = requestAnimationFrame(updateWave);
        };
        updateWave();

    } catch (e) {
        console.error("Wave visualizer init failed:", e);
        // Fallback CSS animation
        waveContainer.classList.add('fallback-animated');
    }
}

window.addMessage = addMessage;
window.addAssistantMessage = addAssistantMessage;
window.sendQuery = sendQuery;
