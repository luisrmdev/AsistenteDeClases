const mediaRecorders = {};
const recordedChunks = {};
const audioContexts = {};
const streams = {};
const tempNames = {};
const gainNodes = {};
const cancelFlags = {};
// Track recording start time per tab (for screenshot timestamps)
const recordingStartTimes = {};
const videoElements = {};

// --- IndexedDB Helper ---
// Schema v2: 'chunks' store (audio) + 'screenshots' store (images)
const DB_NAME = 'AudioRescueDB';
const CHUNKS_STORE = 'chunks';
const SCREENSHOTS_STORE = 'screenshots';
const DB_VERSION = 2;

function openDB() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);

    request.onupgradeneeded = (e) => {
      const db = e.target.result;
      // Create chunks store if missing (v1 → v2 migration)
      if (!db.objectStoreNames.contains(CHUNKS_STORE)) {
        db.createObjectStore(CHUNKS_STORE, { keyPath: 'id', autoIncrement: true });
      }
      // New in v2: screenshots store
      if (!db.objectStoreNames.contains(SCREENSHOTS_STORE)) {
        db.createObjectStore(SCREENSHOTS_STORE, { keyPath: 'id', autoIncrement: true });
      }
    };

    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

// --- Audio chunks ---
async function addChunkToDB(tabId, chunk, customName) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(CHUNKS_STORE, 'readwrite');
    tx.objectStore(CHUNKS_STORE).add({ tabId, chunk, customName, timestamp: Date.now() });
    tx.oncomplete = resolve;
    tx.onerror = reject;
  });
}

async function getChunksFromDB(tabId) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(CHUNKS_STORE, 'readonly');
    const request = tx.objectStore(CHUNKS_STORE).getAll();
    request.onsuccess = () => {
      const all = request.result
        .filter(r => r.tabId === tabId)
        .sort((a, b) => a.timestamp - b.timestamp);
      resolve(all);
    };
    request.onerror = reject;
  });
}

async function deleteChunksFromDB(tabId) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(CHUNKS_STORE, 'readwrite');
    const store = tx.objectStore(CHUNKS_STORE);
    const request = store.getAll();
    request.onsuccess = () => {
      request.result.forEach(r => {
        if (r.tabId === tabId) store.delete(r.id);
      });
      resolve();
    };
    request.onerror = reject;
  });
}

// --- Screenshots ---
async function addScreenshotToDB(tabId, imageBlob, tiempoSegundos) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(SCREENSHOTS_STORE, 'readwrite');
    tx.objectStore(SCREENSHOTS_STORE).add({
      tabId,
      image_blob: imageBlob,
      tiempo_segundos: tiempoSegundos,
      timestamp: Date.now()
    });
    tx.oncomplete = resolve;
    tx.onerror = reject;
  });
}

async function getScreenshotsFromDB(tabId) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(SCREENSHOTS_STORE, 'readonly');
    const request = tx.objectStore(SCREENSHOTS_STORE).getAll();
    request.onsuccess = () => {
      const all = request.result
        .filter(r => r.tabId === tabId)
        .sort((a, b) => a.tiempo_segundos - b.tiempo_segundos);
      resolve(all);
    };
    request.onerror = reject;
  });
}

async function deleteScreenshotsFromDB(tabId) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(SCREENSHOTS_STORE, 'readwrite');
    const store = tx.objectStore(SCREENSHOTS_STORE);
    const request = store.getAll();
    request.onsuccess = () => {
      request.result.forEach(r => {
        if (r.tabId === tabId) store.delete(r.id);
      });
      resolve();
    };
    request.onerror = reject;
  });
}

// --- Message Router ---
chrome.runtime.onMessage.addListener(async (message) => {
  if (message.target !== 'offscreen') return;

  if (message.type === 'START_RECORDING') {
    startRecording(message.streamId, message.tabId, message.ghostMode || false, message.intervalMin || 5);
  } else if (message.type === 'STOP_RECORDING') {
    stopRecording(message.tabId, message.customName);
  } else if (message.type === 'PAUSE_RECORDING') {
    pauseRecording(message.tabId);
  } else if (message.type === 'RESUME_RECORDING') {
    resumeRecording(message.tabId, message.intervalMin || 5);
  } else if (message.type === 'CANCEL_RECORDING') {
    cancelRecording(message.tabId);
  } else if (message.type === 'SET_GHOST_MODE') {
    const isGhost = message.ghostMode;
    const targetTabId = message.tabId;
    if (targetTabId && gainNodes[targetTabId]) {
      gainNodes[targetTabId].gain.value = isGhost ? 0 : 1;
    }
  } else if (message.type === 'MANUAL_SCREENSHOT') {
    // Triggered by the manual button in popup.js during recording
    await captureAndStoreScreenshot(message.tabId, true);
  } else if (message.type === 'AUTO_SCREENSHOT') {
    // Triggered by background.js alarms API to prevent throttling
    await captureAndStoreScreenshot(message.tabId, false);
  }
});

