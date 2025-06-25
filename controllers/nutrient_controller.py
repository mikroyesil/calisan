# controllers/nutrient_controller.py - Improved nutrient dosing system

import time
import datetime
import logging
import threading
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

class NutrientController:
    def __init__(self, db, socketio, sensor_manager=None):
        self.db = db
        self.socketio = socketio
        self.sensor_manager = sensor_manager  # FIXED: Can be None now
        
        # FIXED: Add logger initialization
        self.logger = logging.getLogger(__name__)
        
        # Nutrient targets
        self.ec_target = 1.5  # Target EC (mS/cm)
        self.ph_target = 6.0  # Target pH
        self.ec_tolerance = 0.1  # EC tolerance range (±)
        self.ph_tolerance = 0.2  # pH tolerance range (±)
        
        # Control flags
        self.auto_nutrient = True  # Auto EC dosing enabled
        self.auto_ph = True        # Auto pH dosing enabled
        
        # Pump configuration
        self.pumps = {
            'nutrient_a': {
                'name': 'Nutrient A Pump',
                'state': False,
                'last_dose_time': 0,
                'daily_total_ml': 0.0,
                'flow_rate_ml_per_sec': 1.75  # Fixed flow rate
            },
            'nutrient_b': {
                'name': 'Nutrient B Pump',
                'state': False,
                'last_dose_time': 0,
                'daily_total_ml': 0.0,
                'flow_rate_ml_per_sec': 1.75  # Fixed flow rate
            },
            'ph_up': {
                'name': 'pH Up Pump',
                'state': False,
                'last_dose_time': 0,
                'daily_total_ml': 0.0,
                'flow_rate_ml_per_sec': 1.75  # Fixed flow rate
            },
            'ph_down': {
                'name': 'pH Down Pump',
                'state': False,
                'last_dose_time': 0,
                'daily_total_ml': 0.0,
                'flow_rate_ml_per_sec': 1.75  # Fixed flow rate
            }
        }
        
        # Dosing status
        self.currently_dosing = False
        self.dose_end_time = 0
        self.active_pump = None
        
        # Last update time
        self.last_update_time = 0
        self.last_check_time = 0
        self.last_dose_time = 0  # Add missing initialization
        
        # Load settings from database
        self.settings_cache = None  # Cache for settings
        self.dosing_state_cache = None  # Cache for dosing state
        self.last_sync_time = 0  # Last sync with database
        
        # Add threading lock for dosing operations
        self.dosing_lock = threading.Lock()

        # Fixed dose amount for Nutrient A and B
        self.nutrient_a_b_dose = 10.0

        # Add mock mode for testing when pumps are offline
        self.mock_mode = False
        
        # Arduino API endpoint format - can be easily changed if API changes
        self.pump_endpoint_format = "/pumps/{pump_id}"  # Update this to match your Arduino API
        self.dose_action = "dose"  # The action suffix for dosing
        self.status_action = "status"  # The action suffix for status check

    def sync_with_database(self):
        """Periodically sync settings and dosing state with the database"""
        now = time.time()
        if now - self.last_sync_time > 300:  # Sync every 5 minutes
            self.settings_cache = self.db.get_nutrient_settings()
            self.dosing_state_cache = self.db.get_nutrient_dosing_state()
            self.last_sync_time = now

    def validate_sensor_data(self, sensor_data):
        """Centralized validation for sensor data"""
        if not sensor_data:
            return None
        ec = sensor_data.get('ec')
        ph = sensor_data.get('ph')
        if ec is None or ph is None:
            logger.warning("Missing EC or pH data")
            return None
        return {'ec': ec, 'ph': ph}

    def get_pump_endpoint(self, pump_id, action=None):
        """Generate the proper API endpoint for a pump action
        
        This centralizes endpoint generation to make future API changes easier
        """
        endpoint = self.pump_endpoint_format.format(pump_id=pump_id)
        if action:
            endpoint = f"{endpoint}/{action}"
        return endpoint

    def dose(self, pump_id, amount_ml=None):
        """Unified dosing method for all types of pumps"""
        if pump_id not in self.pumps:
            logger.error(f"Invalid pump ID: {pump_id}")
            return False

        # Use fixed dose for Nutrient A and B
        if pump_id in ['nutrient_a', 'nutrient_b']:
            amount_ml = self.nutrient_a_b_dose
            
        with self.dosing_lock:  # Use thread lock for safety
            # Set dosing status
            self.currently_dosing = True
            self.active_pump = pump_id
            
            # Calculate dosing duration based on flow rate
            duration = amount_ml / self.pumps[pump_id]['flow_rate_ml_per_sec']
            self.dose_end_time = time.time() + duration
            
            # Start dosing
            self.pumps[pump_id]['state'] = True
            current_time = time.time()
            self.pumps[pump_id]['last_dose_time'] = current_time
            self.last_dose_time = current_time  # Update last dose time for cooldown
            
            # IMPORTANT CHANGE: Don't update totals or log success until we confirm pump activation
            # We'll store the amount for later but not commit it yet
            pending_amount = amount_ml
            
            # Check Arduino connection status BEFORE attempting to dose
            arduino_connected = hasattr(self.sensor_manager, 'connected') and self.sensor_manager.connected
            
            # Notify clients that dosing is being attempted (not yet confirmed)
            self.socketio.emit('dosing_attempt', {
                'pump': pump_id,
                'name': self.pumps[pump_id]['name'],
                'amount_ml': pending_amount,
                'timestamp': current_time
            })
            
            # First check actual Arduino connectivity (more reliable than mock_mode)
            if not arduino_connected or self.sensor_manager.circuit_breaker.is_open():
                logger.warning(f"Arduino is offline or circuit breaker open. Cannot dose {pump_id}.")
                
                # Notify clients of offline status
                self.socketio.emit('dosing_error', {
                    'pump': pump_id,
                    'message': 'Arduino is offline. Cannot operate pumps.'
                })
                
                # Reset states
                self.pumps[pump_id]['state'] = False
                self.currently_dosing = False
                self.active_pump = None
                return False
            
            # Check if we're in mock mode (manual override)
            if self.mock_mode:
                logger.warning(f"Pumps are offline. Cannot dose {pump_id}.")
                
                # Notify clients of offline status
                self.socketio.emit('dosing_error', {
                    'pump': pump_id,
                    'message': 'Pumps are offline. Check Arduino connection.'
                })
                
                # Reset states
                self.pumps[pump_id]['state'] = False
                self.currently_dosing = False
                self.active_pump = None
                return False
                
            # Actual pump control via Arduino
            try:
                # Send command to Arduino to activate the pump - use non-blocking mode
                arduino_response = self.sensor_manager.send_command(
                    self.get_pump_endpoint(pump_id, self.dose_action), 
                    {
                        "duration_ms": int(duration * 1000),
                        "amount_ml": pending_amount
                    },
                    blocking=False  # Use non-blocking mode
                )
                
                if not arduino_response:
                    logger.error(f"Failed to connect to pump {pump_id}: Arduino not responding")
                    self.pumps[pump_id]['state'] = False
                    self.currently_dosing = False
                    self.active_pump = None
                    
                    # Notify clients of the failure
                    self.socketio.emit('dosing_error', {
                        'pump': pump_id,
                        'message': 'Failed to connect to pump - Arduino not responding'
                    })
                    return False
                
                if not arduino_response.get('status') == 'command_sent':
                    logger.error(f"Failed to activate pump {pump_id}: {arduino_response}")
                    self.pumps[pump_id]['state'] = False
                    self.currently_dosing = False
                    self.active_pump = None
                    
                    # Notify clients of the failure
                    self.socketio.emit('dosing_error', {
                        'pump': pump_id,
                        'message': f"Pump error: {arduino_response.get('message', 'Unknown error')}"
                    })
                    return False
                
                # NOW we can update totals and log success since Arduino confirmed command receipt
                self.pumps[pump_id]['daily_total_ml'] += pending_amount
                logger.info(f"Dosed {pending_amount}ml using {self.pumps[pump_id]['name']}")
                
                # Log to database
                try:
                    self.db.log_dosing_event(pump_id, pending_amount, current_time)
                except Exception as e:
                    logger.error(f"Failed to log dosing event: {str(e)}")
                
                logger.info(f"Started pump {pump_id} in non-blocking mode, waiting for completion")
                
                # For non-blocking operation, we'll set up a timer to check status periodically
                def check_pump_status():
                    try:
                        status_response = self.sensor_manager.send_command(self.get_pump_endpoint(pump_id, self.status_action), blocking=True)
                        if status_response and status_response.get('state') == 'idle':
                            # Pump is done
                            self.pumps[pump_id]['state'] = False
                            self.currently_dosing = False
                            self.active_pump = None
                            self.socketio.emit('dosing_complete', {'pump': pump_id})
                    except Exception as e:
                        logger.error(f"Error checking pump status: {str(e)}")
                        # Assume pump has completed after timeout 
                        self.pumps[pump_id]['state'] = False
                        self.currently_dosing = False
                        self.active_pump = None
                
                # Set a timer to check status after expected duration
                # This avoids blocking the main thread
                timer = threading.Timer(duration * 1.2, check_pump_status)
                timer.daemon = True
                timer.start()
                
                return True
                    
            except Exception as e:
                logger.error(f"Error during pump control: {str(e)}")
                # Clean up state
                self.pumps[pump_id]['state'] = False
                self.currently_dosing = False
                self.active_pump = None
                
                # Notify clients of the error
                self.socketio.emit('dosing_error', {
                    'pump': pump_id,
                    'message': f"Error: {str(e)}"
                })
                
                return False

    def check_and_adjust_levels(self, sensor_data):
        """Check and adjust nutrient and pH levels"""
        current_time = time.time()
        
        # Don't dose if we're currently dosing or in cooldown period
        if self.currently_dosing or (current_time - self.last_dose_time < 300):  # 5 minute cooldown
            return  # Skip adjustment if we dosed recently
        
        validated_data = self.validate_sensor_data(sensor_data)
        if not validated_data:
            return

        ec = validated_data['ec']
        ph = validated_data['ph']

        # Only proceed if auto controls are enabled
        if self.auto_nutrient:
            # EC control - dose nutrients if too low, notify if too high
            if ec < self.ec_target - self.ec_tolerance:
                self.dose('nutrient_a')
                self.dose('nutrient_b')
            elif ec > self.ec_target + self.ec_tolerance:
                logger.warning(f"EC too high: {ec} mS/cm (target: {self.ec_target}±{self.ec_tolerance})")
                self.socketio.emit('nutrient_warning', {
                    'type': 'ec_high',
                    'value': ec,
                    'target': self.ec_target,
                    'message': f"EC level too high: {ec} mS/cm"
                })

        # pH control - only if auto pH is enabled
        if self.auto_ph:
            # Adjust pH down if too high
            if ph > self.ph_target + self.ph_tolerance:
                self.dose('ph_down', min(5.0, (ph - self.ph_target) * 3.0))

    def update(self, sensor_data=None):
        """Update nutrient controller state"""
        self.sync_with_database()
        if sensor_data is not None:
            self.check_and_adjust_levels(sensor_data)

    def get_settings(self):
        """Get current nutrient settings"""
        return {
            'ec_target': self.ec_target,
            'ph_target': self.ph_target,
            'ec_tolerance': self.ec_tolerance,
            'ph_tolerance': self.ph_tolerance,
            'auto_nutrient': self.auto_nutrient,
            'auto_ph': self.auto_ph
        }
        
    def update_settings(self, settings):
        """Update nutrient controller settings"""
        try:
            if 'ec_target' in settings:
                self.ec_target = float(settings['ec_target'])
            if 'ph_target' in settings:
                self.ph_target = float(settings['ph_target'])
            if 'ec_tolerance' in settings:
                self.ec_tolerance = float(settings['ec_tolerance'])
            if 'ph_tolerance' in settings:
                self.ph_tolerance = float(settings['ph_tolerance'])
            if 'auto_nutrient' in settings:
                self.auto_nutrient = bool(settings['auto_nutrient'])
            if 'auto_ph' in settings:
                self.auto_ph = bool(settings['auto_ph'])
            
            # Store settings in database
            try:
                self.db.save_nutrient_settings(self.get_settings())
            except Exception as e:
                logger.error(f"Failed to save nutrient settings to database: {e}")
                
            logger.info(f"Nutrient settings updated: {self.get_settings()}")
            return True
        except Exception as e:
            logger.error(f"Error updating nutrient settings: {e}")
            return False
            
    def manual_control(self, pump_id, duration=5):
        """Manually control nutrient pumps
        
        Args:
            pump_id (str): ID of the pump to control
            duration (float): Duration in seconds
            
        Returns:
            bool: Success status
        """
        try:
            if pump_id not in self.pumps:
                logger.error(f"Invalid pump ID: {pump_id}")
                return False
                
            # Calculate amount based on duration and flow rate
            amount_ml = duration * self.pumps[pump_id]['flow_rate_ml_per_sec']
            
            # Use the existing dose method
            return self.dose(pump_id, amount_ml)
        except Exception as e:
            logger.error(f"Error in manual control: {e}")
            return False
    
    def reset_daily_totals(self):
        """Reset daily nutrient totals (called at midnight)"""
        logger.info("Resetting daily nutrient totals")
        for pump_id in self.pumps:
            self.pumps[pump_id]['daily_total_ml'] = 0.0
        
        # Log the reset event
        try:
            self.db.log_system_event("nutrient_reset", "Daily nutrient totals reset")
        except Exception as e:
            logger.error(f"Failed to log nutrient reset event: {e}")
    
    def abort_dosing(self):
        """Emergency stop for all dosing"""
        with self.dosing_lock:
            for pump_id in self.pumps:
                self.pumps[pump_id]['state'] = False
            
            self.currently_dosing = False
            self.active_pump = None
            
            # Notify clients
            self.socketio.emit('dosing_aborted', {
                'timestamp': time.time()
            })
            
            logger.warning("Emergency abort of all nutrient dosing")
            return True
    
    def get_current_readings(self):
        """Get current pH and EC readings from sensors"""
        try:
            # FIXED: Handle case where sensor_manager is None
            if self.sensor_manager is None:
                self.logger.warning("No sensor manager available, returning mock data")
                return {
                    'ph': 6.0 + (random.random() - 0.5) * 0.4,  # 5.8 - 6.2
                    'ec': 1.2 + (random.random() - 0.5) * 0.4,  # 1.0 - 1.4
                    'connected': False
                }
            
            readings = self.sensor_manager.read_all_sensors()
            if readings:
                return {
                    'ph': readings.get('ph', 6.0),
                    'ec': readings.get('ec', 1.2),
                    'connected': True
                }
            else:
                self.logger.warning("Failed to read sensors, returning mock data")
                return {
                    'ph': 6.0,
                    'ec': 1.2,
                    'connected': False
                }
        except Exception as e:
            self.logger.error(f"Error reading sensors: {e}")
            return {
                'ph': 6.0,
                'ec': 1.2,
                'connected': False
            }