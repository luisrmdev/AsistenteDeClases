document.addEventListener('DOMContentLoaded', async () => {
  const recordBtn = document.getElementById('recordBtn');
  const stopBtn = document.getElementById('stopBtn');
  const pauseBtn = document.getElementById('pauseBtn');
  const resumeBtn = document.getElementById('resumeBtn');
  const controlsGroup = document.getElementById('controlsGroup');
  const statusIndicator = document.getElementById('statusIndicator');
  const audioWarning = document.getElementById('audioWarning');
  const silenceTimeoutInput = document.getElementById('silenceTimeout');
  const customFilenameInput = document.getElementById('customFilename');
  const countdownDisplay = document.getElementById('countdownDisplay');
  const countdownTimer = document.getElementById('countdownTimer');
  const durationTimer = document.getElementById('durationTimer');

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
      pauseBtn.classList.remove('hidden');
      resumeBtn.classList.add('hidden');
      
      statusIndicator.textContent = 'Grabando...';
      statusIndicator.classList.replace('text-gray-300', 'text-red-400');
      statusIndicator.classList.replace('text-yellow-400', 'text-red-400');
      statusIndicator.classList.add('animate-pulse');
    } else if (state === 'paused') {
      // Permitimos modificar durante la pausa
      silenceTimeoutInput.disabled = false;
      silenceTimeoutInput.classList.remove('opacity-50', 'cursor-not-allowed');
      customFilenameInput.disabled = false;
      customFilenameInput.classList.remove('opacity-50', 'cursor-not-allowed');
      
      recordBtn.classList.add('hidden');
      controlsGroup.classList.remove('hidden');
      pauseBtn.classList.add('hidden');
      resumeBtn.classList.remove('hidden');
      
      statusIndicator.textContent = 'Pausado (Receso)';
      statusIndicator.classList.replace('text-red-400', 'text-yellow-400');
      statusIndicator.classList.remove('animate-pulse');
    } else if (state === 'uploading') {
      silenceTimeoutInput.disabled = true;
      silenceTimeoutInput.classList.add('opacity-50', 'cursor-not-allowed');
      recordBtn.classList.add('hidden');
      controlsGroup.classList.add('hidden');
      statusIndicator.textContent = 'Enviando...';
      statusIndicator.classList.replace('text-red-400', 'text-yellow-400');
      statusIndicator.classList.remove('animate-pulse');
    } else if (state === 'completed') {
      recordBtn.classList.remove('hidden');
      controlsGroup.classList.add('hidden');
      statusIndicator.textContent = 'Completado';
      statusIndicator.classList.replace('text-yellow-400', 'text-green-400');
      
      setTimeout(() => updateUI('idle', audible), 3000);
    } else {
      // idle or error
      recordBtn.classList.remove('hidden');
      controlsGroup.classList.add('hidden');
      statusIndicator.textContent = 'Listo para grabar';
      statusIndicator.classList.remove('text-red-400', 'text-yellow-400', 'text-green-400', 'animate-pulse');
      statusIndicator.classList.add('text-gray-300');

      if (!audible) {
        recordBtn.disabled = true;
        audioWarning.classList.remove('hidden');
      }
    }
  }
});
