# controllers/sensor_manager.py - Sensor reading and management

import datetime
import time
import random  # Used for simulation when Arduino not available
import logging
import requests  # Added for HTTP requests to Arduino API
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import socket

logger = logging.getLogger(__name__)

class SensorManager:
    def __init__(self, arduino_ip="192.168.1.107", arduino_port=80, connection_timeout=5, read_timeout=10, max_retries=3):
        # Configuration for Arduino WiFi API - default IP set to 192.168.1.107
        # Make sure we're always using the correct IP address
        self.arduino_ip = "192.168.1.107"  # Hardcoded to ensure correct IP
        self.arduino_port = arduino_port
        # We now know the correct API endpoint format
        self.arduino_api_url = f"http://{self.arduino_ip}/api" if self.arduino_ip else None
        self.connected = False
        self.last_connection_attempt = 0
        self.connection_retry_interval = 30  # seconds between connection retries
        
        # Request timeout configuration
        self.connection_timeout = connection_timeout
        self.read_timeout = read_timeout
        self.max_retries = max_retries
        
        # Circuit breaker pattern variables
        self.circuit_breaker_open = False
        self.circuit_breaker_open_until = 0
        self.circuit_breaker_fail_count = 0
        self.circuit_breaker_threshold = 3  # Number of failures before opening circuit
        self.circuit_breaker_cooldown = 60  # Base cooldown period in seconds (will be multiplied by # of failures)
        
        # Setup retry strategy for requests
        self.session = self._create_robust_session()
        
        # Atlas Scientific sensor configurations
        self.sensors = {
            'ph': {
                'type': 'atlas_scientific',
                'address': 99,  # I2C address
                'interface': 'i2c',
                'name': 'pH Sensor',
                'enabled': True,
                'last_reading': 0,
                'last_reading_time': 0
            },
            'ec': {
                'type': 'atlas_scientific',
                'address': 100,  # I2C address
                'interface': 'i2c',
                'name': 'EC Sensor',
                'enabled': True,
                'last_reading': 0,
                'last_reading_time': 0
            },
            'temperature': {
                'type': 'atlas_scientific',
                'address': 102,  # I2C address
                'interface': 'i2c',
                'name': 'Temperature Sensor',
                'enabled': True,
                'last_reading': 0,
                'last_reading_time': 0
            },
            'humidity': {
                'type': 'atlas_scientific',  # Changed from dht22 to atlas_scientific
                'address': 111,  # EZO-HUM I2C address
                'interface': 'i2c',  # Changed from pin to i2c interface
                'name': 'EZO-HUM Humidity Sensor',
                'enabled': True,
                'last_reading': 0,
                'last_reading_time': 0
            },
            'co2': {
                'type': 'atlas_scientific',  # Changed from mh_z19 to atlas_scientific
                'address': 105,  # I2C address for CO2 sensor
                'interface': 'i2c',  # Changed from uart to i2c
                'name': 'CO2 Sensor',
                'enabled': True,
                'last_reading': 0,
                'last_reading_time': 0
            }
        }
        
        # Store device states separately
        self.devices = {
            'fans': {'state': False},
            'co2': {'state': False},
            'humidifier': {'state': False},
            'dehumidifier': {'state': False}
        }
        
        # Store last successful sensor readings to use as fallback
        self.last_successful_readings = {}
        
        # Maximum age of readings before considering them stale (in seconds)
        self.max_reading_age = 60
        
        # Log the Arduino IP we're using
        logger.info(f"Initializing SensorManager with Arduino IP: {self.arduino_ip}")
        
        # Test Arduino connection if URL is provided
        if self.arduino_api_url:
            self._test_arduino_connection()
        else:
            logger.info("No Arduino IP configured, running in simulation mode")
    
    def _create_robust_session(self):
        """Create a requests session with retry capabilities"""
        session = requests.Session()
        
        # Configure retry strategy
        retry_strategy = Retry(
            total=self.max_retries,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"]
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        return session
    
    def _is_circuit_breaker_open(self):
        """Check if the circuit breaker is currently open"""
        if self.circuit_breaker_open and time.time() < self.circuit_breaker_open_until:
            return True
        
        # Reset circuit breaker if cooldown period has passed
        if self.circuit_breaker_open and time.time() >= self.circuit_breaker_open_until:
            logger.info("Circuit breaker reset after cooldown period")
            self.circuit_breaker_open = False
            self.circuit_breaker_fail_count = 0
        
        return False
    
    def _record_connection_failure(self):
        """Record a connection failure and possibly open the circuit breaker"""
        self.circuit_breaker_fail_count += 1
        
        # If we've reached the threshold, open the circuit breaker
        if self.circuit_breaker_fail_count >= self.circuit_breaker_threshold:
            # Use exponential backoff for cooldown period
            cooldown_period = self.circuit_breaker_cooldown * (2 ** (min(self.circuit_breaker_fail_count - self.circuit_breaker_threshold, 5)))
            self.circuit_breaker_open_until = time.time() + cooldown_period
            self.circuit_breaker_open = True
            
            # Log when the circuit breaker will be reset
            reset_time = datetime.datetime.fromtimestamp(self.circuit_breaker_open_until)
            logger.warning(f"Circuit breaker opened until {reset_time.strftime('%a %b %d %H:%M:%S %Y')}")
    
    def _is_host_reachable(self):
        """Check if the host is reachable using a simple socket connection"""
        # Check if circuit breaker is open
        if self._is_circuit_breaker_open():
            return False
            
        if not self.arduino_ip:
            return False
            
        try:
            # Try to establish a socket connection to check if host is reachable
            with socket.create_connection((self.arduino_ip, self.arduino_port), timeout=2) as sock:
                return True
        except (socket.timeout, socket.error, ConnectionRefusedError):
            return False
        except Exception as e:
            logger.error(f"Unexpected error checking host reachability: {e}")
            return False
    
    def _test_arduino_connection(self):
        """Test connection to Arduino API with improved error handling"""
        # Check if circuit breaker is open
        if self._is_circuit_breaker_open():
            return False
            
        current_time = time.time()
        
        # Limit connection attempts to avoid repeated failures
        if current_time - self.last_connection_attempt < self.connection_retry_interval and self.last_connection_attempt > 0:
            return self.connected
            
        self.last_connection_attempt = current_time
        
        # First check if the host is even reachable
        if not self._is_host_reachable():
            if self.connected:  # Only log if state changes
                logger.warning(f"Arduino host {self.arduino_ip} is not reachable")
                self.connected = False
            
            # Record connection failure for circuit breaker
            self._record_connection_failure()
            return False
            
        try:
            # Use the known correct endpoint and IP
            logger.debug(f"Testing connection to Arduino API at {self.arduino_ip}")
            # Explicitly use the correct IP address
            response = self.session.get(
                f"http://192.168.1.107/api/sensors", 
                timeout=(self.connection_timeout, self.read_timeout)
            )
            
            if response.status_code == 200:
                if not self.connected:  # Only log if state changes
                    logger.info(f"Successfully connected to Arduino API at {self.arduino_ip}")
                    # Set the correct API base URL
                    self.arduino_api_url = f"http://192.168.1.107/api"
                    logger.info(f"Using API base URL: {self.arduino_api_url}")
                self.connected = True
                
                # Reset circuit breaker on successful connection
                self.circuit_breaker_fail_count = 0
                
                return True
            else:
                logger.error(f"Failed to connect to Arduino API: HTTP {response.status_code}")
                self.connected = False
                
                # Record connection failure for circuit breaker
                self._record_connection_failure()
                return False
                
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Connection error to Arduino API: {e}")
            self.connected = False
            
            # Record connection failure for circuit breaker
            self._record_connection_failure()
            return False
        except requests.exceptions.Timeout as e:
            logger.warning(f"Timeout connecting to Arduino API: {e}")
            self.connected = False
            
            # Record connection failure for circuit breaker
            self._record_connection_failure()
            return False
        except Exception as e:
            logger.error(f"Unexpected error connecting to Arduino API: {e}")
            self.connected = False
            
            # Record connection failure for circuit breaker
            self._record_connection_failure()
            return False
    
    def _fetch_sensor_data_from_arduino(self):
        """Fetch sensor data from Arduino with improved debugging"""
        # Check if circuit breaker is open
        if self._is_circuit_breaker_open():
            logger.debug("Circuit breaker open, skipping Arduino fetch")
            return False
        
        if not self.arduino_api_url:
            logger.debug("No Arduino API URL configured")
            return False
        
        # Ensure we're connected
        if not self.connected:
            connected = self._test_arduino_connection()
            if not connected:
                logger.warning("Arduino connection test failed - unable to reach Arduino at 192.168.1.107")
                return False
        
        try:
            # Use explicit IP to ensure consistency
            url = f"http://192.168.1.107/api/sensors"
            
            # Try a very basic socket connection before doing a full HTTP request
            try:
                with socket.create_connection((self.arduino_ip, self.arduino_port), timeout=1) as sock:
                    logger.info(f"Socket connection to Arduino at {self.arduino_ip}:{self.arduino_port} successful")
            except Exception as socket_err:
                logger.error(f"Socket connection to Arduino failed: {socket_err}")
                self._record_connection_failure()
                return False
            
            logger.info(f"Fetching sensor data from Arduino: {url}")
            fetch_start = time.time()
            
            response = self.session.get(
                url, 
                timeout=(self.connection_timeout, self.read_timeout)
            )
            
            fetch_time = time.time() - fetch_start
            logger.info(f"Arduino API fetch completed in {fetch_time*1000:.0f}ms with status {response.status_code}")
            
            if response.status_code == 200:
                # Log the raw response
                raw_response = response.text
                logger.info(f"Raw Arduino response ({len(raw_response)} bytes): {raw_response[:100]}")
                
                try:
                    data = response.json()
                    logger.info(f"Arduino data parsed successfully: {data}")
                    
                    # Log raw values from JSON
                    logger.info(f"Raw temperature value: {data.get('temperature')}")
                    logger.info(f"Raw humidity value: {data.get('humidity')}")
                    logger.info(f"Raw CO2 value: {data.get('co2')}")
                    
                    # Process the sensor data with explicit null handling
                    now = time.time()
                    
                    # Handle all sensors consistently with minimal processing
                    # Just directly set the values from JSON, handle None values
                    if 'temperature' in data and data['temperature'] is not None:
                        self.sensors['temperature']['last_reading'] = float(data['temperature'])
                        self.sensors['temperature']['last_reading_time'] = now
                    
                    if 'ph' in data and data['ph'] is not None:
                        self.sensors['ph']['last_reading'] = float(data['ph'])
                        self.sensors['ph']['last_reading_time'] = now
                    
                    if 'ec' in data and data['ec'] is not None:
                        self.sensors['ec']['last_reading'] = float(data['ec'])
                        self.sensors['ec']['last_reading_time'] = now
                    
                    # Direct humidity processing without special handling or sanity checks
                    if 'humidity' in data and data['humidity'] is not None:
                        self.sensors['humidity']['last_reading'] = float(data['humidity'])
                        self.sensors['humidity']['last_reading_time'] = now
                        logger.info(f"Set humidity value: {self.sensors['humidity']['last_reading']}")
                    
                    if 'co2' in data and data['co2'] is not None:
                        self.sensors['co2']['last_reading'] = float(data['co2'])
                        self.sensors['co2']['last_reading_time'] = now
                    
                    # Update device states if available
                    if 'devices' in data:
                        for device, state in data['devices'].items():
                            if device in self.devices:
                                self.devices[device] = state
                    
                    return True
                except ValueError as e:
                    logger.error(f"Failed to parse Arduino response as JSON: {e}")
                    self._record_connection_failure()
                    return False
            else:
                logger.error(f"Failed to fetch sensor data: HTTP {response.status_code}, Response: {response.text[:100]}")
                self._record_connection_failure()
                return False
        
        except Exception as e:
            logger.error(f"Error fetching sensor data: {e}")
            self._record_connection_failure()
            self.connected = False
            return False

    def read_sensor(self, sensor_id):
        """Read a specific sensor and return the value"""
        if sensor_id not in self.sensors or not self.sensors[sensor_id]['enabled']:
            return None
        
        config = self.sensors[sensor_id]
        current_time = time.time()
        
        # For Arduino-based sensors, check if we need to fetch new data
        if sensor_id in ['ph', 'ec', 'temperature', 'humidity', 'co2']:
            # If data is old or we don't have a reading yet
            if self.arduino_api_url and (current_time - config['last_reading_time'] > 5):
                self._fetch_sensor_data_from_arduino()
            
            # If we have a recent reading, return it
            if current_time - config['last_reading_time'] < self.max_reading_age:
                return config['last_reading']
            else:
                return None  # Return None instead of simulating data
        
        # For other sensors, check if we can get a reading from Arduino
        # If Arduino not connected, return None instead of simulating
        if self.arduino_api_url and self.connected:
            # Try to use last reading if recent
            if current_time - config['last_reading_time'] < self.max_reading_age:
                return config['last_reading']
            else:
                return None  # Return None if we don't have recent data
        else:
            return None  # Return None instead of simulating

    def read_all_sensors(self):
        """Read all enabled sensors with improved reliability and caching"""
        start_time = time.time()
        
        # If circuit breaker is open, use cached data
        if self._is_circuit_breaker_open() and self.last_successful_readings:
            logger.debug("Using cached sensor readings due to open circuit breaker")
            readings = self.last_successful_readings.copy()
            readings['timestamp'] = time.time()
            readings['cached'] = True
            return readings
        
        # Always try to fetch fresh data from Arduino first
        fetch_success = False
        if self.arduino_api_url:
            fetch_success = self._fetch_sensor_data_from_arduino()
        
        # If Arduino fetch was successful, build readings from updated sensor values
        if fetch_success:
            readings = {}
            current_time = time.time()
            
            # Include all sensor readings that are recent enough
            for sensor_id, config in self.sensors.items():
                if config['enabled'] and current_time - config['last_reading_time'] < self.max_reading_age:
                    readings[sensor_id] = config['last_reading']
            
            # Add device states if available
            if self.devices:
                readings['devices'] = self.devices
            
            # Add timestamp
            readings['timestamp'] = current_time
            readings['fetch_time_ms'] = int((time.time() - start_time) * 1000)
            
            # Cache these successful readings
            if readings and len(readings) > 1:  # More than just the timestamp
                self.last_successful_readings = readings.copy()
                
            return readings
        
        # If fetching failed but we have cached data, use it
        elif self.last_successful_readings:
            logger.warning("Failed to fetch new data, using cached sensor readings")
            readings = self.last_successful_readings.copy()
            readings['timestamp'] = time.time()
            readings['cached'] = True
            return readings
        
        # If all else fails, return empty dict (no data available)
        return {}

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
            
            response = self.session.post(
                f"{self.arduino_api_url}/pump",
                json=data,
                timeout=(self.connection_timeout, self.read_timeout + duration)  # Longer timeout for pump operations
            )
            
            if response.status_code == 200:
                result = response.json()
                return result.get('success', False)
            else:
                logger.error(f"Pump control failed: HTTP {response.status_code}")
                return False
                
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Connection error controlling pump: {e}")
            self.connected = False
            return False
        except requests.exceptions.Timeout as e:
            logger.warning(f"Timeout controlling pump: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error controlling pump: {e}")
            self.connected = False
            return False
