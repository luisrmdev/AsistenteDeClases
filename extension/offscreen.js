const mediaRecorders = {};
const recordedChunks = {};
const audioContexts = {};
const streams = {};
const tempNames = {};
const gainNodes = {};
const cancelFlags = {};
let isGhostMode = false;

chrome.runtime.onMessage.addListener(async (message) => {
  if (message.target !== 'offscreen') return;

  if (message.type === 'START_RECORDING') {
    isGhostMode = message.ghostMode || false;
    startRecording(message.streamId, message.tabId);
  } else if (message.type === 'STOP_RECORDING') {
    stopRecording(message.tabId, message.customName);
  } else if (message.type === 'PAUSE_RECORDING') {
    pauseRecording(message.tabId);
  } else if (message.type === 'RESUME_RECORDING') {
    resumeRecording(message.tabId);
  } else if (message.type === 'CANCEL_RECORDING') {
    cancelRecording(message.tabId);
  } else if (message.type === 'SET_GHOST_MODE') {
    isGhostMode = message.ghostMode;
    // Aplicar a todos los gain nodes activos
    for (const tabId in gainNodes) {
      if (gainNodes[tabId]) {
        gainNodes[tabId].gain.value = isGhostMode ? 0 : 1;
      }
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

async function startRecording(streamId, tabId) {
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
    gainNode.gain.value = isGhostMode ? 0 : 1;
    
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
      updateState(tabId, 'error');
    }
  } catch (error) {
    console.error('Error uploading audio:', error);
    updateState(tabId, 'error');
  }
}
