chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.set({ recordingStates: {}, silenceTimeoutMin: 5 });
});

chrome.runtime.onStartup.addListener(() => {
  chrome.storage.local.set({ recordingStates: {} });
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.target !== 'background') return;

  if (message.type === 'START_RECORDING') {
    startRecording(message.tabId);
  } else if (message.type === 'STOP_RECORDING') {
    stopRecording(message.tabId);
  } else if (message.type === 'UPDATE_STATE') {
    updateState(message.tabId, message.state);
  }
});

// Detectar silencio en las pestañas
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.audible !== undefined) {
    chrome.storage.local.get(['recordingStates', 'silenceTimeoutMin'], (result) => {
      const states = result.recordingStates || {};
      const timeoutMin = result.silenceTimeoutMin || 5;
      const alarmName = `silence_${tabId}`;

      // Si la pestaña se está grabando
      if (states[tabId] === 'recording') {
        if (changeInfo.audible === false) {
          // Dejó de sonar: iniciar temporizador
          chrome.alarms.create(alarmName, { delayInMinutes: timeoutMin });
          console.log(`Pestaña ${tabId} en silencio. Alarma configurada para ${timeoutMin} min.`);
        } else if (changeInfo.audible === true) {
          // Volvió a sonar: cancelar temporizador
          chrome.alarms.clear(alarmName);
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
}

async function startRecording(tabId) {
  const result = await chrome.storage.local.get(['recordingStates']);
  const states = result.recordingStates || {};
  if (states[tabId] === 'recording') return;

  // Get stream ID for the tab
  chrome.tabCapture.getMediaStreamId({ targetTabId: tabId }, async (streamId) => {
    if (chrome.runtime.lastError) {
      console.error(chrome.runtime.lastError.message);
      return;
    }
    
    // Create offscreen document if it doesn't exist
    await setupOffscreenDocument('offscreen.html');

    // Send the streamId to the offscreen document to start recording
    chrome.runtime.sendMessage({
      target: 'offscreen',
      type: 'START_RECORDING',
      streamId: streamId,
      tabId: tabId
    });

    updateState(tabId, 'recording');
    chrome.alarms.clear(`silence_${tabId}`); // Por seguridad
  });
}

async function stopRecording(tabId) {
  try {
    chrome.alarms.clear(`silence_${tabId}`); // Cancelar temporizador de silencio si existe
    await chrome.runtime.sendMessage({
      target: 'offscreen',
      type: 'STOP_RECORDING',
      tabId: tabId
    });
  } catch (err) {
    console.warn("No se pudo contactar al offscreen (probablemente la extensión se recargó). Reiniciando estado.");
    updateState(tabId, 'idle');
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
