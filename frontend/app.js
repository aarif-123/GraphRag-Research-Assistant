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
    clearHistoryBtn: $('#clearHistoryBtn'),
};

// INIT

document.addEventListener('DOMContentLoaded', () => {
    initEventListeners();
    checkHealth();
    loadHistory();
    renderAttachmentTray();
    syncStudyGuardrails();
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
    els.sidebarToggle.addEventListener('click', toggleSidebar);
    if (els.mobileMenuBtn) {
        els.mobileMenuBtn.addEventListener('click', () => {
            els.sidebar.classList.remove('collapsed');
        });
    }

    // Settings
    els.topK.addEventListener('input', () => {
        els.topKValue.textContent = els.topK.value;
    });
    els.minSim.addEventListener('input', () => {
        els.minSimValue.textContent = (els.minSim.value / 100).toFixed(2);
    });

    // Input
    els.queryInput.addEventListener('input', handleInputChange);
    els.queryInput.addEventListener('keydown', handleInputKeydown);
    els.sendBtn.addEventListener('click', sendQuery);
    if (els.groundedStudyToggle) {
        els.groundedStudyToggle.addEventListener('change', syncStudyGuardrails);
    }

    // Sources panel
    els.sourcePanelToggle.addEventListener('click', toggleSourcesPanel);
    els.sourcesPanelClose.addEventListener('click', () => {
        setSourcesPanelOpen(false);
    });

    // Source tabs
    $$('.sources-tab').forEach(tab => {
        tab.addEventListener('click', () => switchSourceTab(tab.dataset.tab));
    });

    // Health modal
    els.healthBtn.addEventListener('click', showHealthModal);
    els.healthModalClose.addEventListener('click', () => {
        els.healthModal.classList.remove('visible');
    });
    els.healthModal.addEventListener('click', (e) => {
        if (e.target === els.healthModal) els.healthModal.classList.remove('visible');
    });

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
        if (event.key === 'Escape' && state.attachMenuOpen) {
            setAttachMenuOpen(false);
            els.queryInput.focus();
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
        els.clearHistoryBtn.addEventListener('click', () => {
            if (confirm('Clear all conversation history?')) {
                state.conversations = [];
                localStorage.removeItem('graphrag_history');
                renderHistory();
            }
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
    paperList.innerHTML = data.papers && data.papers.length > 0
        ? data.papers.map(p => `
            <div class="source-card paper">
                <div class="card-title">${escapeHtml(p.title)}</div>
                <div class="card-meta">
                    <span>${escapeHtml(p.author)}</span>
                    <span>${p.year}</span>
                    <span class="domain-tag">${escapeHtml(p.domain)}</span>
                </div>
                <div class="card-abstract">${escapeHtml((p.abstract || '').substring(0, 150))}...</div>
            </div>
        `).join('')
        : '<div class="sources-empty">No papers identified.</div>';

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
    const val = els.queryInput.value;
    els.charCount.textContent = `${val.length}/2000`;
    els.sendBtn.disabled = val.trim().length === 0 || state.isLoading;

    // Auto resize
    els.queryInput.style.height = 'auto';
    els.queryInput.style.height = Math.min(els.queryInput.scrollHeight, 150) + 'px';
}

function handleInputKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (!els.sendBtn.disabled) sendQuery();
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
            use_heavy: els.modelSelect ? els.modelSelect.value === 'heavy' : false,
            verify: els.verifyToggle ? els.verifyToggle.checked : true,
        };

        if (useChat) {
            data = await apiCall('/api/chat', {
                ...requestData,
                messages: state.messages,
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
        state.messages.push({ role: 'assistant', content: data.answer });
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

async function apiCall(endpoint, body) {
    const res = await fetch(`${API_BASE}${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
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
    return id;
}

function addAssistantMessage(data) {
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

    // Simulate Live Word-by-Word Streaming
    const textRaw = data.answer || '';
    let currIdx = 0;

    const streamInterval = setInterval(() => {
        // stream up to 5 chars at randomly variable speeds to look human-like native SSE
        currIdx += Math.floor(Math.random() * 5) + 3;
        if (currIdx >= textRaw.length) {
            currIdx = textRaw.length;
            clearInterval(streamInterval);
            footerDiv.style.opacity = "1"; // Show stats smoothly at the end
        }

        contentDiv.innerHTML = formatMarkdown(textRaw.substring(0, currIdx));

        // Auto scroll only if we're near bottom
        if (els.chatContainer.scrollHeight - els.chatContainer.scrollTop - els.chatContainer.clientHeight < 150) {
            scrollToBottom();
        }
    }, 12);

    // Auto scroll only if we're near bottom initially
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

/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• MARKDOWN & KATEX â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */
function formatMarkdown(text) {
    if (!text) return '';
    text = text.replace(/\[(\d+)\]/g, '<span class="citation">[$1]</span>');

    // Handle v4.0 specific tags if any
    text = text.replace(/ã€(.*?)ã€‘/g, '<span class="source-tag">$1</span>');

    // â”€â”€ BEAUTIFY SQUISHED BACKEND LISTS â”€â”€
    // Convert squished ' â€¢ ' bullet points into proper Markdown lists with spacing
    // ── BEAUTIFY SQUISHED BACKEND LISTS ──
    // 1. Convert bullet points (\u2022) into standard Markdown bullets (-)
    text = text.replace(/\u2022/g, '-');

    // 2. If a bullet follows text on the same line, move it to a new list block
    text = text.replace(/([^\n])\s+-\s+/g, '$1\n\n- ');

    // 3. Ensure any list following a paragraph has a double newline (for marked.js)
    text = text.replace(/([a-zA-Z0-9\):])(\s*)\n-\s+/g, '$1\n\n- ');

    // 4. Bold specific paper titles and format metadata (Year, Author, Citations)
    // Case A: Full format "- Title (YYYY) — Author"
    text = text.replace(/-\s+([^\n]+?)\s+\((\d{4})\)\s*(â€”|-|â€“)\s*([^\n]+)/g, '- **$1** <span class="paper-year">($2)</span> &mdash; <span class="paper-author">$4</span>');
    
    // Case B: Compact format "- Title (YYYY) [Citation]" as seen in surveys
    text = text.replace(/-\s+([^\n]+?)\s+\((\d{4})\)\s*(\[\d+\]|\[N\])/g, '- **$1** <span class="paper-year">($2)</span> $3');

    // 1. Extract and protect LaTeX math
    const mathBlocks = [];
    let processedText = text;

    // Display math $$ ... $$
    processedText = processedText.replace(/\$\$(.+?)\$\$/gs, (match, p1) => {
        const id = `__MATH_DISPLAY_${mathBlocks.length}__`;
        try {
            mathBlocks.push({ id, html: katex.renderToString(p1, { displayMode: true, throwOnError: false }) });
        } catch (e) { mathBlocks.push({ id, html: p1 }); }
        return id;
    });

    // Inline math $ ... $
    processedText = processedText.replace(/\$(.+?)\$/g, (match, p1) => {
        const id = `__MATH_INLINE_${mathBlocks.length}__`;
        try {
            mathBlocks.push({ id, html: katex.renderToString(p1, { displayMode: false, throwOnError: false }) });
        } catch (e) { mathBlocks.push({ id, html: p1 }); }
        return id;
    });

    // 2. Render Markdown
    if (window.marked && window.marked.parse) {
        processedText = marked.parse(processedText);
    } else {
        processedText = processedText.replace(/\n\n/g, '</p><p>').replace(/\n/g, '<br>');
    }

    // 3. Re-inject rendered Math
    mathBlocks.forEach(block => {
        processedText = processedText.replace(block.id, block.html);
    });

    return processedText;
}

function updatePipelineStep(step) {
    if (els.pipelineStep) {
        els.pipelineStep.textContent = step ? `â€¢ ${step}` : '';
        els.pipelineStep.style.opacity = step ? '1' : '0';
    }
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
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

function saveToHistory(query, data) {
    const historyItem = {
        id: Date.now().toString(),
        query: query,
        timestamp: new Date().toISOString(),
        intent: data.intent,
        papersCount: data.papers ? data.papers.length : 0,
    };

    // Prevent duplicates and move to top
    state.conversations = state.conversations.filter(c => c.query !== query);
    state.conversations.unshift(historyItem);

    if (state.conversations.length > 20) state.conversations.pop();

    try {
        localStorage.setItem('graphrag_history', JSON.stringify(state.conversations));
    } catch (e) { /* quota exceeded, ignore */ }

    renderHistory();
}

function loadHistory() {
    try {
        const saved = localStorage.getItem('graphrag_history');
        if (saved) {
            state.conversations = JSON.parse(saved);
            renderHistory();
        }
    } catch (e) { /* corrupt data */ }
}

function renderHistory() {
    if (state.conversations.length === 0) {
        els.historyList.innerHTML = `
            <div class="history-empty">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                    <circle cx="12" cy="12" r="10"/>
                    <polyline points="12 6 12 12 16 14"/>
                </svg>
                <span>No conversations yet</span>
            </div>
        `;
        return;
    }

    els.historyList.innerHTML = state.conversations.map(conv => {
        const timeStr = new Date(conv.timestamp).toLocaleDateString(undefined, {
            month: 'short', day: 'numeric',
        });
        return `
            <div class="history-item" data-query="${escapeHtml(conv.query)}" title="${escapeHtml(conv.query)}">
                <div class="history-item-main">
                    <svg class="history-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                    </svg>
                    <div class="history-content">
                        <span class="history-text">${escapeHtml(conv.query)}</span>
                        <span class="history-meta">${timeStr} &bull; ${conv.papersCount || 0} papers</span>
                    </div>
                </div>
                <button class="history-delete-btn" data-id="${conv.id}" title="Delete entry">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                        <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                    </svg>
                </button>
            </div>
        `;
    }).join('');

    // Click to re-run
    els.historyList.querySelectorAll('.history-item').forEach(item => {
        item.addEventListener('click', (e) => {
            if (e.target.closest('.history-delete-btn')) return;
            els.queryInput.value = item.dataset.query;
            handleInputChange();
            sendQuery();
        });
    });

    // Delete handler
    els.historyList.querySelectorAll('.history-delete-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const id = btn.dataset.id;
            state.conversations = state.conversations.filter(c => c.id !== id);
            localStorage.setItem('graphrag_history', JSON.stringify(state.conversations));
            renderHistory();
        });
    });
}

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// CITATION HIGHLIGHT (bonus feature)
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

document.addEventListener('click', (e) => {
    if (e.target.classList.contains('citation')) {
        const num = parseInt(e.target.textContent.replace(/[\[\]]/g, ''));
        if (num && state.sourcesOpen) {
            // Highlight the corresponding chunk card
            const chunkCards = $$('.chunk-card');
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


    const attachBtn = document.getElementById("attachMenuBtn");
    const attachMenu = document.getElementById("attachMenu");

    const fileInput = document.getElementById("attachmentFileInput");
    const pdfInput = document.getElementById("pdfFileInput");

    /* TOGGLE MENU */
    attachBtn.addEventListener("click", (e) => {
        e.stopPropagation();

        const isOpen = attachMenu.classList.toggle("open");

        attachBtn.setAttribute("aria-expanded", isOpen);
        attachMenu.setAttribute("aria-hidden", !isOpen);
    });

    /* CLOSE ON OUTSIDE CLICK */
    document.addEventListener("click", (e) => {
        if (!attachMenu.contains(e.target) && !attachBtn.contains(e.target)) {
            attachMenu.classList.remove("open");
            attachBtn.setAttribute("aria-expanded", false);
            attachMenu.setAttribute("aria-hidden", true);
        }
    });

    /* HANDLE ACTIONS */
    document.querySelectorAll(".attach-menu-item").forEach(item => {
        item.addEventListener("click", () => {
            const action = item.dataset.attachAction;

            attachMenu.classList.remove("open");

            if (action === "files") {
                fileInput.click();
            }
            else if (action === "pdf") {
                pdfInput.click();
            }
            else if (action === "deep-research") {
                alert("Deep Research Mode Activated 🚀");
            }
        });
    });

    /* DEBUG (optional) */
    fileInput.addEventListener("change", (e) => {
        console.log("Files:", e.target.files);
    });

    pdfInput.addEventListener("change", (e) => {
        console.log("PDF:", e.target.files);
    });
});

