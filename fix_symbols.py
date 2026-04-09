"""
Reconstruct app.js from the corrupted version.
The file was corrupted by having half of itself duplicated mid-file.

Structure:
- Lines 1-1108 (0-indexed 0-1107): original file header up to addAssistantMessage func start
- Lines 1109-1148: corrupted/truncated version of addAssistantMessage  
- Lines 1149-2733: complete second copy of original file

Strategy:
- Use lines 0-1107 (good prefix)
- Write fixed addAssistantMessage function
- Use lines from second copy starting at addLoadingMessage (0-indexed 2401) to end
"""

with open('frontend/app.js', encoding='utf-8') as f:
    lines = f.readlines()

# Part 1: good prefix = lines 0-1107 (function addAssistantMessage starts at 1108, 0-indexed)
good_prefix = lines[0:1108]

# Part 2: the fixed addAssistantMessage function
fixed_func = '''function addAssistantMessage(data) {
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
        this.innerHTML = '<svg width=\\'11\\' height=\\'11\\' viewBox=\\'0 0 24 24\\' fill=\\'none\\' stroke=\\'currentColor\\' stroke-width=\\'2.5\\'><polyline points=\\'20 6 9 17 4 12\\'/></svg> Copied';
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
            const id = `${authorLast}${yearStr}${p.title.replace(/\\W/g,'').substring(0,8)}`;
            return `@article{${id},\\n  title={${p.title}},\\n  author={${p.author || 'Unknown'}},\\n  year={${yearStr}},\\n  journal={${p.domain || 'Tech. Report'}}\\n}`;
        }).join('\\n\\n'))));
        bibtexHtml = `<span class="message-stat btn-copy" style="color: var(--accent-cyan);" onclick="navigator.clipboard.writeText(decodeURIComponent(escape(atob('${bibtexPayload}')))); this.innerHTML='(Copied!)'; setTimeout(()=>this.innerHTML='BibTeX Export', 2000)">BibTeX Export</span>`;
        
        // Matrix Generator Feature
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

'''

# Part 3: from the second copy, take everything after the second addAssistantMessage function
# addLoadingMessage is at 0-indexed line 2401 in corrupted file
# That corresponds to original addLoadingMessage
good_suffix = lines[2401:]

# Now handle showHealthModal function which has broken chars
# Find it in good_suffix
health_start = None
for i, line in enumerate(good_suffix):
    if 'async function showHealthModal()' in line:
        health_start = i
        break

print(f'showHealthModal in suffix at index: {health_start}')

# Find the function end
health_end = None
for i, line in enumerate(good_suffix[health_start:]):
    if i > 0 and line.startswith('}') and good_suffix[health_start + i - 1].strip().endswith('}'):
        health_end = health_start + i + 1
        break

if health_end is None:
    # Find next function
    for i, line in enumerate(good_suffix[health_start+1:]):
        if line.startswith('// ') or line.startswith('async function') or line.startswith('function '):
            health_end = health_start + 1 + i
            break

print(f'showHealthModal end at: {health_end}')
if health_end:
    old_health_func = ''.join(good_suffix[health_start:health_end])
    print(f'Health func snippet: {repr(old_health_func[:300])}')

# Write fixed showHealthModal content
fixed_health = '''async function showHealthModal() {
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

'''

# Replace the showHealthModal in the suffix
if health_start is not None and health_end is not None:
    good_suffix = good_suffix[:health_start] + [fixed_health] + good_suffix[health_end:]
    print("OK: showHealthModal replaced in suffix")
else:
    print("WARNING: could not locate showHealthModal in suffix")

# Assemble final file
result_lines = good_prefix + [fixed_func] + good_suffix

final_content = ''.join(result_lines)
print(f'Final file size: {len(final_content)} chars, approx {len(result_lines)} lines')

with open('frontend/app.js', 'w', encoding='utf-8') as f:
    f.write(final_content)

print('Done.')
