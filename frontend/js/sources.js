/**
 * sources.js — Right-side sources panel, tabs, D3 knowledge graph visualizer & timeline
 */

import { els, state, $ } from './state.js';
import { escapeHtml } from './utils.js';

export function toggleSourcesPanel() {
    setSourcesPanelOpen(!state.sourcesOpen);
}

export function openSourcesPanel() {
    setSourcesPanelOpen(true);
}

export function setSourcesPanelOpen(isOpen) {
    state.sourcesOpen = isOpen;
    if (els.sourcesPanel) {
        els.sourcesPanel.classList.toggle('open', isOpen);
    }
    document.body.classList.toggle('sources-open', isOpen);
}

export function switchSourceTab(tabName) {
    document.querySelectorAll('.sources-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tabName));
    document.querySelectorAll('.sources-tab-content').forEach(c => c.classList.remove('active'));
    const target = $(`#tab${tabName.charAt(0).toUpperCase() + tabName.slice(1)}`);
    if (target) target.classList.add('active');
}

export function getColorForDomain(domain) {
    const map = {
        'Machine Learning': '#6366f1',
        'Vision': '#14b8a6',
        'NLP': '#f59e0b',
        'Robotics': '#ef4444',
        'Med-AI': '#ec4899'
    };
    return map[domain] || '#64748b';
}

export function renderGraph(papers) {
    if (typeof d3 === 'undefined') return;
    const svg = d3.select("#graphSvg");
    if (svg.empty()) return;
    svg.selectAll("*").remove();

    const graphEmpty = document.getElementById('graphEmpty');
    const graphContainer = document.getElementById('graphContainer');

    if (!papers || papers.length === 0) {
        if (graphEmpty) graphEmpty.style.display = 'flex';
        if (graphContainer) graphContainer.style.display = 'none';
        return;
    }

    if (graphEmpty) graphEmpty.style.display = 'none';
    if (graphContainer) graphContainer.style.display = 'block';

    window.lastGraphPapers = papers;

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
        const toggleBtn = document.getElementById('timelineToggleBtn');
        if (toggleBtn) toggleBtn.innerHTML = window.isTimelineView ? 'Knowledge Graph' : 'Timeline Graph';
    }

    const panel = document.getElementById('sourcesPanel');
    const width = (panel ? panel.clientWidth : 400) - 40;
    const height = 350;
    const g = svg.append("g");

    const zoom = d3.zoom().scaleExtent([0.5, 4]).on("zoom", (event) => g.attr("transform", event.transform));
    svg.call(zoom);

    const resetBtn = document.getElementById('resetGraph');
    if (resetBtn) {
        resetBtn.onclick = () => {
            svg.transition().duration(750).call(zoom.transform, d3.zoomIdentity);
        };
    }

    const nodes = papers.map(p => ({
        id: p.id || p.title,
        title: p.title,
        author: p.author || 'Unknown',
        domain: p.domain || 'General',
        year: parseInt(p.year) || 2020,
        radius: 8 + Math.min((p.citations || 5) / 2, 8)
    }));

    if (window.isTimelineView) {
        const years = nodes.map(n => n.year);
        const minYear = Math.min(...years) - 2;
        const maxYear = Math.max(...years) + 2;

        const xScale = d3.scaleLinear().domain([minYear, maxYear]).range([50, width - 50]);

        g.append("line")
            .attr("x1", 30)
            .attr("y1", height / 2)
            .attr("x2", width - 30)
            .attr("y2", height / 2)
            .attr("stroke", "rgba(255, 255, 255, 0.2)")
            .attr("stroke-width", 2);

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

        nodes.forEach((d, i) => {
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

            nodeGroup.on("mouseover", function () {
                d3.select(this).select("circle").attr("stroke-width", 4).attr("stroke", "#a78bfa");
            }).on("mouseout", function () {
                d3.select(this).select("circle").attr("stroke-width", 2).attr("stroke", "white");
            });
        });

    } else {
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
            .style("text-shadow", "0px 1px 4px rgba(0,0,0,0.9), 0px 0px 2px rgba(0,0,0,1)")
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

export function updateSourcesPanel(data) {
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

    const chunkList = document.getElementById('tabChunks');
    if (chunkList) {
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
    }

    const paperList = document.getElementById('tabPapers');
    if (paperList) {
        let papersHtml = '';
        const dbPapers = data.papers || [];
        const arxivPapers = data.arxiv_papers || [];

        if (dbPapers.length === 0 && arxivPapers.length === 0) {
            papersHtml = '<div class="sources-empty">No papers identified.</div>';
        } else {
            if (arxivPapers.length > 0) {
                papersHtml += `<div style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--accent-cyan); margin: 10px 0; letter-spacing: 0.5px;">Live arXiv References</div>`;
                papersHtml += arxivPapers.map(p => `
                    <div class="source-card paper">
                        <div class="card-title">${escapeHtml(p.title)}</div>
                        <div class="card-meta">
                            <span>${escapeHtml(Array.isArray(p.authors) ? p.authors.join(', ') : (p.author || p.authors || 'Unknown'))}</span>
                            <span>${p.year}</span>
                            <span class="domain-tag" style="background: rgba(239, 68, 68, 0.15); color: #f87171;">${escapeHtml(p.source || 'arXiv')}</span>
                        </div>
                        <div class="card-abstract">${escapeHtml((p.abstract || '').substring(0, 150))}...</div>
                        <div style="display: flex; gap: 8px; margin-top: 12px;">
                            <a href="${p.url}" target="_blank" style="background: rgba(34, 211, 238, 0.1); border: 1px solid rgba(34, 211, 238, 0.3); color: var(--accent-cyan); padding: 5px 10px; border-radius: 6px; font-size: 11px; text-decoration: none; display: inline-flex; align-items: center; gap: 4px;">
                                Abstract
                            </a>
                            <a href="${p.pdf_url}" target="_blank" style="background: rgba(52, 211, 153, 0.1); border: 1px solid rgba(52, 211, 153, 0.3); color: var(--accent-emerald); padding: 5px 10px; border-radius: 6px; font-size: 11px; text-decoration: none; display: inline-flex; align-items: center; gap: 4px;">
                                PDF Document
                            </a>
                        </div>
                    </div>
                `).join('');
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
                            <a href="https://scholar.google.com/scholar?q=${encodeURIComponent(p.title)}" target="_blank" style="background: rgba(99, 102, 241, 0.1); border: 1px solid rgba(99, 102, 241, 0.3); color: var(--primary-light); padding: 5px 10px; border-radius: 6px; font-size: 11px; text-decoration: none; display: inline-flex; align-items: center; gap: 4px;">
                                Google Scholar
                            </a>
                        </div>
                    </div>
                `).join('');
            }
        }
        paperList.innerHTML = papersHtml;
    }

    if (data.papers) {
        renderGraph(data.papers);
    }
}

window.toggleSourcesPanel = toggleSourcesPanel;
window.openSourcesPanel = openSourcesPanel;
window.setSourcesPanelOpen = setSourcesPanelOpen;
window.switchSourceTab = switchSourceTab;
window.updateSourcesPanel = updateSourcesPanel;
window.renderGraph = renderGraph;