function pauseRecording(tabId) {
  const mediaRecorder = mediaRecorders[tabId];
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.pause();
    updateState(tabId, 'paused');
  }
}

function resumeRecording(tabId, intervalMin) {
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

// --- Screenshot Capture ---
/**
 * Ask the background script to capture the visible tab and store the blob.
 * The offscreen document cannot call chrome.tabs.captureVisibleTab directly,
 * so we send a message to background and receive the dataUrl back.
 */
async function captureAndStoreScreenshot(tabId, isManual = false) {
  try {
    const startTime = recordingStartTimes[tabId] || Date.now();
    const tiempoSegundos = Math.floor((Date.now() - startTime) / 1000);

    const video = videoElements[tabId];
    if (!video) {
      console.warn('[Screenshot] No video element found for tab', tabId);
      return;
    }

    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth || 1920;
    canvas.height = video.videoHeight || 1080;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

    canvas.toBlob(async (imageBlob) => {
      if (imageBlob) {
        await addScreenshotToDB(tabId, imageBlob, tiempoSegundos);
        console.log(`[Screenshot] t=${tiempoSegundos}s stored for tab ${tabId} (manual=${isManual})`);
      }
    }, 'image/jpeg', 0.75);
  } catch (e) {
    console.warn('[Screenshot] Capture failed:', e);
  }
}

// Removed startScreenshotInterval as it was migrated to chrome.alarms in background.js (and now back to setInterval in offscreen.js)

// --- Recording Core ---
async function startRecording(streamId, tabId, isGhost, intervalMin) {
  try {
    // Clear any previous orphaned data for this tab
    await deleteChunksFromDB(tabId);
    await deleteScreenshotsFromDB(tabId);

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        mandatory: {
          chromeMediaSource: 'tab',
          chromeMediaSourceId: streamId
        }
      },
      video: {
        mandatory: {
          chromeMediaSource: 'tab',
          chromeMediaSourceId: streamId
        }
      }
    });

    // Create a hidden video element to keep the visual stream alive for canvas capturing
    const videoEl = document.createElement('video');
    videoEl.srcObject = new MediaStream(stream.getVideoTracks());
    videoEl.muted = true;
    await videoEl.play();
    videoElements[tabId] = videoEl;

    const audioStream = new MediaStream(stream.getAudioTracks());
    const audioContext = new AudioContext();
    const source = audioContext.createMediaStreamSource(audioStream);

    const gainNode = audioContext.createGain();
    gainNode.gain.value = isGhost ? 0 : 1;

    source.connect(gainNode);
    gainNode.connect(audioContext.destination);

    streams[tabId] = stream; // Keep original stream to stop all tracks later
    audioContexts[tabId] = audioContext;
    gainNodes[tabId] = gainNode;
    recordedChunks[tabId] = [];
    recordingStartTimes[tabId] = Date.now();

    const mediaRecorder = new MediaRecorder(audioStream, { mimeType: 'audio/webm' });
    mediaRecorders[tabId] = mediaRecorder;

    mediaRecorder.ondataavailable = async (event) => {
      if (event.data.size > 0) {
        recordedChunks[tabId].push(event.data);
        await addChunkToDB(tabId, event.data, tempNames[tabId] || '');
      }
    };

    mediaRecorder.onstop = async () => {
      if (cancelFlags[tabId]) {
        // Clean up without uploading
        if (streams[tabId]) streams[tabId].getTracks().forEach(track => track.stop());
        if (audioContexts[tabId]) audioContexts[tabId].close();

        delete streams[tabId];
        delete audioContexts[tabId];
        delete gainNodes[tabId];
        delete recordedChunks[tabId];
        delete mediaRecorders[tabId];
        delete recordingStartTimes[tabId];
        if (videoElements[tabId]) {
          videoElements[tabId].pause();
          videoElements[tabId].srcObject = null;
          delete videoElements[tabId];
        }

        await deleteChunksFromDB(tabId);
        await deleteScreenshotsFromDB(tabId);
        return;
      }

      updateState(tabId, 'uploading');
      const audioBlob = new Blob(recordedChunks[tabId], { type: 'audio/webm' });

      // Retrieve screenshots before cleanup
      const screenshots = await getScreenshotsFromDB(tabId);

      // Stop all tracks and release resources
      streams[tabId].getTracks().forEach(track => track.stop());
      audioContexts[tabId].close();

      delete streams[tabId];
      delete audioContexts[tabId];
      delete gainNodes[tabId];
      delete recordingStartTimes[tabId];
      if (videoElements[tabId]) {
        videoElements[tabId].pause();
        videoElements[tabId].srcObject = null;
        delete videoElements[tabId];
      }

      const cName = tempNames[tabId];
      delete tempNames[tabId];

      await uploadAudio(audioBlob, screenshots, tabId, cName);
    };

    mediaRecorder.start(5000); // Chunking every 5 seconds

    // Take an initial screenshot immediately at second 0
    await captureAndStoreScreenshot(tabId);

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
  if (screenshotIntervals[tabId]) {
    clearInterval(screenshotIntervals[tabId]);
    delete screenshotIntervals[tabId];
  }
  cancelFlags[tabId] = true;
  const mediaRecorder = mediaRecorders[tabId];
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
  }
}

