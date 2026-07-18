chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.set({ recordingStates: {} });
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
  });
}

async function stopRecording(tabId) {
  try {
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
  // Check all windows controlled by the service worker to see if one 
  // of them is the offscreen document with the given path
  const existingContexts = await chrome.runtime.getContexts({
    contextTypes: ['OFFSCREEN_DOCUMENT'],
    documentUrls: [chrome.runtime.getURL(path)]
  });

  if (existingContexts.length > 0) {
    return;
  }

  // create offscreen document
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
