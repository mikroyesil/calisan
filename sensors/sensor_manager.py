# controllers/sensor_manager.py - Sensor reading and management

import datetime
import time
import logging
import requests
from requests.adapters import HTTPAdapter
import json
import random  # Still needed for simulated sensors

logger = logging.getLogger(__name__)

# HTTP Session for sensor data fetching optimization
class SensorHTTPManager:
    """Optimized HTTP session for sensor data fetching"""
    
    def __init__(self):
        self.session = requests.Session()
        
        # Configure for sensor data fetching
        adapter = HTTPAdapter(
            pool_connections=1,  # One connection pool for sensor Arduino
            pool_maxsize=3,      # Fewer connections needed for sensors
            max_retries=1
        )
        self.session.mount('http://', adapter)
        
        # Sensor-optimized headers
        self.session.headers.update({
            'Connection': 'keep-alive',
            'Keep-Alive': 'timeout=20, max=50'
        })
        
        logger.debug("Sensor HTTP Manager initialized")
    
    def get(self, url, timeout=3, **kwargs):
        """Make GET request with sensor-optimized session"""
        return self.session.get(url, timeout=timeout, **kwargs)

# Global sensor session manager
_sensor_session = SensorHTTPManager()

class SensorManager:
    def __init__(self, arduino_ip=None, arduino_port=80):
        # Configuration for Arduino WiFi API
        self.arduino_api_url = f"http://{arduino_ip}:{arduino_port}/api" if arduino_ip else None
        self.connected = False
        
        # Atlas Scientific sensor configurations
        self.sensors = {
            'ph': {
                'type': 'atlas_scientific',
                'name': 'pH Sensor',
                'enabled': True,
                'last_reading': 0,
                'last_reading_time': 0
            },
            'ec': {
                'type': 'atlas_scientific',
                'name': 'EC Sensor',
                'enabled': True,
                'last_reading': 0,
                'last_reading_time': 0
            },
            'temperature': {
                'type': 'atlas_scientific',
                'name': 'Temperature Sensor',
                'enabled': True,
                'last_reading': 0,
                'last_reading_time': 0
            },
            'humidity': {
                'type': 'dht22',
                'pin': 4,
                'name': 'Humidity Sensor',
                'enabled': True,
                'last_reading': 0,
                'last_reading_time': 0
            },
            'co2': {
                'type': 'mh_z19',
                'interface': 'uart',
                'name': 'CO2 Sensor',
                'enabled': True,
                'last_reading': 0,
                'last_reading_time': 0
            }
        }
        
        # Maximum age of readings before considering them stale (in seconds)
        self.max_reading_age = 60
        
        # Test Arduino connection if URL is provided
        if self.arduino_api_url:
            self._test_arduino_connection()
    
    def _test_arduino_connection(self):
        """Test connection to Arduino API (OPTIMIZED)"""
        try:
            response = _sensor_session.get(f"{self.arduino_api_url}/sensors", timeout=2)
            if response.status_code == 200:
                self.connected = True
                logger.info(f"Successfully connected to Arduino API at {self.arduino_api_url}")
                return True
            else:
                logger.warning(f"Failed to connect to Arduino API: HTTP {response.status_code}")
                self.connected = False
                return False
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout connecting to Arduino API (2s)")
            self.connected = False
            return False
        except requests.exceptions.ConnectionError:
            logger.warning(f"Connection error to Arduino API")
            self.connected = False
            return False
        except Exception as e:
            logger.warning(f"Error connecting to Arduino API: {e}")
            self.connected = False
            return False
    
    def _fetch_sensor_data_from_arduino(self):
        """Fetch sensor data from Arduino via HTTP (OPTIMIZED)"""
        if not self.arduino_api_url:
            return False
            
        if not self.connected:
            if not self._test_arduino_connection():
                return False
                
        try:
            response = _sensor_session.get(f"{self.arduino_api_url}/sensors", timeout=2)
            if response.status_code == 200:
                data = response.json()
                current_time = time.time()
                
                # Update sensor readings
                for sensor_id in ['ph', 'ec', 'temperature']:
                    if sensor_id in data and self.sensors[sensor_id]['enabled']:
                        self.sensors[sensor_id]['last_reading'] = float(data[sensor_id])
                        self.sensors[sensor_id]['last_reading_time'] = current_time
                        
                return True
            else:
                logger.warning(f"Failed to fetch sensor data: HTTP {response.status_code}")
                return False
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout fetching sensor data (2s)")
            self.connected = False
            return False
        except requests.exceptions.ConnectionError:
            logger.warning(f"Connection error fetching sensor data")
            self.connected = False
            return False
        except Exception as e:
            logger.warning(f"Error fetching sensor data: {e}")
            self.connected = False
            return False
    
    def read_sensor(self, sensor_id):
        """Read a specific sensor and return the value"""
        if sensor_id not in self.sensors or not self.sensors[sensor_id]['enabled']:
            return None
        
        config = self.sensors[sensor_id]
        current_time = time.time()
        
        # For Arduino-based sensors, check if we need to fetch new data
        if sensor_id in ['ph', 'ec', 'temperature']:
            # If data is old or we don't have a reading yet
            if self.arduino_api_url and (current_time - config['last_reading_time'] > 5):
                self._fetch_sensor_data_from_arduino()
            
            # If we have a recent reading, return it
            if current_time - config['last_reading_time'] < self.max_reading_age:
                return config['last_reading']
        
        # For other sensors or if Arduino not connected, use simulation
        try:
            if sensor_id == 'ph':
                reading = 6.0 + random.uniform(-0.3, 0.3)
            elif sensor_id == 'ec':
                reading = 1.2 + random.uniform(-0.2, 0.2)
            elif sensor_id == 'temperature':
                reading = 20.0 + random.uniform(-2.0, 2.0)
            elif sensor_id == 'humidity':
                reading = 65.0 + random.uniform(-10.0, 10.0)
            elif sensor_id == 'co2':
                reading = 800.0 + random.uniform(-200.0, 200.0)
            else:
                reading = 0
            
            # Update the sensor's last reading info
            config['last_reading'] = reading
            config['last_reading_time'] = current_time
            
            return reading
        
        except Exception as e:
            logger.error(f"Error reading sensor {sensor_id}: {str(e)}")
            return None
    
    def read_all_sensors(self):
        """Read all enabled sensors and return their values"""
        # Try to update Arduino-based sensors
        if self.arduino_api_url:
            self._fetch_sensor_data_from_arduino()
        
        readings = {}
        for sensor_id in self.sensors:
            if self.sensors[sensor_id]['enabled']:
                reading = self.read_sensor(sensor_id)
                if reading is not None:
                    readings[sensor_id] = reading
        
        # Add timestamp
        readings['timestamp'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        return readings
        
    def control_pump(self, pump_id, state, duration=0):
        """Control Atlas Scientific peristaltic pump via Arduino API"""
        if not self.arduino_api_url:
            logger.error("Cannot control pump: Arduino API URL not configured")
            return False
            
        if not self.connected:
            if not self._test_arduino_connection():
                return False
                
        try:
            data = {
                "pump": pump_id,
                "state": state,
                "duration": duration
            }
            
            response = requests.post(
                f"{self.arduino_api_url}/pump",
                json=data,
                timeout=5  # Longer timeout for pump operations
            )
            
            if response.status_code == 200:
                result = response.json()
                return result.get('success', False)
            else:
                logger.error(f"Pump control failed: HTTP {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"Error controlling pump via API: {e}")
            self.connected = False
            return False
