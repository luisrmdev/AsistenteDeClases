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
  
  const captureTaskBtn = document.getElementById('captureTaskBtn');
  const captureStatus = document.getElementById('captureStatus');
  const previewContainer = document.getElementById('previewContainer');
  const capturePreviewImg = document.getElementById('capturePreviewImg');
  const sendCaptureBtn = document.getElementById('sendCaptureBtn');
  
  const tabBtnAudio = document.getElementById('tab-btn-audio');
  const tabBtnCapture = document.getElementById('tab-btn-capture');
  const tabAudio = document.getElementById('tab-audio');
  const tabCapture = document.getElementById('tab-capture');
  
  let currentCaptureDataUrl = null;

  let countdownInterval = null;

  // Load saved silence timeout
  chrome.storage.local.get(['silenceTimeoutMin'], (result) => {
    if (result.silenceTimeoutMin) {
      silenceTimeoutInput.value = result.silenceTimeoutMin;
    } else {
      chrome.storage.local.set({ silenceTimeoutMin: 5 }); // default
    }
  });

  // Save on change
  silenceTimeoutInput.addEventListener('change', () => {
    let val = parseInt(silenceTimeoutInput.value);
    if (isNaN(val) || val < 1) val = 1;
    silenceTimeoutInput.value = val;
    chrome.storage.local.set({ silenceTimeoutMin: val });
  });

  const ghostModeToggle = document.getElementById('ghostModeToggle');
  // Load saved ghost mode
  chrome.storage.local.get(['ghostMode'], (result) => {
    if (result.ghostMode !== undefined) {
      ghostModeToggle.checked = result.ghostMode;
    }
  });

  ghostModeToggle.addEventListener('change', () => {
    const isGhost = ghostModeToggle.checked;
    chrome.storage.local.set({ ghostMode: isGhost });
    chrome.runtime.sendMessage({ target: 'offscreen', type: 'SET_GHOST_MODE', ghostMode: isGhost });
  });

  // Obtener el tabId actual
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;
  const currentTabId = tab.id;
  const isAudible = tab.audible;

  // Load saved custom name
  chrome.storage.local.get(['customNames'], (result) => {
    const names = result.customNames || {};
    if (names[currentTabId]) {
      customFilenameInput.value = names[currentTabId];
    }
  });

  // Save custom name on change
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
    if (confirm("¿Estás seguro de que deseas cancelar la grabación? Todo el audio se perderá de forma permanente.")) {
      chrome.runtime.sendMessage({ target: 'background', type: 'CANCEL_RECORDING', tabId: currentTabId });
    }
  });

  function updateCountdown() {
    chrome.storage.local.get(['silenceAlarms', 'recordingTimers'], (result) => {
      // Lógica de apagado por silencio
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

      // Lógica de duración de grabación (Matemática de Timestamps)
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
      
      statusIndicator.textContent = 'Grabación Activa';
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
    
    tabBtnAudio.className = 'segment-btn active';
    tabBtnCapture.className = 'segment-btn';
  });

  tabBtnCapture.addEventListener('click', () => {
    tabCapture.classList.remove('hidden');
    tabAudio.classList.add('hidden');
    
    tabBtnCapture.className = 'segment-btn active';
    tabBtnAudio.className = 'segment-btn';
  });

  // --- LOGICA DEL EXTRACTOR VISUAL DE TAREAS ---
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
      previewContainer.classList.remove('hidden');
      previewContainer.style.display = 'flex';
    });
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
      
      captureStatus.textContent = '¡Tarea Guardada!';
      captureStatus.className = 'status-badge status-success mt-4';
      captureStatus.classList.remove('hidden');
      
      setTimeout(() => {
        captureStatus.classList.add('hidden');
        previewContainer.classList.add('hidden');
        previewContainer.style.display = 'none';
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
