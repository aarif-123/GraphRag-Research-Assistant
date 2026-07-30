/**
 * state.js — Application state, API base, and DOM references
 */

export const API_BASE = window.location.origin;

export const state = {
    conversations: [],
    currentConversation: null,
    messages: [],
    isLoading: false,
    sourcesOpen: false,
    attachMenuOpen: false,
    lastResponse: null,
    messageData: new Map(), // Store data for each assistant message for syncing
    pendingAttachments: [],
    wikipediaMode: false,
    deepResearchMode: false,
    audioRecording: false,
    mediaRecorder: null,
    audioChunks: [],
    audioContext: null,
    audioAnalyser: null,
    audioStream: null,
    animationFrameId: null,
    discardRecording: false,
};

export const $ = (sel) => document.querySelector(sel);
export const $$ = (sel) => document.querySelectorAll(sel);

export const els = {};

export function initEls() {
    Object.assign(els, {
        sidebar: document.getElementById('sidebar'),
        sidebarOverlay: document.getElementById('sidebarOverlay'),
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
        upgradeDropdownBtn: $('#upgradeDropdownBtn'),
        paymentModal: $('#paymentModal'),
        paymentModalClose: $('#paymentModalClose'),
        checkoutPayBtn: $('#checkoutPayBtn'),
        micBtn: $('#micBtn'),
        composerMain: $('.composer-main'),
        voiceRecordingOverlay: $('#voiceRecordingOverlay'),
        voiceCancelBtn: $('#voiceCancelBtn'),
        voiceConfirmBtn: $('#voiceConfirmBtn'),
        voiceWaveContainer: $('#voiceWaveContainer'),
        voicePreviewText: $('#voicePreviewText'),
    });
}
