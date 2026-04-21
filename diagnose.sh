#!/bin/bash
# diagnose.sh - System diagnostic script

echo "=========================================="
echo "Food Freshness Monitor - System Diagnostic"
echo "=========================================="
echo ""

# Check 1: Mosquitto Service
echo "[1] Checking Mosquitto MQTT Broker..."
if systemctl is-active --quiet mosquitto; then
    echo "✓ Mosquitto service is ACTIVE"
    sudo systemctl status mosquitto --no-pager | grep -E "(Active|Main PID)"
else
    echo "✗ Mosquitto service is NOT running"
    echo "  Run: sudo systemctl start mosquitto"
fi
echo ""

# Check 2: Mosquitto Process
echo "[2] Checking Mosquitto process..."
if pgrep -x mosquitto > /dev/null; then
    echo "✓ Mosquitto process is running"
    ps aux | grep mosquitto | grep -v grep
else
    echo "✗ Mosquitto process NOT found"
fi
echo ""

# Check 3: MQTT Port
echo "[3] Checking MQTT port 1883..."
if netstat -tuln 2>/dev/null | grep -q ":1883 "; then
    echo "✓ Port 1883 is listening"
    netstat -tuln | grep 1883
elif ss -tuln 2>/dev/null | grep -q ":1883 "; then
    echo "✓ Port 1883 is listening"
    ss -tuln | grep 1883
else
    echo "✗ Port 1883 is NOT listening"
fi
echo ""

# Check 4: Flask Application
echo "[4] Checking Flask application..."
if pgrep -f "python3 app.py" > /dev/null; then
    echo "✓ Flask app.py process is running"
    ps aux | grep "python3 app.py" | grep -v grep
else
    echo "✗ Flask app.py is NOT running"
    echo "  You need to start it: cd ~/foodmon/backend && source venv/bin/activate && python3 app.py"
fi
echo ""

# Check 5: Flask Port
echo "[5] Checking Flask port 5000..."
if netstat -tuln 2>/dev/null | grep -q ":5000 "; then
    echo "✓ Port 5000 is listening"
    netstat -tuln | grep 5000
elif ss -tuln 2>/dev/null | grep -q ":5000 "; then
    echo "✓ Port 5000 is listening"
    ss -tuln | grep 5000
else
    echo "✗ Port 5000 is NOT listening"
    echo "  Flask server is not running or crashed"
fi
echo ""

# Check 6: Python Virtual Environment
echo "[6] Checking Python virtual environment..."
if [ -d "$HOME/foodmon/backend/venv" ]; then
    echo "✓ Virtual environment exists"
    if [ -f "$HOME/foodmon/backend/venv/bin/python3" ]; then
        echo "✓ Python executable found"
        $HOME/foodmon/backend/venv/bin/python3 --version
    else
        echo "✗ Python executable NOT found in venv"
    fi
else
    echo "✗ Virtual environment NOT found at ~/foodmon/backend/venv"
fi
echo ""

# Check 7: Required Python packages
echo "[7] Checking Python packages..."
if [ -f "$HOME/foodmon/backend/venv/bin/pip" ]; then
    cd "$HOME/foodmon/backend"
    source venv/bin/activate
    echo "Checking Flask..."
    pip show flask > /dev/null 2>&1 && echo "✓ Flask installed" || echo "✗ Flask NOT installed"
    echo "Checking paho-mqtt..."
    pip show paho-mqtt > /dev/null 2>&1 && echo "✓ paho-mqtt installed" || echo "✗ paho-mqtt NOT installed"
    echo "Checking flask-socketio..."
    pip show flask-socketio > /dev/null 2>&1 && echo "✓ flask-socketio installed" || echo "✗ flask-socketio NOT installed"
    deactivate
fi
echo ""

# Check 8: File existence
echo "[8] Checking required files..."
FILES=(
    "$HOME/foodmon/backend/app.py"
    "$HOME/foodmon/backend/config.py"
    "$HOME/foodmon/backend/mqtt_handler.py"
    "$HOME/foodmon/backend/ml_engine.py"
    "$HOME/foodmon/backend/actuator_control.py"
)

for file in "${FILES[@]}"; do
    if [ -f "$file" ]; then
        echo "✓ $(basename $file) exists"
    else
        echo "✗ $(basename $file) NOT found"
    fi
done
echo ""

# Check 9: Network connectivity
echo "[9] Checking network..."
echo "Hostname: $(hostname)"
echo "IP Addresses:"
hostname -I
echo ""

# Summary
echo "=========================================="
echo "DIAGNOSTIC SUMMARY"
echo "=========================================="
echo ""
echo "Next steps:"
echo ""

if ! systemctl is-active --quiet mosquitto; then
    echo "1. START MOSQUITTO:"
    echo "   sudo systemctl start mosquitto"
    echo ""
fi

if ! pgrep -f "python3 app.py" > /dev/null; then
    echo "2. START FLASK APP:"
    echo "   cd ~/foodmon/backend"
    echo "   source venv/bin/activate"
    echo "   python3 app.py"
    echo ""
    echo "   Keep this terminal open and running!"
    echo ""
fi

echo "3. VERIFY IN NEW TERMINAL:"
echo "   curl http://localhost:5000/api/foods"
echo ""
echo "4. CHECK MQTT (in new terminal):"
echo "   mosquitto_sub -h localhost -t test"
echo "   (Press Ctrl+C to exit)"
echo ""