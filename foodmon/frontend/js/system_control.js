const GAS_SENSORS = [
    { id: "mq2",   name: "MQ-2"   },
    { id: "mq3",   name: "MQ-3"   },
    { id: "mq4",   name: "MQ-4"   },
    { id: "mq135", name: "MQ-135" },
    { id: "mq136", name: "MQ-136" },
    { id: "mq137", name: "MQ-137" },
    { id: "co2",   name: "CO₂"    }
];

function capitalize(text) { return text ? text.charAt(0).toUpperCase() + text.slice(1) : ''; }

// ── Header clock ───────────────────────────────────────────────────────────────
function updateHeaderTime() {
    document.getElementById('header-time').textContent = new Date().toLocaleTimeString('en-US', {
        hour: '2-digit', minute: '2-digit', hour12: true
    });
}

// ── Food grid (3 rows × 5 cols) ───────────────────────────────────────────────
let selectedFood = null;

async function loadFoods() {
    const res  = await fetch('/api/foods');
    const data = await res.json();
    const grid = document.getElementById('food-grid');
    grid.innerHTML = '';
    (data.foods || []).forEach(food => {
        const btn = document.createElement('button');
        btn.className   = 'food-btn';
        btn.dataset.food = food;
        btn.textContent = capitalize(food);
        btn.addEventListener('click', () => {
            document.querySelectorAll('.food-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            selectedFood = food;
        });
        grid.appendChild(btn);
    });
    // Pre-select first food
    const first = grid.querySelector('.food-btn');
    if (first) { first.classList.add('active'); selectedFood = first.dataset.food; }
}

function getSelectedFood() { return selectedFood; }

// ── Sensor grid ────────────────────────────────────────────────────────────────
function buildSensorGrid() {
    const grid = document.getElementById('sensor-grid');
    grid.innerHTML = '';
    GAS_SENSORS.forEach(sensor => {
        const label = document.createElement('label');
        label.className = 'sensor-pill';
        label.innerHTML = `<input type="checkbox" value="${sensor.id}" checked> <span>${sensor.name}</span>`;
        grid.appendChild(label);
    });
}

function getSelectedSensors() {
    return Array.from(document.querySelectorAll('#sensor-grid input:checked')).map(i => i.value);
}

// ── Session ────────────────────────────────────────────────────────────────────
async function loadSession() {
    const res  = await fetch('/api/session_status');
    const data = await res.json();
    const session = data.session || {};
    updateSessionCard(session);
    updateActuatorLock(session);
}

function setRecordingIndicator(show) {
    const panel = document.getElementById('recording-inline');
    if (panel) panel.classList.toggle('hidden', !show);
}

function updateSessionCard(session) {
    const card = document.getElementById('session-status');
    if (session.status === 'running') {
        card.textContent = `Running \u2022 ${capitalize(session.food_name)} \u2022 ${session.selected_sensors?.length || 0} sensors`;
        card.className = 'status-card running';
        setRecordingIndicator(true);
    } else if (session.status === 'completed') {
        card.textContent = `Completed \u2022 ${capitalize(session.food_name || '')}`;
        card.className = 'status-card completed';
        setRecordingIndicator(false);
    } else {
        card.textContent = 'Idle';
        card.className = 'status-card';
        setRecordingIndicator(false);
    }
}

async function startSession() {
    const food_name       = getSelectedFood();
    const selected_sensors = getSelectedSensors();
    const res  = await fetch('/api/start_session', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ food_name, selected_sensors })
    });
    const data = await res.json();
    if (!data.success) { alert(data.message || 'Failed to start session'); return; }
    updateSessionCard(data.session);
    updateActuatorLock(data.session);
    document.querySelector('[data-tab="session"]').click();
}

async function stopSession() {
    const res  = await fetch('/api/stop_session', { method: 'POST' });
    const data = await res.json();
    if (!data.success) { alert('Failed to stop session'); return; }
    updateSessionCard(data.session);
    updateActuatorLock(data.session);
    document.querySelector('[data-tab="session"]').click();
}

// ── Actuator lock logic ────────────────────────────────────────────────────────
function updateActuatorLock(session) {
    const isRunning = session.status === 'running';
    const banner    = document.getElementById('act-lock-banner');
    const controls  = document.getElementById('act-controls');
    const sendBtn   = document.getElementById('act-send-btn');

    banner.classList.toggle('hidden', !isRunning);
    controls.classList.toggle('act-locked', isRunning);
    if (sendBtn) sendBtn.disabled = isRunning;
}

// ── ON/OFF toggle widgets ──────────────────────────────────────────────────────
function initActuatorToggles() {
    document.querySelectorAll('.act-onoff').forEach(widget => {
        widget.addEventListener('click', () => {
            // Ignore clicks when locked
            if (document.getElementById('act-controls').classList.contains('act-locked')) return;
            const current = widget.dataset.state;
            const next    = current === 'off' ? 'on' : 'off';
            widget.dataset.state = next;
        });
    });
}

function getActuatorStates() {
    const states = {};
    document.querySelectorAll('.act-onoff').forEach(widget => {
        const key = widget.dataset.actuator;
        const on  = widget.dataset.state === 'on';
        // Ventilation sends a level string; cooler and humidifier send booleans
        if (key === 'ventilation') {
            states[key] = on ? 'LOW' : 'OFF';   // manual mode defaults to LOW when ON
        } else {
            states[key] = on;
        }
    });
    return states;
}

// ── Send command to backend ────────────────────────────────────────────────────
async function sendActuatorCommand() {
    const controls = document.getElementById('act-controls');
    if (controls.classList.contains('act-locked')) return;

    const feedback = document.getElementById('act-feedback');
    feedback.textContent = 'Sending…';
    feedback.className   = 'act-feedback';

    try {
        const res  = await fetch('/api/manual_actuator', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify(getActuatorStates())
        });
        const data = await res.json();
        if (data.success) {
            feedback.textContent = 'Command sent \u2713';
            feedback.className   = 'act-feedback act-feedback-ok';
        } else {
            feedback.textContent = data.message || 'Failed';
            feedback.className   = 'act-feedback act-feedback-err';
        }
    } catch (err) {
        feedback.textContent = 'Network error';
        feedback.className   = 'act-feedback act-feedback-err';
    }

    // Clear feedback after 3 s
    setTimeout(() => { feedback.textContent = ''; feedback.className = 'act-feedback'; }, 3000);
}

// ── Tab switching ──────────────────────────────────────────────────────────────
function bindTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', function () {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
        });
    });
}

// ── Boot ───────────────────────────────────────────────────────────────────────
async function boot() {
    bindTabs();
    updateHeaderTime();
    setInterval(updateHeaderTime, 1000);
    await loadFoods();
    buildSensorGrid();
    await loadSession();
    initActuatorToggles();
    document.getElementById('start-btn').addEventListener('click', startSession);
    document.getElementById('stop-btn').addEventListener('click', stopSession);
    document.getElementById('act-send-btn').addEventListener('click', sendActuatorCommand);
}

document.addEventListener('DOMContentLoaded', boot);
