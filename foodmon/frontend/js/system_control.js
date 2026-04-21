const GAS_SENSORS = [
    { id: "mq2", name: "MQ-2" },
    { id: "mq3", name: "MQ-3" },
    { id: "mq4", name: "MQ-4" },
    { id: "mq135", name: "MQ-135" },
    { id: "mq136", name: "MQ-136" },
    { id: "mq137", name: "MQ-137" },
    { id: "co2", name: "CO₂" }
];

function capitalize(text) { return text ? text.charAt(0).toUpperCase() + text.slice(1) : ''; }

function updateHeaderTime() {
    document.getElementById('header-time').textContent = new Date().toLocaleTimeString('en-US', {
        hour: '2-digit', minute: '2-digit', hour12: true
    });
}

async function loadFoods() {
    const res = await fetch('/api/foods');
    const data = await res.json();
    const select = document.getElementById('food-select');
    select.innerHTML = '';
    (data.foods || []).forEach(food => {
        const opt = document.createElement('option');
        opt.value = food;
        opt.textContent = capitalize(food);
        select.appendChild(opt);
    });
}

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

async function loadSession() {
    const res = await fetch('/api/session_status');
    const data = await res.json();
    updateSessionCard(data.session || {});
}

function setRecordingIndicator(show) {
    const panel = document.getElementById('recording-inline');
    if (!panel) return;
    panel.classList.toggle('hidden', !show);
}

function updateSessionCard(session) {
    const card = document.getElementById('session-status');
    if (session.status === 'running') {
        card.textContent = `Running • ${capitalize(session.food_name)} • ${session.selected_sensors?.length || 0} sensors`;
        card.className = 'status-card running';
        setRecordingIndicator(true);
    } else if (session.status === 'completed') {
        card.textContent = `Completed • ${capitalize(session.food_name || '')}`;
        card.className = 'status-card completed';
        setRecordingIndicator(false);
    } else {
        card.textContent = 'Idle';
        card.className = 'status-card';
        setRecordingIndicator(false);
    }
}

async function startSession() {
    const food_name = document.getElementById('food-select').value;
    const selected_sensors = getSelectedSensors();
    const res = await fetch('/api/start_session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ food_name, selected_sensors })
    });
    const data = await res.json();
    if (!data.success) {
        alert(data.message || 'Failed to start session');
        return;
    }
    updateSessionCard(data.session);
    document.querySelector('[data-tab="session"]').click();
}


async function stopSession() {
    const res = await fetch('/api/stop_session', { method: 'POST' });
    const data = await res.json();
    if (!data.success) {
        alert('Failed to stop session');
        return;
    }
    updateSessionCard(data.session);
    document.querySelector('[data-tab="session"]').click();
}


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

async function boot() {
    bindTabs();
    updateHeaderTime();
    setInterval(updateHeaderTime, 1000);
    await loadFoods();
    buildSensorGrid();
    await loadSession();
    document.getElementById('start-btn').addEventListener('click', startSession);
    document.getElementById('stop-btn').addEventListener('click', stopSession);
}

document.addEventListener('DOMContentLoaded', boot);
