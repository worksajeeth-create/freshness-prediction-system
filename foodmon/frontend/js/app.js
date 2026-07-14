const socket = io();
let tempGauge, humidityGauge, scTempGauge, scHumidityGauge;
let freshnessChart, gasChart;
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

// ── Global UI beep ──────────────────────────────────────────────────
// Requirement: "The system shall generate a short beep whenever the
// user presses a touchscreen button, tab, or toggle switch."
//
// Implemented as a single delegated, capturing click listener on
// document so it covers every interactive control on this page —
// including ones added or re-rendered dynamically — without having to
// wire a beep call into each individual handler.
function fireUiBeep() {
    fetch('/api/buzzer_beep', { method: 'POST' }).catch(() => {
        // Silent fail — MQTT/broker may be temporarily unavailable;
        // never block or alarm the user over a missed UI beep.
    });
}

document.addEventListener('click', function (evt) {
    const target = evt.target.closest(
        'button, .tab-btn, .climate-mode-btn, .icon-button, .act-onoff'
    );
    if (target) fireUiBeep();
}, true);

document.addEventListener('DOMContentLoaded', async function () {
    // Storage gauges
    tempGauge     = new TemperatureGauge('temp-gauge');
    humidityGauge = new HumidityGauge('humidity-gauge');

    // Sensor chamber gauges (purple/cyan palette)
    scTempGauge     = new SensorChamberTempGauge('sc-temp-gauge');
    scHumidityGauge = new SensorChamberHumidityGauge('sc-humidity-gauge');

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
        activeGasSensors = Object.keys(SENSOR_META);
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

    const datasets = Object.keys(SENSOR_META).map(sensorId => ({
        label: SENSOR_META[sensorId].name,
        sensorId,
        data: [],
        borderColor: SENSOR_META[sensorId].color,
        backgroundColor: SENSOR_META[sensorId].color + '22',
        borderWidth: 1.5,
        pointRadius: 0,
        tension: 0.3,
        fill: false,
        spanGaps: false
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
    Object.keys(SENSOR_META).forEach(sensorId => {
        const meta = SENSOR_META[sensorId];
        const item = document.createElement('div');
        item.className = 'legend-item';
        item.innerHTML = `<span class="legend-dot" style="background:${meta.color}"></span><span class="legend-name">${meta.name}</span><span class="legend-label">${meta.label}</span>`;
        container.appendChild(item);
    });
}



function updateGasChart(gases, timestamp) {
    if (!gasChart) return;

    const ts = timestamp > 1000000000 ? timestamp : (Date.now() / 1000);
    const d = new Date(ts * 1000);
    const lbl = d.toLocaleTimeString('en-US', {
        hour: '2-digit', minute: '2-digit', hour12: true
    });

    gasChart.data.labels.push(lbl);
    if (gasChart.data.labels.length > GAS_HISTORY_MAX) gasChart.data.labels.shift();

    gasChart.data.datasets.forEach(ds => {
        const raw = gases[ds.sensorId];
        if (raw !== undefined) {
            const ppm = typeof raw === 'object' ? (raw.value ?? 0) : raw;
            ds.data.push(parseFloat(Number(ppm).toFixed(2)));
        } else {
            ds.data.push(null);
        }
        if (ds.data.length > GAS_HISTORY_MAX) ds.data.shift();
    });

    gasChart.update('none');
    document.getElementById('gas-timestamp').textContent = lbl;
}



function initFreshnessChart() {
    const ctx = document.getElementById('freshness-chart').getContext('2d');
    freshnessChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Freshness Index',
                    yAxisID: 'y',
                    data: [],
                    borderColor: '#4CAF50',
                    backgroundColor: 'rgba(76,175,80,0.1)',
                    borderWidth: 2,
                    tension: 0.4,
                    fill: true,
                    pointRadius: 0
                },
                {
                    // Remaining Shelf Life — trends downward as hours_elapsed
                    // increases, showing at a glance how much time is left.
                    label: 'Remaining Life (hrs)',
                    yAxisID: 'y1',
                    data: [],
                    borderColor: '#FF9800',
                    backgroundColor: 'rgba(255,152,0,0.08)',
                    borderWidth: 2,
                    borderDash: [4, 3],
                    tension: 0.4,
                    fill: false,
                    pointRadius: 0
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
                legend: {
                    display: true,
                    labels: { color: '#a0a0a0', boxWidth: 10, font: { size: 8 } }
                }
            },
            scales: {
                x: { ticks: { color: '#a0a0a0', font: { size: 8 }, maxTicksLimit: 5, maxRotation: 0 } },
                y: {
                    type: 'linear',
                    position: 'left',
                    min: 0,
                    max: 100,
                    ticks: { color: '#4CAF50', font: { size: 8 } },
                    title: { display: false }
                },
                y1: {
                    type: 'linear',
                    position: 'right',
                    min: 0,
                    grid: { drawOnChartArea: false },
                    ticks: { color: '#FF9800', font: { size: 8 } }
                }
            }
        }
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
        scTempGauge.setOffline();
        scHumidityGauge.setOffline();
        if (sessionStatus === 'running') {
            document.getElementById('session-line').textContent = 'No recent sensor data';
        }
    }
}

// Formats a remaining-life reading for display. Foods whose model reports
// only whole days round-trip fine through this; the rice regressor's
// hour-level precision (e.g. "3.2 hrs" instead of "0.1 days") shows through
// automatically once the backend starts sending remaining_hours.
function formatRemainingLife(days, hours) {
    const hrs = (hours != null) ? hours : days * 24;
    if (hrs < 24) {
        return hrs.toFixed(1) + ' hrs';
    }
    return days.toFixed(1) + ' days';
}

// ── Socket events ──────────────────────────────────────────────────────────────

socket.on('connect', function () {
    socket.emit('request_update');
});


socket.on('session_update', function (session) {
    currentSession = session;
    sessionStatus = session?.status || 'idle';
    if (session?.food_name) currentFood = session.food_name;
    updateHeaderLabels();
});


socket.on('sensor_update', function (data) {
    lastSensorUpdateMs = Date.now();

    // ── Storage climate ──────────────────────────────────────────────────────
    // app.py emits sensor_data flat: { temperature, humidity,
    //   sensor_chamber_temperature, sensor_chamber_humidity, gases, ... }
    const displayTemp = data.temperature;
    const displayHum  = data.humidity;

    if (displayTemp != null) {
        tempGauge.update(displayTemp);
        document.getElementById('temp-value').textContent = displayTemp.toFixed(1) + '°C';
    }
    if (displayHum != null) {
        humidityGauge.update(displayHum);
        document.getElementById('humidity-value').textContent = displayHum.toFixed(1) + '%';
    }

    // ── Sensor chamber climate (flat keys from app.py) ───────────────────────
    const scTemp = data.sensor_chamber_temperature;
    const scHum  = data.sensor_chamber_humidity;

    if (scTemp != null) {
        scTempGauge.update(scTemp);
        document.getElementById('sc-temp-value').textContent = scTemp.toFixed(1) + '°C';
    }
    if (scHum != null) {
        scHumidityGauge.update(scHum);
        document.getElementById('sc-humidity-value').textContent = scHum.toFixed(1) + '%';
    }

    // ── Gas readings ─────────────────────────────────────────────────────────
    if (data.gases && data.timestamp) {
        updateGasChart(data.gases, data.timestamp);
    }
});


// ── Climate update (nested structure emitted alongside sensor_update) ─────────
// app.py also emits a dedicated 'climate_update' with { storage:{}, sensor_chamber:{} }.
// This handler catches it so either event path works.
socket.on('climate_update', function (data) {
    const storage = data.storage || {};
    if (storage.temperature != null) {
        tempGauge.update(storage.temperature);
        document.getElementById('temp-value').textContent = storage.temperature.toFixed(1) + '°C';
    }
    if (storage.humidity != null) {
        humidityGauge.update(storage.humidity);
        document.getElementById('humidity-value').textContent = storage.humidity.toFixed(1) + '%';
    }

    const sc = data.sensor_chamber || {};
    if (sc.temperature != null) {
        scTempGauge.update(sc.temperature);
        document.getElementById('sc-temp-value').textContent = sc.temperature.toFixed(1) + '°C';
    }
    if (sc.humidity != null) {
        scHumidityGauge.update(sc.humidity);
        document.getElementById('sc-humidity-value').textContent = sc.humidity.toFixed(1) + '%';
    }
});


socket.on('ml_update', function (data) {
    // Status -> color lookup shared by the badge, the big freshness number,
    // and the freshness-trend line on the graph, so all three always agree.
    const STATUS_COLORS = {
        'Fresh':        '#4CAF50',
        'Half-Spoiled': '#FF9800',
        'Spoiled':      '#F44336'
    };

    if (data.freshness_index != null) {
        const freshnessNumberEl = document.getElementById('freshness-index');
        freshnessNumberEl.textContent = Math.round(data.freshness_index);

        const statusColor = STATUS_COLORS[data.status] || null;
        if (statusColor) {
            freshnessNumberEl.style.color = statusColor;
            freshnessNumberEl.style.textShadow = `0 0 14px ${statusColor}80`; // ~50% alpha glow
        }

        const badge = document.getElementById('status-badge');
        badge.textContent = data.status || 'Analyzing...';
        if (data.status === 'Fresh') {
            badge.style.background = '#4CAF50'; badge.style.color = '#1a1a1a';
        } else if (data.status === 'Half-Spoiled') {
            badge.style.background = '#FF9800'; badge.style.color = '#1a1a1a';
        } else if (data.status === 'Spoiled') {
            badge.style.background = '#F44336'; badge.style.color = '#ffffff';
            // Note: the long alert beep itself is fired server-side,
            // exactly once per spoilage event, by run_ml_and_control()
            // in app.py — not from here. This keeps the alert tied to
            // the authoritative ML state rather than to UI redraws,
            // which could otherwise re-fire on every socket update.
        }

        // Recolor the freshness-index line on the graph to match the current
        // status too (whole line, not per-segment — simplest and matches
        // "when it turns spoiled, the graph should be red").
        if (freshnessChart && statusColor) {
            freshnessChart.data.datasets[0].borderColor = statusColor;
            freshnessChart.data.datasets[0].backgroundColor = statusColor + '1A'; // ~10% alpha fill
            freshnessChart.update('none');
        }
    }
    if (data.remaining_days != null) {
        document.getElementById('remaining-days').textContent =
            formatRemainingLife(data.remaining_days, data.remaining_hours);
    } else if (data.freshness_index != null) {
        // remaining_days is null while the trend model is still gathering its
        // first few readings (see RiceTrendModel.MIN_TREND_POINTS) — status
        // and freshness_index are already live at this point, just not the
        // remaining-life estimate yet.
        document.getElementById('remaining-days').textContent = 'Calculating trend...';
    }
    if (data.history && data.history.length > 0) {
        const limited = data.history.slice(-50);
        freshnessChart.data.labels = limited.map(p => {
            const d = new Date(p.timestamp * 1000);
            return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true });
        });
        freshnessChart.data.datasets[0].data = limited.map(p => p.freshness);
        // remaining_hours is only present in history entries logged after this
        // update ships; older entries (or foods whose model predates it) fall
        // back to null so Chart.js just leaves a gap rather than erroring.
        freshnessChart.data.datasets[1].data = limited.map(p =>
            p.remaining_hours != null ? p.remaining_hours : null
        );
        freshnessChart.update('none');
    }
});

