const socket = io();
let tempGauge, humidityGauge, freshnessChart, gasChart;
let currentFood = null;
let currentSession = null;
let sessionStatus = 'idle';
const GAS_HISTORY_MAX = 40;
let activeGasSensors = [];
let lastSensorUpdateMs = 0;

const SENSOR_META = {
    mq2:   { name: 'MQ-2',   color: '#F44336', label: 'LPG, Smoke' },
    mq3:   { name: 'MQ-3',   color: '#FF9800', label: 'Alcohol' },
    mq4:   { name: 'MQ-4',   color: '#FFC107', label: 'Methane' },
    mq135: { name: 'MQ-135', color: '#9C27B0', label: 'VOCs/NH₃' },
    mq136: { name: 'MQ-136', color: '#009688', label: 'H₂S' },
    mq137: { name: 'MQ-137', color: '#00BCD4', label: 'NH₃' },
    co2:   { name: 'CO₂',    color: '#E91E63', label: 'Carbon Dioxide' }
};

function capitalize(text) { return text ? text.charAt(0).toUpperCase() + text.slice(1) : ''; }

document.addEventListener('DOMContentLoaded', async function () {
    tempGauge = new TemperatureGauge('temp-gauge');
    humidityGauge = new HumidityGauge('humidity-gauge');
    initFreshnessChart();
    initGasChart();
    buildLegend();

    document.getElementById('back-button').addEventListener('click', function () {
        window.location.href = '/';
    });

    updateHeaderTime();
    setInterval(updateHeaderTime, 1000);
    setInterval(checkStaleData, 3000);
    await checkSessionStatus();
});

async function checkSessionStatus() {
    try {
        const res = await fetch('/api/session_status');
        const data = await res.json();
        currentSession = data.session || null;
        sessionStatus = currentSession?.status || 'idle';
        if (currentSession?.food_name) currentFood = currentSession.food_name;
        activeGasSensors = currentSession?.selected_sensors?.length ? currentSession.selected_sensors : [];
        updateHeaderLabels();
        initGasChart();
        buildLegend();
    } catch (e) {
        console.error('session status error', e);
    }
}

function updateHeaderLabels() {
    const title = document.getElementById('food-title');
    const line = document.getElementById('session-line');
    if (sessionStatus === 'running' && currentSession?.food_name) {
        title.textContent = `Monitoring: ${capitalize(currentSession.food_name)}`;
        line.textContent = `Session Active • ${currentSession.selected_sensors?.length || 0} gas sensors`;
    } else if (sessionStatus === 'completed' && currentSession?.food_name) {
        title.textContent = `Last Session: ${capitalize(currentSession.food_name)}`;
        line.textContent = 'Monitoring session completed';
    } else {
        title.textContent = 'No Active Session';
        line.textContent = 'Start from System Control';
    }
}

function initGasChart() {
    const ctx = document.getElementById('gas-chart').getContext('2d');
    if (gasChart) gasChart.destroy();
    const selected = activeGasSensors.length ? activeGasSensors : [];
    const datasets = selected.map(sensorId => ({
        label: SENSOR_META[sensorId].name,
        sensorId,
        data: [],
        borderColor: SENSOR_META[sensorId].color,
        backgroundColor: SENSOR_META[sensorId].color + '22',
        borderWidth: 1.5,
        pointRadius: 0,
        tension: 0.3,
        fill: false
    }));

    gasChart = new Chart(ctx, {
        type: 'line',
        data: { labels: [], datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: { legend: { display: false } },
            scales: {
                x: { ticks: { color: '#a0a0a0', font: { size: 8 }, maxTicksLimit: 5, maxRotation: 0 }, grid: { color: 'rgba(255,255,255,0.05)' } },
                y: { min: 0, ticks: { color: '#a0a0a0', font: { size: 8 }, maxTicksLimit: 5 }, grid: { color: 'rgba(255,255,255,0.07)' } }
            }
        }
    });
}

function buildLegend() {
    const container = document.getElementById('gas-legend');
    container.innerHTML = '';
    if (!activeGasSensors.length) {
        container.innerHTML = '<div class="legend-empty">No gas sensors selected for the current session.</div>';
        return;
    }
    activeGasSensors.forEach(sensorId => {
        const meta = SENSOR_META[sensorId];
        const item = document.createElement('div');
        item.className = 'legend-item';
        item.innerHTML = `<span class="legend-dot" style="background:${meta.color}"></span><span class="legend-name">${meta.name}</span><span class="legend-label">${meta.label}</span>`;
        container.appendChild(item);
    });
}

