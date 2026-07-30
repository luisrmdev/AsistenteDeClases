// ---------------------------------------------------------------------------
// Background Service Worker — Meet Audio Recorder Extension v1.2
// ---------------------------------------------------------------------------

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.set({ recordingStates: {}, silenceTimeoutMin: 5, silenceAlarms: {}, recordingTimers: {} });
});

chrome.runtime.onStartup.addListener(() => {
  chrome.storage.local.set({ recordingStates: {}, silenceAlarms: {}, recordingTimers: {} });
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.target !== 'background') return;

  if (message.type === 'START_RECORDING') {
    startRecording(message.tabId);
  } else if (message.type === 'STOP_RECORDING') {
    stopRecording(message.tabId);
  } else if (message.type === 'PAUSE_RECORDING') {
    pauseRecording(message.tabId);
  } else if (message.type === 'RESUME_RECORDING') {
    resumeRecording(message.tabId);
  } else if (message.type === 'CANCEL_RECORDING') {
    cancelRecording(message.tabId);
  } else if (message.type === 'UPDATE_STATE') {
    updateState(message.tabId, message.state);
  } else if (message.type === 'CAPTURE_SCREENSHOT') {
    captureTabScreenshot(message.tabId, sendResponse);
    return true;
  } else if (message.type === 'MANUAL_SCREENSHOT_FROM_POPUP') {
    chrome.runtime.sendMessage({
      target: 'offscreen',
      type: 'MANUAL_SCREENSHOT',
      tabId: message.tabId
    });
  }
});

// --- Screenshot capture ---
async function captureTabScreenshot(tabId, sendResponse) {
  try {
    const tab = await chrome.tabs.get(tabId);
    const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: 'jpeg', quality: 75 });
    sendResponse({ dataUrl });
  } catch (e) {
    console.warn('[Background] Screenshot capture failed:', e);
    sendResponse({ dataUrl: null, error: e.message });
  }
}



// Detectar silencio en las pestañas
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.audible !== undefined) {
    chrome.storage.local.get(['recordingStates', 'silenceTimeoutMin', 'silenceAlarms'], (result) => {
      const states = result.recordingStates || {};
      const timeoutMin = result.silenceTimeoutMin || 5;
      const alarms = result.silenceAlarms || {};
      const alarmName = `silence_${tabId}`;
      if (states[tabId] === 'recording') {
        if (changeInfo.audible === false) {
          chrome.alarms.create(alarmName, { delayInMinutes: timeoutMin });
          const endTimestamp = Date.now() + (timeoutMin * 60000);
          alarms[tabId] = endTimestamp;
          chrome.storage.local.set({ silenceAlarms: alarms });
        } else if (changeInfo.audible === true) {
          chrome.alarms.clear(alarmName);
          delete alarms[tabId];
          chrome.storage.local.set({ silenceAlarms: alarms });
        }
      }
    });
  }
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name.startsWith('silence_')) {
    const tabId = parseInt(alarm.name.replace('silence_', ''));
    stopRecording(tabId);
  } else if (alarm.name.startsWith('screenshot_')) {
    const tabId = parseInt(alarm.name.replace('screenshot_', ''));
    chrome.runtime.sendMessage({
      target: 'offscreen',
      type: 'AUTO_SCREENSHOT',
      tabId: tabId
    }).catch(() => {});
  }
});

async function updateState(tabId, state) {
  const result = await chrome.storage.local.get(['recordingStates']);
  const states = result.recordingStates || {};
  if (state === 'idle') { delete states[tabId]; } else { states[tabId] = state; }
  await chrome.storage.local.set({ recordingStates: states });

  if (state === 'idle' || state === 'error' || state === 'completed') {
    const tResult = await chrome.storage.local.get(['recordingTimers']);
    const timers = tResult.recordingTimers || {};
    if (timers[tabId]) { delete timers[tabId]; await chrome.storage.local.set({ recordingTimers: timers }); }
  }
  if (state === 'idle' || state === 'error' || state === 'completed') {
    const activeStates = Object.values(states);
    const hasActive = activeStates.some(s => s === 'recording' || s === 'paused' || s === 'uploading');
    if (!hasActive) { try { await chrome.offscreen.closeDocument(); } catch (err) {} }
  }
}

