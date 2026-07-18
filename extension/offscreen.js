const mediaRecorders = {};
const recordedChunks = {};
const audioContexts = {};
const streams = {};

chrome.runtime.onMessage.addListener(async (message) => {
  if (message.target !== 'offscreen') return;

  if (message.type === 'START_RECORDING') {
    startRecording(message.streamId, message.tabId);
  } else if (message.type === 'STOP_RECORDING') {
    stopRecording(message.tabId);
  }
});

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
    source.connect(audioContext.destination);

    streams[tabId] = stream;
    audioContexts[tabId] = audioContext;
    recordedChunks[tabId] = [];
    
    const mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
    mediaRecorders[tabId] = mediaRecorder;

    mediaRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) {
        recordedChunks[tabId].push(event.data);
      }
    };

    mediaRecorder.onstop = async () => {
      updateState(tabId, 'uploading');
      const blob = new Blob(recordedChunks[tabId], { type: 'audio/webm' });
      
      // Stop all tracks to release resources
      streams[tabId].getTracks().forEach(track => track.stop());
      audioContexts[tabId].close();
      
      // Clean up dictionary
      delete streams[tabId];
      delete audioContexts[tabId];
      delete recordedChunks[tabId];
      delete mediaRecorders[tabId];

      await uploadAudio(blob, tabId);
    };

    mediaRecorder.start();
  } catch (err) {
    console.error('Error starting recording:', err);
    updateState(tabId, 'error');
  }
}

function stopRecording(tabId) {
  const mediaRecorder = mediaRecorders[tabId];
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
  }
}

async function uploadAudio(blob, tabId) {
  try {
    const formData = new FormData();
    formData.append('audio', blob, 'recording.webm');

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
