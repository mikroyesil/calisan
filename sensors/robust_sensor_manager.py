import logging
import time
import random
import requests
import threading
import json
import os
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)

class CircuitBreaker:
    """Circuit breaker pattern to prevent overwhelming services"""
    
    def __init__(self, failure_threshold=3, reset_timeout=60):
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.open_until = 0
        self.state_lock = threading.RLock()
    
    def is_open(self):
        """Check if circuit is open (service should not be called)"""
        with self.state_lock:
            if time.time() < self.open_until:
                return True
            return False
    
    def record_failure(self):
        """Record a failure, open circuit if threshold reached"""
        with self.state_lock:
            self.failure_count += 1
            if self.failure_count >= self.failure_threshold:
                # Open the circuit
                self.open_until = time.time() + self.reset_timeout
                logger.warning(f"Circuit breaker opened until {time.ctime(self.open_until)}")
                # Double the reset timeout for consecutive opens
                self.reset_timeout = min(600, self.reset_timeout * 2)
                return True
            return False
    
    def record_success(self):
        """Record a successful call, reset failure count"""
        with self.state_lock:
            self.failure_count = 0
            # Reset timeout to original value after success
            self.reset_timeout = 60
            return True


class RobustSensorManager:
    """Sensor manager with offline-first approach and circuit breaker"""
    
    def __init__(self, arduino_ip='192.168.1.100', arduino_port=80, 
                 connection_timeout=8, read_timeout=15, max_retries=2, query_pumps=True):
        """Initialize with offline-first approach"""
        # FIXED: Initialize logger FIRST, before any threads are started
        self.logger = logging.getLogger(__name__)
        
        self.arduino_ip = arduino_ip
        self.arduino_port = arduino_port
        self.arduino_base_url = f"http://{arduino_ip}:{arduino_port}/api"
        self.connected = False
        self.last_sensor_data = {}
        self.cache_file = os.path.join(os.path.dirname(__file__), "sensor_cache.json")
        self._load_cached_data()  # Load cache from disk if available
        
        self.last_successful_read = 0
        self.connection_timeout = connection_timeout
        self.read_timeout = read_timeout  # Increased to 15 seconds
        
        # Request pacing parameters
        self.last_request_time = 0
        self.min_request_interval = 5.0  # Minimum seconds between requests - increased to 5 seconds
        
        # Request deduplication to prevent multiple concurrent requests
        self._request_lock = threading.RLock()
        self._request_in_progress = False
        self._pending_requesters = []
        
        # Circuit breaker for Arduino API
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=3,
            reset_timeout=60
        )
        
        # Create persistent session with retry configuration
        self.session = requests.Session()
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=1.0,  # More aggressive backoff
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"],
            raise_on_status=False
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        
        self.query_pumps = query_pumps  # ADDED: Control whether to query pump endpoints
        
        # Start a thread to periodically check connection if offline (AFTER logger is initialized)
        self._start_reconnect_thread()
    
    def _start_reconnect_thread(self):
        """Start a background thread to attempt reconnection"""
        def reconnect_worker():
            while True:
                # Only try to reconnect if we're disconnected, circuit breaker is closed,
                # and no recent activity (to avoid interfering with active requests)
                if (not self.connected and 
                    not self.circuit_breaker.is_open() and 
                    time.time() - self.last_request_time > 60):  # Wait 60s since last request
                    
                    self.logger.debug("Background thread attempting reconnection...")
                    # Use check_connection to avoid duplicate logic
                    self.check_connection()
                
                # Sleep for 300 seconds (5 minutes) between attempts
                time.sleep(300)
                
        thread = threading.Thread(target=reconnect_worker, daemon=True)
        thread.start()
    
    def _load_cached_data(self):
        """Load cached sensor data from disk"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r') as f:
                    self.last_sensor_data = json.load(f)
                    logger.info(f"Loaded sensor data from cache: {self.cache_file}")
        except Exception as e:
            logger.warning(f"Could not load cached sensor data: {e}")
            # Initialize with safe default values
            self.last_sensor_data = {
                "ph": 6.0,
                "ec": 1.8,
                "temperature": 22.0,
                "timestamp": time.time(),
                "from_cache": True
            }
    
    def _save_cached_data(self):
        """Save current sensor data to disk"""
        try:
            # Add timestamp to the data
            self.last_sensor_data["timestamp"] = time.time()
            with open(self.cache_file, 'w') as f:
                json.dump(self.last_sensor_data, f)
        except Exception as e:
            logger.warning(f"Could not save sensor data to cache: {e}")
    
    def _respect_request_interval(self):
        """Ensure minimum time between requests"""
        now = time.time()
        time_since_last = now - self.last_request_time
        
        if time_since_last < self.min_request_interval:
            sleep_time = self.min_request_interval - time_since_last
            self.logger.debug(f"Rate limiting: sleeping {sleep_time:.2f}s to respect request interval")
            time.sleep(sleep_time)
        
        self.last_request_time = time.time()
    
    def check_connection(self):
        """Check if Arduino API is reachable"""
        # If circuit breaker is open, don't even try
        if self.circuit_breaker.is_open():
            logger.debug("Circuit breaker open, skipping connection check")
            return False
        
        # Respect minimum interval between requests
        self._respect_request_interval()
            
        try:
            # Try a simple health check endpoint first
            response = self.session.get(
                f"{self.arduino_base_url}/sensors",
                timeout=(self.connection_timeout, self.read_timeout)
            )
            
            if response.status_code == 200:
                logger.info(f"Successfully connected to Arduino API")
                self.connected = True
                self.circuit_breaker.record_success()
                return True
            else:
                logger.warning(f"Arduino API responded with status code {response.status_code}")
                self.circuit_breaker.record_failure()
                self.connected = False
                return False
        except Exception as e:
            logger.warning(f"Connection check failed: {str(e)}")
            self.circuit_breaker.record_failure()
            self.connected = False
            return False
    
    def send_command(self, endpoint, data=None, blocking=False):
        """Send command to Arduino API with non-blocking option
        
        Args:
            endpoint (str): API endpoint (e.g., "/pump/nutrient_a/dose")
            data (dict, optional): Data to send with the request
            blocking (bool): Whether to wait for response or run in background thread
            
        Returns:
            dict: Response from Arduino or None on failure
        """
        # If circuit breaker is open, don't even try
        if self.circuit_breaker.is_open():
            logger.debug(f"Circuit breaker open, skipping command: {endpoint}")
            return None
            
        # Extract Arduino IP and port from the base_url
        # Expected format: "http://{arduino_ip}:{arduino_port}/api"
        parts = self.arduino_base_url.split('/')
        if len(parts) >= 3:
            host_part = parts[2].split(':')
            arduino_ip = host_part[0]
            arduino_port = int(host_part[1]) if len(host_part) > 1 else 80
        else:
            # Default fallback
            arduino_ip = '192.168.1.100'
            arduino_port = 80
            
        url = f"{self.arduino_base_url}{endpoint}"
        
        # For non-blocking calls, use a thread
        if not blocking:
            thread = threading.Thread(
                target=self._execute_command,
                args=(url, data)
            )
            thread.daemon = True
            thread.start()
            return {"status": "command_sent", "thread_started": True}
        
        # For blocking calls, execute directly and return result
        return self._execute_command(url, data)
    
    def _execute_command(self, url, data=None):
        """Execute command with rate limiting and circuit breaker logic"""
        # Respect minimum interval between requests
        self._respect_request_interval()
        
        try:
            if data:
                response = self.session.post(url, json=data, 
                                           timeout=(self.connection_timeout, self.read_timeout))
            else:
                response = self.session.get(url, 
                                          timeout=(self.connection_timeout, self.read_timeout))
            
            if response.status_code == 200:
                try:
                    result = response.json()
                    self.circuit_breaker.record_success()
                    return result
                except ValueError:
                    logger.error(f"Invalid JSON response from Arduino: {url}")
                    self.circuit_breaker.record_failure()
                    return None
            else:
                logger.error(f"Arduino command failed with status {response.status_code}: {url}")
                self.circuit_breaker.record_failure()
                return None
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Error sending command to Arduino ({url}): {str(e)}")
            self.circuit_breaker.record_failure()
            return None
    
    def _make_request(self, endpoint):
        """Helper method for making individual sensor requests"""
        # If circuit breaker is open, don't even try
        if self.circuit_breaker.is_open():
            self.logger.debug(f"Circuit breaker open, skipping request: {endpoint}")
            return None
        
        # If not connected, try to connect first
        if not self.connected:
            connected = self.check_connection()
            if not connected:
                return None
        
        # Respect minimum interval between requests
        self._respect_request_interval()
        
        try:
            url = f"{self.arduino_base_url}{endpoint}"
            response = self.session.get(url, timeout=(self.connection_timeout, self.read_timeout))
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    self.circuit_breaker.record_success()
                    return data
                except ValueError:
                    self.logger.warning(f"Invalid JSON response from {endpoint}")
                    self.circuit_breaker.record_failure()
                    return None
            else:
                self.logger.warning(f"Request to {endpoint} failed with status {response.status_code}")
                self.circuit_breaker.record_failure()
                return None
                
        except Exception as e:
            self.logger.warning(f"Error making request to {endpoint}: {e}")
            self.circuit_breaker.record_failure()
            return None

    # FIXED: Remove the duplicate read_all_sensors method and keep only the working one
    def read_all_sensors(self):
        """Read all available sensor data from Arduino with request deduplication"""
        # Check if another request is already in progress
        with self._request_lock:
            if self._request_in_progress:
                # Wait for the in-progress request to complete
                self.logger.debug("Request already in progress, waiting for result...")
                # Return cached data while waiting
                return self.last_sensor_data
            
            # Mark that we're starting a request
            self._request_in_progress = True
        
        try:
            # If circuit breaker is open, don't even try to connect
            if self.circuit_breaker.is_open():
                self.logger.debug("Circuit breaker open, returning cached data")
                return self.last_sensor_data
            
            # Respect minimum interval between requests
            self._respect_request_interval()
            
            # Make a single request that serves both connection check and data retrieval
            response = self.session.get(
                f"{self.arduino_base_url}/sensors", 
                timeout=(self.connection_timeout, self.read_timeout)
            )
            
            if response.status_code == 200:
                # Parse JSON data
                try:
                    data = response.json()
                    
                    # Validate essential data fields
                    if 'ph' in data and 'ec' in data and 'temperature' in data:
                        # Update last known good data and save to cache
                        self.last_sensor_data = data
                        self.last_successful_read = time.time()
                        self.circuit_breaker.record_success()
                        self.connected = True  # Update connection status
                        self._save_cached_data()
                        return data
                    else:
                        self.logger.warning("Incomplete sensor data received")
                        self.circuit_breaker.record_failure()
                        self.connected = False  # Update connection status
                        return self.last_sensor_data
                except ValueError:
                    self.logger.warning("Invalid JSON response")
                    self.circuit_breaker.record_failure()
                    self.connected = False  # Update connection status
                    return self.last_sensor_data
            else:
                self.logger.warning(f"API responded with status code: {response.status_code}")
                self.circuit_breaker.record_failure()
                self.connected = False  # Update connection status
                return self.last_sensor_data
                
        except Exception as e:
            self.logger.warning(f"Error reading sensor data: {str(e)}")
            self.circuit_breaker.record_failure()
            self.connected = False  # Update connection status
            return self.last_sensor_data
        finally:
            # Always reset the request flag
            with self._request_lock:
                self._request_in_progress = False
