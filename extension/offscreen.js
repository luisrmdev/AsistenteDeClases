const mediaRecorders = {};
const recordedChunks = {};
const audioContexts = {};
const streams = {};
const tempNames = {};
const gainNodes = {};
const cancelFlags = {};
// Store screenshot capture intervals per tab
const screenshotIntervals = {};
// Track recording start time per tab (for screenshot timestamps)
const recordingStartTimes = {};

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
  } else if (message.type === 'MANUAL_SCREENSHOT') {
    // Triggered by the manual button in popup.js during recording
    await captureAndStoreScreenshot(message.tabId, true);
  }
});

function pauseRecording(tabId) {
  // Pause screenshot interval
  if (screenshotIntervals[tabId]) {
    clearInterval(screenshotIntervals[tabId]);
    delete screenshotIntervals[tabId];
  }
  const mediaRecorder = mediaRecorders[tabId];
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.pause();
    updateState(tabId, 'paused');
  }
}

function resumeRecording(tabId) {
  // Resume screenshot interval
  startScreenshotInterval(tabId);
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

    // Request background to capture and send back dataUrl
    const response = await chrome.runtime.sendMessage({
      target: 'background',
      type: 'CAPTURE_SCREENSHOT',
      tabId: tabId
    });

    if (response && response.dataUrl) {
      // Convert dataUrl → Blob
      const res = await fetch(response.dataUrl);
      const imageBlob = await res.blob();
      await addScreenshotToDB(tabId, imageBlob, tiempoSegundos);
      console.log(`[Screenshot] t=${tiempoSegundos}s stored for tab ${tabId} (manual=${isManual})`);
    }
  } catch (e) {
    console.warn('[Screenshot] Capture failed:', e);
  }
}

function startScreenshotInterval(tabId) {
  // Clear any existing interval
  if (screenshotIntervals[tabId]) clearInterval(screenshotIntervals[tabId]);
  // Capture every 5 minutes (300000ms)
  screenshotIntervals[tabId] = setInterval(async () => {
    await captureAndStoreScreenshot(tabId);
  }, 5 * 60 * 1000);
}

// --- Recording Core ---
async function startRecording(streamId, tabId, isGhost) {
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
      }
    });

    const audioContext = new AudioContext();
    const source = audioContext.createMediaStreamSource(stream);

    const gainNode = audioContext.createGain();
    gainNode.gain.value = isGhost ? 0 : 1;

    source.connect(gainNode);
    gainNode.connect(audioContext.destination);

    streams[tabId] = stream;
    audioContexts[tabId] = audioContext;
    gainNodes[tabId] = gainNode;
    recordedChunks[tabId] = [];
    recordingStartTimes[tabId] = Date.now();

    const mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
    mediaRecorders[tabId] = mediaRecorder;

    mediaRecorder.ondataavailable = async (event) => {
      if (event.data.size > 0) {
        recordedChunks[tabId].push(event.data);
        await addChunkToDB(tabId, event.data, tempNames[tabId] || '');
      }
    };

    mediaRecorder.onstop = async () => {
      // Stop screenshot interval
      if (screenshotIntervals[tabId]) {
        clearInterval(screenshotIntervals[tabId]);
        delete screenshotIntervals[tabId];
      }

      if (cancelFlags[tabId]) {
        // Clean up without uploading
        if (streams[tabId]) streams[tabId].getTracks().forEach(track => track.stop());
        if (audioContexts[tabId]) audioContexts[tabId].close();

        delete streams[tabId];
        delete audioContexts[tabId];
        delete gainNodes[tabId];
        delete recordedChunks[tabId];
        delete mediaRecorders[tabId];
        delete tempNames[tabId];
        delete cancelFlags[tabId];
        delete recordingStartTimes[tabId];

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
      delete recordedChunks[tabId];
      delete mediaRecorders[tabId];
      delete recordingStartTimes[tabId];

      const cName = tempNames[tabId];
      delete tempNames[tabId];

      await uploadAudio(audioBlob, screenshots, tabId, cName);
    };

    mediaRecorder.start(5000); // Chunking every 5 seconds

    // Start time-lapse screenshot interval
    startScreenshotInterval(tabId);

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
  cancelFlags[tabId] = true;
  if (screenshotIntervals[tabId]) {
    clearInterval(screenshotIntervals[tabId]);
    delete screenshotIntervals[tabId];
  }
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
      updateState(tabId, 'completed');
      await deleteChunksFromDB(tabId);
      await deleteScreenshotsFromDB(tabId);
    } else {
      console.error('Upload failed:', response.statusText);
      await saveLocallyFallback(audioBlob, screenshots, customName, tabId);
    }
  } catch (error) {
    console.error('Error uploading:', error);
    await saveLocallyFallback(audioBlob, screenshots, customName, tabId);
  }
}

