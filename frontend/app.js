/**
 * Aether Research Assistant — Frontend Main Entry Point (ES Module)
 */

import { initEls, els, state, $, $$ } from './js/state.js';
import {
    isMobileViewport, toggleSidebar, openMobileSidebar, closeMobileSidebar,
    initMobileSidebar, initMermaidModal, postProcessResponse, openMermaidModal,
    closeMermaidModal, copyTextToClipboard
} from './js/utils.js';
import {
    loadSettingsFromLocalStorage, saveSettingsToLocalStorage, resetSettingsToDefaults, syncStudyGuardrails
} from './js/settings.js';
import { checkHealth, showHealthModal } from './js/health.js';
import { initAuth, startRazorpayCheckout } from './js/auth.js';
import { loadHistory, renderHistory, startNewChat } from './js/history.js';
import {
    toggleSourcesPanel, openSourcesPanel, setSourcesPanelOpen, switchSourceTab,
    renderGraph, updateSourcesPanel
} from './js/sources.js';
import {
    sendQuery, handleInputChange, handleInputKeydown, renderAttachmentTray,
    setAttachMenuOpen, handleAttachAction, processSelectedFiles, toggleSpeechToText, stopSpeechToText,
    updateQueryInputPlaceholder
} from './js/chat.js';

// Global exports for inline HTML event handlers
window.toggleSourcesPanel = toggleSourcesPanel;
window.openSourcesPanel = openSourcesPanel;
window.setSourcesPanelOpen = setSourcesPanelOpen;
window.switchSourceTab = switchSourceTab;
window.updateSourcesPanel = updateSourcesPanel;
window.renderGraph = renderGraph;
window.postProcessResponse = postProcessResponse;
window.openMermaidModal = openMermaidModal;
window.closeMermaidModal = closeMermaidModal;

// INIT ON DOM CONTENT LOADED
document.addEventListener('DOMContentLoaded', () => {
    initEls();

    if (window.mermaid) {
        try {
            mermaid.initialize({
                startOnLoad: false,
                theme: 'dark',
                securityLevel: 'loose',
                suppressErrorAlerts: true
            });
        } catch (e) {
            console.error('Failed to initialize Mermaid:', e);
        }
    }

    loadSettingsFromLocalStorage();
    initEventListeners();
    initPanelResizer();
    initMobileSidebar();
    checkHealth();
    setInterval(checkHealth, 30000);
    initAuth();
    renderAttachmentTray();
    if (els.queryInput) els.queryInput.focus();
});

// PANEL RESIZER DRAG
function initPanelResizer() {
    const resizer = document.getElementById('panelResizer');
    const sourcesPanel = document.getElementById('sourcesPanel');
    const body = document.body;

    if (!resizer || !sourcesPanel) return;

    let isResizing = false;

    resizer.addEventListener('mousedown', (e) => {
        isResizing = true;
        resizer.classList.add('active');
        body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
        e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
        if (!isResizing) return;
        let newWidth = window.innerWidth - e.clientX;
        if (newWidth < 300) newWidth = 300;
        if (newWidth > 800) newWidth = 800;

        document.documentElement.style.setProperty('--sources-width', newWidth + 'px');
        sourcesPanel.style.width = newWidth + 'px';

        if (sourcesPanel.classList.contains('open') && window.lastGraphPapers) {
            clearTimeout(window.resizeGraphTimeout);
            window.resizeGraphTimeout = setTimeout(() => {
                const tabGraph = document.getElementById('tabGraph');
                if (tabGraph && tabGraph.classList.contains('active')) {
                    renderGraph(window.lastGraphPapers);
                }
            }, 100);
        }
    });

    document.addEventListener('mouseup', () => {
        if (isResizing) {
            isResizing = false;
            resizer.classList.remove('active');
            body.style.cursor = 'default';
            document.body.style.userSelect = 'auto';
        }
    });
}

