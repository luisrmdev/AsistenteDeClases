const mediaRecorders = {};
const recordedChunks = {};
const audioContexts = {};
const streams = {};
const tempNames = {};
const gainNodes = {};
const cancelFlags = {};

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

    mediaRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) {
        recordedChunks[tabId].push(event.data);
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

    mediaRecorder.start();
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
    const reader = new FileReader();
    reader.readAsDataURL(audioBlob);
    reader.onloadend = async () => {
      const base64data = reader.result;
      const chunkSize = 5 * 1024 * 1024; // 5MB per chunk to be safe with IPC limits
      const totalChunks = Math.ceil(base64data.length / chunkSize);
      const fileId = "backup_" + tabId + "_" + Date.now();
      
      for (let i = 0; i < totalChunks; i++) {
        const chunk = base64data.slice(i * chunkSize, (i + 1) * chunkSize);
        await chrome.runtime.sendMessage({
          target: 'background',
          type: 'DOWNLOAD_FALLBACK_CHUNK',
          fileId: fileId,
          chunk: chunk,
          index: i,
          total: totalChunks,
          customName: customName,
          tabId: tabId
        });
      }
    };
  } catch (e) {
    console.error('Fallback serialization failed', e);
    updateState(tabId, 'error');
  }
}
