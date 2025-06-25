#!/bin/bash
# install.sh - Installation script for Vertical Farm Control System

echo "===== Vertical Farm Control System Installer ====="
echo "Setting up virtual environment..."

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "Python 3 is required but not installed. Please install Python 3 first."
    exit 1
fi

# Remove existing virtual environment if it exists
if [ -d "venv" ]; then
    echo "Removing existing virtual environment..."
    rm -rf venv
fi

# Create fresh virtual environment
echo "Creating new virtual environment..."
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Install basic requirements first
echo "Installing basic dependencies..."
python3 -m pip install --upgrade pip
python3 -m pip install --upgrade setuptools wheel

# Install essential modules for the application
echo "Installing essential Python modules..."
python3 -m pip install flask flask-socketio
python3 -m pip install requests
python3 -m pip install pytz
python3 -m pip install apscheduler
python3 -m pip install sqlite3 || echo "sqlite3 is built-in to Python"

# Create Arduino connectivity test tool
echo "Creating Arduino connectivity test tool..."
cat > test_arduino_connection.py << EOL
#!/usr/bin/env python3
"""Test Arduino connectivity"""

import requests
import time
import sys
import argparse

def test_connection(host="192.168.1.100", port=80, endpoint="/api/sensors", retries=3, timeout=5):
    """Test connection to Arduino"""
    url = f"http://{host}:{port}{endpoint}"
    
    print(f"Testing connection to {url} with {retries} retries and {timeout}s timeout")
    
    success = False
    
    for i in range(retries):
        try:
            print(f"Attempt {i+1}/{retries}...")
            response = requests.get(url, timeout=timeout)
            print(f"  Status: {response.status_code}")
            
            if response.status_code == 200:
                print("  Response data:")
                print(f"  {response.text.strip()}")
                success = True
                break
                
        except requests.exceptions.ConnectTimeout:
            print("  ERROR: Connection timeout")
        except requests.exceptions.ReadTimeout:
            print("  ERROR: Read timeout")
        except requests.exceptions.ConnectionError as e:
            print(f"  ERROR: Connection error: {e}")
        except Exception as e:
            print(f"  ERROR: {e}")
            
        # Wait before retrying
        if i < retries - 1:
            print(f"  Waiting 2 seconds before next attempt...")
            time.sleep(2)
    
    if success:
        print("\nConnection SUCCESSFUL!")
        return 0
    else:
        print("\nConnection FAILED!")
        return 1

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Arduino connectivity")
    parser.add_argument("--host", default="192.168.1.100", help="Arduino IP address")
    parser.add_argument("--port", type=int, default=80, help="Arduino port")
    parser.add_argument("--retries", type=int, default=3, help="Number of connection attempts")
    parser.add_argument("--timeout", type=int, default=5, help="Connection timeout in seconds")
    
    args = parser.parse_args()
    sys.exit(test_connection(
        host=args.host,
        port=args.port,
        retries=args.retries,
        timeout=args.timeout
    ))
EOL

chmod +x test_arduino_connection.py

# Check if running on macOS (has brew)
if command -v brew &> /dev/null; then
    # Install numpy using brew (for macOS compatibility)
    echo "Installing numpy using brew..."
    brew install numpy
    
    # Install requirements without numpy (create a temporary requirements file)
    echo "Installing project dependencies..."
    grep -v "numpy" requirements.txt > requirements_temp.txt
    python3 -m pip install -r requirements_temp.txt
    rm requirements_temp.txt
    
    # Link brew's numpy to the virtual environment
    python3 -m pip install numpy --no-binary :all:
else
    # Install all requirements directly
    echo "Installing all project dependencies..."
    python3 -m pip install -r requirements.txt
fi

# Install the package in development mode
echo "Installing package in development mode..."
python3 -m pip install -e .

# Check if running on Raspberry Pi
if [ -f "/etc/rpi-issue" ] || grep -q "Raspberry Pi" /proc/cpuinfo; then
    echo "Raspberry Pi detected. Installing RPi.GPIO..."
    python3 -m pip install RPi.GPIO
    
    echo "Setting up directories..."
    mkdir -p data logs
    chmod 755 data logs
    
    # Create systemd service file
    echo "Creating systemd service file..."
    cat > vertical-farm.service << EOL
[Unit]
Description=Vertical Farm Control System
After=network.target

[Service]
User=$(whoami)
WorkingDirectory=$(pwd)
ExecStart=$(which python3) app.py
Restart=always
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOL
    
    echo "To install the service, run:"
    echo "sudo cp vertical-farm.service /etc/systemd/system/"
    echo "sudo systemctl enable vertical-farm.service"
    echo "sudo systemctl start vertical-farm.service"
else
    echo "This does not appear to be a Raspberry Pi."
    echo "The system will still work, but GPIO functionality will be limited."
fi

# Create run script
echo "Creating run script..."
cat > run.sh << EOL
#!/bin/bash
source venv/bin/activate
python3 app.py
EOL

chmod +x run.sh

echo "===== Installation Complete ====="
echo "To start the application, run: ./run.sh"
echo "To test Arduino connectivity: python3 test_arduino_connection.py"
echo "Then navigate to http://localhost:5000 in your web browser."
