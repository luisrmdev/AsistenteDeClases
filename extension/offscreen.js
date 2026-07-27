const mediaRecorders = {};
const recordedChunks = {};
const audioContexts = {};
const streams = {};
const tempNames = {};
const gainNodes = {};
const cancelFlags = {};

// --- IndexedDB Helper ---
const DB_NAME = 'AudioRescueDB';
const STORE_NAME = 'chunks';
const DB_VERSION = 1;

function openDB() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = (e) => {
      e.target.result.createObjectStore(STORE_NAME, { keyPath: 'id', autoIncrement: true });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function addChunkToDB(tabId, chunk, customName) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite');
    tx.objectStore(STORE_NAME).add({ tabId, chunk, customName, timestamp: Date.now() });
    tx.oncomplete = resolve;
    tx.onerror = reject;
  });
}

async function getChunksFromDB(tabId) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readonly');
    const request = tx.objectStore(STORE_NAME).getAll();
    request.onsuccess = () => {
      const all = request.result.filter(r => r.tabId === tabId).sort((a,b) => a.timestamp - b.timestamp);
      resolve(all);
    };
    request.onerror = reject;
  });
}

async function deleteChunksFromDB(tabId) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite');
    const store = tx.objectStore(STORE_NAME);
    const request = store.getAll();
    request.onsuccess = () => {
      request.result.forEach(r => {
        if (r.tabId === tabId) {
          store.delete(r.id);
        }
      });
      resolve();
    };
    request.onerror = reject;
  });
}

chrome.runtime.onMessage.addListener(async (message) => {
  if (message.target !== 'offscreen') return;

  if (message.type === 'START_RECORDING') {
    startRecording(message.streamId, message.tabId, message.ghostMode || false);
  } else if (message.type === 'STOP_RECORDING') {
    stopRecording(message.tabId, message.customName);
  } else if (message.type === 'PAUSE_RECORDING') {
    pauseRecording(message.tabId);
  } else if (message.type === 'RESUME_RECORDING') {
    resumeRecording(message.tabId);
  } else if (message.type === 'CANCEL_RECORDING') {
    cancelRecording(message.tabId);
  } else if (message.type === 'SET_GHOST_MODE') {
    const isGhost = message.ghostMode;
    const targetTabId = message.tabId;
    if (targetTabId && gainNodes[targetTabId]) {
      gainNodes[targetTabId].gain.value = isGhost ? 0 : 1;
    }
  }
});

function pauseRecording(tabId) {
  const mediaRecorder = mediaRecorders[tabId];
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.pause();
    updateState(tabId, 'paused');
  }
}

function resumeRecording(tabId) {
  const mediaRecorder = mediaRecorders[tabId];
  if (mediaRecorder && mediaRecorder.state === 'paused') {
    mediaRecorder.resume();
    updateState(tabId, 'recording');
  }
}

function updateState(tabId, state) {
  chrome.runtime.sendMessage({
    target: 'background',
    type: 'UPDATE_STATE',
    tabId: tabId,
    state: state
  });
}

async function startRecording(streamId, tabId, isGhost) {
  try {
    // Clear any previous orphaned chunks for this tab just in case
    await deleteChunksFromDB(tabId);

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        mandatory: {
          chromeMediaSource: 'tab',
          chromeMediaSourceId: streamId
        }
      }
    });

    const audioContext = new AudioContext();
    const source = audioContext.createMediaStreamSource(stream);
    
    // Crear nodo de ganancia para controlar el volumen hacia los altavoces
    const gainNode = audioContext.createGain();
    gainNode.gain.value = isGhost ? 0 : 1;
    
    source.connect(gainNode);
    gainNode.connect(audioContext.destination);

    streams[tabId] = stream;
    audioContexts[tabId] = audioContext;
    gainNodes[tabId] = gainNode;
    recordedChunks[tabId] = [];
    
    const mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
    mediaRecorders[tabId] = mediaRecorder;

    mediaRecorder.ondataavailable = async (event) => {
      if (event.data.size > 0) {
        recordedChunks[tabId].push(event.data);
        await addChunkToDB(tabId, event.data, tempNames[tabId] || '');
      }
    };

    mediaRecorder.onstop = async () => {
      if (cancelFlags[tabId]) {
        // Just clean up without uploading
        if (streams[tabId]) streams[tabId].getTracks().forEach(track => track.stop());
        if (audioContexts[tabId]) audioContexts[tabId].close();
        
        delete streams[tabId];
        delete audioContexts[tabId];
        delete gainNodes[tabId];
        delete recordedChunks[tabId];
        delete mediaRecorders[tabId];
        delete tempNames[tabId];
        delete cancelFlags[tabId];
        await deleteChunksFromDB(tabId);
        return;
      }

      updateState(tabId, 'uploading');
      const blob = new Blob(recordedChunks[tabId], { type: 'audio/webm' });
      
      // Stop all tracks to release resources
      streams[tabId].getTracks().forEach(track => track.stop());
      audioContexts[tabId].close();
      
      // Clean up dictionary
      delete streams[tabId];
      delete audioContexts[tabId];
      delete gainNodes[tabId];
      delete recordedChunks[tabId];
      delete mediaRecorders[tabId];

      const cName = tempNames[tabId];
      delete tempNames[tabId];
      await uploadAudio(blob, tabId, cName);
    };

    mediaRecorder.start(5000); // FASE 1: Chunking cada 5 segundos
  } catch (err) {
    console.error('Error starting recording:', err);
    updateState(tabId, 'error');
  }
}

function stopRecording(tabId, customName) {
  if (customName) {
    tempNames[tabId] = customName;
  }
  const mediaRecorder = mediaRecorders[tabId];
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
  }
}

function cancelRecording(tabId) {
  cancelFlags[tabId] = true;
  const mediaRecorder = mediaRecorders[tabId];
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
  }
}

async function uploadAudio(audioBlob, tabId, customName) {
  updateState(tabId, 'uploading');
  
  try {
    const formData = new FormData();
    formData.append('audio', audioBlob, 'grabacion.webm');

    if (customName && customName.trim() !== '') {
      formData.append('custom_name', customName.trim());
    }

    const response = await fetch('http://localhost:8000/upload', {
      method: 'POST',
      body: formData
    });

    if (response.ok) {
      console.log('Audio uploaded successfully');
      updateState(tabId, 'completed');
      await deleteChunksFromDB(tabId); // Limpiar DB en éxito
    } else {
      console.error('Failed to upload audio', response.statusText);
      await saveAudioLocallyFallback(audioBlob, customName, tabId);
    }
  } catch (error) {
    console.error('Error uploading audio:', error);
    await saveAudioLocallyFallback(audioBlob, customName, tabId);
  }
}

async function saveAudioLocallyFallback(audioBlob, customName, tabId) {
  try {
    const url = URL.createObjectURL(audioBlob);
    chrome.runtime.sendMessage({
      target: 'background',
      type: 'DOWNLOAD_FALLBACK_URL',
      url: url,
      customName: customName,
      tabId: tabId
    });
    // Se deja en DB para que el usuario esté seguro, o podemos borrarlo?
    // Popup recovery se encargará de borrarlo o el usuario. En fallback no borramos, dejamos en DB o que lo borre la descarga en background.
    // Wait, let background trigger delete when downloaded, or keep it simple.
  } catch (e) {
    console.error('Fallback URL creation failed', e);
    updateState(tabId, 'error');
  }
}

