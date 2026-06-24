/**
 * GraphRAG Research Assistant â€” Frontend Logic
 * Handles chat, sources panel, health checks, and history
 */

// CONFIG & STATE

const API_BASE = window.location.origin;

const state = {
    conversations: [],
    currentConversation: null,
    messages: [],
    isLoading: false,
    sourcesOpen: false,
    attachMenuOpen: false,
    lastResponse: null,
    messageData: new Map(), // Store data for each assistant message for syncing
    pendingAttachments: [],
};

// DOM REFS

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const els = {
    sidebar: document.getElementById('sidebar'),
    sidebarToggle: document.getElementById('sidebarToggle'),
    mobileMenuBtn: document.getElementById('mobileMenuBtn'),
    pipelineStep: document.getElementById('pipelineStep'),

    // Chat components
    chatContainer: document.getElementById('chatContainer'),
    historyList: $('#historyList'),
    topK: $('#topK'),
    topKValue: $('#topKValue'),
    minSim: $('#minSim'),
    minSimValue: $('#minSimValue'),
    temperature: $('#temperature'),
    temperatureValue: $('#temperatureValue'),
    modelSelect: $('#modelSelect'),
    verifyToggle: $('#verifyToggle'),
    groundedStudyToggle: $('#groundedStudyToggle'),
    healthBtn: $('#healthBtn'),
    connectionStatus: $('#connectionStatus'),
    chatMessages: $('#chatMessages'),
    welcomeScreen: $('#welcomeScreen'),
    queryInput: $('#queryInput'),
    sendBtn: $('#sendBtn'),
    charCount: $('#charCount'),
    attachmentTray: $('#attachmentTray'),
    attachMenuBtn: $('#attachMenuBtn'),
    attachMenu: $('#attachMenu'),
    attachmentFileInput: $('#attachmentFileInput'),
    sourcesPanel: $('#sourcesPanel'),
    sourcePanelToggle: $('#sourcePanelToggle'),
    sourcesPanelClose: $('#sourcesPanelClose'),
    sourcesContent: $('#sourcesContent'),
    pdfFileInput: $('#pdfFileInput'),
    videoFileInput: $('#videoFileInput'),
    studyGuardrailsCard: $('#studyGuardrailsCard'),
    healthModal: $('#healthModal'),
    healthModalClose: $('#healthModalClose'),
    healthModalBody: $('#healthModalBody'),
    settingsToggleBtn: $('#settingsToggleBtn'),
    settingsModal: $('#settingsModal'),
    settingsModalClose: $('#settingsModalClose'),
    clearHistoryBtn: $('#clearHistoryBtn'),
    linkModal: $('#linkModal'),
    linkModalClose: $('#linkModalClose'),
    paperUrlInput: $('#paperUrlInput'),
    submitPaperUrlBtn: $('#submitPaperUrlBtn'),
    linkBtn: $('#linkBtn'),
    profileModal: $('#profileModal'),
    profileModalClose: $('#profileModalClose'),
    profileSettingsBtn: $('#profileSettingsBtn'),
};

// INIT

document.addEventListener('DOMContentLoaded', () => {
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
    checkHealth();
    initSupabase();
    renderAttachmentTray();
    els.queryInput.focus();
});

