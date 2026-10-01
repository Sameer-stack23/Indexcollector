const statusBadge = document.querySelector('#status-badge');
const modeValue = document.querySelector('#mode-value');
const pidValue = document.querySelector('#pid-value');
const startedValue = document.querySelector('#started-value');
const logs = document.querySelector('#logs');
const message = document.querySelector('#message');
const startButton = document.querySelector('#start-button');
const stopButton = document.querySelector('#stop-button');
const modeSelect = document.querySelector('#mode');
const dateRange = document.querySelector('#date-range');

function renderStatus(status) {
  statusBadge.textContent = status.running ? 'Running' : 'Stopped';
  statusBadge.className = `badge ${status.running ? 'running' : 'stopped'}`;
  modeValue.textContent = status.mode || '—';
  pidValue.textContent = status.pid || '—';
  startedValue.textContent = status.started_at || '—';
  startButton.disabled = status.running;
  stopButton.disabled = !status.running;
  logs.textContent = status.logs.length ? status.logs.join('\n') : 'No collector output yet.';
  logs.scrollTop = logs.scrollHeight;
}

async function refreshStatus() {
  try {
    const response = await fetch('/api/status');
    renderStatus(await response.json());
  } catch {
    message.textContent = 'Dashboard server is not reachable.';
  }
}

async function sendCommand(endpoint, payload = {}) {
  const response = await fetch(endpoint, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  message.textContent = result.message;
  renderStatus(result.status);
}

startButton.addEventListener('click', () => {
  sendCommand('/api/start', {
    mode: modeSelect.value,
    from_date: document.querySelector('#from-date').value,
    to_date: document.querySelector('#to-date').value,
  });
});
modeSelect.addEventListener('change', () => {
  dateRange.hidden = modeSelect.value !== 'historical';
});
stopButton.addEventListener('click', () => sendCommand('/api/stop'));
document.querySelector('#refresh-button').addEventListener('click', refreshStatus);

refreshStatus();
dateRange.hidden = modeSelect.value !== 'historical';
// Status polling is lightweight and does not call the market API.
setInterval(refreshStatus, 5000);
