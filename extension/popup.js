document.addEventListener('DOMContentLoaded', async () => {
  // Initialize Lucide icons
  lucide.createIcons();

  const recordBtn = document.getElementById('recordBtn');
  const stopBtn = document.getElementById('stopBtn');
  const pauseBtn = document.getElementById('pauseBtn');
  const resumeBtn = document.getElementById('resumeBtn');
  const cancelBtn = document.getElementById('cancelBtn');
  const controlsGroup = document.getElementById('controlsGroup');
  const statusIndicator = document.getElementById('statusIndicator');
  const audioWarning = document.getElementById('audioWarning');
  const silenceTimeoutInput = document.getElementById('silenceTimeout');
  const countdownDisplay = document.getElementById('countdownDisplay');
  const countdownTimer = document.getElementById('countdownTimer');
  const durationTimer = document.getElementById('durationTimer');

  // Manual screenshot button (visible only while recording)
  const manualScreenshotBtn = document.getElementById('manualScreenshotBtn');
  const screenshotFeedback = document.getElementById('screenshotFeedback');

  const tabAudio = document.getElementById('tab-audio');
  
  const settingsBtn = document.getElementById('settingsBtn');
  const tabSettings = document.getElementById('tab-settings');
  const closeSettingsBtn = document.getElementById('closeSettingsBtn');
  const screenshotIntervalMin = document.getElementById('screenshotIntervalMin');
  const backupAskAlways = document.getElementById('backupAskAlways');

  let countdownInterval = null;

  // --- FASE 1: Recuperacion de Desastres (v2 — limpia chunks + screenshots) ---
  async function runDisasterRecovery() {
    try {
      const db = await new Promise((resolve, reject) => {
        const req = indexedDB.open('AudioRescueDB', 2);
        req.onupgradeneeded = (e) => {
          const dbInstance = e.target.result;
          if (!dbInstance.objectStoreNames.contains('chunks')) {
            dbInstance.createObjectStore('chunks', { keyPath: 'id', autoIncrement: true });
          }
          if (!dbInstance.objectStoreNames.contains('screenshots')) {
            dbInstance.createObjectStore('screenshots', { keyPath: 'id', autoIncrement: true });
          }
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      });

      if (!db.objectStoreNames.contains('chunks')) return;

      const tx = db.transaction('chunks', 'readonly');
      const store = tx.objectStore('chunks');
      const req = store.getAll();

      req.onsuccess = async () => {
        const allChunks = req.result;
        if (!allChunks || allChunks.length === 0) return;

        // Group by tabId
        const byTab = {};
        allChunks.forEach(c => {
          if (!byTab[c.tabId]) byTab[c.tabId] = [];
          byTab[c.tabId].push(c);
        });

        const states = await new Promise(r => chrome.storage.local.get(['recordingStates'], res => r(res.recordingStates || {})));

        for (const tabIdStr in byTab) {
          const tabId = parseInt(tabIdStr);
          const state = states[tabId];
          if (state !== 'recording' && state !== 'paused' && state !== 'uploading') {
            console.log(`Orphaned recording found for tab ${tabId}. Recovering...`);
            const chunks = byTab[tabId].sort((a, b) => a.timestamp - b.timestamp).map(c => c.chunk);
            const blob = new Blob(chunks, { type: 'audio/webm' });
            const url = URL.createObjectURL(blob);

            const rootSubfolder = 'Backups_Clases/';
            
            const dateStr = new Date().toISOString().replace(/[:.]/g, '-');
            const safeCustomName = 'sesion';
            const sessionFolder = `${rootSubfolder}${safeCustomName}_${dateStr}/`;

            // Download audio
            const filename = `${sessionFolder}grabacion.webm`;
            chrome.downloads.download({ url: url, filename: filename, saveAs: false }, (downloadId) => {
              if (chrome.runtime.lastError) {
                 console.error("Audio download failed in popup:", chrome.runtime.lastError.message);
              }
              chrome.runtime.sendMessage({ target: 'background', type: 'UPDATE_STATE', state: 'fallback_saved', tabId: tabId });
            });

            // Recover and download screenshots, then clean them up
            if (db.objectStoreNames.contains('screenshots')) {
              const sTx = db.transaction('screenshots', 'readonly');
              const sStore = sTx.objectStore('screenshots');
              const sReq = sStore.getAll();
              sReq.onsuccess = () => {
                const tabScreenshots = sReq.result.filter(s => s.tabId === tabId);
                
                // Download each screenshot
                tabScreenshots.sort((a, b) => a.tiempo_segundos - b.tiempo_segundos).forEach((shot, index) => {
                  const paddedIndex = String(index).padStart(3, '0');
                  const imgFilename = `${sessionFolder}captura_${paddedIndex}_t${shot.tiempo_segundos}s.jpg`;
                  const imgUrl = URL.createObjectURL(shot.image_blob);
                  chrome.downloads.download({ url: imgUrl, filename: imgFilename, saveAs: false }, () => {
                     if (chrome.runtime.lastError) console.error("Screenshot download failed:", chrome.runtime.lastError.message);
                  });
                });

                // Clean downloaded screenshots from IndexedDB
                const delTx = db.transaction('screenshots', 'readwrite');
                const delStore = delTx.objectStore('screenshots');
                tabScreenshots.forEach(s => delStore.delete(s.id));
              };
            }

            // Clean audio chunks
            const dTx = db.transaction('chunks', 'readwrite');
            const dStore = dTx.objectStore('chunks');
            allChunks.filter(c => c.tabId === tabId).forEach(c => {
              dStore.delete(c.id);
            });
          }
        }
      };
    } catch(e) {
      console.warn("Error en Disaster Recovery:", e);
    }
  }

  runDisasterRecovery();

  // Load saved silence timeout
  chrome.storage.local.get(['silenceTimeoutMin'], (result) => {
    if (result.silenceTimeoutMin) {
      silenceTimeoutInput.value = result.silenceTimeoutMin;
    } else {
      chrome.storage.local.set({ silenceTimeoutMin: 5 });
    }
  });

  silenceTimeoutInput.addEventListener('change', () => {
    let val = parseInt(silenceTimeoutInput.value);
    if (isNaN(val) || val < 1) val = 1;
    silenceTimeoutInput.value = val;
    chrome.storage.local.set({ silenceTimeoutMin: val });
  });

  // Obtener el tabId actual
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;
  const currentTabId = tab.id;
  let isAudible = tab.audible;

  const ghostModeToggle = document.getElementById('ghostModeToggle');
  chrome.storage.local.get(['ghostModes'], (result) => {
    const modes = result.ghostModes || {};
    if (modes[currentTabId] !== undefined) {
      ghostModeToggle.checked = modes[currentTabId];
    } else {
      ghostModeToggle.checked = false;
    }
  });

  ghostModeToggle.addEventListener('change', () => {
    const isGhost = ghostModeToggle.checked;
    chrome.storage.local.get(['ghostModes'], (result) => {
      const modes = result.ghostModes || {};
      modes[currentTabId] = isGhost;
      chrome.storage.local.set({ ghostModes: modes });
    });
    chrome.runtime.sendMessage({ target: 'offscreen', type: 'SET_GHOST_MODE', ghostMode: isGhost, tabId: currentTabId });
  });

  const apiUrlInput = document.getElementById('apiUrlInput');
  const jwtTokenInput = document.getElementById('jwtTokenInput');

  // Load settings
  chrome.storage.local.get(['backupAskAlways', 'screenshotIntervalMin', 'apiUrl', 'jwtToken'], async (result) => {
    backupAskAlways.checked = result.backupAskAlways || false;
    screenshotIntervalMin.value = result.screenshotIntervalMin || 5;
    apiUrlInput.value = result.apiUrl || 'http://localhost:8000';
    jwtTokenInput.value = result.jwtToken || '';
  });

  screenshotIntervalMin.addEventListener('change', () => {
    let val = parseInt(screenshotIntervalMin.value);
    if (isNaN(val) || val < 1) val = 5;
    chrome.storage.local.set({ screenshotIntervalMin: val });
  });

  backupAskAlways.addEventListener('change', () => {
    chrome.storage.local.set({ backupAskAlways: backupAskAlways.checked });
  });

  apiUrlInput.addEventListener('change', () => {
    let val = apiUrlInput.value.trim();
    if (val.endsWith('/')) val = val.slice(0, -1);
    chrome.storage.local.set({ apiUrl: val });
  });

  jwtTokenInput.addEventListener('change', () => {
    chrome.storage.local.set({ jwtToken: jwtTokenInput.value.trim() });
  });



  // Check current state for this specific tab
  chrome.storage.local.get(['recordingStates'], (result) => {
    const states = result.recordingStates || {};
    updateUI(states[currentTabId] || 'idle', isAudible);
  });

  // Listen for state changes from background
  chrome.storage.onChanged.addListener((changes, namespace) => {
    if (namespace === 'local' && changes.recordingStates) {
      const states = changes.recordingStates.newValue || {};
      chrome.tabs.get(currentTabId, (updatedTab) => {
        isAudible = updatedTab ? updatedTab.audible : false;
        updateUI(states[currentTabId] || 'idle', isAudible);
      });
    }
  });

  // Listen for audio changes in real-time while popup is open
  chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    if (tabId === currentTabId && changeInfo.audible !== undefined) {
      isAudible = changeInfo.audible;
      chrome.storage.local.get(['recordingStates'], (result) => {
        const states = result.recordingStates || {};
        updateUI(states[currentTabId] || 'idle', isAudible);
      });
    }
  });

  let isStarting = false;
  recordBtn.addEventListener('click', () => {
    if (isStarting) return;
    isStarting = true;
    recordBtn.disabled = true;
    recordBtn.style.opacity = '0.5';
    chrome.runtime.sendMessage({ target: 'background', type: 'START_RECORDING', tabId: currentTabId });
  });

  stopBtn.addEventListener('click', () => {
    chrome.runtime.sendMessage({ target: 'background', type: 'STOP_RECORDING', tabId: currentTabId });
  });

  pauseBtn.addEventListener('click', () => {
    chrome.runtime.sendMessage({ target: 'background', type: 'PAUSE_RECORDING', tabId: currentTabId });
  });

  resumeBtn.addEventListener('click', () => {
    chrome.runtime.sendMessage({ target: 'background', type: 'RESUME_RECORDING', tabId: currentTabId });
  });

  cancelBtn.addEventListener('click', () => {
    if (confirm("¿Estás seguro de que deseas cancelar la grabación? Todo el audio se perderá de forma permanente.")) {
      chrome.runtime.sendMessage({ target: 'background', type: 'CANCEL_RECORDING', tabId: currentTabId });
    }
  });

  // --- Manual Screenshot Button ---
  if (manualScreenshotBtn) {
    manualScreenshotBtn.addEventListener('click', () => {
      chrome.runtime.sendMessage({
        target: 'background',
        type: 'MANUAL_SCREENSHOT_FROM_POPUP',
        tabId: currentTabId
      });
      if (screenshotFeedback) {
        screenshotFeedback.classList.remove('hidden');
        setTimeout(() => screenshotFeedback.classList.add('hidden'), 2000);
      }
    });
  }

  function updateCountdown() {
    chrome.storage.local.get(['silenceAlarms', 'recordingTimers'], (result) => {
      const alarms = result.silenceAlarms || {};
      const endTime = alarms[currentTabId];
      if (endTime && endTime > Date.now()) {
        const remainingMs = endTime - Date.now();
        const mins = Math.floor(remainingMs / 60000);
        const secs = Math.floor((remainingMs % 60000) / 1000);
        countdownTimer.textContent = `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
        countdownDisplay.classList.remove('hidden');
      } else {
        countdownDisplay.classList.add('hidden');
      }

      const timers = result.recordingTimers || {};
      const t = timers[currentTabId];
      if (t) {
        let elapsed = t.elapsed || 0;
        if (!t.paused) {
          elapsed += (Date.now() - t.startTime);
        }

        const totalSecs = Math.floor(elapsed / 1000);
        const h = Math.floor(totalSecs / 3600);
        const m = Math.floor((totalSecs % 3600) / 60);
        const s = totalSecs % 60;

        const pad = (num) => num.toString().padStart(2, '0');
        durationTimer.textContent = h > 0 ? `${pad(h)}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
        durationTimer.classList.remove('hidden');
      } else {
        durationTimer.classList.add('hidden');
      }
    });
  }

  if (countdownInterval) clearInterval(countdownInterval);
  countdownInterval = setInterval(updateCountdown, 1000);
  updateCountdown();

  function updateUI(state, audible) {
    audioWarning.classList.add('hidden');
    isStarting = false;
    recordBtn.disabled = false;
    recordBtn.style.opacity = '1';
    silenceTimeoutInput.disabled = false;

    // Hide manual screenshot button by default
    if (manualScreenshotBtn) manualScreenshotBtn.classList.add('hidden');

    if (state === 'recording') {
      silenceTimeoutInput.disabled = true;
      recordBtn.classList.add('hidden');
      controlsGroup.classList.remove('hidden');
      controlsGroup.classList.add('flex-row');
      pauseBtn.classList.remove('hidden');
      resumeBtn.classList.add('hidden');

      // Show manual screenshot button during active recording
      if (manualScreenshotBtn) manualScreenshotBtn.classList.remove('hidden');

      statusIndicator.textContent = 'Grabación Activa';
      statusIndicator.className = 'status-badge status-recording';
    } else if (state === 'paused') {
      silenceTimeoutInput.disabled = false;

      recordBtn.classList.add('hidden');
      controlsGroup.classList.remove('hidden');
      controlsGroup.classList.add('flex-row');
      pauseBtn.classList.add('hidden');
      resumeBtn.classList.remove('hidden');

      statusIndicator.textContent = 'En Espera';
      statusIndicator.className = 'status-badge status-idle';
    } else if (state === 'uploading') {
      silenceTimeoutInput.disabled = true;
      recordBtn.classList.add('hidden');
      controlsGroup.classList.remove('flex-row');
      controlsGroup.classList.add('hidden');
      statusIndicator.textContent = 'Procesando...';
      statusIndicator.className = 'status-badge status-idle';
    } else if (state === 'completed') {
      recordBtn.classList.remove('hidden');
      controlsGroup.classList.remove('flex-row');
      controlsGroup.classList.add('hidden');
      statusIndicator.textContent = 'Finalizado';
      statusIndicator.className = 'status-badge status-success';

      setTimeout(() => updateUI('idle', audible), 3000);
    } else if (state === 'fallback_saved') {
      recordBtn.classList.remove('hidden');
      controlsGroup.classList.remove('flex-row');
      controlsGroup.classList.add('hidden');
      statusIndicator.textContent = 'Audio guardado localmente como respaldo.';
      statusIndicator.className = 'status-badge status-warning';

      setTimeout(() => updateUI('idle', audible), 5000);
    } else {
      recordBtn.classList.remove('hidden');
      controlsGroup.classList.remove('flex-row');
      controlsGroup.classList.add('hidden');
      statusIndicator.textContent = 'Listo para Iniciar';
      statusIndicator.className = 'status-badge status-idle';

      if (!audible) {
        recordBtn.disabled = true;
        audioWarning.classList.remove('hidden');
      }
    }
  }

  // --- TABS LOGIC (Settings vs Audio) ---
  settingsBtn.addEventListener('click', () => {
    tabAudio.classList.add('hidden');
    tabSettings.classList.remove('hidden');
  });

  closeSettingsBtn.addEventListener('click', () => {
    tabSettings.classList.add('hidden');
    tabAudio.classList.remove('hidden');
  });
});
