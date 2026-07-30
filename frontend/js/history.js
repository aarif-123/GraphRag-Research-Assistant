/**
 * history.js — Conversation session loading, rendering, switching, renaming & deletion
 */

import { API_BASE, els, state } from './state.js';
import { getAuthHeader } from './auth.js';
import { escapeHtml } from './utils.js';

export async function generatePremiumTitle(query) {
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
    return query.length > 30 ? query.substring(0, 30) + '...' : query;
}

export async function saveToHistory(query, data) {
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

export async function loadHistory() {
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

export function renderHistory() {
    if (!els.historyList) return;
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

export function loadSession(session) {
    state.currentConversation = session.id;
    state.messages = session.messages || [];

    if (els.chatMessages) els.chatMessages.innerHTML = '';
    if (els.welcomeScreen) {
        els.welcomeScreen.style.display = 'none';
    }

    state.messages.forEach(msg => {
        if (msg.role === 'user') {
            if (window.addMessage) window.addMessage('user', msg.content);
        } else if (msg.role === 'assistant') {
            if (msg.data) {
                const assistantMsgId = window.addAssistantMessage ? window.addAssistantMessage(msg.data, false) : null;
                if (assistantMsgId) state.messageData.set(assistantMsgId, msg.data);
                state.lastResponse = msg.data;
                if (window.updateSourcesPanel) window.updateSourcesPanel(msg.data);
            } else {
                if (window.addMessage) window.addMessage('assistant', msg.content);
            }
        }
    });

    document.querySelectorAll('.history-item').forEach(item => {
        item.classList.toggle('active', item.dataset.id === session.id);
    });
}

export async function renameSession(id, newTitle) {
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

export async function deleteSession(id) {
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

export function startNewChat() {
    state.currentConversation = null;
    state.messages = [];
    if (els.chatMessages) els.chatMessages.innerHTML = '';
    if (els.welcomeScreen) {
        els.welcomeScreen.style.display = 'flex';
    }
    document.querySelectorAll('.history-item').forEach(item => item.classList.remove('active'));
    if (els.queryInput) {
        els.queryInput.value = '';
        els.queryInput.focus();
    }
}

window.loadHistory = loadHistory;
window.startNewChat = startNewChat;