// --- Upload: Audio + Screenshots ---
async function uploadAudio(audioBlob, screenshots, tabId, customName) {
  updateState(tabId, 'uploading');

  try {
    const formData = new FormData();
    formData.append('audio', audioBlob, 'grabacion.webm');

    if (customName && customName.trim() !== '') {
      formData.append('custom_name', customName.trim());
    }

    // Append each screenshot image as a separate file
    screenshots.forEach((shot, index) => {
      const paddedIndex = String(index).padStart(3, '0');
      const filename = `captura_${paddedIndex}_t${shot.tiempo_segundos}s.jpg`;
      formData.append('imagenes', shot.image_blob, filename);
    });

    const response = await fetch('http://localhost:8000/upload', {
      method: 'POST',
      body: formData
    });

    if (response.ok) {
      console.log('Upload successful (audio + screenshots)');
      await deleteChunksFromDB(tabId);
      await deleteScreenshotsFromDB(tabId);
      updateState(tabId, 'completed');
    } else {
      console.error('Upload failed:', response.statusText);
      await saveLocallyFallback(audioBlob, screenshots, customName, tabId);
    }
  } catch (error) {
    if (error.name === 'TypeError' && error.message === 'Failed to fetch') {
      console.warn('Network error or Extension Reload detected. Upload aborted. Triggering local backup fallback...', error.message);
    } else {
      console.error('Error uploading:', error);
    }
    await saveLocallyFallback(audioBlob, screenshots, customName, tabId);
  }
}

// --- Fallback: Save everything locally ---
async function saveLocallyFallback(audioBlob, screenshots, customName, tabId) {
  try {
    const rootSubfolder = 'Backups_Clases/';
    
    // We ignore askAlways for backups to prevent spamming the user with 20 popups
    // All files will go directly to the specific folder.
    
    const dateStr = new Date().toISOString().replace(/[:.]/g, '-');
    const safeCustomName = customName ? customName.trim().replace(/[^a-zA-Z0-9_-]/g, '_') : 'sesion';
    
    // Create a unique folder for this specific session backup
    const sessionFolder = `${rootSubfolder}${safeCustomName}_${dateStr}/`;

    // 1. Download Audio
    const audioUrl = URL.createObjectURL(audioBlob);
    const audioFilename = `${sessionFolder}grabacion.webm`;
    
    chrome.downloads.download({ url: audioUrl, filename: audioFilename, saveAs: false }, () => {
       if (chrome.runtime.lastError) console.error("Audio download failed:", chrome.runtime.lastError.message);
    });

    // 2. Download Screenshots
    if (screenshots && screenshots.length > 0) {
      screenshots.forEach((shot, index) => {
        const paddedIndex = String(index).padStart(3, '0');
        const imgFilename = `${sessionFolder}captura_${paddedIndex}_t${shot.tiempo_segundos}s.jpg`;
        const imgUrl = URL.createObjectURL(shot.image_blob);
        
        chrome.downloads.download({ url: imgUrl, filename: imgFilename, saveAs: false }, () => {
           if (chrome.runtime.lastError) console.error(`Screenshot ${index} download failed:`, chrome.runtime.lastError.message);
        });
      });
    }
    
    updateState(tabId, 'fallback_saved');
  } catch (e) {
    console.error('Fallback save failed:', e);
    updateState(tabId, 'error');
  }
}
