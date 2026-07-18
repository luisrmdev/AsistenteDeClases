document.addEventListener('DOMContentLoaded', async () => {
  const recordBtn = document.getElementById('recordBtn');
  const stopBtn = document.getElementById('stopBtn');
  const statusIndicator = document.getElementById('statusIndicator');

  // Obtener el tabId actual
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;
  const currentTabId = tab.id;

  // Check current state for this specific tab
  chrome.storage.local.get(['recordingStates'], (result) => {
    const states = result.recordingStates || {};
    updateUI(states[currentTabId] || 'idle');
  });

  // Listen for state changes from background
  chrome.storage.onChanged.addListener((changes, namespace) => {
    if (namespace === 'local' && changes.recordingStates) {
      const states = changes.recordingStates.newValue || {};
      updateUI(states[currentTabId] || 'idle');
    }
  });

  recordBtn.addEventListener('click', () => {
    chrome.runtime.sendMessage({ target: 'background', type: 'START_RECORDING', tabId: currentTabId });
  });

  stopBtn.addEventListener('click', () => {
    chrome.runtime.sendMessage({ target: 'background', type: 'STOP_RECORDING', tabId: currentTabId });
  });

  function updateUI(state) {
    if (state === 'recording') {
      recordBtn.classList.add('hidden');
      stopBtn.classList.remove('hidden');
      statusIndicator.textContent = 'Grabando...';
      statusIndicator.classList.replace('text-gray-300', 'text-red-400');
      statusIndicator.classList.add('animate-pulse');
    } else if (state === 'uploading') {
      recordBtn.classList.add('hidden');
      stopBtn.classList.add('hidden');
      statusIndicator.textContent = 'Enviando...';
      statusIndicator.classList.replace('text-red-400', 'text-yellow-400');
      statusIndicator.classList.remove('animate-pulse');
    } else if (state === 'completed') {
      recordBtn.classList.remove('hidden');
      stopBtn.classList.add('hidden');
      statusIndicator.textContent = 'Completado';
      statusIndicator.classList.replace('text-yellow-400', 'text-green-400');
      
      setTimeout(() => updateUI('idle'), 3000);
    } else {
      // idle or error
      recordBtn.classList.remove('hidden');
      stopBtn.classList.add('hidden');
      statusIndicator.textContent = 'Listo para grabar';
      statusIndicator.classList.remove('text-red-400', 'text-yellow-400', 'text-green-400', 'animate-pulse');
      statusIndicator.classList.add('text-gray-300');
    }
  }
});