function initEventListeners() {
    // Sidebar
    if (els.sidebarToggle) {
        els.sidebarToggle.addEventListener('click', toggleSidebar);
    }
    if (els.mobileMenuBtn) {
        els.mobileMenuBtn.addEventListener('click', () => {
            if (isMobileViewport()) {
                openMobileSidebar();
            } else {
                if (els.sidebar) els.sidebar.classList.remove('collapsed');
            }
        });
    }
    if (els.sidebarOverlay) {
        els.sidebarOverlay.addEventListener('click', closeMobileSidebar);
        els.sidebarOverlay.addEventListener('touchstart', closeMobileSidebar, { passive: true });
    }

    // Settings Sliders & Inputs
    if (els.topK) {
        els.topK.addEventListener('input', () => {
            if (els.topKValue) els.topKValue.textContent = els.topK.value;
            saveSettingsToLocalStorage();
        });
    }
    if (els.minSim) {
        els.minSim.addEventListener('input', () => {
            if (els.minSimValue) els.minSimValue.textContent = (els.minSim.value / 100).toFixed(2);
            saveSettingsToLocalStorage();
        });
    }
    if (els.temperature) {
        els.temperature.addEventListener('input', () => {
            if (els.temperatureValue) els.temperatureValue.textContent = parseFloat(els.temperature.value).toFixed(1);
            saveSettingsToLocalStorage();
        });
    }
    if (els.verifyToggle) {
        els.verifyToggle.addEventListener('change', () => {
            saveSettingsToLocalStorage();
        });
    }
    if (els.groundedStudyToggle) {
        els.groundedStudyToggle.addEventListener('change', () => {
            syncStudyGuardrails();
            saveSettingsToLocalStorage();
        });
    }
    if (els.modelSelect) {
        els.modelSelect.addEventListener('change', () => {
            state.deepResearchMode = els.modelSelect.value === 'heavy';
            renderAttachmentTray();
            saveSettingsToLocalStorage();
        });
    }

    const resetSettingsBtn = document.getElementById('resetSettingsBtn');
    if (resetSettingsBtn) {
        resetSettingsBtn.addEventListener('click', resetSettingsToDefaults);
    }

    const historySearchInput = document.getElementById('historySearchInput');
    if (historySearchInput) {
        historySearchInput.addEventListener('input', () => {
            renderHistory();
        });
    }

    // Chat Query Input
    if (els.queryInput) {
        els.queryInput.addEventListener('input', handleInputChange);
        els.queryInput.addEventListener('keydown', handleInputKeydown);
    }
    if (els.sendBtn) {
        els.sendBtn.addEventListener('click', sendQuery);
    }

    // Sources panel
    if (els.sourcePanelToggle) {
        els.sourcePanelToggle.addEventListener('click', toggleSourcesPanel);
    }
    if (els.sourcesPanelClose) {
        els.sourcesPanelClose.addEventListener('click', () => {
            setSourcesPanelOpen(false);
        });
    }

    $$('.sources-tab').forEach(tab => {
        tab.addEventListener('click', () => switchSourceTab(tab.dataset.tab));
    });

    // Health modal
    if (els.healthBtn) {
        els.healthBtn.addEventListener('click', showHealthModal);
    }
    if (els.healthModalClose) {
        els.healthModalClose.addEventListener('click', () => {
            if (els.healthModal) els.healthModal.classList.remove('visible');
        });
    }
    if (els.healthModal) {
        els.healthModal.addEventListener('click', (e) => {
            if (e.target === els.healthModal) els.healthModal.classList.remove('visible');
        });
    }

    // Settings modal
    if (els.settingsToggleBtn) {
        els.settingsToggleBtn.addEventListener('click', () => {
            if (els.settingsModal) els.settingsModal.classList.add('visible');
        });
    }
    if (els.settingsModalClose) {
        els.settingsModalClose.addEventListener('click', () => {
            if (els.settingsModal) els.settingsModal.classList.remove('visible');
        });
    }
    if (els.settingsModal) {
        els.settingsModal.addEventListener('click', (e) => {
            if (e.target === els.settingsModal) els.settingsModal.classList.remove('visible');
        });
    }

    // Link modal & button
    if (els.linkBtn) {
        els.linkBtn.addEventListener('click', () => {
            if (els.linkModal) {
                els.linkModal.classList.add('visible');
                setTimeout(() => els.paperUrlInput?.focus(), 100);
            }
        });
    }

    if (els.micBtn) {
        els.micBtn.addEventListener('click', toggleSpeechToText);
    }
    if (els.voiceCancelBtn) {
        els.voiceCancelBtn.addEventListener('click', () => {
            stopSpeechToText(true);
        });
    }
    if (els.voiceConfirmBtn) {
        els.voiceConfirmBtn.addEventListener('click', () => {
            stopSpeechToText(false);
        });
    }

    if (els.linkModalClose) {
        els.linkModalClose.addEventListener('click', () => {
            if (els.linkModal) els.linkModal.classList.remove('visible');
        });
    }
    if (els.linkModal) {
        els.linkModal.addEventListener('click', (e) => {
            if (e.target === els.linkModal) els.linkModal.classList.remove('visible');
        });
    }
    if (els.submitPaperUrlBtn && els.paperUrlInput) {
        const handleUrlSubmit = () => {
            const url = els.paperUrlInput.value.trim();
            if (!url) return;

            state.pendingAttachments.push({
                id: `link-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
                name: url,
                size: 0,
                mime: 'text/url',
                url: url
            });
            renderAttachmentTray();

            if (els.linkModal) els.linkModal.classList.remove('visible');
            els.paperUrlInput.value = '';
        };

        els.submitPaperUrlBtn.addEventListener('click', handleUrlSubmit);
        els.paperUrlInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                handleUrlSubmit();
            }
        });
    }

    // Welcome Cards
    $$('.welcome-card').forEach(card => {
        card.addEventListener('click', () => {
            if (els.queryInput) {
                els.queryInput.value = card.dataset.query;
                handleInputChange();
                sendQuery();
            }
        });
    });

    $$('.study-prompt-chip').forEach(chip => {
        chip.addEventListener('click', () => {
            if (els.queryInput) {
                els.queryInput.value = chip.dataset.studyPrompt;
                handleInputChange();
                els.queryInput.focus();
            }
        });
    });

    if (els.attachMenuBtn) {
        els.attachMenuBtn.addEventListener('click', (event) => {
            event.stopPropagation();
            setAttachMenuOpen(!state.attachMenuOpen);
        });
    }

    if (els.attachMenu) {
        els.attachMenu.querySelectorAll('.attach-menu-item').forEach(item => {
            item.addEventListener('click', () => {
                handleAttachAction(item.dataset.attachAction);
            });
        });
    }

    if (els.attachmentFileInput) {
        els.attachmentFileInput.addEventListener('change', (event) => {
            processSelectedFiles(event.target.files);
            event.target.value = '';
        });
    }

    if (els.pdfFileInput) {
        els.pdfFileInput.addEventListener('change', (event) => {
            processSelectedFiles(event.target.files, { onlyPdf: true });
            event.target.value = '';
        });
    }

    if (els.videoFileInput) {
        els.videoFileInput.addEventListener('change', (event) => {
            processSelectedFiles(event.target.files, { onlyVideo: true });
            event.target.value = '';
        });
    }

    document.addEventListener('click', (event) => {
        if (!state.attachMenuOpen) return;
        const clickInsideMenu = els.attachMenu?.contains(event.target);
        const clickOnButton = els.attachMenuBtn?.contains(event.target);
        if (!clickInsideMenu && !clickOnButton) {
            setAttachMenuOpen(false);
        }
    });

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            if (isMobileViewport() && els.sidebar && els.sidebar.classList.contains('mobile-open')) {
                closeMobileSidebar();
                return;
            }
            if (state.attachMenuOpen) {
                setAttachMenuOpen(false);
                if (els.queryInput) els.queryInput.focus();
            }
            if (els.settingsModal && els.settingsModal.classList.contains('visible')) {
                els.settingsModal.classList.remove('visible');
            }
            if (els.healthModal && els.healthModal.classList.contains('visible')) {
                els.healthModal.classList.remove('visible');
            }
            if (els.linkModal && els.linkModal.classList.contains('visible')) {
                els.linkModal.classList.remove('visible');
            }
            if (els.profileModal && els.profileModal.classList.contains('visible')) {
                els.profileModal.classList.remove('visible');
            }
        }
    });

    document.documentElement.setAttribute('data-theme', 'dark');

    // Clear History
    if (els.clearHistoryBtn) {
        els.clearHistoryBtn.addEventListener('click', async () => {
            if (confirm('Clear all conversation history?')) {
                try {
                    const token = localStorage.getItem('aether_token');
                    const res = await fetch(`${API_BASE}/api/history`, {
                        method: 'DELETE',
                        headers: token ? { 'Authorization': `Bearer ${token}` } : {}
                    });
                    if (res.ok) {
                        state.conversations = [];
                        startNewChat();
                    }
                } catch (e) {
                    console.error("Error clearing history:", e);
                }
            }
        });
    }

    // Profile Dropdown & Modal
    const profileBtn = document.getElementById('userProfileBtn');
    const dropdown = document.getElementById('userDropdown');
    if (profileBtn && dropdown) {
        profileBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            dropdown.style.display = dropdown.style.display === 'none' ? 'block' : 'none';
        });

        document.addEventListener('click', () => {
            if (dropdown) dropdown.style.display = 'none';
        });
    }

    const logoutBtn = document.getElementById('logoutBtn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', () => {
            localStorage.removeItem('aether_token');
            window.location.href = '/';
        });
    }

    const newChatBtn = document.getElementById('newChatBtn');
    if (newChatBtn) {
        newChatBtn.addEventListener('click', () => {
            startNewChat();
        });
    }

    initMermaidModal();

    // Global Link Opener
    document.addEventListener('click', (e) => {
        const link = e.target.closest('a');
        if (link && link.href) {
            try {
                const url = new URL(link.href);
                const currentUrl = new URL(window.location.href);
                if (url.origin === currentUrl.origin && url.pathname === currentUrl.pathname && url.hash) {
                    return;
                }
            } catch (err) { }
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
        }
    });

    // Global Copy Button Handler
    document.addEventListener('click', async (e) => {
        const btn = e.target.closest('.btn-copy');
        if (!btn) return;

        const targetSelector = btn.getAttribute('data-copy-target');
        if (!targetSelector) return;

        const messageEl = btn.closest('.message');
        const targetEl = messageEl ? messageEl.querySelector(targetSelector) : null;
        if (targetEl) {
            const success = await copyTextToClipboard(targetEl.innerText || targetEl.textContent);
            if (success) {
                const originalHtml = btn.innerHTML;
                btn.innerHTML = `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg> Copied!`;
                btn.classList.add('copied');
                setTimeout(() => {
                    btn.innerHTML = originalHtml;
                    btn.classList.remove('copied');
                }, 2000);
            }
        }
    });

    // Payment Checkout
    if (els.checkoutPayBtn) {
        els.checkoutPayBtn.addEventListener('click', async () => {
            const btn = els.checkoutPayBtn;
            const originalHtml = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = `<span>Initiating Checkout...</span><div class="loading-spinner" style="width:14px; height:14px; border-width:2px; margin:0; display:inline-block; border-color: rgba(255,255,255,0.3) rgba(255,255,255,0.3) transparent white; border-radius:50%; animation: spin 0.8s linear infinite;"></div>`;

            try {
                await startRazorpayCheckout();
            } finally {
                btn.disabled = false;
                btn.innerHTML = originalHtml;
            }
        });
    }
}
