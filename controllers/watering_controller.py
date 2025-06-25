# controllers/watering_controller.py - Improved watering system control

import time
import datetime
import logging
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

class WateringController:
    def __init__(self, db, socketio, relay_controller=None, light_controller=None):
        self.db = db
        self.socketio = socketio
        self.relay_controller = relay_controller
        self.light_controller = light_controller  # Add light controller for day/night detection
        self.water_pump_id = 'water_pump'
        self.water_pump_relay_channel = 16  # Using relay channel 16 for water pump
        
        # Default values that will be overridden by load_settings if found in DB
        self.cycle_minutes_per_hour = 10.0
        self.active_hours_start = 6  # 6:00 AM
        self.active_hours_end = 22   # 10:00 PM
        self.cycle_seconds_on = 30   # Default cycle pattern: 30 seconds on
        self.cycle_seconds_off = 270 # followed by 4.5 minutes off
        
        # Day/Night cycle settings - separate settings for when lights are on vs off
        self.day_cycle_seconds_on = 30    # Day cycle: 30 seconds on
        self.day_cycle_seconds_off = 270  # Day cycle: 4.5 minutes off
        self.night_cycle_seconds_on = 20  # Night cycle: 20 seconds on (less watering)
        self.night_cycle_seconds_off = 600 # Night cycle: 10 minutes off (longer rest)
        
        self.daily_limit = 60.0      # Maximum 60 minutes of watering per day
        self.manual_watering_duration = 1  # Default for manual watering input
        self.max_continuous_run = 5  # Maximum continuous run time in minutes

        # Runtime state variables - not stored in settings
        self.daily_run_minutes = 0.0 # Reset daily at midnight
        self.pump_state = False      # Current state of the pump
        self.last_run_time = 0       # Last time the pump ran
        self.manual_mode = False     # Flag for manual operation
        self.manual_end_time = 0     # When manual mode should end
        self.enabled = True          # Master enable/disable flag
        self.last_warning_time = 0   # Time of last warning message
        
        # Status variables
        self.last_state_change = 0   # Last time pump state was changed
        self.min_state_change_interval = 10  # Minimum seconds between state changes
        self.last_schedule_check = 0  # Last time schedule was checked
        
        # Scheduler information
        self.schedules = []  # List to store watering schedules
        self.scheduler_initialized = False
        
        # Add emergency safety features
        self.last_verified_hardware_state = None
        self.last_force_off_attempt = 0
        self.force_off_interval = 60  # Only try force off once per minute
        self.force_verify_interval = 30  # Verify hardware state every 30 seconds
        self.last_hardware_verification = 0  # Last time we verified hardware state
        
        # Track forced shutdowns
        self.emergency_shutdown_active = False
        self.emergency_shutdown_time = 0
        
        # Maximum allowed continuous run time regardless of other settings (30 minutes absolute maximum)
        self.absolute_max_run_minutes = 30
        
        # Load settings from database and override defaults
        self.load_settings()
        
        # Load watering schedules
        self.load_schedules()
        
        # Try to get the initial pump state from hardware
        self._check_actual_pump_state()

    def _check_actual_pump_state(self):
        """Initialize hardware to match controller state on startup"""
        try:
            # Default controller state starts as OFF for safety
            # Instead of updating the controller to match hardware, make hardware match controller
            if hasattr(self, '_verify_and_correct_hardware_state'):
                logger.info("Initializing hardware to match controller state (OFF)")
                self._verify_and_correct_hardware_state(self.pump_state)
            else:
                # Fallback for earlier versions without the verify method
                if self.relay_controller and hasattr(self.relay_controller, 'get_relay'):
                    try:
                        # Just check hardware state for logging purposes
                        actual_state = self.relay_controller.get_relay(self.water_pump_relay_channel)
                        logger.info(f"Initial hardware pump state: {bool(actual_state)}, controller state: {self.pump_state}")
                    except Exception as e:
                        logger.warning(f"Could not get initial pump state from relay controller: {e}")
        except ImportError:
            logger.warning("Could not import relay_controller from app")
        except Exception as e:
            logger.warning(f"Error initializing pump state: {e}")

    def load_settings(self):
        """Load watering settings from database - NO SCHEDULES, only cycle settings"""
        try:
            # Load ONLY settings from database (cycle configuration)
            settings = self.db.get_watering_settings()
            if settings:
                logger.info(f"🚰 Loaded watering cycle settings from database: {settings}")
                # Update instance variables with loaded settings
                self.enabled = settings.get('enabled', True)
                self.cycle_minutes_per_hour = settings.get('cycle_minutes_per_hour', 5.0)
                self.active_hours_start = settings.get('active_hours_start', 6)
                self.active_hours_end = settings.get('active_hours_end', 20)
                self.cycle_seconds_on = settings.get('cycle_seconds_on', 30)
                self.cycle_seconds_off = settings.get('cycle_seconds_off', 300)
                
                # Load day/night cycle settings with fallback to main cycle settings
                self.day_cycle_seconds_on = settings.get('day_cycle_seconds_on', self.cycle_seconds_on)
                self.day_cycle_seconds_off = settings.get('day_cycle_seconds_off', self.cycle_seconds_off)
                self.night_cycle_seconds_on = settings.get('night_cycle_seconds_on', self.cycle_seconds_on) 
                self.night_cycle_seconds_off = settings.get('night_cycle_seconds_off', self.cycle_seconds_off)
                
                self.daily_limit = settings.get('daily_limit', 60.0)
                self.manual_watering_duration = settings.get('manual_watering_duration', 10)
                self.max_continuous_run = settings.get('max_continuous_run', 5)
                
                # LOG the actual values that were loaded
                logger.info(f"🚰 Applied settings - ON: {self.cycle_seconds_on}s, OFF: {self.cycle_seconds_off}s, Active: {self.active_hours_start}-{self.active_hours_end}")
            else:
                logger.info("🚰 No watering cycle settings in database, using defaults")
                logger.info(f"🚰 Default settings - ON: {self.cycle_seconds_on}s, OFF: {self.cycle_seconds_off}s, Active: {self.active_hours_start}-{self.active_hours_end}")

            # REMOVED: No more schedule loading - only cycle settings matter
            self.schedules = []  # Always empty - we don't use schedules
            logger.info("🚰 Schedule system disabled - using cycle settings only")
                
        except Exception as e:
            logger.error(f"🚰 Error loading watering cycle settings: {e}")
            self.schedules = []

    def _validate_timed_schedule(self, schedule):
        """Validate a TIMED watering schedule (different from cyclic settings)"""
        try:
            # TIMED schedules need: start_time, duration, enabled
            required_fields = ['start_time', 'duration', 'enabled']
            
            if isinstance(schedule, dict):
                missing_fields = [field for field in required_fields if field not in schedule]
                if missing_fields:
                    logger.warning(f"🚰 Timed schedule missing fields: {missing_fields}")
                    return False
                return True
            else:
                logger.warning(f"🚰 Invalid timed schedule format: {type(schedule)}")
                return False
                
        except Exception as e:
            logger.error(f"🚰 Error validating timed schedule: {e}")
            return False

    def load_schedules(self):
        """DISABLED: No schedules needed - only cycle settings"""
        logger.info("🚰 Schedule loading disabled - using cycle settings only")
        self.schedules = []
        return True

    def save_schedule(self, schedule_data):
        """DISABLED: No schedules needed - only cycle settings"""
        logger.info("🚰 Schedule saving disabled - use cycle settings instead")
        return False
    
    def delete_schedule(self, schedule_id):
        """DISABLED: No schedules needed - only cycle settings"""
        logger.info("🚰 Schedule deletion disabled - no schedules used")
        return False
    
    def get_schedules(self):
        """DISABLED: No schedules needed - only cycle settings"""
        logger.info("🚰 No schedules - using cycle settings only")
        return []

    def execute_scheduled_watering(self, schedule_id):
        """DISABLED: No schedules needed - only cycle settings"""
        logger.info("🚰 Scheduled watering disabled - using cycle settings only")
        return False

    def register_with_scheduler(self, scheduler):
        """DISABLED: No schedules needed - only cycle settings"""
        logger.info("🚰 Scheduler registration disabled - using cycle settings only")
        self.scheduler_initialized = True
        return True

    def save_settings(self):
        """Save current instance settings to the database as a dictionary."""
        try:
            # Create a settings dictionary from current instance attributes
            settings = {
                'enabled': self.enabled,
                'cycle_minutes_per_hour': self.cycle_minutes_per_hour,
                'active_hours_start': self.active_hours_start,
                'active_hours_end': self.active_hours_end,
                'cycle_seconds_on': self.cycle_seconds_on,
                'cycle_seconds_off': self.cycle_seconds_off,
                'day_cycle_seconds_on': self.day_cycle_seconds_on,
                'day_cycle_seconds_off': self.day_cycle_seconds_off,
                'night_cycle_seconds_on': self.night_cycle_seconds_on,
                'night_cycle_seconds_off': self.night_cycle_seconds_off,
                'daily_limit': self.daily_limit,
                'manual_watering_duration': self.manual_watering_duration,
                'max_continuous_run': self.max_continuous_run,
                'updated_at': int(time.time())
            }
            
            # Save the settings dictionary to database
            logger.info(f"Saving watering settings to DB: {settings}")
            
            # FIX: Pass parameters individually to match the expected Database method signature
            # Instead of passing the entire settings dictionary
            success = self.db.save_watering_settings(
                enabled=settings['enabled'],
                cycle_minutes_per_hour=settings['cycle_minutes_per_hour'],
                active_hours_start=settings['active_hours_start'],
                active_hours_end=settings['active_hours_end'],
                cycle_seconds_on=settings['cycle_seconds_on'],
                cycle_seconds_off=settings['cycle_seconds_off'],
                day_cycle_seconds_on=settings['day_cycle_seconds_on'],
                day_cycle_seconds_off=settings['day_cycle_seconds_off'],
                night_cycle_seconds_on=settings['night_cycle_seconds_on'],
                night_cycle_seconds_off=settings['night_cycle_seconds_off'],
                daily_limit=settings['daily_limit'], 
                manual_watering_duration=settings['manual_watering_duration'],
                max_continuous_run=settings['max_continuous_run'],
                updated_at=settings['updated_at']
            )
            
            if success:
                logger.info("Watering settings saved successfully")
                return True
            else:
                logger.error("Failed to save watering settings to the database")
                return False
        except Exception as e:
            logger.error(f"Error saving watering settings: {e}", exc_info=True)
            return False

    def get_settings(self):
        """Return current watering settings as a dictionary."""
        # Get current cycle information
        try:
            cycle_on, cycle_off, cycle_type = self._get_current_cycle_settings()
            current_cycle_info = {
                'cycle_type': cycle_type,
                'cycle_seconds_on': cycle_on,
                'cycle_seconds_off': cycle_off,
                'lights_on': cycle_type == 'day' if cycle_type in ['day', 'night'] else None
            }
        except Exception as e:
            logger.warning(f"Error getting current cycle info: {e}")
            current_cycle_info = {
                'cycle_type': 'main',
                'cycle_seconds_on': self.cycle_seconds_on,
                'cycle_seconds_off': self.cycle_seconds_off,
                'lights_on': None
            }
        
        return {
            'enabled': self.enabled,
            'cycle_minutes_per_hour': self.cycle_minutes_per_hour,
            'active_hours_start': self.active_hours_start,
            'active_hours_end': self.active_hours_end,
            'cycle_seconds_on': self.cycle_seconds_on,
            'cycle_seconds_off': self.cycle_seconds_off,
            'day_cycle_seconds_on': self.day_cycle_seconds_on,
            'day_cycle_seconds_off': self.day_cycle_seconds_off,
            'night_cycle_seconds_on': self.night_cycle_seconds_on,
            'night_cycle_seconds_off': self.night_cycle_seconds_off,
            'daily_limit': self.daily_limit,
            'daily_run_minutes': self.daily_run_minutes,
            'pump_state': self.pump_state,
            'manual_mode': self.manual_mode,
            'manual_end_time': self.manual_end_time,
            'manual_watering_duration': self.manual_watering_duration,
            'max_continuous_run': self.max_continuous_run,
            'max_daily_minutes': self.daily_limit,  # For UI compatibility
            'current_cycle': current_cycle_info  # Add current cycle information
        }

    def update_settings(self, data):
        """Update watering settings from provided data."""
        try:
            logger.info(f"🚰 BEFORE UPDATE: ON={self.cycle_seconds_on}s, OFF={self.cycle_seconds_off}s")
            logger.info(f"🚰 Updating watering settings with: {data}")
            changed = False
            
            if 'enabled' in data:
                new_value = bool(data['enabled'])
                if self.enabled != new_value:
                    self.enabled = new_value
                    changed = True
                    logger.info(f"🚰 Updated enabled: {self.enabled}")
                    # Turn off pump if system is being disabled and pump is running
                    if not self.enabled and self.pump_state:
                        self._set_pump_state(False)
            
            if 'cycle_minutes_per_hour' in data:
                new_value = float(data['cycle_minutes_per_hour'])
                if self.cycle_minutes_per_hour != new_value:
                    self.cycle_minutes_per_hour = new_value
                    changed = True
                    logger.info(f"🚰 Updated cycle_minutes_per_hour: {self.cycle_minutes_per_hour}")
            
            if 'active_hours_start' in data:
                new_value = int(data['active_hours_start'])
                if self.active_hours_start != new_value:
                    self.active_hours_start = new_value
                    changed = True
                    logger.info(f"🚰 Updated active_hours_start: {self.active_hours_start}")
            
            if 'active_hours_end' in data:
                new_value = int(data['active_hours_end'])
                if self.active_hours_end != new_value:
                    self.active_hours_end = new_value
                    changed = True
                    logger.info(f"🚰 Updated active_hours_end: {self.active_hours_end}")
            
            if 'cycle_seconds_on' in data:
                new_value = int(data['cycle_seconds_on'])
                # Enforce minimum ON time of 15 seconds for better visibility
                if new_value > 0 and new_value < 15:
                    logger.info(f"🚰 Adjusting cycle_seconds_on from {new_value} to minimum 15 seconds for visibility")
                    new_value = 15
                if self.cycle_seconds_on != new_value:
                    old_value = self.cycle_seconds_on
                    self.cycle_seconds_on = new_value
                    changed = True
                    logger.info(f"🚰 CRITICAL: Updated cycle_seconds_on: {old_value} -> {self.cycle_seconds_on}")
            
            if 'cycle_seconds_off' in data:
                new_value = int(data['cycle_seconds_off'])
                if self.cycle_seconds_off != new_value:
                    old_value = self.cycle_seconds_off
                    self.cycle_seconds_off = new_value
                    changed = True
                    logger.info(f"🚰 CRITICAL: Updated cycle_seconds_off: {old_value} -> {self.cycle_seconds_off}")
            
            # Handle day/night cycle settings
            if 'day_cycle_seconds_on' in data:
                new_value = int(data['day_cycle_seconds_on'])
                if new_value > 0 and new_value < 15:
                    new_value = 15  # Enforce minimum
                if self.day_cycle_seconds_on != new_value:
                    old_value = self.day_cycle_seconds_on
                    self.day_cycle_seconds_on = new_value
                    changed = True
                    logger.info(f"🚰 Updated day_cycle_seconds_on: {old_value} -> {self.day_cycle_seconds_on}")
            
            if 'day_cycle_seconds_off' in data:
                new_value = int(data['day_cycle_seconds_off'])
                if self.day_cycle_seconds_off != new_value:
                    old_value = self.day_cycle_seconds_off
                    self.day_cycle_seconds_off = new_value
                    changed = True
                    logger.info(f"🚰 Updated day_cycle_seconds_off: {old_value} -> {self.day_cycle_seconds_off}")
            
            if 'night_cycle_seconds_on' in data:
                new_value = int(data['night_cycle_seconds_on'])
                if new_value > 0 and new_value < 15:
                    new_value = 15  # Enforce minimum
                if self.night_cycle_seconds_on != new_value:
                    old_value = self.night_cycle_seconds_on
                    self.night_cycle_seconds_on = new_value
                    changed = True
                    logger.info(f"🚰 Updated night_cycle_seconds_on: {old_value} -> {self.night_cycle_seconds_on}")
            
            if 'night_cycle_seconds_off' in data:
                new_value = int(data['night_cycle_seconds_off'])
                if self.night_cycle_seconds_off != new_value:
                    old_value = self.night_cycle_seconds_off
                    self.night_cycle_seconds_off = new_value
                    changed = True
                    logger.info(f"🚰 Updated night_cycle_seconds_off: {old_value} -> {self.night_cycle_seconds_off}")
            
            if 'daily_limit' in data:
                new_value = float(data['daily_limit'])
                if self.daily_limit != new_value:
                    self.daily_limit = new_value
                    changed = True
                    logger.info(f"🚰 Updated daily_limit: {self.daily_limit}")
                    
            if 'manual_watering_duration' in data:
                new_value = int(data['manual_watering_duration'])
                if self.manual_watering_duration != new_value:
                    self.manual_watering_duration = new_value
                    changed = True
                    logger.info(f"🚰 Updated manual_watering_duration: {self.manual_watering_duration}")

            if 'max_continuous_run' in data:
                new_value = int(data['max_continuous_run'])
                if self.max_continuous_run != new_value:
                    self.max_continuous_run = new_value
                    changed = True
                    logger.info(f"🚰 Updated max_continuous_run: {self.max_continuous_run}")
            
            logger.info(f"🚰 AFTER UPDATE: ON={self.cycle_seconds_on}s, OFF={self.cycle_seconds_off}s")
            
            if changed:
                # Make sure to call save_settings to persist changes
                success = self.save_settings()
                if not success:
                    logger.error("🚰 Failed to save settings to database")
                    return False
                    
                logger.info("🚰 Settings updated and saved to database")
                
                # FORCE reload from database to verify save worked
                self.load_settings()
                logger.info(f"🚰 VERIFIED AFTER RELOAD: ON={self.cycle_seconds_on}s, OFF={self.cycle_seconds_off}s")
                
                # Force an immediate update to apply new settings to the hardware
                logger.info("🚰 Triggering immediate update to apply new watering settings")
                
                # Store the original rate limiting values
                original_min_state_change_interval = self.min_state_change_interval
                original_last_schedule_check = self.last_schedule_check
                
                try:
                    # Completely disable rate limiting to ensure changes apply immediately
                    self.min_state_change_interval = 0
                    self.last_schedule_check = 0  # Reset the last check time to force update
                    self.last_state_change = 0  # Reset last state change time
                    
                    # First, turn off pump to reset the cycle
                    if self.pump_state:
                        logger.info("🚰 Turning pump off to reset cycle with new settings")
                        self._force_pump_off()
                        time.sleep(0.5)  # Small delay to ensure commands don't conflict
                    
                    # Force a direct calculation of the pump state with new settings
                    now = time.time()
                    current_datetime = datetime.datetime.now()
                    
                    # Calculate whether pump should be on right now with new settings
                    should_be_on = self._calculate_pump_state(current_datetime, now)
                    logger.info(f"🚰 New settings calculation result: pump should be {'ON' if should_be_on else 'OFF'}")
                    
                    # Apply the calculated state directly using the most reliable methods
                    if should_be_on:
                        logger.info("🚰 Setting pump ON with new settings")
                        success = self._set_pump_state(True)
                        if not success:
                            # Retry once if failed
                            logger.warning("🚰 First attempt to set pump ON failed, retrying...")
                            time.sleep(0.5)
                            self._set_pump_state(True)
                    else:
                        logger.info("🚰 Setting pump OFF with new settings")
                        success = self._force_pump_off()  # Use force off for more reliable shutdown
                        
                    # Force the actual hardware state to match our desired state
                    self._verify_and_correct_hardware_state(should_be_on)
                    
                    # Ensure update runs without rate limiting
                    self.last_schedule_check = 0
                    
                    # Call update to ensure everything is consistent
                    self.update(None)
                    
                    # Set last_schedule_check to now to avoid immediate re-update
                    self.last_schedule_check = now
                    logger.info("🚰 Settings have been successfully applied to hardware")
                    
                except Exception as e:
                    logger.error(f"🚰 Error during immediate settings update: {e}")
                    # Even if we had an error, try one more verification to make sure the hardware state is correct
                    try:
                        self._verify_and_correct_hardware_state(self._calculate_pump_state(datetime.datetime.now(), time.time()))
                    except Exception as verify_error:
                        logger.error(f"🚰 Additional error during hardware verification: {verify_error}")
                    
                finally:
                    # Restore rate limiting after applying changes
                    self.min_state_change_interval = original_min_state_change_interval
                    logger.info("🚰 Restored rate limiting after applying settings")
                
                # Emit event to notify clients
                if self.socketio:
                    try:
                        self.socketio.emit('watering_settings_updated', self.get_settings())
                        logger.info("🚰 Emitted watering_settings_updated event")
                        
                        # Also recalculate and emit the next cycle info
                        next_cycle_info = self.calculate_next_cycle_info()
                        self.socketio.emit('watering_next_cycle_updated', next_cycle_info)
                        logger.info(f"🚰 Emitted next cycle update: {next_cycle_info}")
                    except Exception as e:
                        logger.error(f"🚰 Error emitting settings update event: {e}")
            else:
                logger.info("🚰 No settings were changed")
                
            return True
        except Exception as e:
            logger.error(f"🚰 Error updating watering settings: {e}", exc_info=True)
            return False
            
    def calculate_next_cycle_info(self):
        """Calculate when the next watering cycle will occur with simplified output"""
        try:
            # Create a default response with minimal debug info
            next_cycle_info = {
                'time': '--:--',
                'status_text': 'Unknown', 
                'badge_class': 'bg-secondary',
                'debug_info': {}
            }
            
            # Add basic debug info (removed detailed cycle position data)
            debug_info = {
                'cycle_minutes_per_hour': self.cycle_minutes_per_hour,
                'cycle_seconds_on': self.cycle_seconds_on,
                'cycle_seconds_off': self.cycle_seconds_off,
                'enabled': self.enabled,
                'daily_run_minutes': self.daily_run_minutes,
                'daily_limit': self.daily_limit,
                'pump_state': self.pump_state,
                'manual_mode': self.manual_mode
            }
            next_cycle_info['debug_info'] = debug_info
            
            # Basic status checks
            if not self.enabled:
                next_cycle_info['status_text'] = 'Disabled'
                next_cycle_info['badge_class'] = 'bg-danger'
                return next_cycle_info
            
            # Handle manual mode first
            if self.manual_mode and self.manual_end_time > 0:
                now = time.time()
                remaining_seconds = max(0, self.manual_end_time - now)
                if remaining_seconds > 0:
                    minutes = int(remaining_seconds // 60)
                    seconds = int(remaining_seconds % 60)
                    next_cycle_info['time'] = f"{minutes:02d}:{seconds:02d}"
                    next_cycle_info['status_text'] = 'Manual'
                    next_cycle_info['badge_class'] = 'bg-warning'
                    return next_cycle_info
            
            # Check if we're in active hours
            current_datetime = datetime.datetime.now()
            current_hour = current_datetime.hour
            is_active = self._is_active_hour(current_hour)
            
            if not is_active:
                next_cycle_info['status_text'] = 'Outside Hours'
                next_cycle_info['badge_class'] = 'bg-secondary'
                
                # Calculate time until next active hour
                start = self.active_hours_start
                if current_hour < start:
                    # Next active period is later today
                    next_cycle_info['time'] = f"{start:02d}:00"
                else:
                    # Next active period is tomorrow
                    next_cycle_info['time'] = f"{self.active_hours_start:02d}:00 (tomorrow)"
                
                return next_cycle_info
            
            # If we're in active hours but no watering scheduled
            if self.cycle_minutes_per_hour <= 0:
                next_cycle_info['status_text'] = 'No Schedule'
                next_cycle_info['badge_class'] = 'bg-secondary'
                return next_cycle_info
            
            # Handle continuous operation
            if self.cycle_seconds_off <= 0:
                next_cycle_info['time'] = 'Continuous'
                next_cycle_info['status_text'] = 'Always On'
                next_cycle_info['badge_class'] = 'bg-success'
                debug_info['mode'] = 'continuous'
                return next_cycle_info
            
            # Determine the next state change based on current cycle
            on_seconds = self.cycle_seconds_on
            off_seconds = self.cycle_seconds_off
            cycle_length = on_seconds + off_seconds
            
            # Get current position in hour/cycle
            current_second_of_hour = (current_datetime.minute * 60) + current_datetime.second
            position_in_cycle = current_second_of_hour % cycle_length
            in_on_phase = position_in_cycle < on_seconds
            
            # Add minimal cycle state info
            debug_info['next_status'] = "Off" if in_on_phase else "On"
            
            # Calculate next event time
            if in_on_phase:
                seconds_to_next = on_seconds - position_in_cycle
            else:
                seconds_to_next = cycle_length - position_in_cycle
                
            next_cycle_time = current_datetime + datetime.timedelta(seconds=seconds_to_next)
            next_cycle_info['time'] = next_cycle_time.strftime('%H:%M:%S')
            next_cycle_info['status_text'] = 'Cyclic'
            next_cycle_info['badge_class'] = 'bg-primary'
            
            return next_cycle_info
        except Exception as e:
            logger.error(f"Error calculating next cycle info: {e}")
            return {
                'time': '--:--',
                'status_text': 'Error',
                'badge_class': 'bg-danger'
            }

    def update(self, sensor_data=None):
        """
        Update watering system based on schedule and limits.
        This is called by the scheduler at regular intervals.
        """
        now = time.time()
        current_datetime = datetime.datetime.now()
        
        # Rate limiting - only check watering every 5 seconds at most (reduced from 10)
        if now - self.last_schedule_check < 5:
            return
        
        self.last_schedule_check = now
        
        # First, verify hardware state periodically
        if now - self.last_hardware_verification > self.force_verify_interval:
            self._verify_hardware_state()
            self.last_hardware_verification = now
        
        # EMERGENCY SAFETY: Force turn off if pump has been running too long
        if self.pump_state and not self.emergency_shutdown_active:
            # Check if pump has been running longer than absolute max
            run_time_minutes = (now - self.last_state_change) / 60
            
            if run_time_minutes > self.absolute_max_run_minutes:
                logger.error(f"EMERGENCY SHUTDOWN: Pump has been running for {run_time_minutes:.1f} minutes (exceeds {self.absolute_max_run_minutes} min limit)")
                
                # Activate emergency shutdown
                self.emergency_shutdown_active = True
                self.emergency_shutdown_time = now
                
                # Force pump off with multiple methods
                self._force_pump_off()
                
                # Log emergency event
                self.db.log_event('watering', {
                    'action': 'emergency_shutdown',
                    'run_minutes': run_time_minutes,
                    'max_allowed': self.absolute_max_run_minutes
                })
                
                return
        
        # If emergency shutdown is active, maintain it for 5 minutes
        if self.emergency_shutdown_active:
            # If pump is still on after emergency shutdown, try again
            if self.pump_state and now - self.last_force_off_attempt > self.force_off_interval:
                logger.error("Pump still on after emergency shutdown - retrying shutdown")
                self._force_pump_off()
                self.last_force_off_attempt = now
                
            # Keep emergency shutdown active for 5 minutes
            if now - self.emergency_shutdown_time < 300:  # 5 minutes
                if self.pump_state:
                    self._force_pump_off()  # Try again if pump is still on
                return
            else:
                # Clear emergency after 5 minutes
                logger.info("Clearing emergency shutdown status")
                self.emergency_shutdown_active = False
        
        # Reset daily run time at midnight
        if current_datetime.hour == 0 and current_datetime.minute < 5:
            if self.daily_run_minutes > 0:
                logger.info(f"Resetting daily watering counter. Previous total: {self.daily_run_minutes:.1f} minutes")
                self.daily_run_minutes = 0.0
                self.last_warning_time = 0  # Reset warning timer at midnight
        
        # If we're in manual mode, check if it's time to end
        if self.manual_mode and now >= self.manual_end_time:
            logger.info("Manual watering cycle completed")
            self._set_pump_state(False)
            self.manual_mode = False
            
            # Log event with duration validation
            duration = max(0, (self.manual_end_time - self.last_state_change)) / 60
            self.db.log_event('watering', {
                'action': 'completed',
                'trigger': 'manual',
                'duration_minutes': duration
            })
            
            # Emit an event to notify clients that manual mode has ended
            if self.socketio:
                self.socketio.emit('watering_status', {
                    'status': 'manual_completed',
                    'daily_total': self.daily_run_minutes
                })
            
            return
        
        # Don't interfere with manual mode
        if self.manual_mode:
            # Log this occasionally, not every update
            if int(now) % 60 == 0:  # Log once per minute
                remaining = max(0, self.manual_end_time - now)
                logger.debug(f"In manual mode, {remaining:.0f} seconds remaining. Skipping cycle check.")
            
            # SAFETY: If manual mode but pump is off, clear manual mode
            if not self.pump_state:
                logger.warning("Detected inconsistency: manual mode active but pump is OFF. Clearing manual mode.")
                self.manual_mode = False
                self.manual_end_time = 0
                
            return
        
        # Check if system is enabled
        if not self.enabled:
            # Make sure pump is off if system is disabled
            if self.pump_state:
                logger.info("Turning off pump: System is disabled")
                self._set_pump_state(False)  # Changed to forced off for safety
            return
        
        # Check if we're within active hours
        current_hour = current_datetime.hour
        is_active_hour = self._is_active_hour(current_hour)
        
        if not is_active_hour:
            # Turn off pump if it's outside active hours
            if self.pump_state:
                logger.info(f"Turning off pump: Outside active hours (current hour: {current_hour}, active: {self.active_hours_start}-{self.active_hours_end})")
                self._force_pump_off()  # Use force off for more reliable shutdown
            return
        
        # Check if we're under the daily limit with improved warning logic
        if self.daily_run_minutes >= self.daily_limit:
            # Only log a warning if:
            # 1. The pump is currently on, or
            # 2. We haven't warned in the last 5 minutes
            if self.pump_state or (now - self.last_warning_time > 300):
                logger.warning(f"Daily watering limit reached: {self.daily_run_minutes:.1f} minutes")
                self.last_warning_time = now
                
                # Emit status update via Socket.IO
                if self.socketio:
                    self.socketio.emit('watering_status', {
                        'status': 'limit_reached',
                        'daily_total': self.daily_run_minutes,
                        'limit': self.daily_limit
                    })
            
            if self.pump_state:
                logger.info("Turning off pump: Daily limit reached")
                self._force_pump_off()  # Use force off for more reliable shutdown
            return
        
        # Skip the rest if no watering should happen this hour
        if self.cycle_minutes_per_hour <= 0:
            # No watering needed this hour
            if self.pump_state:
                logger.info("Turning off pump: cycle_minutes_per_hour is 0")
                self._force_pump_off()  # Use force off for more reliable shutdown
            return
        
        # Standard cycle pattern logic - unified approach
        try:
            # Use the centralized pump state calculation method for consistency
            should_be_on = self._calculate_pump_state(current_datetime, now)
            
            # Get current cycle settings for logging purposes
            on_seconds, off_seconds, cycle_type = self._get_current_cycle_settings()
            
            # Additional logic just for logging and event tracking
            if off_seconds <= 0:
                # Special case: Continuous operation
                cycle_length = 3600  # Whole hour
                cycles_per_hour = 1
                position_in_cycle = 0  # Not applicable
                cycle_index = 0  # Not applicable
            else:
                # Normal cycle operation
                cycle_length = on_seconds + off_seconds
                cycles_per_hour = int(3600 / cycle_length)
                
                # Current position in hour
                current_second_of_hour = (current_datetime.minute * 60) + current_datetime.second
                
                # Calculate cycle position
                cycle_index = (current_second_of_hour // cycle_length) % max(1, cycles_per_hour)
                position_in_cycle = current_second_of_hour % cycle_length
            
            # Enhanced logging with day/night info
            if int(now) % 30 < 5:  # Log approximately every 30 seconds
                logger.info(
                    f"Cycle status: {'ON' if should_be_on else 'OFF'} ({cycle_type} mode), "
                    f"cycle_len={cycle_length}s, cycles/hr={cycles_per_hour}, "
                    f"position={position_in_cycle}/{cycle_length}, on_thresh={on_seconds}s"
                )
            
            # Only change state if needed and not too soon since last change
            if should_be_on != self.pump_state and (now - self.last_state_change >= self.min_state_change_interval):
                logger.info(f"Changing pump state to {should_be_on}")
                
                if should_be_on:
                    # Normal on
                    success = self._set_pump_state(True)
                else:
                    # Force off for more reliable shutdown
                    success = self._force_pump_off()
                
                if success and should_be_on:
                    # Log event if pump just turned on
                    self.db.log_event('watering', {
                        'action': 'started',
                        'trigger': 'cycle',
                        'duration_seconds': on_seconds,
                        'cycle_index': cycle_index,
                        'total_cycles': cycles_per_hour
                    })
                    
        except Exception as e:
            logger.error(f"Error in watering cycle calculation: {e}", exc_info=True)
            # Safety: turn off pump if there's an error in the calculation
            if self.pump_state:
                self._force_pump_off()  # Use force off for more reliable shutdown

    def _get_current_cycle_settings(self):
        """Get the appropriate cycle settings based on whether lights are on (day) or off (night)"""
        try:
            # Check if we have a light controller and it's available
            if self.light_controller and hasattr(self.light_controller, 'are_lights_on'):
                lights_are_on = self.light_controller.are_lights_on()
                
                if lights_are_on:
                    # Daytime settings - when lights are on, plants need more water
                    cycle_on = self.day_cycle_seconds_on
                    cycle_off = self.day_cycle_seconds_off
                    logger.debug(f"🚰 Using DAY cycle settings: {cycle_on}s ON, {cycle_off}s OFF (lights are on)")
                else:
                    # Nighttime settings - when lights are off, plants need less water
                    cycle_on = self.night_cycle_seconds_on
                    cycle_off = self.night_cycle_seconds_off
                    logger.debug(f"🚰 Using NIGHT cycle settings: {cycle_on}s ON, {cycle_off}s OFF (lights are off)")
                
                return cycle_on, cycle_off, "day" if lights_are_on else "night"
            else:
                # Fallback to main cycle settings if no light controller available
                logger.debug("🚰 No light controller available, using main cycle settings")
                return self.cycle_seconds_on, self.cycle_seconds_off, "main"
                
        except Exception as e:
            logger.warning(f"🚰 Error determining day/night status, using main cycle settings: {e}")
            return self.cycle_seconds_on, self.cycle_seconds_off, "main"

    def _is_active_hour(self, current_hour):
        """Check if current hour is within active watering hours"""
        start = self.active_hours_start
        end = self.active_hours_end
        
        # Special case: if start and end are the same, consider it active 24/7
        if start == end:
            return True
            
        if start < end:
            # Normal case (e.g., 6:00 to 22:00)
            return start <= current_hour < end
        else:
            # Overnight case (e.g., 22:00 to 6:00)
            return current_hour >= start or current_hour < end

    def _set_pump_state(self, state):
        """Set the water pump state using the modbus controller from app.py"""
        # Store current state before any changes
        original_state = self.pump_state
        
        try:
            # Don't change state if it's already in that state
            if self.pump_state == state:
                logger.debug(f"Pump already in state {state}, skipping update")
                return True
            
            now = time.time()
            
            # Track pump runtime if turning off
            if self.pump_state and not state:
                runtime_minutes = (now - self.last_state_change) / 60
                self.daily_run_minutes += runtime_minutes
                logger.info(f"Watering completed, runtime: {runtime_minutes:.2f} minutes, daily total: {self.daily_run_minutes:.2f} minutes")
            
            # Update controller state first
            self.pump_state = state
            self.last_state_change = now
            
            # Use only the reliable relay_controller method that works
            success = False
            hardware_error = None
            
            # Method 1: Try to use relay_controller directly (most reliable)
            try:
                if self.relay_controller and hasattr(self.relay_controller, 'set_relay'):
                    logger.info(f"Setting water pump relay (channel {self.water_pump_relay_channel}) to {'ON' if state else 'OFF'} using relay_controller")
                    success = self.relay_controller.set_relay(self.water_pump_relay_channel, state)
                    if success:
                        logger.info(f"Successfully set pump state using relay_controller")
                    else:
                        logger.warning("relay_controller.set_relay returned False")
                else:
                    logger.warning("relay_controller not available or missing set_relay method")
            except Exception as e:
                logger.warning(f"Could not use relay_controller: {e}")
                hardware_error = str(e)
                success = False
            
            # Fallback: Use standard API call as backup, but with a relative path
            if not success:
                try:
                    import requests
                    
                    # Use full URL with proper scheme
                    response = requests.post(
                        'http://localhost:5001/api/relay-control',
                        json={
                            'channel': self.water_pump_relay_channel,
                            'state': state
                        },
                        timeout=3
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        if data.get('status') == 'success':
                            logger.info(f"Successfully set pump state using relay-control API")
                            success = True
                        else:
                            logger.warning(f"API returned error: {data.get('message')}")
                            success = False
                    else:
                        logger.warning(f"API request failed with status {response.status_code}")
                        success = False
                        
                except Exception as e:
                    logger.warning(f"Error with relay-control API call: {e}")
                    if not hardware_error:
                        hardware_error = str(e)
            
            # Always emit events to update UI
            if self.socketio:
                self.socketio.emit('pump_state_change', {
                    'state': self.pump_state,
                    'time': datetime.datetime.now().strftime("%H:%M:%S"),
                    'daily_total': self.daily_run_minutes
                })
                
                self.socketio.emit('relay_state_change', {
                    'channel': self.water_pump_relay_channel,
                    'state': self.pump_state
                })
            
            # If controlling hardware failed but we updated internal state, log warning
            if not success:
                logger.warning(f"Failed to set pump hardware state to {state}, but updated controller state")
                # We return True because the controller state was updated, even if hardware control failed
                return True
                
            return success
            
        except Exception as e:
            logger.error(f"Error controlling water pump: {e}", exc_info=True)
            # Revert controller state to previous state
            self.pump_state = original_state
            return False

    # Add these new safety methods to verify and force pump state
    def _verify_hardware_state(self):
        """Check if hardware pump state matches our controller state"""
        try:
            # Methods to check actual hardware state
            hardware_state = None
            
            # Method 1: Try relay_controller
            try:
                if self.relay_controller and hasattr(self.relay_controller, 'get_relay'):
                    hardware_state = bool(self.relay_controller.get_relay(self.water_pump_relay_channel))
                    logger.debug(f"Hardware state from relay_controller: {hardware_state}")
                else:
                    logger.debug("relay_controller not available for state verification")
            except Exception as e:
                logger.warning(f"Could not check relay state: {e}")
            
            # Method 2: Try API if relay_controller failed
            if hardware_state is None:
                try:
                    import requests
                    
                    response = requests.get('http://localhost:5001/api/relay-states', timeout=2)
                    if response.status_code == 200:
                        data = response.json()
                        if 'states' in data and str(self.water_pump_relay_channel) in data['states']:
                            hardware_state = bool(data['states'][str(self.water_pump_relay_channel)])
                            logger.debug(f"Hardware state from API: {hardware_state}")
                except Exception as e:
                    logger.warning(f"Could not check relay state through any method: {e}")
            
            # If we got a hardware state, compare with controller state
            if hardware_state is not None:
                self.last_verified_hardware_state = hardware_state
                
                # Detect mismatch
                if hardware_state != self.pump_state:
                    logger.warning(f"Hardware pump state ({hardware_state}) doesn't match controller state ({self.pump_state})")
                    
                    # Whether hardware is ON or OFF, always make hardware match the controller's desired state
                    logger.info("Making hardware match controller's desired state")
                    self._verify_and_correct_hardware_state(self.pump_state)
                else:
                    logger.debug(f"Hardware pump state ({hardware_state}) matches controller state")
        except Exception as e:
            logger.error(f"Error verifying hardware state: {e}")

    def _force_pump_off(self):
        """Forcefully turn off the pump using working methods only"""
        logger.info("FORCE PUMP OFF: Using working methods to ensure pump is OFF")
        
        # Update internal state
        self.pump_state = False
        self.last_state_change = time.time()
        self.last_force_off_attempt = time.time()
        
        # Track runtime for daily limit
        runtime_minutes = (time.time() - self.last_state_change) / 60
        if runtime_minutes > 0:
            self.daily_run_minutes += runtime_minutes
        
        success = False
        methods_success = []
        methods_failed = []
        
        # METHOD 1: Use relay_controller - THIS WORKS
        try:
            if self.relay_controller and hasattr(self.relay_controller, 'set_relay'):
                logger.info(f"Forcing pump OFF with relay_controller (Method 1)")
                result = self.relay_controller.set_relay(self.water_pump_relay_channel, False)
                if result:
                    methods_success.append("relay_controller")
                    success = True
                else:
                    methods_failed.append("relay_controller")
            else:
                logger.warning("relay_controller not available")
                methods_failed.append("relay_controller_unavailable")
        except Exception as e:
            logger.error(f"Error with relay_controller during force off: {e}")
            methods_failed.append("relay_controller")
        
        # METHOD 2: Use relay-control API - THIS WORKS
        if not success:
            try:
                import requests
                logger.info(f"Forcing pump OFF with relay-control API")
                
                response = requests.post(
                    'http://localhost:5001/api/relay-control',
                    json={
                        'channel': self.water_pump_relay_channel,
                        'state': False
                    },
                    timeout=3
                )
                
                if response.status_code == 200:
                    data = response.json()
                    if data.get('status') == 'success':
                        methods_success.append("relay_api")
                        success = True
                    else:
                        methods_failed.append("relay_api")
                else:
                    methods_failed.append("relay_api")
            except Exception as e:
                logger.error(f"Error with relay-control API during force off: {e}")
                methods_failed.append("relay_api")
        
        # Log the results
        if success:
            logger.info(f"Force pump OFF succeeded with methods: {', '.join(methods_success)}")
        else:
            logger.error(f"Force pump OFF FAILED with all methods: {', '.join(methods_failed)}")
        
        # Always emit events to update UI
        if self.socketio:
            self.socketio.emit('pump_state_change', {
                'state': False,
                'time': datetime.datetime.now().strftime("%H:%M:%S"),
                'daily_total': self.daily_run_minutes,
                'force_off': True
            })
            
            self.socketio.emit('relay_state_change', {
                'channel': self.water_pump_relay_channel,
                'state': False
            })
        
        return success

    def manual_control(self, state, duration=None):
        """Manually control the water pump"""
        if state:
            # Starting the pump
            if duration is None:
                duration = self.manual_watering_duration
            
            # Enforce max continuous run limit
            duration = min(duration, self.max_continuous_run)
            
            # Convert to seconds
            duration_seconds = duration * 60
            
            # Check if this would exceed daily limit
            if self.daily_run_minutes + (duration_seconds / 60) > self.daily_limit:
                logger.warning(f"Manual watering rejected: would exceed daily limit of {self.daily_limit} minutes")
                return {'status': 'error', 'message': f'Would exceed daily limit of {self.daily_limit} minutes'}
            
            # Set manual mode
            self.manual_mode = True
            self.manual_end_time = time.time() + duration_seconds
            
            # Turn on the pump
            if not self.pump_state:
                self._set_pump_state(True)
            
            # Log event
            self.db.log_event('watering', {
                'action': 'started',
                'trigger': 'manual',
                'duration_minutes': duration
            })
            
            logger.info(f"Manual watering started for {duration} minutes")
            return {'status': 'success', 'message': f'Watering started for {duration} minutes'}
        else:
            # Stopping the pump
            if self.pump_state:
                self._set_pump_state(False)
                
                # Log event
                self.db.log_event('watering', {
                    'action': 'stopped',
                    'trigger': 'manual'
                })
                
                # Clear manual mode
                self.manual_mode = False
                
                logger.info("Manual watering stopped")
                return {'status': 'success', 'message': 'Watering stopped'}
            return {'status': 'success', 'message': 'Pump already off'}

    # Add a utility method for CRC calculation
    def _calculate_modbus_crc16(self, data):
        """Calculate Modbus RTU CRC16"""
        crc = 0xFFFF
        for b in data:
            crc ^= b
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc = crc >> 1
        return [crc & 0xFF, (crc >> 8) & 0xFF]

    def load_schedules(self):
        """DISABLED: No schedules needed - only cycle settings"""
        logger.info("🚰 Schedule loading disabled - using cycle settings only")
        self.schedules = []
        return True

    def save_schedule(self, schedule_data):
        """DISABLED: No schedules needed - only cycle settings"""
        logger.info("🚰 Schedule saving disabled - use cycle settings instead")
        return False
    
    def delete_schedule(self, schedule_id):
        """DISABLED: No schedules needed - only cycle settings"""
        logger.info("🚰 Schedule deletion disabled - no schedules used")
        return False
    
    def get_schedules(self):
        """DISABLED: No schedules needed - only cycle settings"""
        logger.info("🚰 No schedules - using cycle settings only")
        return []

    def execute_scheduled_watering(self, schedule_id):
        """DISABLED: No schedules needed - only cycle settings"""
        logger.info("🚰 Scheduled watering disabled - using cycle settings only")
        return False

    def register_with_scheduler(self, scheduler):
        """DISABLED: No schedules needed - only cycle settings"""
        logger.info("🚰 Scheduler registration disabled - using cycle settings only")
        self.scheduler_initialized = True
        return True

    def _calculate_pump_state(self, current_datetime, now):
        """
        Calculate whether the pump should be on based on current time and settings.
        This is used to determine the immediate state after settings change.
        
        Args:
            current_datetime: Current datetime object
            now: Current timestamp
            
        Returns:
            bool: True if the pump should be on, False otherwise
        """
        try:
            # First check if manual mode is active - it overrides all other calculations
            if self.manual_mode and now < self.manual_end_time:
                logger.info("Manual mode is active, pump should be ON")
                return True
            
            # Check if system is enabled overall
            if not self.enabled:
                logger.info("Watering system is disabled, pump should be OFF")
                return False
            
            # Check if in active hours
            current_hour = current_datetime.hour
            is_active_hour = self._is_active_hour(current_hour)
            if not is_active_hour:
                logger.info(f"Outside active hours ({self.active_hours_start}-{self.active_hours_end}), pump should be OFF")
                return False
            
            # Check daily limit
            if self.daily_run_minutes >= self.daily_limit:
                logger.info(f"Daily limit reached ({self.daily_run_minutes:.1f}/{self.daily_limit} min), pump should be OFF")
                return False
            
            # Check if watering is configured for this hour
            if self.cycle_minutes_per_hour <= 0:
                logger.info("Cycle minutes per hour is 0, pump should be OFF")
                return False
            
            # Get appropriate cycle settings based on day/night status
            on_seconds, off_seconds, cycle_type = self._get_current_cycle_settings()
            
            # If cycle_seconds_on is invalid, pump should be off
            if on_seconds <= 0:
                logger.info(f"Invalid {cycle_type}_cycle_seconds_on value, pump should be OFF")
                return False
                
            # Handle continuous operation mode
            if off_seconds <= 0:
                logger.info(f"Continuous operation mode ({cycle_type}, off_seconds=0), pump should be ON")
                return True
                
            # Normal cycle operation with day/night awareness
            cycle_length = on_seconds + off_seconds
            
            # Calculate current position in cycle
            current_second_of_hour = (current_datetime.minute * 60) + current_datetime.second
            position_in_cycle = current_second_of_hour % cycle_length
            
            # Determine if pump should be ON based on position in cycle
            should_be_on = position_in_cycle < on_seconds
            
            logger.info(f"Calculated pump state: {should_be_on} (position {position_in_cycle}/{cycle_length}, on_threshold={on_seconds})")
            return should_be_on
            
        except Exception as e:
            logger.error(f"Error calculating pump state: {e}")
            # Default to OFF for safety in case of errors
            return False

    def _verify_and_correct_hardware_state(self, expected_state):
        """
        Verifies that the hardware state matches the expected state and corrects it if needed.
        This is used specifically during settings updates to ensure hardware is in the correct state.
        
        Args:
            expected_state: The state the pump hardware should be in (True for ON, False for OFF)
        
        Returns:
            bool: True if successful or already in correct state, False if verification/correction failed
        """
        logger.info(f"Verifying hardware state matches expected state: {'ON' if expected_state else 'OFF'}")
        try:
            # Get the current hardware state using the most reliable method
            hardware_state = None
            
            # Try relay_controller first
            try:
                if self.relay_controller and hasattr(self.relay_controller, 'get_relay'):
                    hardware_state = bool(self.relay_controller.get_relay(self.water_pump_relay_channel))
                    logger.debug(f"Hardware state from relay_controller: {hardware_state}")
                else:
                    logger.debug("relay_controller not available for state verification")
            except Exception as e:
                logger.warning(f"Could not check relay state via controller: {e}")
            
            # Try API if relay_controller failed
            if hardware_state is None:
                try:
                    import requests
                    
                    response = requests.get('http://localhost:5001/api/relay-states', timeout=2)
                    if response.status_code == 200:
                        data = response.json()
                        if 'states' in data and str(self.water_pump_relay_channel) in data['states']:
                            hardware_state = bool(data['states'][str(self.water_pump_relay_channel)])
                            logger.debug(f"Hardware state from API: {hardware_state}")
                except Exception as e:
                    logger.warning(f"Could not check relay state via API: {e}")
            
            # If we couldn't get hardware state, log error and return
            if hardware_state is None:
                logger.error("Failed to determine hardware pump state during verification")
                return False
            
            # If hardware state matches expected state, we're done
            if hardware_state == expected_state:
                logger.info(f"Hardware state ({hardware_state}) correctly matches expected state")
                return True
            
            # Hardware state doesn't match expected state, attempt to correct
            logger.warning(f"Hardware state ({hardware_state}) doesn't match expected state ({expected_state}), correcting...")
            
            # Make 3 attempts to set it correctly
            for attempt in range(1, 4):
                logger.info(f"Attempt {attempt}/3 to correct hardware state")
                
                if expected_state:
                    # Should be ON but is OFF - use reliable method
                    success = self._set_pump_state(True)
                else:
                    # Should be OFF but is ON - force off
                    success = self._force_pump_off()
                
                # Wait a moment for the change to take effect
                time.sleep(0.5)
                
                # Verify the change
                try:
                    if self.relay_controller and hasattr(self.relay_controller, 'get_relay'):
                        hardware_state = bool(self.relay_controller.get_relay(self.water_pump_relay_channel))
                        logger.info(f"Post-correction hardware state: {hardware_state}")
                        
                        if hardware_state == expected_state:
                            logger.info("Hardware state successfully corrected")
                            return True
                    else:
                        logger.warning("Couldn't verify state correction - relay_controller unavailable")
                        # Assume success if we can't verify
                        return success
                except Exception as e:
                    logger.warning(f"Error verifying pump state correction: {e}")
            
            # If we get here, correction failed
            logger.error("Failed to correct pump hardware state after 3 attempts")
            return False
            
        except Exception as e:
            logger.error(f"Error during hardware state verification and correction: {e}")
            return False