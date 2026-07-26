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
  }
});

// Detectar silencio en las pestañas
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.audible !== undefined) {
    chrome.storage.local.get(['recordingStates', 'silenceTimeoutMin', 'silenceAlarms'], (result) => {
      const states = result.recordingStates || {};
      const timeoutMin = result.silenceTimeoutMin || 5;
      const alarms = result.silenceAlarms || {};
      const alarmName = `silence_${tabId}`;

      // Si la pestaña se está grabando (y no está en pausa)
      if (states[tabId] === 'recording') {
        if (changeInfo.audible === false) {
          // Dejó de sonar: iniciar temporizador y guardar timestamp
          chrome.alarms.create(alarmName, { delayInMinutes: timeoutMin });
          const endTimestamp = Date.now() + (timeoutMin * 60000);
          alarms[tabId] = endTimestamp;
          chrome.storage.local.set({ silenceAlarms: alarms });
          console.log(`Pestaña ${tabId} en silencio. Alarma configurada para ${timeoutMin} min.`);
        } else if (changeInfo.audible === true) {
          // Volvió a sonar: cancelar temporizador
          chrome.alarms.clear(alarmName);
          delete alarms[tabId];
          chrome.storage.local.set({ silenceAlarms: alarms });
          console.log(`Pestaña ${tabId} sonando. Alarma cancelada.`);
        }
      }
    });
  }
});

// Manejar el auto-apagado por silencio
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name.startsWith('silence_')) {
    const tabId = parseInt(alarm.name.replace('silence_', ''));
    console.log(`Silencio prolongado en pestaña ${tabId}. Deteniendo grabación...`);
    stopRecording(tabId);
  }
});

async function updateState(tabId, state) {
  const result = await chrome.storage.local.get(['recordingStates']);
  const states = result.recordingStates || {};
  
  if (state === 'idle') {
    delete states[tabId];
  } else {
    states[tabId] = state;
  }
  
  await chrome.storage.local.set({ recordingStates: states });
  
  // Limpiar timer si vuelve a estado base o hay error
  if (state === 'idle' || state === 'error' || state === 'completed') {
    const tResult = await chrome.storage.local.get(['recordingTimers']);
    const timers = tResult.recordingTimers || {};
    if (timers[tabId]) {
      delete timers[tabId];
      await chrome.storage.local.set({ recordingTimers: timers });
    }
  }

  // Cerrar documento offscreen si ya no hay grabaciones activas
  if (state === 'idle' || state === 'error' || state === 'completed') {
    const activeStates = Object.values(states);
    const hasActive = activeStates.some(s => s === 'recording' || s === 'paused' || s === 'uploading');
    if (!hasActive) {
      try {
        await chrome.offscreen.closeDocument();
      } catch (err) {}
    }
  }
}

async function startRecording(tabId) {
  const result = await chrome.storage.local.get(['recordingStates']);
  const states = result.recordingStates || {};
  if (states[tabId] === 'recording' || states[tabId] === 'paused') return;

  chrome.tabCapture.getMediaStreamId({ targetTabId: tabId }, async (streamId) => {
    if (chrome.runtime.lastError) {
      console.error(chrome.runtime.lastError.message);
      return;
    }
    
    await setupOffscreenDocument('offscreen.html');
    
    const ghostRes = await chrome.storage.local.get(['ghostMode']);
    const isGhost = ghostRes.ghostMode || false;

    chrome.runtime.sendMessage({
      target: 'offscreen',
      type: 'START_RECORDING',
      streamId: streamId,
      tabId: tabId,
      ghostMode: isGhost
    });

    updateState(tabId, 'recording');
    clearSilenceAlarm(tabId);
    
    // Iniciar timer
    const timerRes = await chrome.storage.local.get(['recordingTimers']);
    const timers = timerRes.recordingTimers || {};
    timers[tabId] = { startTime: Date.now(), elapsed: 0, paused: false };
    await chrome.storage.local.set({ recordingTimers: timers });
  });
}

async function pauseRecording(tabId) {
  clearSilenceAlarm(tabId);
  
  const timerRes = await chrome.storage.local.get(['recordingTimers']);
  const timers = timerRes.recordingTimers || {};
  const t = timers[tabId];
  if (t && !t.paused) {
    t.elapsed += (Date.now() - t.startTime);
    t.paused = true;
    await chrome.storage.local.set({ recordingTimers: timers });
  }

  try {
    await chrome.runtime.sendMessage({
      target: 'offscreen',
      type: 'PAUSE_RECORDING',
      tabId: tabId
    });
  } catch (err) {
    console.warn("Error enviando pausa al offscreen", err);
  }
}

async function resumeRecording(tabId) {
  const timerRes = await chrome.storage.local.get(['recordingTimers']);
  const timers = timerRes.recordingTimers || {};
  const t = timers[tabId];
  if (t && t.paused) {
    t.startTime = Date.now();
    t.paused = false;
    await chrome.storage.local.set({ recordingTimers: timers });
  }

  try {
    await chrome.runtime.sendMessage({
      target: 'offscreen',
      type: 'RESUME_RECORDING',
      tabId: tabId
    });
  } catch (err) {
    console.warn("Error enviando reanudar al offscreen", err);
  }
}

async function stopRecording(tabId) {
  try {
    clearSilenceAlarm(tabId);
    
    // Obtener customName para pasarlo a offscreen
    const result = await chrome.storage.local.get(['customNames', 'recordingTimers']);
    const names = result.customNames || {};
    const customName = names[tabId] || '';
    
    // Limpiar timer
    const timers = result.recordingTimers || {};
    if (timers[tabId]) {
      delete timers[tabId];
      await chrome.storage.local.set({ recordingTimers: timers });
    }

    await chrome.runtime.sendMessage({
      target: 'offscreen',
      type: 'STOP_RECORDING',
      tabId: tabId,
      customName: customName
    });
  } catch (err) {
    console.warn("No se pudo contactar al offscreen (probablemente la extensión se recargó). Reiniciando estado.");
    updateState(tabId, 'idle');
  }
}

async function cancelRecording(tabId) {
  try {
    clearSilenceAlarm(tabId);
    
    const result = await chrome.storage.local.get(['recordingTimers']);
    const timers = result.recordingTimers || {};
    if (timers[tabId]) {
      delete timers[tabId];
      await chrome.storage.local.set({ recordingTimers: timers });
    }

    await chrome.runtime.sendMessage({
      target: 'offscreen',
      type: 'CANCEL_RECORDING',
      tabId: tabId
    });
    
    updateState(tabId, 'idle');
  } catch (err) {
    console.warn("Error enviando cancelación al offscreen", err);
    updateState(tabId, 'idle');
  }
}

async function clearSilenceAlarm(tabId) {
  chrome.alarms.clear(`silence_${tabId}`);
  const result = await chrome.storage.local.get(['silenceAlarms']);
  const alarms = result.silenceAlarms || {};
  if (alarms[tabId]) {
    delete alarms[tabId];
    await chrome.storage.local.set({ silenceAlarms: alarms });
  }
}

async function setupOffscreenDocument(path) {
  const existingContexts = await chrome.runtime.getContexts({
    contextTypes: ['OFFSCREEN_DOCUMENT'],
    documentUrls: [chrome.runtime.getURL(path)]
  });

  if (existingContexts.length > 0) {
    return;
  }

  if (creating) {
    await creating;
  } else {
    creating = chrome.offscreen.createDocument({
      url: path,
      reasons: ['USER_MEDIA'],
      justification: 'Recording tab audio'
    });
    await creating;
    creating = null;
  }
}
let creating; // A global promise to avoid race conditions