// Panel Resizer Logic
document.addEventListener('DOMContentLoaded', () => {
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
                if (document.getElementById('tabGraph').classList.contains('active')) {
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
});

function initEventListeners() {
    // Sidebar
    if (els.sidebarToggle) {
        els.sidebarToggle.addEventListener('click', toggleSidebar);
    }
    if (els.mobileMenuBtn) {
        els.mobileMenuBtn.addEventListener('click', () => {
            els.sidebar.classList.remove('collapsed');
        });
    }

    // Settings
    if (els.topK) {
        els.topK.addEventListener('input', () => {
            els.topKValue.textContent = els.topK.value;
            saveSettingsToLocalStorage();
        });
    }
    if (els.minSim) {
        els.minSim.addEventListener('input', () => {
            els.minSimValue.textContent = (els.minSim.value / 100).toFixed(2);
            saveSettingsToLocalStorage();
        });
    }
    if (els.temperature) {
        els.temperature.addEventListener('input', () => {
            els.temperatureValue.textContent = parseFloat(els.temperature.value).toFixed(1);
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

    // Input
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

    // Source tabs
    $$('.sources-tab').forEach(tab => {
        tab.addEventListener('click', () => switchSourceTab(tab.dataset.tab));
    });

    // Health modal
    if (els.healthBtn) {
        els.healthBtn.addEventListener('click', showHealthModal);
    }
    if (els.healthModalClose) {
        els.healthModalClose.addEventListener('click', () => {
            els.healthModal.classList.remove('visible');
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
            els.settingsModal.classList.add('visible');
        });
    }
    if (els.settingsModalClose) {
        els.settingsModalClose.addEventListener('click', () => {
            els.settingsModal.classList.remove('visible');
        });
    }
    if (els.settingsModal) {
        els.settingsModal.addEventListener('click', (e) => {
            if (e.target === els.settingsModal) els.settingsModal.classList.remove('visible');
        });
    }

    // Link button directly on input bar
    if (els.linkBtn) {
        els.linkBtn.addEventListener('click', () => {
            if (els.linkModal) {
                els.linkModal.classList.add('visible');
                setTimeout(() => els.paperUrlInput?.focus(), 100);
            }
        });
    }

    // Link modal
    if (els.linkModalClose) {
        els.linkModalClose.addEventListener('click', () => {
            els.linkModal.classList.remove('visible');
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
            // Append URL to query input
            const currentVal = els.queryInput.value.trim();
            els.queryInput.value = currentVal ? `${currentVal} ${url}` : url;
            // Focus queryInput and trigger change
            els.queryInput.focus();
            handleInputChange();
            // Close and clear modal
            els.linkModal.classList.remove('visible');
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

    // Welcome cards
    $$('.welcome-card').forEach(card => {
        card.addEventListener('click', () => {
            els.queryInput.value = card.dataset.query;
            handleInputChange();
            sendQuery();
        });
    });

    $$('.study-prompt-chip').forEach(chip => {
        chip.addEventListener('click', () => {
            els.queryInput.value = chip.dataset.studyPrompt;
            handleInputChange();
            els.queryInput.focus();
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
            if (state.attachMenuOpen) {
                setAttachMenuOpen(false);
                els.queryInput.focus();
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

    // Theme toggle
    const themeBtn = document.getElementById('themeToggle');
    const themeIcon = document.getElementById('themeIcon');

    function updateThemeIcon(theme) {
        if (!themeIcon) return;
        if (theme === 'light') {
            themeIcon.innerHTML = `<circle cx="12" cy="12" r="5"></circle><line x1="12" y1="1" x2="12" y2="3"></line><line x1="12" y1="21" x2="12" y2="23"></line><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line><line x1="1" y1="12" x2="3" y2="12"></line><line x1="21" y1="12" x2="23" y2="12"></line><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line>`;
        } else {
            themeIcon.innerHTML = `<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>`;
        }
    }

    const savedTheme = localStorage.getItem('theme') || 'dark';
    document.documentElement.setAttribute('data-theme', savedTheme);
    updateThemeIcon(savedTheme);

    if (themeBtn) {
        themeBtn.addEventListener('click', () => {
            const currentTheme = document.documentElement.getAttribute('data-theme');
            const newTheme = currentTheme === 'light' ? 'dark' : 'light';
            document.documentElement.setAttribute('data-theme', newTheme);
            localStorage.setItem('theme', newTheme);
            updateThemeIcon(newTheme);
        });
    }

    // Clear History
    if (els.clearHistoryBtn) {
        els.clearHistoryBtn.addEventListener('click', async () => {
            if (confirm('Clear all conversation history?')) {
                try {
                    const res = await fetch(`${API_BASE}/api/history`, {
                        method: 'DELETE',
                        headers: getAuthHeader()
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

    // User Profile Dropdown Toggle
    const profileBtn = document.getElementById('userProfileBtn');
    const dropdown = document.getElementById('userDropdown');
    if (profileBtn && dropdown) {
        profileBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            dropdown.style.display = dropdown.style.display === 'none' ? 'block' : 'none';
        });
        
        // Close dropdown on click outside
        document.addEventListener('click', () => {
            if (dropdown) dropdown.style.display = 'none';
        });
    }

    // Profile Modal Event Listeners
    if (els.profileSettingsBtn) {
        els.profileSettingsBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            if (dropdown) dropdown.style.display = 'none';
            
            // Clear message alerts
            const errorEl = document.getElementById('profileError');
            const successEl = document.getElementById('profileSuccess');
            if (errorEl) errorEl.style.display = 'none';
            if (successEl) successEl.style.display = 'none';
            
            // Load user profile details
            if (supabase) {
                try {
                    const { data: { user }, error } = await supabase.auth.getUser();
                    if (error) throw error;
                    if (user) {
                        const emailInput = document.getElementById('profileEmail');
                        const fullNameInput = document.getElementById('profileFullName');
                        const institutionInput = document.getElementById('profileInstitution');
                        const roleSelect = document.getElementById('profileRole');
                        
                        if (emailInput) emailInput.value = user.email || '';
                        
                        const meta = user.user_metadata || {};
                        if (fullNameInput) fullNameInput.value = meta.full_name || '';
                        if (institutionInput) institutionInput.value = meta.institution || '';
                        if (roleSelect) roleSelect.value = meta.role || '';
                        
                        els.profileModal.classList.add('visible');
                    }
                } catch (err) {
                    console.error("Failed to fetch profile details:", err);
                    alert("Error loading profile: " + err.message);
                }
            }
        });
    }

    if (els.profileModalClose) {
        els.profileModalClose.addEventListener('click', () => {
            els.profileModal.classList.remove('visible');
        });
    }

    if (els.profileModal) {
        els.profileModal.addEventListener('click', (e) => {
            if (e.target === els.profileModal) {
                els.profileModal.classList.remove('visible');
            }
        });
    }

    const profileForm = document.getElementById('profileForm');
    if (profileForm) {
        profileForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const errorEl = document.getElementById('profileError');
            const successEl = document.getElementById('profileSuccess');
            const submitBtn = document.getElementById('saveProfileBtn');
            
            if (errorEl) errorEl.style.display = 'none';
            if (successEl) successEl.style.display = 'none';
            
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.textContent = 'Saving Profile...';
            }
            
            const fullName = document.getElementById('profileFullName')?.value.trim();
            const institution = document.getElementById('profileInstitution')?.value.trim();
            const role = document.getElementById('profileRole')?.value;
            
            if (supabase) {
                try {
                    const { data, error } = await supabase.auth.updateUser({
                        data: {
                            full_name: fullName,
                            institution: institution,
                            role: role
                        }
                    });
                    
                    if (error) throw error;
                    
                    if (successEl) {
                        successEl.textContent = 'Profile details updated successfully!';
                        successEl.style.display = 'block';
                    }
                    
                    updateUserUI(data.user);
                } catch (err) {
                    console.error("Profile update error:", err);
                    if (errorEl) {
                        errorEl.textContent = err.message || 'An error occurred during update.';
                        errorEl.style.display = 'block';
                    }
                } finally {
                    if (submitBtn) {
                        submitBtn.disabled = false;
                        submitBtn.textContent = 'Update Profile Details';
                    }
                }
            }
        });
    }

    const passwordForm = document.getElementById('passwordForm');
    if (passwordForm) {
        passwordForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const errorEl = document.getElementById('profileError');
            const successEl = document.getElementById('profileSuccess');
            const submitBtn = document.getElementById('savePasswordBtn');
            
            if (errorEl) errorEl.style.display = 'none';
            if (successEl) successEl.style.display = 'none';
            
            const password = document.getElementById('profilePassword')?.value;
            const confirmPassword = document.getElementById('profileConfirmPassword')?.value;
            
            if (password.length < 6) {
                if (errorEl) {
                    errorEl.textContent = 'Password must be at least 6 characters long.';
                    errorEl.style.display = 'block';
                }
                return;
            }
            
            if (password !== confirmPassword) {
                if (errorEl) {
                    errorEl.textContent = 'Passwords do not match.';
                    errorEl.style.display = 'block';
                }
                return;
            }
            
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.textContent = 'Updating Password...';
            }
            
            if (supabase) {
                try {
                    const { error } = await supabase.auth.updateUser({
                        password: password
                    });
                    
                    if (error) throw error;
                    
                    if (successEl) {
                        successEl.textContent = 'Password updated successfully!';
                        successEl.style.display = 'block';
                    }
                    
                    passwordForm.reset();
                } catch (err) {
                    console.error("Password update error:", err);
                    if (errorEl) {
                        errorEl.textContent = err.message || 'Failed to update password.';
                        errorEl.style.display = 'block';
                    }
                } finally {
                    if (submitBtn) {
                        submitBtn.disabled = false;
                        submitBtn.textContent = 'Update Password';
                    }
                }
            }
        });
    }

    // Logout Action
    const logoutBtn = document.getElementById('logoutBtn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', async () => {
            if (supabase) {
                await supabase.auth.signOut();
                localStorage.removeItem('aether_token');
                window.location.href = '/';
            }
        });
    }

    // New Chat Action
    const newChatBtn = document.getElementById('newChatBtn');
    if (newChatBtn) {
        newChatBtn.addEventListener('click', () => {
            startNewChat();
        });
    }
}

// SIDEBAR

function toggleSidebar() {
    els.sidebar.classList.toggle('collapsed');
}

// SOURCES PANEL

function toggleSourcesPanel() {
    setSourcesPanelOpen(!state.sourcesOpen);
}

function openSourcesPanel() {
    setSourcesPanelOpen(true);
}

function setSourcesPanelOpen(isOpen) {
    state.sourcesOpen = isOpen;
    els.sourcesPanel.classList.toggle('open', isOpen);
    document.body.classList.toggle('sources-open', isOpen);
}

function switchSourceTab(tabName) {
    $$('.sources-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tabName));
    $$('.sources-tab-content').forEach(c => c.classList.remove('active'));
    $(`#tab${tabName.charAt(0).toUpperCase() + tabName.slice(1)}`).classList.add('active');
}

function setAttachMenuOpen(isOpen) {
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

function handleAttachAction(action) {
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
            els.modelSelect.value = 'heavy';
            els.verifyToggle.checked = true;
            if (els.groundedStudyToggle) {
                els.groundedStudyToggle.checked = true;
                syncStudyGuardrails();
            }
            break;
        default:
            break;
    }
    setAttachMenuOpen(false);
}

function processSelectedFiles(fileList, opts = {}) {
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

function addPendingAttachments(fileList) {
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

function renderAttachmentTray() {
    if (!els.attachmentTray) return;

    if (!state.pendingAttachments.length) {
        els.attachmentTray.classList.remove('visible');
        els.attachmentTray.innerHTML = '';
        return;
    }

    els.attachmentTray.classList.add('visible');
    els.attachmentTray.innerHTML = state.pendingAttachments.map(file => {
        let kind = 'file';
        if (file.mime.startsWith('image/')) kind = 'image';
        if (file.mime.startsWith('video/')) kind = 'video';
        if (file.mime === 'application/pdf') kind = 'PDF document';
        if (file.name.endsWith('.docx') || file.name.endsWith('.doc')) kind = 'Word document';

        return `
            <div class="attachment-card">
                <div class="attachment-icon-box">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"></path>
                        <polyline points="13 2 13 9 20 9"></polyline>
                    </svg>
                </div>
                <div class="attachment-info">
                    <span class="attachment-name" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</span>
                    <span class="attachment-type">${kind}</span>
                </div>
                <button class="attachment-remove" data-attachment-id="${file.id}" aria-label="Remove attachment">×</button>
            </div>
        `;
    }).join('');

    els.attachmentTray.querySelectorAll('.attachment-remove').forEach(button => {
        button.addEventListener('click', () => {
            state.pendingAttachments = state.pendingAttachments.filter(file => file.id !== button.dataset.attachmentId);
            renderAttachmentTray();
        });
    });
}


function syncStudyGuardrails() {
    const enabled = !!els.groundedStudyToggle?.checked;
    if (els.studyGuardrailsCard) {
        els.studyGuardrailsCard.classList.toggle('is-disabled', !enabled);
    }
}

function formatFileSize(sizeBytes) {
    if (!sizeBytes) return '0 MB';
    const mb = sizeBytes / (1024 * 1024);
    return `${mb.toFixed(mb >= 10 ? 0 : 1)} MB`;
}

// D3 GRAPH ENGINE
function renderGraph(papers) {
    const svg = d3.select("#graphSvg");
    svg.selectAll("*").remove();

    if (!papers || papers.length === 0) {
        document.getElementById('graphEmpty').style.display = 'flex';
        document.getElementById('graphContainer').style.display = 'none';
        return;
    }

    document.getElementById('graphEmpty').style.display = 'none';
    document.getElementById('graphContainer').style.display = 'block';

    window.lastGraphPapers = papers;

    // Build timeline toggle
    if (!document.getElementById('timelineToggleBtn')) {
        const btn = document.createElement('button');
        btn.id = 'timelineToggleBtn';
        btn.innerHTML = window.isTimelineView ? 'Knowledge Graph' : 'Timeline Graph';
        btn.className = 'btn-health';
        btn.style = 'position: absolute; right: 20px; top: 15px; width: auto; z-index: 10; padding: 6px 12px; font-size: 12px; border-radius: 20px; background: rgba(99, 102, 241, 0.1); border: 1px solid var(--primary); color: var(--primary-light); cursor: pointer; backdrop-filter: blur(8px); transition: all 0.2s ease;';
        btn.onclick = () => {
            window.isTimelineView = !window.isTimelineView;
            renderGraph(window.lastGraphPapers);
        };
        const tabGraphElement = document.getElementById('tabGraph');
        if (tabGraphElement) {
            tabGraphElement.appendChild(btn);
        }
    } else {
        document.getElementById('timelineToggleBtn').innerHTML = window.isTimelineView ? 'Knowledge Graph' : 'Timeline Graph';
    }

    const width = document.getElementById('sourcesPanel').clientWidth - 40;
    const height = 350;
    const g = svg.append("g");

    // Add zoom
    const zoom = d3.zoom().scaleExtent([0.5, 4]).on("zoom", (event) => g.attr("transform", event.transform));
    svg.call(zoom);

    document.getElementById('resetGraph').onclick = () => {
        svg.transition().duration(750).call(zoom.transform, d3.zoomIdentity);
    };

    const nodes = papers.map(p => ({
        id: p.id || p.title,
        title: p.title,
        author: p.author || 'Unknown',
        domain: p.domain || 'General',
        year: parseInt(p.year) || 2020,
        radius: 8 + Math.min((p.citations || 5) / 2, 8)
    }));

    if (window.isTimelineView) {
        // Draw static linear timeline
        const years = nodes.map(n => n.year);
        // Ensure some spread if all years are same
        const minYear = Math.min(...years) - 2;
        const maxYear = Math.max(...years) + 2;

        const xScale = d3.scaleLinear().domain([minYear, maxYear]).range([50, width - 50]);

        // Draw main timeline axis
        g.append("line")
            .attr("x1", 30)
            .attr("y1", height / 2)
            .attr("x2", width - 30)
            .attr("y2", height / 2)
            .attr("stroke", "rgba(255, 255, 255, 0.2)")
            .attr("stroke-width", 2);

        // Add year ticks
        const yearSet = Array.from(new Set(years)).sort();
        yearSet.forEach(yr => {
            g.append("circle")
                .attr("cx", xScale(yr))
                .attr("cy", height / 2)
                .attr("r", 4)
                .attr("fill", "var(--text-muted)");

            g.append("text")
                .attr("x", xScale(yr))
                .attr("y", height / 2 + 25)
                .attr("text-anchor", "middle")
                .attr("fill", "var(--text-muted)")
                .style("font-size", "12px")
                .style("font-weight", "600")
                .text(yr);
        });

        // Add nodes along timeline with staggering to avoid overlap
        nodes.forEach((d, i) => {
            // stagger y position more aggressively to prevent text collision
            const yOffset = height / 2 + (i % 2 === 0 ? -60 - (i % 4) * 30 : 60 + (i % 4) * 30);

            g.append("line")
                .attr("x1", xScale(d.year))
                .attr("y1", height / 2)
                .attr("x2", xScale(d.year))
                .attr("y2", yOffset)
                .attr("stroke", getColorForDomain(d.domain))
                .attr("stroke-width", 1.5)
                .attr("stroke-dasharray", "4,4")
                .style("opacity", 0.6);

            const nodeGroup = g.append("g")
                .attr("transform", `translate(${xScale(d.year)}, ${yOffset})`);

            nodeGroup.append("circle")
                .attr("r", d.radius)
                .attr("fill", getColorForDomain(d.domain))
                .attr("stroke", "white")
                .attr("stroke-width", 2);

            nodeGroup.append("text")
                .attr("y", -20)
                .attr("text-anchor", "middle")
                .attr("fill", "white")
                .style("font-size", "11px")
                .style("font-weight", "600")
                .style("text-shadow", "0px 1px 4px rgba(0,0,0,0.9), 0px 0px 2px rgba(0,0,0,1)")
                .text(d.title.length > 25 ? d.title.substring(0, 25) + "..." : d.title);

            nodeGroup.append("title").text(`${d.title}\n${d.author} (${d.year})`);

            // Hover effect
            nodeGroup.on("mouseover", function () {
                d3.select(this).select("circle").attr("stroke-width", 4).attr("stroke", "#a78bfa");
            }).on("mouseout", function () {
                d3.select(this).select("circle").attr("stroke-width", 2).attr("stroke", "white");
            });
        });

    } else {
        // Original force graph standard layout
        const links = [];
        for (let i = 0; i < nodes.length; i++) {
            for (let j = i + 1; j < nodes.length; j++) {
                if (nodes[i].domain === nodes[j].domain) {
                    links.push({ source: nodes[i].id, target: nodes[j].id, value: 1 });
                }
            }
        }

        const simulation = d3.forceSimulation(nodes)
            .force("link", d3.forceLink(links).id(d => d.id).distance(160))
            .force("charge", d3.forceManyBody().strength(-400))
            .force("center", d3.forceCenter(width / 2, height / 2))
            .force("collision", d3.forceCollide().radius(d => d.radius + 30));

        const link = g.append("g")
            .attr("stroke", "rgba(255,255,255,0.15)")
            .attr("stroke-width", 1.5)
            .selectAll("line")
            .data(links)
            .enter().append("line");

        const node = g.append("g")
            .selectAll("g")
            .data(nodes)
            .enter().append("g")
            .call(d3.drag()
                .on("start", (event, d) => {
                    if (!event.active) simulation.alphaTarget(0.3).restart();
                    d.fx = d.x; d.fy = d.y;
                })
                .on("drag", (event, d) => { d.fx = event.x; d.fy = event.y; })
                .on("end", (event, d) => {
                    if (!event.active) simulation.alphaTarget(0);
                    d.fx = null; d.fy = null;
                }));

        node.append("circle")
            .attr("r", d => d.radius)
            .attr("fill", d => getColorForDomain(d.domain))
            .attr("stroke", "rgba(255,255,255,0.8)")
            .attr("stroke-width", 2);

        node.append("text")
            .attr("dy", d => d.radius + 18)
            .attr("text-anchor", "middle")
            .style("fill", "white")
            .style("font-size", "11px")
            .style("font-weight", "600")
            .style("text-shadow", "0px 1px 4px rgba(0,0,0,0.9), 0px 0px 2px rgba(0,0,0,1), 0px 2px 8px rgba(0,0,0,1)")
            .text(d => d.title.length > 25 ? d.title.substring(0, 25) + "..." : d.title);

        node.append("title").text(d => `${d.title}\n${d.author} (${d.year})`);

        simulation.on("tick", () => {
            link.attr("x1", d => d.source.x)
                .attr("y1", d => d.source.y)
                .attr("x2", d => d.target.x)
                .attr("y2", d => d.target.y);
            node.attr("transform", d => `translate(${d.x},${d.y})`);
        });
    }
}

function getColorForDomain(domain) {
    const map = {
        'Machine Learning': '#6366f1',
        'Vision': '#14b8a6',
        'NLP': '#f59e0b',
        'Robotics': '#ef4444',
        'Med-AI': '#ec4899'
    };
    return map[domain] || '#64748b';
}

function updateSourcesPanel(data) {
    // Reasoning embedded in Overview Container to look better
    const overviewContainer = document.getElementById('sourcesOverviewContainer');
    if (overviewContainer) {
        overviewContainer.innerHTML = `
            <div class="reasoning-card gemini-style-card dismissible-card">
                <button class="card-dismiss-btn" onclick="this.parentElement.style.display='none'" title="Dismiss">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                    </svg>
                </button>
                <div class="reasoning-title" style="display: flex; align-items: center; gap: 8px; font-weight: 600; color: var(--primary-light); margin-bottom: 8px;">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
                    Aether Reasoning Process
                </div>
                <div class="reasoning-text" style="color: var(--text-secondary); font-size: 13px; line-height: 1.5;">${escapeHtml(data.reasoning_path || 'Evaluated context, identified relevant entities, synthesized final answer using cross-referenced knowledge.')}</div>
                ${data.intent ? `<div class="reasoning-tag" style="margin-top: 10px; font-size: 11px; padding: 4px 8px; background: var(--accent-subtle); border-radius: 4px; display: inline-block; color: var(--primary-light);">Route identified as: <strong>${data.intent}</strong></div>` : ''}
            </div>
        `;
    }

    // Chunks (Smart Highlights -> Intelligence Extraction)
    const chunkList = document.getElementById('tabChunks');
    chunkList.innerHTML = data.chunks && data.chunks.length > 0
        ? '<div class="extracted-insights-timeline">' + data.chunks.map((c, idx) => {
            const fullText = c.chunk || c.text || c.content || '';
            const title = c.title || c.paper_title || 'Unknown Paper';
            const pageInfo = c.page ? `Page ${c.page}` : 'Section Match';
            const simScore = c.similarity ? (c.similarity * 100).toFixed(0) : 'High';

            return `
            <div class="insight-node" style="margin-bottom: 20px; padding: 18px; border-radius: 12px; background: var(--bg-paper); border: 1px solid var(--surface-glass-border); box-shadow: var(--shadow-sm);">
                <div class="insight-header" style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; border-bottom: 1px solid var(--surface-glass-border); padding-bottom: 12px;">
                    <div style="display: flex; gap: 12px; align-items: center;">
                        <span style="display: flex; align-items: center; justify-content: center; width: 24px; height: 24px; background: var(--primary); color: white; border-radius: 50%; font-size: 12px; font-weight: 600;">${idx + 1}</span>
                        <div>
                            <span style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-tertiary);">Source Material</span>
                            <h4 style="margin: 2px 0 0 0; color: var(--text-primary); font-size: 14px; font-weight: 600;">${escapeHtml(title)}</h4>
                        </div>
                    </div>
                </div>
                
                <div class="insight-metadata" style="display: flex; gap: 8px; margin-bottom: 16px;">
                    <span style="background: var(--bg-accent); color: var(--text-secondary); padding: 4px 10px; border-radius: 6px; font-size: 11px; font-family: var(--font-mono); display: flex; align-items: center; gap: 4px;">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>
                        ${escapeHtml(pageInfo)}
                    </span>
                    <span style="background: rgba(52, 211, 153, 0.1); color: var(--accent-emerald); padding: 4px 10px; border-radius: 6px; font-size: 11px; font-family: var(--font-mono); display: flex; align-items: center; gap: 4px;">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><circle cx="12" cy="12" r="6"></circle><circle cx="12" cy="12" r="2"></circle></svg>
                        ${escapeHtml(simScore)}% Match
                    </span>
                </div>
                
                <div class="insight-content-data" style="background: var(--bg-elevated); padding: 14px; border-radius: 8px; border-left: 3px solid var(--accent-cyan);">
                    <div style="font-size: 10px; font-weight: 600; text-transform: uppercase; color: var(--accent-cyan); margin-bottom: 8px; letter-spacing: 0.5px;">Extracted Chunk Data</div>
                    <p class="chunk-highlightable" style="color: var(--text-secondary); font-size: 13px; line-height: 1.6; margin: 0; font-family: var(--font-sans);">${escapeHtml(fullText)}</p>
                </div>
            </div>
            `;
        }).join('') + '</div>'
        : '<div class="sources-empty">No extracted knowledge found.</div>';

    // Papers
    const paperList = document.getElementById('tabPapers');
    let papersHtml = '';

    const dbPapers = data.papers || [];
    const arxivPapers = data.arxiv_papers || [];

    if (dbPapers.length === 0 && arxivPapers.length === 0) {
        papersHtml = '<div class="sources-empty">No papers identified.</div>';
    } else {
        if (arxivPapers.length > 0) {
            papersHtml += `<div style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--accent-cyan); margin: 10px 0; letter-spacing: 0.5px;">Live arXiv References</div>`;
            papersHtml += arxivPapers.map(p => {
                const upvotesHtml = p.hf_upvotes ? `
                    <span class="domain-tag" style="background: rgba(236, 72, 153, 0.15); color: #f472b6; display: inline-flex; align-items: center; gap: 4px; border: 1px solid rgba(236, 72, 153, 0.25);">
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor" style="display:inline-block; vertical-align:middle;"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>
                        ${p.hf_upvotes}
                    </span>
                ` : '';

                const reposHtml = p.code_repos && p.code_repos.length > 0 ? `
                    <div style="margin-top: 10px; display: flex; flex-wrap: wrap; gap: 6px; align-items: center;">
                        <span style="font-size: 11px; color: var(--text-tertiary);">Code:</span>
                        ${p.code_repos.map(repo => `
                            <a href="${repo.url}" target="_blank" style="background: rgba(255, 255, 255, 0.05); border: 1px solid var(--surface-glass-border); color: var(--text-primary); padding: 3px 8px; border-radius: 4px; font-size: 11px; text-decoration: none; display: inline-flex; align-items: center; gap: 4px;" class="repo-badge">
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z"/></svg>
                                ${escapeHtml(repo.name)} ${repo.stars ? `★${repo.stars.toLocaleString()}` : ''}
                            </a>
                        `).join('')}
                    </div>
                ` : '';

                const datasetsHtml = p.datasets && p.datasets.length > 0 ? `
                    <div style="margin-top: 6px; display: flex; flex-wrap: wrap; gap: 6px; align-items: center;">
                        <span style="font-size: 11px; color: var(--text-tertiary);">Datasets:</span>
                        ${p.datasets.map(ds => `
                            <a href="${ds.url}" target="_blank" style="background: rgba(34, 211, 238, 0.05); border: 1px solid rgba(34, 211, 238, 0.2); color: var(--accent-cyan); padding: 2px 6px; border-radius: 4px; font-size: 11px; text-decoration: none; display: inline-flex; align-items: center; gap: 4px;" class="dataset-badge">
                                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22c5.523 0 10-2.239 10-5V7c0-2.761-4.477-5-10-5S2 4.239 2 7v10c0 2.761 4.477 5 10 5z"/><path d="M2 7c0 2.76 4.477 5 10 5s10-2.24 10-5"/><path d="M2 12c0 2.76 4.477 5 10 5s10-2.24 10-5"/></svg>
                                ${escapeHtml(ds.name)}
                            </a>
                        `).join('')}
                    </div>
                ` : '';

                const modelsHtml = p.linked_models && p.linked_models.length > 0 ? `
                    <div style="margin-top: 6px; display: flex; flex-wrap: wrap; gap: 6px; align-items: center;">
                        <span style="font-size: 11px; color: var(--text-tertiary);">HF Models:</span>
                        ${p.linked_models.slice(0, 3).map(m => `
                            <a href="${m.url}" target="_blank" style="background: rgba(99, 102, 241, 0.05); border: 1px solid rgba(99, 102, 241, 0.2); color: var(--primary-light); padding: 2px 6px; border-radius: 4px; font-size: 11px; text-decoration: none; display: inline-flex; align-items: center; gap: 4px;" class="model-badge">
                                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18"/><line x1="7" y1="2" x2="7" y2="22"/><line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/><line x1="2" y1="7" x2="7" y2="7"/><line x1="2" y1="17" x2="7" y2="17"/><line x1="17" y1="17" x2="22" y2="17"/><line x1="17" y1="7" x2="22" y2="7"/></svg>
                                ${escapeHtml(m.id.split('/').pop())}
                            </a>
                        `).join('')}
                        ${p.linked_models.length > 3 ? `<span style="font-size: 10px; color: var(--text-tertiary);">+${p.linked_models.length - 3} more</span>` : ''}
                    </div>
                ` : '';

                const spacesHtml = p.linked_spaces && p.linked_spaces.length > 0 ? `
                    <div style="margin-top: 6px; display: flex; flex-wrap: wrap; gap: 6px; align-items: center;">
                        <span style="font-size: 11px; color: var(--text-tertiary);">Spaces:</span>
                        ${p.linked_spaces.slice(0, 3).map(s => `
                            <a href="${s.url}" target="_blank" style="background: rgba(236, 72, 153, 0.05); border: 1px solid rgba(236, 72, 153, 0.2); color: #f472b6; padding: 2px 6px; border-radius: 4px; font-size: 11px; text-decoration: none; display: inline-flex; align-items: center; gap: 4px;" class="space-badge">
                                <span>${escapeHtml(s.emoji)}</span>
                                ${escapeHtml(s.id.split('/').pop())}
                            </a>
                        `).join('')}
                        ${p.linked_spaces.length > 3 ? `<span style="font-size: 10px; color: var(--text-tertiary);">+${p.linked_spaces.length - 3} more</span>` : ''}
                    </div>
                ` : '';

                return `
                    <div class="source-card paper">
                        <div class="card-title">${escapeHtml(p.title)}</div>
                        <div class="card-meta">
                            <span>${escapeHtml(Array.isArray(p.authors) ? p.authors.join(', ') : (p.author || p.authors || 'Unknown'))}</span>
                            <span>${p.year}</span>
                            <span class="domain-tag" style="background: rgba(239, 68, 68, 0.15); color: #f87171;">arXiv</span>
                            ${upvotesHtml}
                        </div>
                        <div class="card-abstract">${escapeHtml((p.abstract || '').substring(0, 150))}...</div>
                        
                        ${reposHtml}
                        ${datasetsHtml}
                        ${modelsHtml}
                        ${spacesHtml}
                        
                        <div style="display: flex; gap: 8px; margin-top: 12px; border-top: 1px solid var(--surface-glass-border); padding-top: 12px;">
                            <a href="${p.url}" target="_blank" style="background: rgba(34, 211, 238, 0.1); border: 1px solid rgba(34, 211, 238, 0.3); color: var(--accent-cyan); padding: 5px 10px; border-radius: 6px; font-size: 11px; text-decoration: none; display: inline-flex; align-items: center; gap: 4px; font-weight: 500; transition: all 0.2s;" onmouseover="this.style.background='rgba(34, 211, 238, 0.2)'" onmouseout="this.style.background='rgba(34, 211, 238, 0.1)'">
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
                                Abstract
                            </a>
                            <a href="${p.pdf_url}" target="_blank" style="background: rgba(52, 211, 153, 0.1); border: 1px solid rgba(52, 211, 153, 0.3); color: var(--accent-emerald); padding: 5px 10px; border-radius: 6px; font-size: 11px; text-decoration: none; display: inline-flex; align-items: center; gap: 4px; font-weight: 500; transition: all 0.2s;" onmouseover="this.style.background='rgba(52, 211, 153, 0.2)'" onmouseout="this.style.background='rgba(52, 211, 153, 0.1)'">
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
                                PDF Document
                            </a>
                        </div>
                    </div>
                `;
            }).join('');
        }

        if (dbPapers.length > 0) {
            papersHtml += `<div style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--primary-light); margin: 20px 0 10px 0; letter-spacing: 0.5px;">Graph Database Papers</div>`;
            papersHtml += dbPapers.map(p => `
                <div class="source-card paper">
                    <div class="card-title">${escapeHtml(p.title)}</div>
                    <div class="card-meta">
                        <span>${escapeHtml(Array.isArray(p.authors) ? p.authors.join(', ') : (p.author || p.authors || 'Unknown'))}</span>
                        <span>${p.year}</span>
                        <span class="domain-tag">${escapeHtml(p.domain || 'General')}</span>
                    </div>
                    <div class="card-abstract">${escapeHtml((p.abstract || '').substring(0, 150))}...</div>
                    <div style="display: flex; gap: 8px; margin-top: 12px;">
                        <a href="https://scholar.google.com/scholar?q=${encodeURIComponent(p.title)}" target="_blank" style="background: rgba(99, 102, 241, 0.1); border: 1px solid rgba(99, 102, 241, 0.3); color: var(--primary-light); padding: 5px 10px; border-radius: 6px; font-size: 11px; text-decoration: none; display: inline-flex; align-items: center; gap: 4px; font-weight: 500; transition: all 0.2s;" onmouseover="this.style.background='rgba(99, 102, 241, 0.2)'" onmouseout="this.style.background='rgba(99, 102, 241, 0.1)'">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                            Google Scholar
                        </a>
                    </div>
                </div>
            `).join('');
        }
    }
    paperList.innerHTML = papersHtml;

    // Graph View
    if (data.papers) {
        renderGraph(data.papers);
    }

    // Verification
    const verifTab = document.getElementById('tabVerification');
    if (data.verification) {
        const v = data.verification;
        const confidencePercent = (v.confidence * 100).toFixed(0);
        verifTab.innerHTML = `
            <div class="verif-summary">
                <div class="verif-score-circle" style="--percent: ${confidencePercent}">
                    <span class="score-val">${confidencePercent}%</span>
                </div>
                <div class="verif-meta">
                    <h3>${v.verdict || 'PASSED'}</h3>
                    <p>Evidence consistency check completed.</p>
                </div>
            </div>
            ${v.flagged_claims && v.flagged_claims.length > 0 ? `
                <div class="verif-flagged">
                    <h4>Low-Confidence Claims</h4>
                    ${v.flagged_claims.map(c => `<div class="flag-item">! ${escapeHtml(c)}</div>`).join('')}
                </div>
            ` : '<div class="verif-success">OK All claims backed by sources.</div>'}
        `;
    } else {
        verifTab.innerHTML = '<div class="sources-empty">No verification data available.</div>';
    }

    const elementsToTypeset = [];
    if (chunkList) elementsToTypeset.push(chunkList);
    if (verifTab) elementsToTypeset.push(verifTab);
    typesetMath(elementsToTypeset);
}

// Smart Highlighting Logic
function highlightChunk(element) {
    // Remove previous highlights
    document.querySelectorAll('.chunk-highlightable').forEach(el => {
        el.classList.remove('active-highlight');
        el.innerHTML = el.innerHTML.replace(/<mark class="smart-highlight">/g, '').replace(/<\/mark>/g, '');
    });

    const p = element.querySelector('.chunk-highlightable');
    p.classList.add('active-highlight');

    // Simulate smart semantic extraction by isolating the most relevant sentence
    const text = p.innerHTML;
    const sentences = text.split('. ');
    if (sentences.length > 1) {
        // Highlight the middle/dense sentence representing the semantic match
        const highlightIdx = Math.floor((sentences.length - 1) / 2);
        sentences[highlightIdx] = `<mark class="smart-highlight">${sentences[highlightIdx]}</mark>`;
        p.innerHTML = sentences.join('. ');
    } else {
        p.innerHTML = `<mark class="smart-highlight">${text}</mark>`;
    }
}

function setSourcesLoading() {
    const overviewContainer = document.getElementById('sourcesOverviewContainer');
    if (overviewContainer) {
        overviewContainer.innerHTML = `
            <div class="reasoning-card loading">
                <div class="reasoning-title pulse">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>
                    Executing Brain Strategy...
                </div>
                <div class="skeleton-loader" style="height: 40px; width: 100%; border-radius: 8px; margin-top: 10px;"></div>
            </div>
        `;
    }
    document.getElementById('tabChunks').innerHTML = `<div class="sources-empty"><span>Accessing Knowledge Base...</span></div>`;
    document.getElementById('tabPapers').innerHTML = `<div class="sources-empty"><span>Retrieving Research Network...</span></div>`;
    document.getElementById('tabVerification').innerHTML = `<div class="sources-empty"><span>Preparing Grounding Pass...</span></div>`;

    // Switch to status indicators
    const tabs = document.querySelectorAll('.sources-tab');
    tabs.forEach(t => t.classList.remove('active'));
    tabs[0].classList.add('active');

    const contents = document.querySelectorAll('.sources-tab-content');
    contents.forEach(c => c.classList.remove('active'));
    contents[0].classList.add('active');
}

// -------------------------------------------------------------------------
// INPUT HANDLING
// -------------------------------------------------------------------------

function handleInputChange() {
    if (!els.queryInput) return;
    const val = els.queryInput.value;
    if (els.charCount) {
        els.charCount.textContent = `${val.length}/2000`;
    }
    if (els.sendBtn) {
        els.sendBtn.disabled = val.trim().length === 0 || state.isLoading;
    }

    // Auto resize
    els.queryInput.style.height = 'auto';
    els.queryInput.style.height = Math.min(els.queryInput.scrollHeight, 150) + 'px';
}

function handleInputKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (els.sendBtn && !els.sendBtn.disabled) sendQuery();
    }
}

// -------------------------------------------------------------------------
// SEND QUERY
// -------------------------------------------------------------------------

async function sendQuery() {
    const query = els.queryInput.value.trim();
    if (!query || state.isLoading) return;

    setAttachMenuOpen(false);
    state.isLoading = true;
    els.sendBtn.disabled = true;

    // Hide welcome screen
    if (els.welcomeScreen) {
        els.welcomeScreen.style.display = 'none';
    }

    // Add user message
    const outgoingAttachments = [...state.pendingAttachments];
    addMessage('user', query, { attachments: outgoingAttachments });
    state.messages.push({ role: 'user', content: query });
    state.pendingAttachments = [];
    renderAttachmentTray();

    // Clear input
    els.queryInput.value = '';
    handleInputChange();

    // Add loading indicator
    const loadingId = addLoadingMessage();
    setSourcesLoading();

    // Pipeline status simulation
    const steps = ["Planning Strategy", "Searching Knowledge Graph", "Retrieving Papers", "Semantic Vector Search", "Applying MMR Reranking", "Reasoning & Synthesis", "Verifying for Hallucinations"];
    let stepIdx = 0;
    updatePipelineStep(steps[stepIdx]);
    const stepInterval = setInterval(() => {
        if (stepIdx < steps.length - 1) {
            stepIdx++;
            updatePipelineStep(steps[stepIdx]);
        }
    }, 2500);

    // Build request
    const useChat = state.messages.length > 2;

    try {
        let data;
        const requestData = {
            top_k: els.topK ? parseInt(els.topK.value) : 5,
            min_similarity: els.minSim ? parseFloat(els.minSim.value) / 100 : 0.1,
            temperature: els.temperature ? parseFloat(els.temperature.value) : 0.0,
            use_heavy: els.modelSelect ? els.modelSelect.value === 'heavy' : false,
            verify: els.verifyToggle ? els.verifyToggle.checked : true,
        };

        if (useChat) {
            const cleanMessages = state.messages.map(m => ({ role: m.role, content: m.content }));
            data = await apiCall('/api/chat', {
                ...requestData,
                messages: cleanMessages,
            });
        } else {
            data = await apiCall('/api/research', {
                ...requestData,
                query: query,
            });
        }

        // Finalize pipeline
        clearInterval(stepInterval);
        updatePipelineStep("Complete");
        setTimeout(() => updatePipelineStep(null), 2000);

        // Remove loading
        removeMessage(loadingId);

        // Add assistant message
        state.messages.push({ role: 'assistant', content: data.answer, data: data });
        const assistantMsgId = addAssistantMessage(data);
        state.messageData.set(assistantMsgId, data);

        // Update sources panel
        state.lastResponse = data;
        updateSourcesPanel(data);

        // Auto-open sources if there are papers/chunks
        if ((data.papers && data.papers.length > 0) || (data.chunks && data.chunks.length > 0)) {
            if (!state.sourcesOpen) {
                toggleSourcesPanel();
            }
        }

        // Save to history
        saveToHistory(query, data);

    } catch (err) {
        clearInterval(stepInterval);
        updatePipelineStep(null);
        removeMessage(loadingId);
        addMessage('assistant', `(!) Error: ${err.message}`, { isError: true });
    }

    state.isLoading = false;
    els.sendBtn.disabled = els.queryInput.value.trim().length === 0;
}

// -------------------------------------------------------------------------
// API CALL
// -------------------------------------------------------------------------

function getAuthHeader() {
    const token = localStorage.getItem('aether_token');
    return token ? { 'Authorization': `Bearer ${token}` } : {};
}

async function apiCall(endpoint, body, method = 'POST') {
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
        throw new Error(err.detail || `HTTP ${res.status}`);
    }

    return res.json();
}

// -------------------------------------------------------------------------
// MESSAGE RENDERING
// -------------------------------------------------------------------------

function addMessage(role, content, opts = {}) {
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
                        <span>${formatFileSize(file.size)}</span>
                    </span>
                `).join('')}
            </div>
        `
        : '';

    div.innerHTML = `
        <div class="message-avatar">${avatar}</div>
        <div class="message-body">
            <div class="message-header">
                <span class="message-sender">${role === 'user' ? 'You' : 'Aether'}</span>
                <span class="message-meta">${new Date().toLocaleTimeString()}</span>
            </div>
            <div class="message-content">${opts.isError ? content : formatMarkdown(content)}</div>
            ${attachmentsHtml}
        </div>
    `;

    els.chatMessages.appendChild(div);
    scrollToBottom();

    if (!opts.isError) {
        const contentDiv = div.querySelector('.message-content');
        if (contentDiv) {
            typesetMath([contentDiv]);
            postProcessResponse(contentDiv);
        }
    }

    return id;
}

function addAssistantMessage(data, stream = true) {
    const id = 'msg-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
    const div = document.createElement('div');
    div.className = 'message assistant';
    div.id = id;

    // Build verification badge
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

    // Warning
    let warningHtml = '';
    if (data.warning) {
        warningHtml = `<div class="message-warning" style="display:flex;align-items:flex-start;gap:6px"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="flex-shrink:0;margin-top:1px"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>${escapeHtml(data.warning)}</div>`;
    }

    // Footer stats
    const stats = [];
    if (data.latency_ms) stats.push(`<span class="message-stat">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
        ${data.latency_ms}ms
    </span>`);
    if (data.model_used) stats.push(`<span class="message-stat">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z"/></svg>
        ${data.model_used}
    </span>`);
    if (data.intent) stats.push(`<span class="message-stat">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 11-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
        Aether Optimized
    </span>`);
    if (data.papers) stats.push(`<span class="message-stat clickable" onclick="openSourcesPanel(); switchSourceTab('papers')">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
        ${data.papers.length} Papers Found
    </span>`);
    if (data.chunks) stats.push(`<span class="message-stat clickable" onclick="openSourcesPanel(); switchSourceTab('chunks')">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="13 2 13 9 20 9"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
        ${data.chunks.length} Knowledge Chunks
    </span>`);

    const copyBtnHtml = `<span class="message-stat btn-copy" onclick="
        var t = this.closest('.message-body') ? this.closest('.message-body').querySelector('.message-content') : null;
        if (t) navigator.clipboard.writeText(t.innerText);
        const o = this.innerHTML;
        this.innerHTML = '<svg width=\'11\' height=\'11\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'currentColor\' stroke-width=\'2.5\'><polyline points=\'20 6 9 17 4 12\'/></svg> Copied';
        setTimeout(() => this.innerHTML = o, 2000);
    ">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/></svg>
        Copy
    </span>`;

    let bibtexHtml = '';
    let matrixHtml = '';
    if (data.papers && data.papers.length > 0) {
        // Construct BibTeX block
        const bibtexPayload = btoa(unescape(encodeURIComponent(data.papers.map(p => {
            const authorLast = p.author ? p.author.split(' ').pop() : 'Unknown';
            const yearStr = p.year || '2020';
            const id = `${authorLast}${yearStr}${p.title.replace(/\W/g, '').substring(0, 8)}`;
            return `@article{${id},\n  title={${p.title}},\n  author={${p.author || 'Unknown'}},\n  year={${yearStr}},\n  journal={${p.domain || 'Tech. Report'}}\n}`;
        }).join('\n\n'))));
        bibtexHtml = `<span class="message-stat btn-copy" style="color: var(--accent-cyan);" onclick="navigator.clipboard.writeText(decodeURIComponent(escape(atob('${bibtexPayload}')))); this.innerHTML='(Copied!)'; setTimeout(()=>this.innerHTML='BibTeX Export', 2000)">BibTeX Export</span>`;

        // Matrix Generator Feature
        const topTitles = data.papers.slice(0, 4).map(p => p.title).join(' | ');
        matrixHtml = `<span class="message-stat btn-copy" style="color: var(--accent-emerald);" onclick="document.getElementById('queryInput').value = 'Generate a tight markdown comparison matrix table for these papers: ${topTitles.replace(/'/g, "\'")} (Compare Methodology, Datasets, and Accuracy)'; document.getElementById('queryInput').focus(); document.getElementById('sendBtn').click();">Matrix Summary</span>`;
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
                    ${copyBtnHtml} ${bibtexHtml} ${matrixHtml}
                </div>
            </div>
        </div>
    `;

    els.chatMessages.appendChild(div);
    const contentDiv = div.querySelector('.message-content');
    const footerDiv = div.querySelector('.message-footer');

    const textRaw = data.answer || '';

    if (!stream) {
        contentDiv.innerHTML = formatMarkdown(textRaw);
        typesetMath([contentDiv]);
        postProcessResponse(contentDiv);
        footerDiv.style.opacity = '1';
    } else {
        let currIdx = 0;
        const streamInterval = setInterval(() => {
            currIdx += Math.floor(Math.random() * 5) + 3;
            const finished = currIdx >= textRaw.length;
            if (finished) {
                currIdx = textRaw.length;
                clearInterval(streamInterval);
            }

            contentDiv.innerHTML = formatMarkdown(textRaw.substring(0, currIdx));

            if (finished) {
                footerDiv.style.opacity = '1';
                typesetMath([contentDiv]);
                postProcessResponse(contentDiv);
            }

            if (els.chatContainer.scrollHeight - els.chatContainer.scrollTop - els.chatContainer.clientHeight < 150) {
                scrollToBottom();
            }
        }, 12);
    }

    scrollToBottom();
    return id;
}

function addLoadingMessage() {
    const id = 'loading-' + Date.now();
    const div = document.createElement('div');
    div.className = 'message assistant';
    div.id = id;

    div.innerHTML = `
        <div class="message-avatar" style="font-size:24px; color: var(--primary-light);">âœ¨</div>
        <div class="message-body">
            <div class="message-loading">
                <div class="typing-dots">
                    <span></span><span></span><span></span>
                </div>
                <span>Reasoning and verifying sources...</span>
            </div>
        </div>
    `;

    els.chatMessages.appendChild(div);
    scrollToBottom();
    return id;
}

function removeMessage(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

function scrollToBottom() {
    els.chatContainer.scrollTo({
        top: els.chatContainer.scrollHeight,
        behavior: 'smooth',
    });
}


/* ═══════════════════════════ MARKDOWN & KATEX ═══════════════════════════ */

function formatMarkdown(text) {
    if (!text) return '';
    text = text.replace(/\[(\d+)\](?!\()/g, '<span class="citation">[$1]</span>');

    // Handle v4.0 specific tags if any
    text = text.replace(/【(.*?)】/g, '<span class="source-tag">$1</span>');

    // ── BEAUTIFY SQUISHED BACKEND LISTS ──
    // Convert squished ' • ' bullet points into proper Markdown lists with spacing
    // ── BEAUTIFY SQUISHED BACKEND LISTS ──
    // 1. Convert bullet points (\u2022) into standard Markdown bullets (-)
    text = text.replace(/\u2022/g, '-');

    // 2. If a bullet follows text on the same line, move it to a new list block
    text = text.replace(/([^\n])\s+-\s+/g, '$1\n\n- ');

    // 3. Ensure any list following a paragraph has a double newline (for marked.js)
    text = text.replace(/([a-zA-Z0-9\):])(\s*)\n-\s+/g, '$1\n\n- ');

    // 4. Bold specific paper titles and format metadata (Year, Author, Citations)
    // Case A: Full format "- Title (YYYY) — Author"
    text = text.replace(/-\s+([^\n]+?)\s+\((\d{4})\)\s*(—|-|–)\s*([^\n]+)/g, '- **$1** <span class="paper-year">($2)</span> &mdash; <span class="paper-author">$4</span>');

    // Case B: Compact format "- Title (YYYY) [Citation]" as seen in surveys
    text = text.replace(/-\s+([^\n]+?)\s+\((\d{4})\)\s*(\[\d+\]|\[N\])/g, '- **$1** <span class="paper-year">($2)</span> $3');

    // 1. Extract and protect LaTeX math
    const mathBlocks = [];
    let processedText = text;

    // A. Display math: \begin{equation} ... \end{equation} (and other environments)
    processedText = processedText.replace(/\\begin\{([a-zA-Z\*]+)\}([\s\S]*?)\\end\{\1\}/g, (match) => {
        const id = `MATHDISPLAYPLACEHOLDER${mathBlocks.length}`;
        mathBlocks.push({ id, raw: match });
        return id;
    });

    // B. Display math: \[ ... \]
    processedText = processedText.replace(/\\\[([\s\S]*?)\\\]/g, (match) => {
        const id = `MATHDISPLAYPLACEHOLDER${mathBlocks.length}`;
        mathBlocks.push({ id, raw: match });
        return id;
    });

    // C. Display math: $$ ... $$
    processedText = processedText.replace(/\$\$([\s\S]*?)\$\$/g, (match) => {
        const id = `MATHDISPLAYPLACEHOLDER${mathBlocks.length}`;
        mathBlocks.push({ id, raw: match });
        return id;
    });

    // D. Inline math: \( ... \)
    processedText = processedText.replace(/\\\(([\s\S]*?)\\\)/g, (match) => {
        const id = `MATHINLINEPLACEHOLDER${mathBlocks.length}`;
        mathBlocks.push({ id, raw: match });
        return id;
    });

    // E. Inline math: $ ... $ (avoiding currency matches like $50)
    processedText = processedText.replace(/\$([^\$\s](?:[^\$]*?[^\$\s])?)\$/g, (match, p1) => {
        if (p1.includes('\n')) return match;
        const id = `MATHINLINEPLACEHOLDER${mathBlocks.length}`;
        mathBlocks.push({ id, raw: match });
        return id;
    });

    // 2. Render Markdown
    if (window.marked && window.marked.parse) {
        processedText = marked.parse(processedText);
    } else {
        processedText = processedText.replace(/\n\n/g, '</p><p>').replace(/\n/g, '<br>');
    }

    // 3. Re-inject raw Math for MathJax
    mathBlocks.forEach(block => {
        processedText = processedText.replace(block.id, () => block.raw);
    });

    return processedText;
}

function updatePipelineStep(step) {
    if (els.pipelineStep) {
        els.pipelineStep.textContent = step ? `• ${step}` : '';
        els.pipelineStep.style.opacity = step ? '1' : '0';
    }
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// Robust MathJax Typesetting Helper
function typesetMath(elements) {
    if (!elements || elements.length === 0) return;

    const runTypeset = () => {
        if (window.MathJax && window.MathJax.typesetPromise) {
            window.MathJax.typesetPromise(elements).catch(err => console.warn('MathJax typesetting error:', err));
        }
    };

    if (window.MathJax && window.MathJax.typesetPromise) {
        runTypeset();
    } else {
        const script = document.getElementById('MathJax-script');
        if (script) {
            script.addEventListener('load', () => {
                if (window.MathJax && window.MathJax.startup && window.MathJax.startup.promise) {
                    window.MathJax.startup.promise.then(runTypeset);
                } else {
                    runTypeset();
                }
            }, { once: true });
        }
    }
}


// Helper to escape HTML characters for safe raw code fallback display
function escapeHtml(str) {
    if (!str) return '';
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

// Automatic Mermaid flowchart syntax sanitizer/healer
function sanitizeMermaidCode(code) {
    if (!code) return '';
    
    let lines = code.split('\n');
    let processedLines = [];
    
    // Improved connection regex to handle spaces around pipe labels and -- text -->, etc.
    const connectionRegex = /(\s*(?:-->|==>|-\.->)\s*\|[^|]+\|\s*|\s*--\s*[^-]+?\s*-->\s*|\s*==\s*[^=]+?\s*==>\s*|\s*-\.\s*[^\.]+\s*\.-\s*>\s*|-->|---|==>|-\.-|-.->|->)/;
    
    const wrapLabel = (label) => {
        let clean = label.trim();
        // If it starts and ends with double quotes, strip them first to escape inner quotes properly
        if (clean.startsWith('"') && clean.endsWith('"')) {
            let inner = clean.substring(1, clean.length - 1);
            inner = inner.replace(/\\"/g, '"').replace(/"/g, '\\"');
            return `"${inner}"`;
        }
        // Escape double quotes inside the label
        clean = clean.replace(/\\"/g, '"').replace(/"/g, '\\"');
        return `"${clean}"`;
    };
    
    const sanitizeNodePart = (part) => {
        let p = part.trim();
        if (!p) return '';
        
        // Stadium: id([label])
        let stadiumMatch = p.match(/^([^\[\(\{\>"]+)\(\[(.+)\]\)$/);
        if (stadiumMatch) {
            return `${stadiumMatch[1].trim().replace(/[^a-zA-Z0-9_-]/g, '_')}([${wrapLabel(stadiumMatch[2])}])`;
        }
        
        // Database: id[(label)]
        let dbMatch = p.match(/^([^\[\(\{\>"]+)\[\((.+)\)\]$/);
        if (dbMatch) {
            return `${dbMatch[1].trim().replace(/[^a-zA-Z0-9_-]/g, '_')}[(${wrapLabel(dbMatch[2])})]`;
        }
        
        // Circle: id((label))
        let circleMatch = p.match(/^([^\[\(\{\>"]+)\(\((.+)\)\)$/);
        if (circleMatch) {
            return `${circleMatch[1].trim().replace(/[^a-zA-Z0-9_-]/g, '_')}((${wrapLabel(circleMatch[2])}))`;
        }
        
        // Hexagon: id{{label}}
        let hexMatch = p.match(/^([^\[\(\{\>"]+)\{\{(.+)\}\}$/);
        if (hexMatch) {
            return `${hexMatch[1].trim().replace(/[^a-zA-Z0-9_-]/g, '_')}{{${wrapLabel(hexMatch[2])}}}`;
        }
        
        // Subroutine: id[[label]]
        let subMatch = p.match(/^([^\[\(\{\>"]+)\[\[(.+)\]\]$/);
        if (subMatch) {
            return `${subMatch[1].trim().replace(/[^a-zA-Z0-9_-]/g, '_')}[[${wrapLabel(subMatch[2])}]]`;
        }
        
        // Parallelogram: id[/label/] or id[\label\]
        let paraMatch1 = p.match(/^([^\[\(\{\>"]+)\[\/(.+)\/\]$/);
        if (paraMatch1) {
            return `${paraMatch1[1].trim().replace(/[^a-zA-Z0-9_-]/g, '_')}[/${wrapLabel(paraMatch1[2])}/]`;
        }
        let paraMatch2 = p.match(/^([^\[\(\{\>"]+)\[\\(.+)\\\]$/);
        if (paraMatch2) {
            return `${paraMatch2[1].trim().replace(/[^a-zA-Z0-9_-]/g, '_')}[\\${wrapLabel(paraMatch2[2])}\\]`;
        }
        
        // Rounded edges: id(label)
        let singleRoundMatch = p.match(/^([^\[\(\{\>"]+)\((.+)\)$/);
        if (singleRoundMatch) {
            return `${singleRoundMatch[1].trim().replace(/[^a-zA-Z0-9_-]/g, '_')}(${wrapLabel(singleRoundMatch[2])})`;
        }
        
        // Rectangle: id[label]
        let rectMatch = p.match(/^([^\[\(\{\>"]+)\[(.+)\]$/);
        if (rectMatch) {
            return `${rectMatch[1].trim().replace(/[^a-zA-Z0-9_-]/g, '_')}[${wrapLabel(rectMatch[2])}]`;
        }
        
        // Diamond: id{label}
        let diamondMatch = p.match(/^([^\[\(\{\>"]+)\{(.+)\}$/);
        if (diamondMatch) {
            return `${diamondMatch[1].trim().replace(/[^a-zA-Z0-9_-]/g, '_')}{${wrapLabel(diamondMatch[2])}}`;
        }
        
        // Asymmetric: id>label]
        let asymMatch = p.match(/^([^\[\(\{\>"]+)>(.+)\]$/);
        if (asymMatch) {
            return `${asymMatch[1].trim().replace(/[^a-zA-Z0-9_-]/g, '_')}>${wrapLabel(asymMatch[2])}]`;
        }
        
        // No shape found. It's just a raw node ID.
        // Replace invalid characters with underscores
        return p.replace(/[^a-zA-Z0-9_-]/g, '_');
    };
    
    let isFlowchart = false;
    for (let line of lines) {
        let trimmed = line.trim();
        if (/^(graph|flowchart)/i.test(trimmed)) {
            isFlowchart = true;
            break;
        }
    }
    
    if (!isFlowchart) {
        return code;
    }
    
    for (let line of lines) {
        let trimmed = line.trim();
        if (!trimmed || trimmed.startsWith('%%')) {
            processedLines.push(line);
            continue;
        }
        
        // Skip header definitions
        if (/^(graph|flowchart)/i.test(trimmed)) {
            processedLines.push(line);
            continue;
        }
        
        // Subgraph start/end
        if (trimmed.toLowerCase().startsWith('subgraph ')) {
            let content = trimmed.substring(9).trim();
            let shapeMatch = content.match(/^([^\[\(\{\>"]+)([\(\[\{>"].*)$/);
            if (shapeMatch) {
                let id = shapeMatch[1].trim().replace(/[^a-zA-Z0-9_-]/g, '_');
                let label = shapeMatch[2].trim();
                processedLines.push(`subgraph ${id} ${label}`);
            } else {
                processedLines.push(`subgraph ${content.replace(/[^a-zA-Z0-9_-]/g, '_')}`);
            }
            continue;
        }
        if (trimmed.toLowerCase() === 'end') {
            processedLines.push(line);
            continue;
        }
        
        // Style or click directives
        if (trimmed.toLowerCase().startsWith('style ') || trimmed.toLowerCase().startsWith('click ') || trimmed.toLowerCase().startsWith('classdef ') || trimmed.toLowerCase().startsWith('class ')) {
            processedLines.push(line);
            continue;
        }
        
        // Process standard line with potential connections
        let parts = line.split(connectionRegex);
        let processedParts = [];
        for (let i = 0; i < parts.length; i++) {
            if (i % 2 === 0) {
                // Node part
                processedParts.push(sanitizeNodePart(parts[i]));
            } else {
                // Connection part
                processedParts.push(parts[i]);
            }
        }
        
        processedLines.push(processedParts.join(' '));
    }
    
    return processedLines.join('\n');
}

// Post-processes HTML inside messages to render callout blocks and Mermaid diagrams
async function postProcessResponse(container) {
    if (!container) return;

    // 1. Process GitHub-style alerts in blockquotes (e.g. [!NOTE], [!WARNING], etc.)
    const blockquotes = container.querySelectorAll('blockquote');
    blockquotes.forEach(bq => {
        const html = bq.innerHTML;
        if (html.includes('[!NOTE]') || html.includes('[!IMPORTANT]') || html.includes('[!WARNING]') || html.includes('[!TIP]') || html.includes('[!CAUTION]')) {
            let type = 'note';
            let title = 'Note';
            
            if (html.includes('[!IMPORTANT]')) { type = 'important'; title = 'Important'; }
            else if (html.includes('[!WARNING]')) { type = 'warning'; title = 'Warning'; }
            else if (html.includes('[!TIP]')) { type = 'tip'; title = 'Tip'; }
            else if (html.includes('[!CAUTION]')) { type = 'caution'; title = 'Caution'; }
            
            const cleanHtml = html
                .replace(/\[!(NOTE|IMPORTANT|WARNING|TIP|CAUTION)\]/g, '')
                .replace(/^<p>\s*<br>/, '<p>')
                .replace(/^<p>\s*/, '<p>');
                
            const div = document.createElement('div');
            div.className = `callout ${type}`;
            
            let colorVar = 'var(--accent-cyan)';
            if (type === 'important') colorVar = 'var(--accent-purple)';
            else if (type === 'tip') colorVar = 'var(--accent-emerald)';
            else if (type === 'warning') colorVar = 'var(--accent-amber)';
            else if (type === 'caution') colorVar = 'var(--accent-red)';
            
            div.innerHTML = `<div class="callout-title" style="font-weight: 700; margin-bottom: 4px; color: ${colorVar}">${title}</div>${cleanHtml}`;
            bq.replaceWith(div);
        }
    });

    // 2. Render Mermaid diagrams
    if (window.mermaid) {
        const mermaidCodes = container.querySelectorAll('pre code.language-mermaid');
        if (mermaidCodes.length > 0) {
            for (let i = 0; i < mermaidCodes.length; i++) {
                const codeEl = mermaidCodes[i];
                const preEl = codeEl.parentElement;
                const codeText = codeEl.textContent.trim();
                
                const wrapper = document.createElement('div');
                wrapper.className = 'mermaid-container';
                wrapper.style.margin = '16px 0';
                wrapper.style.padding = '12px';
                wrapper.style.background = 'rgba(15, 23, 42, 0.6)';
                wrapper.style.border = '1px solid var(--glass-border)';
                wrapper.style.borderRadius = 'var(--radius-md)';
                wrapper.style.overflowX = 'auto';
                wrapper.style.display = 'flex';
                wrapper.style.flexDirection = 'column';
                wrapper.style.alignItems = 'center';
                
                let renderSuccess = false;
                let svgContent = '';
                const uniqueId = `mermaid-chart-${Date.now()}-${i}-${Math.floor(Math.random() * 1000)}`;
                
                // Try rendering original first
                try {
                    // Pre-validate original syntax to prevent Mermaid rendering its default error box
                    await mermaid.parse(codeText);
                    
                    const { svg } = await mermaid.render(uniqueId, codeText);
                    svgContent = svg;
                    renderSuccess = true;
                } catch (err) {
                    console.warn('Mermaid rendering original failed, trying sanitized version...', err);
                    
                    // Clean up bad elements created by mermaid error handler
                    const badEl = document.getElementById(uniqueId);
                    if (badEl) badEl.remove();
                    const badElBind = document.getElementById(`d${uniqueId}`);
                    if (badElBind) badElBind.remove();
                    
                    try {
                        const sanitizedCode = sanitizeMermaidCode(codeText);
                        // Validate sanitized syntax
                        await mermaid.parse(sanitizedCode);
                        
                        const { svg } = await mermaid.render(uniqueId, sanitizedCode);
                        svgContent = svg;
                        renderSuccess = true;
                    } catch (err2) {
                        console.error('Mermaid rendering sanitized version also failed:', err2);
                        const badEl2 = document.getElementById(uniqueId);
                        if (badEl2) badEl2.remove();
                        const badEl2Bind = document.getElementById(`d${uniqueId}`);
                        if (badEl2Bind) badEl2Bind.remove();
                    }
                }
                
                if (renderSuccess) {
                    wrapper.innerHTML = svgContent;
                } else {
                    wrapper.innerHTML = `
                        <div class="mermaid-fallback" style="width: 100%; color: var(--text-muted); font-size: 14px; text-align: left;">
                            <div style="display: flex; align-items: center; gap: 8px; color: var(--accent-red); font-weight: 600; margin-bottom: 6px;">
                                <svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink: 0;">
                                    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
                                    <line x1="12" y1="9" x2="12" y2="13"></line>
                                    <line x1="12" y1="17" x2="12.01" y2="17"></line>
                                </svg>
                                <span>Diagram Rendering Error</span>
                            </div>
                            <p style="margin: 0 0 8px 0; font-size: 13px;">This flowchart has syntax errors and could not be rendered visually.</p>
                            <details class="mermaid-fallback-details" style="border: 1px solid var(--glass-border); border-radius: 4px; background: rgba(0,0,0,0.2);">
                                <summary style="cursor: pointer; padding: 6px 10px; font-weight: 500; font-size: 12px; color: var(--accent-cyan); user-select: none;">
                                    View Raw Diagram Code
                                </summary>
                                <pre style="margin: 0; padding: 10px; font-family: monospace; font-size: 12px; color: var(--text-light); overflow-x: auto; background: rgba(0, 0, 0, 0.4); border-top: 1px solid var(--glass-border); white-space: pre-wrap; word-break: break-all;">${escapeHtml(codeText)}</pre>
                            </details>
                        </div>
                    `;
                }
                
                preEl.replaceWith(wrapper);
            }
        }
    }
}


// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// HEALTH CHECK
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

async function checkHealth() {
    const dot = els.connectionStatus.querySelector('.status-dot');
    const text = els.connectionStatus.querySelector('.status-text');

    try {
        const res = await fetch(`${API_BASE}/api/health`);
        const data = await res.json();

        if (data.ready) {
            dot.className = 'status-dot connected';
            text.textContent = 'Connected';
        } else {
            dot.className = 'status-dot error';
            text.textContent = 'Degraded';
        }
    } catch (e) {
        dot.className = 'status-dot error';
        text.textContent = 'Disconnected';
    }
}

async function showHealthModal() {
    els.healthModal.classList.add('visible');
    els.healthModalBody.innerHTML = `
        <div style="display:flex;align-items:center;gap:12px;justify-content:center;padding:20px">
            <div class="loading-spinner"></div>
            <span>Running health checks...</span>
        </div>
    `;

    try {
        const res = await fetch(`${API_BASE}/api/health/full`);
        const data = await res.json();

        const checks = Object.entries(data.checks || {}).map(([name, status]) => {
            const isOk = status === 'ok';
            return `
                <div class="health-check">
                    <span class="health-check-name">${name}</span>
                    <span class="health-check-status ${isOk ? 'health-ok' : 'health-error'}">${isOk
                    ? '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg> OK'
                    : '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Error'}</span>
                </div>
            `;
        }).join('');

        const overallClass = data.status === 'healthy' ? 'healthy' : 'degraded';
        els.healthModalBody.innerHTML = `
            <div class="health-overall ${overallClass}">
                ${data.status === 'healthy'
                ? '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>'
                : '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>'}
                System ${data.status}
            </div>
            ${checks}
        `;
    } catch (e) {
        els.healthModalBody.innerHTML = `
            <div class="health-overall degraded">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
                Cannot reach API server
            </div>
            <p style="text-align:center;color:var(--text-muted);font-size:13px">
                Make sure the backend is running on ${API_BASE}
            </p>
        `;
    }
}


// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// HISTORY
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

async function generatePremiumTitle(query) {
    try {
        const res = await fetch(`${API_BASE}/v1/chat/completions`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                messages: [
                    {
                        role: "user",
                        content: `Generate a concise 3 to 5 word title for an academic chat session based on this query. Do not include quotes, markdown, or any introductory text. Return ONLY the title.\n\nQuery: ${query}`
                    }
                ],
                temperature: 0.3,
                max_tokens: 15
            })
        });
        if (res.ok) {
            const data = await res.json();
            const title = data.choices[0].message.content.trim().replace(/^["']|["']$/g, '');
            if (title) return title;
        }
    } catch (e) {
        console.error("Failed to generate premium title:", e);
    }
    // Fallback title
    return query.length > 30 ? query.substring(0, 30) + '...' : query;
}

async function saveToHistory(query, data) {
    if (!state.currentConversation) {
        try {
            const title = await generatePremiumTitle(query);
            const res = await fetch(`${API_BASE}/api/history`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...getAuthHeader()
                },
                body: JSON.stringify({
                    title: title,
                    messages: state.messages
                })
            });
            if (res.ok) {
                const session = await res.json();
                state.currentConversation = session.id;
                state.conversations.unshift(session);
                renderHistory();
            }
        } catch (e) {
            console.error("Error creating chat session:", e);
        }
    } else {
        try {
            const res = await fetch(`${API_BASE}/api/history/${state.currentConversation}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    ...getAuthHeader()
                },
                body: JSON.stringify({
                    messages: state.messages
                })
            });
            if (res.ok) {
                const updatedSession = await res.json();
                state.conversations = state.conversations.filter(c => c.id !== updatedSession.id);
                state.conversations.unshift(updatedSession);
                renderHistory();
            }
        } catch (e) {
            console.error("Error updating chat session:", e);
        }
    }
}

async function loadHistory() {
    try {
        const res = await fetch(`${API_BASE}/api/history`, {
            headers: getAuthHeader()
        });
        if (res.ok) {
            state.conversations = await res.json();
            renderHistory();
        } else if (res.status === 401) {
            window.location.href = '/';
        }
    } catch (e) {
        console.error("Error loading chat history:", e);
    }
}

function renderHistory() {
    const searchInput = document.getElementById('historySearchInput');
    const query = searchInput ? searchInput.value.trim().toLowerCase() : '';
    
    const filteredConversations = state.conversations.filter(conv => {
        const title = (conv.title || '').toLowerCase();
        return title.includes(query);
    });

    if (filteredConversations.length === 0) {
        els.historyList.innerHTML = `
            <div class="history-empty">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                    <circle cx="12" cy="12" r="10"/>
                    <polyline points="12 6 12 12 16 14"/>
                </svg>
                <span>${query ? 'No matching chats' : 'No conversations yet'}</span>
            </div>
        `;
        return;
    }

    els.historyList.innerHTML = filteredConversations.map(conv => {
        const isActive = state.currentConversation === conv.id ? 'active' : '';
        const title = conv.title || 'New Chat';
        
        return `
            <div class="history-item ${isActive}" data-id="${conv.id}">
                <div class="history-title-container">
                    <svg class="history-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                    </svg>
                    <span class="history-item-title" title="${escapeHtml(title)}">${escapeHtml(title)}</span>
                </div>
                <div class="history-actions-buttons">
                    <button class="history-action-btn btn-rename" title="Rename chat">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7M18.5 2.5a2.121 2.121 0 1 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                        </svg>
                    </button>
                    <button class="history-action-btn btn-delete" title="Delete chat">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <polyline points="3 6 5 6 21 6"></polyline>
                            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                        </svg>
                    </button>
                </div>
            </div>
        `;
    }).join('');

    els.historyList.querySelectorAll('.history-item').forEach(item => {
        const id = item.dataset.id;
        const conv = state.conversations.find(c => c.id === id);
        
        item.querySelector('.history-title-container').addEventListener('click', () => {
            loadSession(conv);
        });

        const renameBtn = item.querySelector('.btn-rename');
        if (renameBtn) {
            renameBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const newTitle = prompt("Enter new title for this chat:", conv.title || "New Chat");
                if (newTitle && newTitle.trim()) {
                    await renameSession(id, newTitle.trim());
                }
            });
        }

        const deleteBtn = item.querySelector('.btn-delete');
        if (deleteBtn) {
            deleteBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                if (confirm("Are you sure you want to delete this chat session?")) {
                    await deleteSession(id);
                }
            });
        }
    });
}