socket.on('actuator_update', function (data) {
    const cooler = document.getElementById('cooler-status');
    cooler.textContent = data.cooler ? 'ON' : 'OFF';
    cooler.className   = 'actuator-status' + (data.cooler ? ' on' : '');
    const coolerWrap = document.getElementById('cooler-icon-wrap');
    if (coolerWrap) coolerWrap.className = 'actuator-icon-wrap cooler-svg-icon' + (data.cooler ? ' svg-icon-on' : '');

    const vent = document.getElementById('vent-status');
    vent.textContent = data.ventilation || 'OFF';
    vent.className   = 'actuator-status' + (data.ventilation !== 'OFF' ? ' on' : '');
    const ventWrap = document.getElementById('vent-icon-wrap');
    if (ventWrap) ventWrap.className = 'actuator-icon-wrap vent-svg-icon' + (data.ventilation !== 'OFF' ? ' svg-icon-on' : '');

    const humid = document.getElementById('humid-status');
    humid.textContent = data.humidifier ? 'ON' : 'OFF';
    humid.className   = 'actuator-status' + (data.humidifier ? ' on' : '');
    const humidWrap = document.getElementById('humid-icon-wrap');
    if (humidWrap) humidWrap.className = 'actuator-icon-wrap humid-svg-icon' + (data.humidifier ? ' svg-icon-on' : '');
});
