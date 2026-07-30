/**
 * settings.js — LocalStorage configuration and setting management
 */

import { els, state } from './state.js';

export function syncStudyGuardrails() {
    const enabled = !!els.groundedStudyToggle?.checked;
    if (els.studyGuardrailsCard) {
        els.studyGuardrailsCard.style.display = enabled ? 'block' : 'none';
    }
}

export function loadSettingsFromLocalStorage() {
    try {
        const topK = localStorage.getItem('aether_settings_topK') || '5';
        const minSim = localStorage.getItem('aether_settings_minSim') || '22';
        const temperature = localStorage.getItem('aether_settings_temperature') || '0.0';
        const verify = localStorage.getItem('aether_settings_verify') !== 'false'; // defaults to true
        const studyMode = localStorage.getItem('aether_settings_studyMode') === 'true'; // defaults to false
        const model = localStorage.getItem('aether_settings_model') || 'light';

        state.deepResearchMode = model === 'heavy';

        if (els.topK) {
            els.topK.value = topK;
            if (els.topKValue) els.topKValue.textContent = topK;
        }
        if (els.minSim) {
            els.minSim.value = minSim;
            if (els.minSimValue) els.minSimValue.textContent = (minSim / 100).toFixed(2);
        }
        if (els.temperature) {
            els.temperature.value = temperature;
            if (els.temperatureValue) els.temperatureValue.textContent = parseFloat(temperature).toFixed(1);
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

export function saveSettingsToLocalStorage() {
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

export function resetSettingsToDefaults() {
    if (els.topK) {
        els.topK.value = '5';
        if (els.topKValue) els.topKValue.textContent = '5';
    }
    if (els.minSim) {
        els.minSim.value = '22';
        if (els.minSimValue) els.minSimValue.textContent = '0.22';
    }
    if (els.temperature) {
        els.temperature.value = '0.0';
        if (els.temperatureValue) els.temperatureValue.textContent = '0.0';
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