function updateGasChart(gases, timestamp) {
    if (!gasChart || !activeGasSensors.length) return;
    const d = new Date(timestamp * 1000);
    const lbl = d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true });
    gasChart.data.labels.push(lbl);
    if (gasChart.data.labels.length > GAS_HISTORY_MAX) gasChart.data.labels.shift();

    gasChart.data.datasets.forEach(ds => {
        const raw = gases[ds.sensorId];
        const ppm = raw !== undefined ? (typeof raw === 'object' ? (raw.value ?? 0) : raw) : 0;
        ds.data.push(parseFloat(Number(ppm).toFixed(2)));
        if (ds.data.length > GAS_HISTORY_MAX) ds.data.shift();
    });
    gasChart.update('none');
    document.getElementById('gas-timestamp').textContent = lbl;
}

function initFreshnessChart() {
    const ctx = document.getElementById('freshness-chart').getContext('2d');
    freshnessChart = new Chart(ctx, {
        type: 'line',
        data: { labels: [], datasets: [{ label: 'Freshness', data: [], borderColor: '#4CAF50', backgroundColor: 'rgba(76,175,80,0.1)', borderWidth: 2, tension: 0.4, fill: true, pointRadius: 0 }] },
        options: { responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { display: false } }, scales: { y: { min: 0, max: 100 }, x: {} } }
    });
}

function updateHeaderTime() {
    document.getElementById('header-time').textContent = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true });
}

function checkStaleData() {
    if (!lastSensorUpdateMs) return;
    if (Date.now() - lastSensorUpdateMs > 20000) {
        tempGauge.setOffline();
        humidityGauge.setOffline();
        if (sessionStatus === 'running') {
            document.getElementById('session-line').textContent = 'No recent sensor data';
        }
    }
}

socket.on('connect', function () {
    socket.emit('request_update');
});

socket.on('session_update', function (session) {
    currentSession = session;
    sessionStatus = session?.status || 'idle';
    if (session?.food_name) currentFood = session.food_name;
    activeGasSensors = session?.selected_sensors?.length ? session.selected_sensors : [];
    updateHeaderLabels();
    initGasChart();
    buildLegend();
});

socket.on('sensor_update', function (data) {
    lastSensorUpdateMs = Date.now();
    if (data.temperature != null) {
        tempGauge.update(data.temperature);
        document.getElementById('temp-value').textContent = data.temperature.toFixed(1) + '°C';
    }
    if (data.humidity != null) {
        humidityGauge.update(data.humidity);
        document.getElementById('humidity-value').textContent = data.humidity.toFixed(1) + '%';
    }
    if (data.gases && data.timestamp) {
        updateGasChart(data.gases, data.timestamp);
    }
});

socket.on('ml_update', function (data) {
    if (data.freshness_index != null) {
        document.getElementById('freshness-index').textContent = Math.round(data.freshness_index);
        const badge = document.getElementById('status-badge');
        badge.textContent = data.status || 'Analyzing...';
        if (data.status === 'Fresh') {
            badge.style.background = '#4CAF50'; badge.style.color = '#1a1a1a';
        } else if (data.status === 'Half-Spoiled') {
            badge.style.background = '#FF9800'; badge.style.color = '#1a1a1a';
        } else if (data.status === 'Spoiled') {
            badge.style.background = '#F44336'; badge.style.color = '#ffffff';
        }
    }
    if (data.remaining_days != null) {
        document.getElementById('remaining-days').textContent = data.remaining_days + ' days';
    }
    if (data.history && data.history.length > 0) {
        const limited = data.history.slice(-50);
        freshnessChart.data.labels = limited.map(p => {
            const d = new Date(p.timestamp * 1000);
            return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true });
        });
        freshnessChart.data.datasets[0].data = limited.map(p => p.freshness);
        freshnessChart.update('none');
    }
});

socket.on('actuator_update', function (data) {
    const cooler = document.getElementById('cooler-status');
    cooler.textContent  = data.cooler ? 'ON' : 'OFF';
    cooler.className    = 'actuator-status' + (data.cooler ? ' on' : '');

    const vent = document.getElementById('vent-status');
    vent.textContent    = data.ventilation || 'OFF';
    vent.className      = 'actuator-status' + (data.ventilation !== 'OFF' ? ' on' : '');

    const humid = document.getElementById('humid-status');
    humid.textContent   = data.humidifier ? 'ON' : 'OFF';
    humid.className     = 'actuator-status' + (data.humidifier ? ' on' : '');
});
