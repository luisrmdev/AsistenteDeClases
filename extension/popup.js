document.addEventListener('DOMContentLoaded', async () => {
  const recordBtn = document.getElementById('recordBtn');
  const stopBtn = document.getElementById('stopBtn');
  const pauseBtn = document.getElementById('pauseBtn');
  const resumeBtn = document.getElementById('resumeBtn');
  const cancelBtn = document.getElementById('cancelBtn');
  const controlsGroup = document.getElementById('controlsGroup');
  const statusIndicator = document.getElementById('statusIndicator');
  const audioWarning = document.getElementById('audioWarning');
  const silenceTimeoutInput = document.getElementById('silenceTimeout');
  const customFilenameInput = document.getElementById('customFilename');
  const countdownDisplay = document.getElementById('countdownDisplay');
  const countdownTimer = document.getElementById('countdownTimer');
  const durationTimer = document.getElementById('durationTimer');

  // Manual screenshot button (visible only while recording)
  const manualScreenshotBtn = document.getElementById('manualScreenshotBtn');
  const screenshotFeedback = document.getElementById('screenshotFeedback');

  const captureTaskBtn = document.getElementById('captureTaskBtn');
  const captureStatus = document.getElementById('captureStatus');
  const previewContainer = document.getElementById('previewContainer');
  const capturePreviewImg = document.getElementById('capturePreviewImg');
  const sendCaptureBtn = document.getElementById('sendCaptureBtn');
  const cancelCaptureBtn = document.getElementById('cancelCaptureBtn');
  const captureTaskBtnContainer = document.getElementById('captureTaskBtnContainer');

  const tabBtnAudio = document.getElementById('tab-btn-audio');
  const tabBtnCapture = document.getElementById('tab-btn-capture');
  const tabAudio = document.getElementById('tab-audio');
  const tabCapture = document.getElementById('tab-capture');

  const settingsBtn = document.getElementById('settingsBtn');
  const tabSettings = document.getElementById('tab-settings');
  const closeSettingsBtn = document.getElementById('closeSettingsBtn');
  const backupSubfolder = document.getElementById('backupSubfolder');
  const screenshotIntervalMin = document.getElementById('screenshotIntervalMin');
  const backupAskAlways = document.getElementById('backupAskAlways');

  let currentCaptureDataUrl = null;
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
            const customName = byTab[tabId][0].customName || '';
            const blob = new Blob(chunks, { type: 'audio/webm' });
            const url = URL.createObjectURL(blob);

            const result = await chrome.storage.local.get(['backupSubfolder', 'backupAskAlways']);
            let subfolder = (result.backupSubfolder !== undefined) ? result.backupSubfolder : 'Backups_Clases/';
            subfolder = subfolder.replace(/^\/+/, ''); // Sanitize leading slash
            if (subfolder && !subfolder.endsWith('/')) subfolder += '/';
            const askAlways = result.backupAskAlways || false;
            const dateStr = new Date().toISOString().replace(/[:.]/g, '-');
            const safeCustomName = customName ? customName.trim().replace(/[^a-zA-Z0-9_-]/g, '_') + '-' : '';
            const filename = `${subfolder}backup-${safeCustomName}${dateStr}.webm`;

            chrome.downloads.download({ url: url, filename: filename, saveAs: askAlways }, (downloadId) => {
              if (chrome.runtime.lastError) {
                 console.error("Download failed in popup:", chrome.runtime.lastError.message);
              }
              chrome.runtime.sendMessage({ target: 'background', type: 'UPDATE_STATE', state: 'fallback_saved', tabId: tabId });
            });

            // Clean chunks
            const dTx = db.transaction('chunks', 'readwrite');
            const dStore = dTx.objectStore('chunks');
            allChunks.filter(c => c.tabId === tabId).forEach(c => {
              dStore.delete(c.id);
            });

            // Clean orphaned screenshots too
            if (db.objectStoreNames.contains('screenshots')) {
              const sTx = db.transaction('screenshots', 'readwrite');
              const sStore = sTx.objectStore('screenshots');
              const sReq = sStore.getAll();
              sReq.onsuccess = () => {
                sReq.result.filter(s => s.tabId === tabId).forEach(s => sStore.delete(s.id));
              };
            }
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
  const isAudible = tab.audible;

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

  // Load and save backup settings
  chrome.storage.local.get(['backupSubfolder', 'backupAskAlways', 'screenshotIntervalMin'], async (result) => {
    let subfolder = result.backupSubfolder !== undefined ? result.backupSubfolder : 'Backups_Clases/';
    
    // Sync con el backend si está disponible
    try {
      const res = await fetch('http://localhost:8000/api/settings');
      if (res.ok) {
        const data = await res.json();
        if (data.extension_backup_dir !== undefined) {
          subfolder = data.extension_backup_dir;
          chrome.storage.local.set({ backupSubfolder: subfolder });
        }
      }
    } catch (e) {
      console.log('Backend no disponible para sincronizar subcarpeta de backup');
    }

    backupSubfolder.value = subfolder;
    backupAskAlways.checked = result.backupAskAlways || false;
    screenshotIntervalMin.value = result.screenshotIntervalMin || 5;
  });

  screenshotIntervalMin.addEventListener('change', () => {
    let val = parseInt(screenshotIntervalMin.value);
    if (isNaN(val) || val < 1) val = 5;
    chrome.storage.local.set({ screenshotIntervalMin: val });
  });

  backupSubfolder.addEventListener('input', () => {
    chrome.storage.local.set({ backupSubfolder: backupSubfolder.value });
  });

  backupAskAlways.addEventListener('change', () => {
    chrome.storage.local.set({ backupAskAlways: backupAskAlways.checked });
  });

  // Load saved custom name
  chrome.storage.local.get(['customNames'], (result) => {
    const names = result.customNames || {};
    if (names[currentTabId]) {
      customFilenameInput.value = names[currentTabId];
    }
  });

  customFilenameInput.addEventListener('input', () => {
    chrome.storage.local.get(['customNames'], (result) => {
      const names = result.customNames || {};
      names[currentTabId] = customFilenameInput.value;
      chrome.storage.local.set({ customNames: names });
    });
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
        updateUI(states[currentTabId] || 'idle', updatedTab ? updatedTab.audible : false);
      });
    }
  });

  recordBtn.addEventListener('click', () => {
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
    if (confirm("Estas seguro de que deseas cancelar la grabacion? Todo el audio se perdera de forma permanente.")) {
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
    recordBtn.disabled = false;
    silenceTimeoutInput.disabled = false;
    silenceTimeoutInput.classList.remove('opacity-50', 'cursor-not-allowed');
    customFilenameInput.disabled = false;
    customFilenameInput.classList.remove('opacity-50', 'cursor-not-allowed');

    // Hide manual screenshot button by default
    if (manualScreenshotBtn) manualScreenshotBtn.classList.add('hidden');

    if (state === 'recording') {
      silenceTimeoutInput.disabled = true;
      silenceTimeoutInput.classList.add('opacity-50', 'cursor-not-allowed');
      customFilenameInput.disabled = true;
      customFilenameInput.classList.add('opacity-50', 'cursor-not-allowed');
      recordBtn.classList.add('hidden');
      controlsGroup.classList.remove('hidden');
      controlsGroup.classList.add('flex-row');
      pauseBtn.classList.remove('hidden');
      resumeBtn.classList.add('hidden');

      // Show manual screenshot button during active recording
      if (manualScreenshotBtn) manualScreenshotBtn.classList.remove('hidden');

      statusIndicator.textContent = 'Grabacion Activa';
      statusIndicator.className = 'status-badge status-recording';
    } else if (state === 'paused') {
      silenceTimeoutInput.disabled = false;
      silenceTimeoutInput.classList.remove('opacity-50', 'cursor-not-allowed');
      customFilenameInput.disabled = false;
      customFilenameInput.classList.remove('opacity-50', 'cursor-not-allowed');

      recordBtn.classList.add('hidden');
      controlsGroup.classList.remove('hidden');
      controlsGroup.classList.add('flex-row');
      pauseBtn.classList.add('hidden');
      resumeBtn.classList.remove('hidden');

      statusIndicator.textContent = 'En Espera';
      statusIndicator.className = 'status-badge status-idle';
    } else if (state === 'uploading') {
      silenceTimeoutInput.disabled = true;
      silenceTimeoutInput.classList.add('opacity-50', 'cursor-not-allowed');
      recordBtn.classList.add('hidden');
      controlsGroup.classList.remove('flex-row');
      controlsGroup.classList.add('hidden');
      statusIndicator.textContent = 'Procesando';
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
      statusIndicator.textContent = 'Servidor inalcanzable. Audio guardado localmente como respaldo.';
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

  // --- TABS LOGIC ---
  tabBtnAudio.addEventListener('click', () => {
    tabAudio.classList.remove('hidden');
    tabCapture.classList.add('hidden');
    tabSettings.classList.add('hidden');
    tabBtnAudio.className = 'segment-btn active';
    tabBtnCapture.className = 'segment-btn';
  });

  tabBtnCapture.addEventListener('click', () => {
    tabCapture.classList.remove('hidden');
    tabAudio.classList.add('hidden');
    tabSettings.classList.add('hidden');
    tabBtnCapture.className = 'segment-btn active';
    tabBtnAudio.className = 'segment-btn';
  });

  settingsBtn.addEventListener('click', () => {
    tabAudio.classList.add('hidden');
    tabCapture.classList.add('hidden');
    tabSettings.classList.remove('hidden');
    tabBtnAudio.classList.remove('active');
    tabBtnCapture.classList.remove('active');
  });

  closeSettingsBtn.addEventListener('click', () => {
    tabSettings.classList.add('hidden');
    tabAudio.classList.remove('hidden');
    tabBtnAudio.classList.add('active');
  });


  captureTaskBtn.addEventListener('click', () => {
    captureTaskBtn.disabled = true;
    captureStatus.classList.add('hidden');
    previewContainer.classList.add('hidden');

    chrome.tabs.captureVisibleTab(null, { format: 'jpeg', quality: 80 }, (dataUrl) => {
      captureTaskBtn.disabled = false;
      if (chrome.runtime.lastError) {
        captureStatus.textContent = 'Error: ' + chrome.runtime.lastError.message;
        captureStatus.className = 'status-badge status-recording mt-4';
        captureStatus.classList.remove('hidden');
        return;
      }
      currentCaptureDataUrl = dataUrl;
      capturePreviewImg.src = dataUrl;
      captureTaskBtnContainer.classList.add('hidden');
      previewContainer.classList.remove('hidden');
      previewContainer.style.display = 'flex';
    });
  });

  cancelCaptureBtn.addEventListener('click', () => {
    currentCaptureDataUrl = null;
    previewContainer.classList.add('hidden');
    previewContainer.style.display = 'none';
    captureTaskBtnContainer.classList.remove('hidden');
    captureStatus.classList.add('hidden');
  });

  sendCaptureBtn.addEventListener('click', async () => {
    if (!currentCaptureDataUrl) return;

    try {
      sendCaptureBtn.disabled = true;
      captureStatus.textContent = 'Procesando Tarea...';
      captureStatus.className = 'status-badge status-idle mt-4';
      captureStatus.classList.remove('hidden');

      const response = await fetch('http://localhost:8000/api/extract-task', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_base64: currentCaptureDataUrl })
      });

      if (!response.ok) {
          const errJson = await response.json().catch(() => ({}));
          throw new Error(errJson.detail || 'Error en el servidor');
      }

      captureStatus.textContent = 'Tarea Guardada!';
      captureStatus.className = 'status-badge status-success mt-4';
      captureStatus.classList.remove('hidden');

      setTimeout(() => {
        captureStatus.classList.add('hidden');
        previewContainer.classList.add('hidden');
        previewContainer.style.display = 'none';
        captureTaskBtnContainer.classList.remove('hidden');
        sendCaptureBtn.disabled = false;
        currentCaptureDataUrl = null;
      }, 4000);

    } catch (err) {
      captureStatus.textContent = 'Error: ' + err.message;
      captureStatus.className = 'status-badge status-recording mt-4';
      captureStatus.classList.remove('hidden');
      setTimeout(() => {
          captureStatus.classList.add('hidden');
          sendCaptureBtn.disabled = false;
      }, 4000);
    }
  });
});
