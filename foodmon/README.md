# FoodMon Rebuild

Rebuilt session-based architecture for your food freshness monitoring project.

## Main ideas
- ESP can record directly to cloud while Raspberry Pi is off.
- Raspberry Pi acts as kiosk HMI, dashboard, ML inference node, and controller when on.
- Manual sessions: **Start Monitoring** / **Stop Monitoring**.
- System Control page lets the user choose:
  - food
  - the 7 gas sensors only: `mq2`, `mq3`, `mq4`, `mq135`, `mq136`, `mq137`, `co2`
- Temperature and humidity stay active in live monitoring and ML.

## Backend files
- `backend/config.py`
- `backend/cloud_client.py`
- `backend/session_manager.py`
- `backend/mqtt_handler.py`
- `backend/ml_engine.py`
- `backend/actuator_control.py`
- `backend/app.py`

## Frontend files
- `frontend/index.html`
- `frontend/system_control.html`
- `frontend/dashboard.html`
- `frontend/js/system_control.js`
- `frontend/js/app.js`
- `frontend/js/gauges.js`
- `frontend/css/style.css`

## Important environment variables
For Firebase Realtime Database:
- `FOODMON_CLOUD_PROVIDER=firebase`
- `FOODMON_FIREBASE_DB_URL=https://YOUR_PROJECT.firebaseio.com`
- `FOODMON_FIREBASE_AUTH=YOUR_TOKEN` (optional if public rules during testing)

## Run
```bash
cd backend
pip install -r ../requirements.txt
python app.py
```

## Notes
- This rebuild keeps the industrial dark HMI design.
- Models are loaded from `models/<food>.pkl`; dummy models are used if missing.
- MQTT topics for control:
  - `foodmon/control/start`
  - `foodmon/control/stop`