function loadSession(session) {
    state.currentConversation = session.id;
    state.messages = session.messages || [];
    
    els.chatMessages.innerHTML = '';
    if (els.welcomeScreen) {
        els.welcomeScreen.style.display = 'none';
    }
    
    state.messages.forEach(msg => {
        if (msg.role === 'user') {
            addMessage('user', msg.content);
        } else if (msg.role === 'assistant') {
            if (msg.data) {
                const assistantMsgId = addAssistantMessage(msg.data, false);
                state.messageData.set(assistantMsgId, msg.data);
                state.lastResponse = msg.data;
                updateSourcesPanel(msg.data);
            } else {
                addMessage('assistant', msg.content);
            }
        }
    });
    
    $$('.history-item').forEach(item => {
        item.classList.toggle('active', item.dataset.id === session.id);
    });
}

async function renameSession(id, newTitle) {
    try {
        const res = await fetch(`${API_BASE}/api/history/${id}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                ...getAuthHeader()
            },
            body: JSON.stringify({
                title: newTitle
            })
        });
        if (res.ok) {
            const updated = await res.json();
            state.conversations = state.conversations.map(c => c.id === id ? updated : c);
            renderHistory();
        }
    } catch (e) {
        console.error("Error renaming chat session:", e);
    }
}

async function deleteSession(id) {
    try {
        const res = await fetch(`${API_BASE}/api/history/${id}`, {
            method: 'DELETE',
            headers: getAuthHeader()
        });
        if (res.ok) {
            state.conversations = state.conversations.filter(c => c.id !== id);
            if (state.currentConversation === id) {
                startNewChat();
            } else {
                renderHistory();
            }
        }
    } catch (e) {
        console.error("Error deleting chat session:", e);
    }
}

function startNewChat() {
    state.currentConversation = null;
    state.messages = [];
    els.chatMessages.innerHTML = '';
    if (els.welcomeScreen) {
        els.welcomeScreen.style.display = 'flex';
    }
    $$('.history-item').forEach(item => item.classList.remove('active'));
    els.queryInput.value = '';
    els.queryInput.focus();
}

function updateUserUI(user) {
    if (!user) return;
    const email = user.email || 'user@university.edu';
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

async function initSupabase() {
    try {
        const res = await fetch('/api/config');
        const config = await res.json();
        supabase = window.supabase.createClient(config.supabase_url, config.supabase_anon_key);
        
        supabase.auth.onAuthStateChange((event, session) => {
            if (session) {
                localStorage.setItem('aether_token', session.access_token);
                updateUserUI(session.user);
            } else if (event === 'SIGNED_OUT') {
                localStorage.removeItem('aether_token');
                window.location.href = '/';
            }
        });

        const { data: { session } } = await supabase.auth.getSession();
        if (!session) {
            window.location.href = '/';
            return;
        }
        
        localStorage.setItem('aether_token', session.access_token);
        updateUserUI(session.user);
        await loadHistory();
    } catch (e) {
        console.error("Failed to initialize Supabase:", e);
    }
}

function loadSettingsFromLocalStorage() {
    try {
        const topK = localStorage.getItem('aether_settings_topK') || '5';
        const minSim = localStorage.getItem('aether_settings_minSim') || '22';
        const temperature = localStorage.getItem('aether_settings_temperature') || '0.0';
        const verify = localStorage.getItem('aether_settings_verify') !== 'false'; // defaults to true
        const studyMode = localStorage.getItem('aether_settings_studyMode') === 'true'; // defaults to false
        const model = localStorage.getItem('aether_settings_model') || 'light';
        
        if (els.topK) {
            els.topK.value = topK;
            els.topKValue.textContent = topK;
        }
        if (els.minSim) {
            els.minSim.value = minSim;
            els.minSimValue.textContent = (minSim / 100).toFixed(2);
        }
        if (els.temperature) {
            els.temperature.value = temperature;
            els.temperatureValue.textContent = parseFloat(temperature).toFixed(1);
        }
        if (els.verifyToggle) {
            els.verifyToggle.checked = verify;
        }
        if (els.groundedStudyToggle) {
            els.groundedStudyToggle.checked = studyMode;
        }
        if (els.modelSelect) {
            els.modelSelect.value = model;
        }
        syncStudyGuardrails();
    } catch (e) {
        console.error("Failed to load settings from localStorage:", e);
    }
}

function saveSettingsToLocalStorage() {
    try {
        if (els.topK) localStorage.setItem('aether_settings_topK', els.topK.value);
        if (els.minSim) localStorage.setItem('aether_settings_minSim', els.minSim.value);
        if (els.temperature) localStorage.setItem('aether_settings_temperature', els.temperature.value);
        if (els.verifyToggle) localStorage.setItem('aether_settings_verify', els.verifyToggle.checked);
        if (els.groundedStudyToggle) localStorage.setItem('aether_settings_studyMode', els.groundedStudyToggle.checked);
        if (els.modelSelect) localStorage.setItem('aether_settings_model', els.modelSelect.value);
    } catch (e) {
        console.error("Failed to save settings to localStorage:", e);
    }
}

function resetSettingsToDefaults() {
    if (els.topK) {
        els.topK.value = '5';
        els.topKValue.textContent = '5';
    }
    if (els.minSim) {
        els.minSim.value = '22';
        els.minSimValue.textContent = '0.22';
    }
    if (els.temperature) {
        els.temperature.value = '0.0';
        els.temperatureValue.textContent = '0.0';
    }
    if (els.verifyToggle) {
        els.verifyToggle.checked = true;
    }
    if (els.groundedStudyToggle) {
        els.groundedStudyToggle.checked = false;
    }
    if (els.modelSelect) {
        els.modelSelect.value = 'light';
    }
    saveSettingsToLocalStorage();
    syncStudyGuardrails();
}

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// CITATION HIGHLIGHT (bonus feature)
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

document.addEventListener('click', (e) => {
    if (e.target.classList.contains('citation')) {
        const num = parseInt(e.target.textContent.replace(/[\[\]]/g, ''));
        if (num && state.sourcesOpen) {
            // Highlight the corresponding chunk card
            const chunkCards = $$('.insight-node');
            if (chunkCards[num - 1]) {
                chunkCards[num - 1].scrollIntoView({ behavior: 'smooth', block: 'center' });
                chunkCards[num - 1].style.borderColor = 'var(--accent-primary)';
                chunkCards[num - 1].style.boxShadow = 'var(--shadow-glow)';
                setTimeout(() => {
                    chunkCards[num - 1].style.borderColor = '';
                    chunkCards[num - 1].style.boxShadow = '';
                }, 2000);
            }
        }
        // Open sources panel if not open
        if (!state.sourcesOpen) {
            toggleSourcesPanel();
            switchSourceTab('chunks');
        }
    }
});

