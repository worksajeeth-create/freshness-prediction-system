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

    banner.classList.toggle('hidden', !isRunning);
    controls.classList.toggle('act-locked', isRunning);
}

// ── ON/OFF toggle widgets — directly control the actuator, no timer ────────────
// Each switch is wired to its own MQTT-backed actuator. Flipping a switch sends
// that single actuator's new state to the backend immediately. The actuator
// then stays exactly as commanded (no auto-off) until the switch is flipped
// again by the user.

function actuatorPayloadFor(key, isOn) {
    // Ventilation sends a level string; cooler and humidifier send booleans.
    if (key === 'ventilation') {
        return isOn ? 'LOW' : 'OFF';   // manual mode defaults to LOW when ON
    }
    return isOn;
}

function showActFeedback(text, kind) {
    const feedback = document.getElementById('act-feedback');
    if (!feedback) return;
    feedback.textContent = text;
    feedback.className   = 'act-feedback' + (kind ? ` act-feedback-${kind}` : '');
    clearTimeout(showActFeedback._t);
    showActFeedback._t = setTimeout(() => {
        feedback.textContent = '';
        feedback.className   = 'act-feedback';
    }, 2000);
}

// Minimum time between accepted toggles on the *same* switch. Touchscreens
// frequently emit a "ghost" duplicate click event for a single physical tap
// (touch + emulated mouse click, or a stray second event from the panel
// driver). Without a guard, that duplicate event immediately flips the
// switch back, and any further duplicates compound — each extra click adds
// another silent on/off flip, which is why the ON duration kept shrinking
// on repeated presses. This cooldown collapses any duplicate events from one
// physical tap into a single state change.
const ACT_TOGGLE_COOLDOWN_MS = 400;

function initActuatorToggles() {
    document.querySelectorAll('.act-onoff').forEach(widget => {
        // Guard against this function (or boot()) accidentally running more
        // than once and stacking duplicate listeners on the same element —
        // that alone would make every tap fire twice.
        if (widget.dataset.toggleBound === 'true') return;
        widget.dataset.toggleBound = 'true';
        widget.style.touchAction = 'manipulation'; // suppress double-tap/zoom gesture handling

        let busy = false;        // true while a request for this widget is in flight
        let lastToggleAt = 0;    // timestamp of the last accepted toggle

        widget.addEventListener('click', async (evt) => {
            evt.preventDefault();

            // Ignore clicks when locked (an active monitoring session is running)
            if (document.getElementById('act-controls').classList.contains('act-locked')) return;

            // Ignore a request already in flight, and ignore anything that
            // arrives within the cooldown window of the last accepted toggle
            // (this is what absorbs duplicate/ghost click events).
            const now = Date.now();
            if (busy || (now - lastToggleAt) < ACT_TOGGLE_COOLDOWN_MS) return;

            busy = true;
            lastToggleAt = now;

            const key     = widget.dataset.actuator;
            const current = widget.dataset.state;
            const next    = current === 'off' ? 'on' : 'off';

            // Update the switch immediately for a responsive feel.
            widget.dataset.state = next;
            showActFeedback('Sending…');

            try {
                const res  = await fetch('/api/manual_actuator', {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify({ [key]: actuatorPayloadFor(key, next === 'on') })
                });
                const data = await res.json();
                if (data.success) {
                    showActFeedback('Updated \u2713', 'ok');
                } else {
                    // Revert the switch if the command failed to send.
                    widget.dataset.state = current;
                    showActFeedback(data.message || 'Failed', 'err');
                }
            } catch (err) {
                widget.dataset.state = current;
                showActFeedback('Network error', 'err');
            } finally {
                busy = false;
            }
        });
    });
}

// Sync the toggle switches with whatever state the backend currently reports
// (e.g. after a page refresh) so they don't default back to OFF visually.
async function syncActuatorToggles() {
    try {
        const res  = await fetch('/api/current_data');
        const data = await res.json();
        const status = data.actuator_status || {};
        document.querySelectorAll('.act-onoff').forEach(widget => {
            const key = widget.dataset.actuator;
            let isOn = false;
            if (key === 'ventilation') {
                isOn = (status.ventilation || 'OFF') !== 'OFF';
            } else {
                isOn = !!status[key];
            }
            widget.dataset.state = isOn ? 'on' : 'off';
        });
    } catch (err) {
        // Leave switches at their default state if this fails.
    }
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
    // Defensive: if this script ever gets re-run on the same page (e.g. a
    // kiosk wrapper re-injecting it without a full reload), don't initialise
    // everything a second time — that would double up event listeners and
    // cause every tap to fire twice.
    if (window.__foodmonSystemControlBooted) return;
    window.__foodmonSystemControlBooted = true;

    bindTabs();
    updateHeaderTime();
    setInterval(updateHeaderTime, 1000);
    await loadFoods();
    buildSensorGrid();
    await loadSession();
    initActuatorToggles();
    await syncActuatorToggles();
    document.getElementById('start-btn').addEventListener('click', startSession);
    document.getElementById('stop-btn').addEventListener('click', stopSession);
}

document.addEventListener('DOMContentLoaded', boot);