// --- Fallback: Save everything locally ---
async function saveLocallyFallback(audioBlob, screenshots, customName, tabId) {
  try {
    // Try to package as a .zip using JSZip (loaded via importScripts or dynamic import)
    // If JSZip is not available, fall back to separate downloads
    let zipSuccess = false;

    try {
      // Attempt dynamic JSZip import (requires jszip.min.js in extension)
      // We try-catch so missing JSZip doesn't break the entire fallback
      const JSZip = await loadJSZip();
      if (JSZip) {
        const zip = new JSZip();
        zip.file('grabacion.webm', audioBlob);

        screenshots.forEach((shot, index) => {
          const paddedIndex = String(index).padStart(3, '0');
          const filename = `captura_${paddedIndex}_t${shot.tiempo_segundos}s.jpg`;
          zip.file(filename, shot.image_blob);
        });

        const zipBlob = await zip.generateAsync({ type: 'blob' });
        const zipUrl = URL.createObjectURL(zipBlob);
        const dateStr = new Date().toISOString().replace(/[:.]/g, '-');
        const safeCustomName = customName ? customName.trim().replace(/[^a-zA-Z0-9_-]/g, '_') + '-' : '';

        chrome.runtime.sendMessage({
          target: 'background',
          type: 'DOWNLOAD_FALLBACK_URL',
          url: zipUrl,
          filename: `backup-${safeCustomName}${dateStr}.zip`,
          customName: customName,
          tabId: tabId
        });
        zipSuccess = true;
      }
    } catch (zipErr) {
      console.warn('[Fallback] JSZip not available, falling back to separate files:', zipErr);
    }

    if (!zipSuccess) {
      // Fallback B: Download audio .webm + JSON with screenshots in base64
      const audioUrl = URL.createObjectURL(audioBlob);
      chrome.runtime.sendMessage({
        target: 'background',
        type: 'DOWNLOAD_FALLBACK_URL',
        url: audioUrl,
        customName: customName,
        tabId: tabId
      });

      // If there are screenshots, build a base64 JSON
      if (screenshots.length > 0) {
        const screenshotData = await Promise.all(
          screenshots.map(async (shot, index) => {
            const reader = new FileReader();
            const base64 = await new Promise((resolve) => {
              reader.onload = () => resolve(reader.result);
              reader.readAsDataURL(shot.image_blob);
            });
            return {
              index,
              tiempo_segundos: shot.tiempo_segundos,
              filename: `captura_${String(index).padStart(3, '0')}_t${shot.tiempo_segundos}s.jpg`,
              base64
            };
          })
        );

        const jsonBlob = new Blob([JSON.stringify({ capturas: screenshotData }, null, 2)], { type: 'application/json' });
        const jsonUrl = URL.createObjectURL(jsonBlob);
        chrome.runtime.sendMessage({
          target: 'background',
          type: 'DOWNLOAD_FALLBACK_JSON',
          url: jsonUrl,
          customName: customName,
          tabId: tabId
        });
      }
    }
  } catch (e) {
    console.error('Fallback save failed:', e);
    updateState(tabId, 'error');
  }
}

// Attempt to load JSZip (bundled in extension dir as jszip.min.js)
async function loadJSZip() {
  try {
    // JSZip can be loaded via importScripts in service worker context
    // In offscreen document context, we use a dynamic <script> approach
    if (typeof JSZip !== 'undefined') return JSZip;
    return null;
  } catch {
    return null;
  }
}
