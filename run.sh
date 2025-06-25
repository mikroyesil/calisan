#!/bin/bash

echo "Starting Vertical Farm Control System..."

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "Virtual environment not found. Please run ./install.sh first."
    exit 1
fi

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Check if Python can import required modules
echo "Checking dependencies..."
python3 -c "import flask, flask_socketio, pytz, apscheduler; print('All dependencies OK')" || {
    echo "Missing dependencies. Please run: pip install -r requirements.txt"
    exit 1
}

# Check if port 5000 is in use and suggest disabling AirPlay
if lsof -Pi :5000 -sTCP:LISTEN -t >/dev/null ; then
    echo "WARNING: Port 5000 is in use (likely by macOS AirPlay Receiver)"
    echo "The app will automatically use port 5001 instead"
    echo "To free port 5000: System Preferences -> Sharing -> Disable 'AirPlay Receiver'"
fi

# Set environment variables
export FLASK_ENV=development
export PYTHONPATH="${PWD}:${PYTHONPATH}"

echo "Starting Flask application..."
echo "The application will be accessible at:"
echo "  - http://localhost:5000 (primary)"
echo "  - http://127.0.0.1:5000 (alternative)"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

# Run the application
python3 app.py