async function startRecording(tabId) {
  const result = await chrome.storage.local.get(['recordingStates']);
  const states = result.recordingStates || {};
  if (states[tabId] === 'recording' || states[tabId] === 'paused') return;

  chrome.tabCapture.getMediaStreamId({ targetTabId: tabId }, async (streamId) => {
    if (chrome.runtime.lastError) { console.error(chrome.runtime.lastError.message); return; }
    await setupOffscreenDocument('offscreen.html');
    const configRes = await chrome.storage.local.get(['ghostModes', 'screenshotIntervalMin']);
    const ghostModes = configRes.ghostModes || {};
    const isGhost = ghostModes[tabId] || false;
    const intervalMin = configRes.screenshotIntervalMin || 5;

    chrome.runtime.sendMessage({ target: 'offscreen', type: 'START_RECORDING', streamId, tabId, ghostMode: isGhost });
    updateState(tabId, 'recording');
    clearSilenceAlarm(tabId);
    chrome.alarms.create(`screenshot_${tabId}`, { periodInMinutes: intervalMin });

    const timerRes = await chrome.storage.local.get(['recordingTimers']);
    const timers = timerRes.recordingTimers || {};
    timers[tabId] = { startTime: Date.now(), elapsed: 0, paused: false };
    await chrome.storage.local.set({ recordingTimers: timers });

    try {
      chrome.scripting.executeScript({
        target: { tabId },
        func: () => {
          window._asistenteBeforeUnload = (e) => { e.preventDefault(); e.returnValue = "¿Seguro que quieres salir? Se detendrá la grabación."; return e.returnValue; };
          window.addEventListener('beforeunload', window._asistenteBeforeUnload);
        }
      });
    } catch(err) { console.warn("No se pudo inyectar protección en pestaña:", err); }
  });
}

async function pauseRecording(tabId) {
  clearSilenceAlarm(tabId);
  chrome.alarms.clear(`screenshot_${tabId}`);
  const timerRes = await chrome.storage.local.get(['recordingTimers']);
  const timers = timerRes.recordingTimers || {};
  const t = timers[tabId];
  if (t && !t.paused) { t.elapsed += (Date.now() - t.startTime); t.paused = true; await chrome.storage.local.set({ recordingTimers: timers }); }
  try { await chrome.runtime.sendMessage({ target: 'offscreen', type: 'PAUSE_RECORDING', tabId }); } catch (err) { console.warn("Error enviando pausa al offscreen", err); }
}

async function resumeRecording(tabId) {
  const configRes = await chrome.storage.local.get(['recordingTimers', 'screenshotIntervalMin']);
  const timers = configRes.recordingTimers || {};
  const intervalMin = configRes.screenshotIntervalMin || 5;
  const t = timers[tabId];
  if (t && t.paused) { t.startTime = Date.now(); t.paused = false; await chrome.storage.local.set({ recordingTimers: timers }); }
  try { await chrome.runtime.sendMessage({ target: 'offscreen', type: 'RESUME_RECORDING', tabId }); } catch (err) { console.warn("Error enviando reanudar al offscreen", err); }
  chrome.alarms.create(`screenshot_${tabId}`, { periodInMinutes: intervalMin });
}

async function stopRecording(tabId) {
  try {
    clearSilenceAlarm(tabId);
    chrome.alarms.clear(`screenshot_${tabId}`);
    const result = await chrome.storage.local.get(['customNames', 'recordingTimers']);
    const customName = (result.customNames || {})[tabId] || '';
    const timers = result.recordingTimers || {};
    if (timers[tabId]) { delete timers[tabId]; await chrome.storage.local.set({ recordingTimers: timers }); }
    await chrome.runtime.sendMessage({ target: 'offscreen', type: 'STOP_RECORDING', tabId, customName });
    try { chrome.scripting.executeScript({ target: { tabId }, func: () => { if (window._asistenteBeforeUnload) { window.removeEventListener('beforeunload', window._asistenteBeforeUnload); delete window._asistenteBeforeUnload; } } }); } catch(err) {}
  } catch (err) { console.warn("No se pudo contactar al offscreen. Reiniciando estado."); updateState(tabId, 'idle'); }
}

async function cancelRecording(tabId) {
  try {
    clearSilenceAlarm(tabId);
    chrome.alarms.clear(`screenshot_${tabId}`);
    const result = await chrome.storage.local.get(['recordingTimers']);
    const timers = result.recordingTimers || {};
    if (timers[tabId]) { delete timers[tabId]; await chrome.storage.local.set({ recordingTimers: timers }); }
    await chrome.runtime.sendMessage({ target: 'offscreen', type: 'CANCEL_RECORDING', tabId });
    try { chrome.scripting.executeScript({ target: { tabId }, func: () => { if (window._asistenteBeforeUnload) { window.removeEventListener('beforeunload', window._asistenteBeforeUnload); delete window._asistenteBeforeUnload; } } }); } catch(err) {}
    updateState(tabId, 'idle');
  } catch (err) { console.warn("Error enviando cancelación al offscreen"); updateState(tabId, 'idle'); }
}

async function clearSilenceAlarm(tabId) {
  chrome.alarms.clear(`silence_${tabId}`);
  const result = await chrome.storage.local.get(['silenceAlarms']);
  const alarms = result.silenceAlarms || {};
  if (alarms[tabId]) { delete alarms[tabId]; await chrome.storage.local.set({ silenceAlarms: alarms }); }
}

async function setupOffscreenDocument(path) {
  const existingContexts = await chrome.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT'], documentUrls: [chrome.runtime.getURL(path)] });
  if (existingContexts.length > 0) return;
  if (creating) { await creating; } else {
    creating = chrome.offscreen.createDocument({ url: path, reasons: ['USER_MEDIA'], justification: 'Recording tab audio' });
    await creating; creating = null;
  }
}
let creating;
