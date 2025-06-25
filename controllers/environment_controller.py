# controllers/environment_controller.py
# CO2 environmental control system

import time
import logging
import requests
from requests.adapters import HTTPAdapter
from datetime import datetime
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# HTTP Session for connection pooling and keep-alive optimization
class HTTPSessionManager:
    """Manages HTTP sessions with connection pooling and keep-alive for Arduino communication"""
    
    def __init__(self):
        self.session = requests.Session()
        
        # Configure connection pooling and keep-alive
        adapter = HTTPAdapter(
            pool_connections=2,  # Number of connection pools
            pool_maxsize=5,      # Maximum connections per pool
            max_retries=1        # Quick retry on connection issues
        )
        self.session.mount('http://', adapter)
        
        # Set default headers for keep-alive
        self.session.headers.update({
            'Connection': 'keep-alive',
            'Keep-Alive': 'timeout=30, max=100'
        })
        
        logger.info("HTTP Session Manager initialized with connection pooling")
    
    def get(self, url, timeout=0.5, **kwargs):
        """Make GET request with optimized session"""
        return self.session.get(url, timeout=timeout, **kwargs)
    
    def close(self):
        """Close the session and cleanup connections"""
        self.session.close()

# Global session manager instance
_session_manager = HTTPSessionManager()

class EnvironmentController:
    def __init__(self, db, socketio, sensor_manager=None, relay_controller=None, light_controller=None, ir_controller=None):
        self.db = db
        self.socketio = socketio
        self.sensor_manager = sensor_manager
        self.relay_controller = relay_controller
        self.light_controller = light_controller
        self.ir_controller = ir_controller

        # CO2 Control System - Main Arduino with 8-channel relay module
        self.co2_arduino_ip = '192.168.1.107'  # Same as main sensor Arduino
        self.co2_arduino_port = 80              # Same as main sensor Arduino
        self.co2_channels = [1, 2]              # Channels 1&2 for CO2 valve
        self.co2_state = False                  # Current CO2 injector state
        
        # CO2 control settings with day/night targets
        self.co2_mode = 'auto'          # 'auto', 'manual_on', 'manual_off'
        self.co2_day_target = 1200      # Target CO2 PPM during day
        self.co2_night_target = 800     # Target CO2 PPM during night
        self.co2_tolerance = 25         # Reduced tolerance for faster response
        self.co2_day_start = 6          # Hour when day cycle starts (6 AM)
        self.co2_day_end = 22           # Hour when day cycle ends (10 PM)
        
        # CO2 throttling to prevent excessive switching - MAXIMUM SPEED
        self._last_co2_update = 0
        self._co2_update_interval = 0.05  # 50ms for maximum response speed
        
        # Air Conditioner on relay channel 15 + IR control (Airfel only)
        self.air_conditioner_channel = 15
        self.air_conditioner_state = False
        
        # Airfel AC settings - simplified for single brand
        self.ac_ir_enabled = True  # Enable IR control for advanced features
        self.ac_settings = {
            'power': False,
            'temperature': 24,
            'mode': 'cool',
            'fan_speed': 'medium',
            'brand': 'Airfel'  # Fixed brand
        }
        
        # Load settings
        self.settings = self.load_settings()
        
        # Circulation Fan Control - Channels 17-24 on Modbus relay controller
        self.fan_mapping = {
            'circulation_fan_1': 17,
            'circulation_fan_2': 18,
            'circulation_fan_3': 19,
            'circulation_fan_4': 20,
            'circulation_fan_5': 21,
            'circulation_fan_6': 22,
            'circulation_fan_7': 23,
            'circulation_fan_8': 24
        }
        
        # Fan control state tracking
        self.fan_states = {}  # Track individual fan states
        self.fan_mode = 'off'  # 'continuous', 'intermittent', 'off'
        self.fan_on_minutes = 5
        self.fan_off_minutes = 10
        self._last_fan_update = 0
        
        # Initialize fan states
        for fan_name in self.fan_mapping.keys():
            self.fan_states[fan_name] = False
            
        logger.info(f"🌀 Circulation fan control initialized with {len(self.fan_mapping)} fans on channels 17-24")

    def load_settings(self):
        """Load environment settings from the database and apply them"""
        try:
            settings = self.db.get_environment_settings()
            if settings:
                logger.info("Loaded environment settings from database, applying to controller...")
                
                # Apply saved settings to override hardcoded defaults
                if isinstance(settings, dict):
                    # New format: dictionary with named keys
                    if 'co2_mode' in settings:
                        self.co2_mode = settings['co2_mode']
                        logger.info(f"Applied CO2 mode: {self.co2_mode}")
                    
                    if 'co2_day_target' in settings:
                        self.co2_day_target = int(settings['co2_day_target'])
                        logger.info(f"Applied CO2 day target: {self.co2_day_target} PPM")
                    
                    if 'co2_night_target' in settings:
                        self.co2_night_target = int(settings['co2_night_target'])
                        logger.info(f"Applied CO2 night target: {self.co2_night_target} PPM")
                    
                    if 'co2_tolerance' in settings:
                        self.co2_tolerance = int(settings['co2_tolerance'])
                        logger.info(f"Applied CO2 tolerance: {self.co2_tolerance} PPM")
                    
                    if 'co2_day_start' in settings:
                        self.co2_day_start = int(settings['co2_day_start'])
                        logger.info(f"Applied CO2 day start: {self.co2_day_start}:00")
                    
                    if 'co2_day_end' in settings:
                        self.co2_day_end = int(settings['co2_day_end'])
                        logger.info(f"Applied CO2 day end: {self.co2_day_end}:00")
                
                logger.info("✅ Environment controller settings loaded from database and applied successfully")
                return settings
            else:
                logger.info("No environment settings found in database, using hardcoded defaults")
                return None
        except Exception as e:
            logger.error(f"Error loading environment settings: {e}")
            return None
    
    def save_settings(self):
        """Save current environment controller settings to the database"""
        try:
            # Prepare all current settings as a dictionary
            settings = {
                'co2_mode': self.co2_mode,
                'co2_day_target': self.co2_day_target,
                'co2_night_target': self.co2_night_target,
                'co2_tolerance': self.co2_tolerance,
                'co2_day_start': self.co2_day_start,
                'co2_day_end': self.co2_day_end
            }
            
            logger.info(f"Saving environment controller settings to database: {settings}")
            success = self.db.save_environment_settings(settings)
            
            if success:
                logger.info("✅ Environment controller settings saved to database successfully")
            else:
                logger.error("❌ Failed to save environment controller settings to database")
                
            return success
        except Exception as e:
            logger.error(f"Error saving environment settings: {e}")
            return False
    
    def update(self, sensor_data=None):
        """Update CO2 control based on sensor data"""
        try:
            # CO2 control based on sensor data
            if sensor_data:
                self._control_co2_injector(sensor_data)
                
        except Exception as e:
            logger.error(f"Error in environment controller update: {e}")

    def _is_lights_on_period(self):
        """Determine if lights should be on based on light controller schedules"""
        try:
            if not self.light_controller:
                # Fallback to fixed hours if no light controller available
                current_hour = datetime.now().hour
                return self.co2_day_start <= current_hour < self.co2_day_end
            
            # Get current time
            current_time = datetime.now().time()
            
            # Check all enabled schedules
            for schedule in self.light_controller.schedules:
                if not schedule.get('enabled', True):
                    continue
                
                # Parse schedule times
                start_time = datetime.strptime(schedule['start_time'], '%H:%M').time()
                end_time = datetime.strptime(schedule['end_time'], '%H:%M').time()
                
                # Check if current time is within schedule
                if start_time <= end_time:
                    # Normal schedule (e.g., 06:00 to 22:00)
                    if start_time <= current_time <= end_time:
                        return True
                else:
                    # Schedule crosses midnight (e.g., 22:00 to 06:00)
                    if current_time >= start_time or current_time <= end_time:
                        return True
            
            # No active schedules found, lights should be off
            return False
            
        except Exception as e:
            logger.error(f"Error determining light period: {e}")
            # Fallback to fixed hours
            current_hour = datetime.now().hour
            return self.co2_day_start <= current_hour < self.co2_day_end

    def manual_control(self, device_id, state):
        """Manually control environment devices - ULTRA FAST for CO2"""
        # Handle CO2 injector manual control
        if device_id == 'co2_injector':
            if state:
                self.co2_mode = 'manual_on'
            else:
                self.co2_mode = 'manual_off'
            
            # Manual control for immediate response
            logger.info(f"🌱 CO2 MANUAL CONTROL: Direct control requested")
            
            success = self._send_co2_command(state)
            if success:
                self.co2_state = state
                logger.info(f"🌱 CO2 MANUAL CONTROL: Injector set to {'ON' if state else 'OFF'}")
            return success
        
        # Handle circulation fan manual control
        elif device_id in self.fan_mapping:
            channel = self.fan_mapping[device_id]
            if self.relay_controller:
                success = self.relay_controller.set_relay(channel, state)
                if success:
                    self.fan_states[device_id] = state
                    logger.info(f"🌀 FAN MANUAL CONTROL: {device_id} (Ch.{channel}) set to {'ON' if state else 'OFF'}")
                return success
            else:
                logger.warning(f"🌀 FAN MANUAL CONTROL: Relay controller not available for {device_id}")
                return False
        
        logger.warning(f"Unknown device ID: {device_id}")
        return False

    def get_settings(self):
        """Get current CO2 environment settings"""
        # Get CO2 relay status from main Arduino
        co2_status = self.get_co2_relay_status()
        
        return {
            'co2_mode': self.co2_mode,
            'co2_day_target': self.co2_day_target,
            'co2_night_target': self.co2_night_target,
            'co2_tolerance': self.co2_tolerance,
            'co2_day_start': self.co2_day_start,
            'co2_day_end': self.co2_day_end,
            'co2_state': co2_status.get('overall_state', self.co2_state),
            'co2_channels': self.co2_channels,
            'co2_arduino_ip': self.co2_arduino_ip,
            'co2_arduino_port': self.co2_arduino_port,
            'co2_hardware_connected': co2_status.get('connected', False)
        }

    def update_settings(self, data):
        """Update environment settings"""
        try:
            # CO2 settings updates with detailed logging
            if 'co2_mode' in data:
                old_co2_mode = self.co2_mode
                self.co2_mode = data['co2_mode']
                logger.info(f"🌱 CO2 CONTROL: Mode changed from {old_co2_mode} to {self.co2_mode}")
                
            if 'co2_day_target' in data:
                old_day_target = self.co2_day_target
                self.co2_day_target = int(data['co2_day_target'])
                logger.info(f"🌱 CO2 CONTROL: Day target changed from {old_day_target} to {self.co2_day_target} PPM")
                
            if 'co2_night_target' in data:
                old_night_target = self.co2_night_target
                self.co2_night_target = int(data['co2_night_target'])
                logger.info(f"🌱 CO2 CONTROL: Night target changed from {old_night_target} to {self.co2_night_target} PPM")
                
            if 'co2_tolerance' in data:
                old_tolerance = self.co2_tolerance
                self.co2_tolerance = int(data['co2_tolerance'])
                logger.info(f"🌱 CO2 CONTROL: Tolerance changed from {old_tolerance} to {self.co2_tolerance} PPM")
                
            if 'co2_day_start' in data:
                old_day_start = self.co2_day_start
                self.co2_day_start = int(data['co2_day_start'])
                logger.info(f"🌱 CO2 CONTROL: Day start changed from {old_day_start}:00 to {self.co2_day_start}:00")
                
            if 'co2_day_end' in data:
                old_day_end = self.co2_day_end
                self.co2_day_end = int(data['co2_day_end'])
                logger.info(f"🌱 CO2 CONTROL: Day end changed from {old_day_end}:00 to {self.co2_day_end}:00")
            
            # Fan settings updates
            if 'fan_mode' in data:
                old_fan_mode = self.fan_mode
                self.fan_mode = data['fan_mode']
                logger.info(f"🌀 FAN CONTROL: Mode changed from {old_fan_mode} to {self.fan_mode}")
                
                # Apply the new fan mode immediately
                self._control_circulation_fans(self.fan_mode)
                
            if 'fan_on_minutes' in data:
                old_fan_on = self.fan_on_minutes
                self.fan_on_minutes = int(data['fan_on_minutes'])
                logger.info(f"🌀 FAN CONTROL: On time changed from {old_fan_on} to {self.fan_on_minutes} minutes")
                
            if 'fan_off_minutes' in data:
                old_fan_off = self.fan_off_minutes
                self.fan_off_minutes = int(data['fan_off_minutes'])
                logger.info(f"🌀 FAN CONTROL: Off time changed from {old_fan_off} to {self.fan_off_minutes} minutes")
            
            logger.info(f"Environment settings updated: {data}")
            return True
        except Exception as e:
            logger.error(f"Error updating environment settings: {e}")
            return False

    def _control_circulation_fans(self, mode='off'):
        """Control all circulation fans based on mode"""
        try:
            if not self.relay_controller:
                logger.warning("🌀 Cannot control circulation fans - relay controller not available")
                return False
                
            success_count = 0
            total_fans = len(self.fan_mapping)
            
            if mode == 'off':
                # Turn off all fans
                for fan_name, channel in self.fan_mapping.items():
                    if self.relay_controller.set_relay(channel, False):
                        self.fan_states[fan_name] = False
                        success_count += 1
                        logger.info(f"🌀 Fan {fan_name} (Ch.{channel}) turned OFF")
                    else:
                        logger.error(f"🌀 Failed to turn OFF fan {fan_name} (Ch.{channel})")
                        
            elif mode == 'continuous':
                # Turn on all fans
                for fan_name, channel in self.fan_mapping.items():
                    if self.relay_controller.set_relay(channel, True):
                        self.fan_states[fan_name] = True
                        success_count += 1
                        logger.info(f"🌀 Fan {fan_name} (Ch.{channel}) turned ON")
                    else:
                        logger.error(f"🌀 Failed to turn ON fan {fan_name} (Ch.{channel})")
                        
            elif mode == 'intermittent':
                # For intermittent mode, turn on all fans now
                # The actual intermittent timing would be handled by a background task
                for fan_name, channel in self.fan_mapping.items():
                    if self.relay_controller.set_relay(channel, True):
                        self.fan_states[fan_name] = True
                        success_count += 1
                        logger.info(f"🌀 Fan {fan_name} (Ch.{channel}) turned ON (intermittent mode)")
                    else:
                        logger.error(f"🌀 Failed to turn ON fan {fan_name} (Ch.{channel})")
            
            logger.info(f"🌀 Circulation fan control: {success_count}/{total_fans} fans controlled successfully in {mode} mode")
            return success_count == total_fans
            
        except Exception as e:
            logger.error(f"🌀 Error controlling circulation fans: {e}")
            return False
    
    def update_fan_control(self):
        """Update circulation fan control based on current mode and timer"""
        try:
            now = time.time()
            
            # Check if it's time to toggle fans
            if self.fan_mode == 'intermittent':
                time_since_last_update = now - self._last_fan_update
                
                # Toggle fans if interval has passed
                if time_since_last_update >= (self.fan_on_minutes * 60):
                    logger.info("🌀 Intermittent fan ON time reached, toggling fans ON")
                    self._control_circulation_fans('continuous')
                    self._last_fan_update = now
                    
                elif time_since_last_update >= (self.fan_off_minutes * 60):
                    logger.info("🌀 Intermittent fan OFF time reached, toggling fans OFF")
                    self._control_circulation_fans('off')
                    self._last_fan_update = now
            
        except Exception as e:
            logger.error(f"Error updating fan control: {e}")

    def cleanup(self):
        """Cleanup resources and close HTTP sessions"""
        try:
            logger.info("Environment controller cleanup started")
            # Note: Session manager is global, cleanup is handled at application level
            logger.info("Environment controller cleanup completed")
        except Exception as e:
            logger.error(f"Error during environment controller cleanup: {e}")

    def _control_co2_injector(self, sensor_data):
        """Control CO2 injector based on sensor readings and light schedule day/night targets - ULTRA FAST"""
        try:
            now = time.time()
            
            # Get current CO2 reading
            current_co2 = sensor_data.get('co2', 0)
            if current_co2 == 0:
                logger.debug("🌱 No CO2 sensor data available")
                return
                
            # ULTRA FAST MODE: Reduce throttling for critical CO2 levels
            time_since_last_update = now - self._last_co2_update
            
            # Emergency override for critical CO2 levels (far from target)
            is_emergency = False
            is_day_cycle = self._is_lights_on_period()
            target_co2 = self.co2_day_target if is_day_cycle else self.co2_night_target
            
            # Define emergency conditions
            emergency_low = target_co2 - (self.co2_tolerance * 3)  # Way too low
            emergency_high = target_co2 + (self.co2_tolerance * 3)  # Way too high
            
            if current_co2 < emergency_low or current_co2 > emergency_high:
                is_emergency = True
                logger.warning(f"🚨 CO2 EMERGENCY: {current_co2}ppm (target: {target_co2}ppm) - Overriding throttling!")
            
            # Check throttling - allow emergency conditions to bypass
            if not is_emergency and time_since_last_update < self._co2_update_interval:
                remaining_time = self._co2_update_interval - time_since_last_update
                logger.debug(f"🌱 CO2 throttled: {remaining_time:.1f}s remaining")
                return
                
            cycle_type = "DAY (Lights ON)" if is_day_cycle else "NIGHT (Lights OFF)"
            emergency_flag = " 🚨 EMERGENCY" if is_emergency else ""
            
            # Always log current status for debugging
            logger.info(f"🌱 CO2 STATUS{emergency_flag}: Current={current_co2}ppm, Target={target_co2}ppm, Mode={self.co2_mode}, Cycle={cycle_type}, State={self.co2_state}")
            
            # Only control if in auto mode
            if self.co2_mode == 'auto':
                should_inject = False
                action_reason = ""
                
                # ULTRA FAST LOGIC: More aggressive thresholds for faster response
                fast_tolerance = max(1, self.co2_tolerance // 4)  # Use quarter tolerance for ultra fast switching
                
                # Turn on if CO2 is below target minus fast tolerance
                if current_co2 < (target_co2 - fast_tolerance):
                    should_inject = True
                    action_reason = f"{current_co2} < {target_co2 - fast_tolerance} -> INJECT (ultra-fast)"
                    
                # Turn off if CO2 is above target plus fast tolerance  
                elif current_co2 > (target_co2 + fast_tolerance):
                    should_inject = False
                    action_reason = f"{current_co2} > {target_co2 + fast_tolerance} -> STOP (ultra-fast)"
                else:
                    # Within ultra-fast tolerance - maintain current state
                    should_inject = self.co2_state
                    action_reason = f"Within ultra-fast tolerance (±{fast_tolerance}), maintaining: {should_inject}"
                    
                logger.info(f"🌱 CO2 LOGIC: {action_reason}")
                    
                # Apply the CO2 injection state (accept partial success)
                if should_inject != self.co2_state:
                    logger.info(f"🌱 CO2 STATE CHANGE: {self.co2_state} -> {should_inject}")
                    success = self._send_co2_command(should_inject)
                    
                    # Accept partial success (at least one channel working)
                    if success:
                        self.co2_state = should_inject
                        self._last_co2_update = now
                        logger.info(f"✅ CO2 {cycle_type}: {current_co2}ppm -> Target: {target_co2}ppm -> Injector {'ON' if should_inject else 'OFF'}")
                        
                        # Emit status update
                        if self.socketio:
                            self.socketio.emit('co2_status', {
                                'state': should_inject,
                                'current_ppm': current_co2,
                                'target_ppm': target_co2,
                                'cycle': cycle_type.lower(),
                                'emergency': is_emergency
                            })
                    else:
                        logger.warning(f"⚠️ CO2 COMMAND PARTIAL FAILURE: Attempted to change state to {should_inject} but no channels responded")
                        # Don't update internal state if command completely failed
                else:
                    logger.debug(f"🌱 CO2 NO CHANGE: State already correct ({self.co2_state})")
                            
            elif self.co2_mode == 'manual_on':
                logger.info(f"🌱 CO2 MANUAL MODE: Forcing ON")
                if not self.co2_state:
                    success = self._send_co2_command(True)
                    if success:
                        self.co2_state = True
                        self._last_co2_update = now
                        logger.info(f"✅ CO2 MANUAL: Injector turned ON")
                        
            elif self.co2_mode == 'manual_off':
                logger.info(f"🌱 CO2 MANUAL MODE: Forcing OFF")
                if self.co2_state:
                    success = self._send_co2_command(False)
                    if success:
                        self.co2_state = False
                        self._last_co2_update = now
                        logger.info(f"✅ CO2 MANUAL: Injector turned OFF")
                        
        except Exception as e:
            logger.error(f"❌ CO2 CONTROL ERROR: {e}")

    def _send_co2_command(self, state):
        """Send HTTP command to main Arduino to control CO2 valve via relay channels - ULTRA FAST"""
        try:
            command_state = "on" if state else "off"
            logger.info(f"🌱 SENDING CO2 COMMAND: {command_state.upper()} to channels {self.co2_channels}")
            
            successful_channels = 0
            
            for channel in self.co2_channels:
                url = f"http://{self.co2_arduino_ip}:{self.co2_arduino_port}/api/relay?channel={channel}&state={command_state}"
                logger.info(f"🌱 HTTP REQUEST Channel {channel}: {url}")
                
                # Maximum speed request with ultra-minimal timeout
                try:
                    response = requests.get(url, timeout=0.1)  # Maximum speed 100ms timeout
                    if response.status_code == 200:
                        logger.info(f"🌱 ✅ CO2 channel {channel} -> {command_state.upper()} (ultra-fast)")
                        successful_channels += 1
                        success = True
                        continue
                except requests.exceptions.Timeout:
                    logger.warning(f"⚠️ CO2 channel {channel} -> TIMEOUT on max-speed attempt (0.1s)")
                except Exception as e:
                    logger.warning(f"⚠️ CO2 channel {channel} -> ERROR on ultra-fast attempt: {e}")
                
                # Quick retry with fast timeout if first attempt failed
                success = False
                try:
                    response = requests.get(url, timeout=0.5)  # Quick retry with 500ms timeout
                    if response.status_code == 200:
                        logger.info(f"🌱 ✅ CO2 channel {channel} -> {command_state.upper()} (retry)")
                        successful_channels += 1
                        success = True
                    else:
                        logger.error(f"❌ CO2 channel {channel} -> HTTP {response.status_code} (retry)")
                except requests.exceptions.Timeout:
                    logger.error(f"❌ CO2 channel {channel} -> RETRY TIMEOUT (0.5s)")
                except Exception as e:
                    logger.error(f"❌ CO2 channel {channel} -> RETRY ERROR: {e}")
            
            total_channels = len(self.co2_channels)
            if successful_channels == 0:
                logger.error(f"❌ CO2 CONTROL FAILED: 0/{total_channels} channels responded")
                return False
            elif successful_channels < total_channels:
                logger.warning(f"⚠️ CO2 PARTIAL SUCCESS: {successful_channels}/{total_channels} channels responded")
                return True  # Partial success is still success
            else:
                logger.info(f"🌱 ✅ CO2 CONTROL SUCCESS: All {total_channels} channels responded")
                return True
                
        except Exception as e:
            logger.error(f"❌ CO2 COMMAND ERROR: {e}")
            return False
        
    # IR Air Conditioner Control Methods
    def set_ac_power(self, power_on: bool) -> bool:
        """Control air conditioner power via relay + IR"""
        try:
            success = True
            
            # First, control the main power relay (channel 15)
            if self.relay_controller:
                relay_success = self.relay_controller.set_relay(self.air_conditioner_channel, power_on)
                if relay_success:
                    self.air_conditioner_state = power_on
                    logger.info(f"AC RELAY: Power {'ON' if power_on else 'OFF'} (Channel {self.air_conditioner_channel})")
                else:
                    success = False
                    logger.error(f"AC RELAY: Failed to control power relay")
            
            # Then, send IR command for advanced control
            if self.ac_ir_enabled and self.ir_controller and success:
                ir_success = self.ir_controller.set_ac_power(power_on)
                if ir_success:
                    self.ac_settings['power'] = power_on
                    logger.info(f"AC IR: Power {'ON' if power_on else 'OFF'} via IR")
                else:
                    logger.warning(f"AC IR: Failed to send power command via IR")
                    # Don't fail the whole operation if only IR fails
            
            # Update settings and emit socket event
            if success:
                self.ac_settings['power'] = power_on
                try:
                    self.socketio.emit('ac_state_change', {
                        'power': power_on,
                        'temperature': self.ac_settings['temperature'],
                        'mode': self.ac_settings['mode'],
                        'fan_speed': self.ac_settings['fan_speed'],
                        'timestamp': datetime.now().isoformat()
                    })
                except Exception as socket_error:
                    logger.warning(f"Socket emit error (ac_state_change): {socket_error}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error controlling AC power: {e}")
            return False
    
    def set_ac_temperature(self, temperature: int) -> bool:
        """Set air conditioner temperature via IR"""
        if not 16 <= temperature <= 30:
            logger.error(f"Invalid AC temperature {temperature}. Must be between 16-30°C")
            return False
        
        try:
            # Only use IR for temperature control
            if self.ac_ir_enabled and self.ir_controller:
                success = self.ir_controller.set_ac_temperature(temperature)
                if success:
                    self.ac_settings['temperature'] = temperature
                    logger.info(f"AC IR: Temperature set to {temperature}°C")
                    
                    # Emit socket event
                    try:
                        self.socketio.emit('ac_state_change', {
                            'power': self.ac_settings['power'],
                            'temperature': temperature,
                            'mode': self.ac_settings['mode'],
                            'fan_speed': self.ac_settings['fan_speed'],
                            'timestamp': datetime.now().isoformat()
                        })
                    except Exception as socket_error:
                        logger.warning(f"Socket emit error (ac_temp_change): {socket_error}")
                    
                    return True
                else:
                    logger.error(f"AC IR: Failed to set temperature to {temperature}°C")
                    return False
            else:
                logger.warning("AC IR: IR controller not available for temperature control")
                return False
                
        except Exception as e:
            logger.error(f"Error setting AC temperature: {e}")
            return False
    
    def set_ac_mode(self, mode: str) -> bool:
        """Set air conditioner mode via IR"""
        valid_modes = ['cool', 'heat', 'fan', 'auto', 'dry']
        if mode not in valid_modes:
            logger.error(f"Invalid AC mode {mode}. Must be one of: {valid_modes}")
            return False
        
        try:
            if self.ac_ir_enabled and self.ir_controller:
                success = self.ir_controller.set_ac_mode(mode)
                if success:
                    self.ac_settings['mode'] = mode
                    logger.info(f"AC IR: Mode set to {mode}")
                    
                    # Emit socket event
                    try:
                        self.socketio.emit('ac_state_change', {
                            'power': self.ac_settings['power'],
                            'temperature': self.ac_settings['temperature'],
                            'mode': mode,
                            'fan_speed': self.ac_settings['fan_speed'],
                            'timestamp': datetime.now().isoformat()
                        })
                    except Exception as socket_error:
                        logger.warning(f"Socket emit error (ac_mode_change): {socket_error}")
                    
                    return True
                else:
                    logger.error(f"AC IR: Failed to set mode to {mode}")
                    return False
            else:
                logger.warning("AC IR: IR controller not available for mode control")
                return False
                
        except Exception as e:
            logger.error(f"Error setting AC mode: {e}")
            return False
    
    def set_ac_fan_speed(self, speed: str) -> bool:
        """Set air conditioner fan speed via IR"""
        valid_speeds = ['low', 'medium', 'high', 'auto']
        if speed not in valid_speeds:
            logger.error(f"Invalid AC fan speed {speed}. Must be one of: {valid_speeds}")
            return False
        
        try:
            if self.ac_ir_enabled and self.ir_controller:
                success = self.ir_controller.set_ac_fan_speed(speed)
                if success:
                    self.ac_settings['fan_speed'] = speed
                    logger.info(f"AC IR: Fan speed set to {speed}")
                    
                    # Emit socket event
                    try:
                        self.socketio.emit('ac_state_change', {
                            'power': self.ac_settings['power'],
                            'temperature': self.ac_settings['temperature'],
                            'mode': self.ac_settings['mode'],
                            'fan_speed': speed,
                            'timestamp': datetime.now().isoformat()
                        })
                    except Exception as socket_error:
                        logger.warning(f"Socket emit error (ac_fan_change): {socket_error}")
                    
                    return True
                else:
                    logger.error(f"AC IR: Failed to set fan speed to {speed}")
                    return False
            else:
                logger.warning("AC IR: IR controller not available for fan speed control")
                return False
                
        except Exception as e:
            logger.error(f"Error setting AC fan speed: {e}")
            return False
    
    def get_ac_status(self) -> Dict[str, Any]:
        """Get current air conditioner status"""
        status = {
            'relay_state': self.air_conditioner_state,
            'relay_channel': self.air_conditioner_channel,
            'ir_enabled': self.ac_ir_enabled,
            'settings': self.ac_settings.copy()
        }
        
        # Add IR controller status if available
        if self.ir_controller:
            ir_status = self.ir_controller.get_connection_status()
            status['ir_controller'] = ir_status
            status['ir_state'] = self.ir_controller.get_ac_state()
        
        return status
    
    def get_co2_relay_status(self):
        """Get current CO2 relay status from main Arduino - optimized connection"""
        try:
            url = f"http://{self.co2_arduino_ip}:{self.co2_arduino_port}/api/relay"
            
            logger.debug(f"🌱 Checking CO2 relay status: {url}")
            
            # Try quick connection first
            try:
                response = requests.get(url, timeout=0.5)  # Fast 500ms timeout
                if response.status_code == 200:
                    data = response.json()
                    relays = data.get('relays', [])
                    
                    logger.debug(f"🌱 Arduino returned {len(relays)} relays (quick)")
                    
                    # Check status of CO2 channels
                    co2_states = {}
                    for relay in relays:
                        channel = relay.get('channel')
                        if channel in self.co2_channels:
                            state = relay.get('state') == 'on'
                            co2_states[f'channel_{channel}'] = state
                            logger.debug(f"🌱 CO2 Channel {channel}: {relay.get('state')} -> {state}")
                    
                    # Determine overall CO2 state (consider ON if any channel is ON)
                    overall_state = any(co2_states.values()) if co2_states else False
                    
                    logger.debug(f"🌱 CO2 relay status check complete: {co2_states}, overall: {overall_state}")
                    return {
                        'overall_state': overall_state,
                        'channel_states': co2_states,
                        'connected': True,
                        'arduino_response': f"{len(relays)} relays found"
                    }
                else:
                    logger.warning(f"❌ Arduino returned HTTP {response.status_code}: {response.text}")
                    return {'overall_state': False, 'channel_states': {}, 'connected': False, 'error': f'HTTP {response.status_code}'}
                    
            except requests.exceptions.Timeout:
                logger.debug(f"🌱 Quick status check timeout (0.5s), skipping detailed status")
                # Don't retry for status check - just return disconnected state
                return {'overall_state': self.co2_state, 'channel_states': {}, 'connected': False, 'error': 'TIMEOUT_QUICK'}
            except requests.exceptions.ConnectionError:
                logger.debug(f"🌱 Connection error on status check")
                return {'overall_state': self.co2_state, 'channel_states': {}, 'connected': False, 'error': 'CONNECTION_ERROR'}
            except Exception as e:
                logger.debug(f"🌱 Error on status check: {e}")
                return {'overall_state': self.co2_state, 'channel_states': {}, 'connected': False, 'error': str(e)}
                
        except Exception as e:
            logger.warning(f"❌ Error getting CO2 relay status: {e}")
            return {'overall_state': self.co2_state, 'channel_states': {}, 'connected': False, 'error': str(e)}
    
    def test_co2_system(self):
        """Test CO2 system integration with main Arduino"""
        logger.info("🌱 Testing CO2 system integration with main Arduino...")
        
        try:
            # Get initial status
            initial_status = self.get_co2_relay_status()
            logger.info(f"🌱 Initial CO2 status: {initial_status}")
            
            # Test turning CO2 ON
            logger.info("🌱 Testing CO2 ON...")
            success_on = self._send_co2_command(True)
            if success_on:
                time.sleep(2)  # Wait for relay to respond
                status_on = self.get_co2_relay_status()
                logger.info(f"🌱 CO2 ON status: {status_on}")
            
            # Test turning CO2 OFF
            logger.info("🌱 Testing CO2 OFF...")
            success_off = self._send_co2_command(False)
            if success_off:
                time.sleep(2)  # Wait for relay to respond
                status_off = self.get_co2_relay_status()
                logger.info(f"🌱 CO2 OFF status: {status_off}")
            
            # Test results
            test_results = {
                'arduino_connected': initial_status.get('connected', False),
                'co2_on_command': success_on,
                'co2_off_command': success_off,
                'channels_tested': self.co2_channels,
                'arduino_ip': self.co2_arduino_ip,
                'arduino_port': self.co2_arduino_port
            }
            
            if success_on and success_off:
                logger.info("🌱 ✅ CO2 system test PASSED - All commands successful")
            else:
                logger.warning("🌱 ⚠️ CO2 system test PARTIAL - Some commands failed")
                
            return test_results
            
        except Exception as e:
            logger.error(f"🌱 ❌ CO2 system test FAILED: {e}")
            return {'error': str(e), 'test_passed': False}
    
    def force_co2_update(self, sensor_data=None):
        """Force CO2 update by resetting throttling - for testing purposes"""
        logger.info("🌱 FORCING CO2 UPDATE - Resetting throttling")
        self._last_co2_update = 0  # Reset throttling
        
        if sensor_data is None:
            # Use fake sensor data for testing
            sensor_data = {
                'co2': 400,  # Low CO2 to trigger injection
                'temperature': 25.0,
                'humidity': 60.0
            }
            logger.info(f"🌱 Using test sensor data: {sensor_data}")
        
        self._control_co2_injector(sensor_data)
    
    def ultra_fast_co2_control(self, target_state=None, reason="manual"):
        """Ultra-fast CO2 control for immediate response"""
        logger.info(f"🚨 ULTRA FAST CO2 CONTROL: {reason}")
        
        try:
            if target_state is not None:
                # Direct state control
                success = self._send_co2_command(target_state)
                if success:
                    self.co2_state = target_state
                    self._last_co2_update = time.time()
                    logger.info(f"🚨 ✅ ULTRA FAST CO2: Injector set to {'ON' if target_state else 'OFF'}")
                    
                    # Emit immediate status update
                    if self.socketio:
                        self.socketio.emit('co2_status', {
                            'state': target_state,
                            'current_ppm': 0,  # Unknown in fast mode
                            'target_ppm': 0,   # Unknown in fast mode
                            'cycle': 'ultra_fast',
                            'emergency': True
                        })
                return success
            else:
                # Force immediate sensor-based update
                self._last_co2_update = 0  # Reset throttling
                
                # Try to get current sensor data
                if hasattr(self, 'sensor_manager') and self.sensor_manager:
                    try:
                        current_data = self.sensor_manager.get_latest_readings()
                        if current_data and current_data.get('co2', 0) > 0:
                            self._control_co2_injector(current_data)
                            return True
                    except Exception as e:
                        logger.warning(f"Could not get current sensor data: {e}")
                
                # Fallback to test data
                test_data = {'co2': 400, 'temperature': 25.0, 'humidity': 60.0}
                logger.info(f"🚨 Using fallback test data: {test_data}")
                self._control_co2_injector(test_data)
                return True
                
        except Exception as e:
            logger.error(f"❌ ULTRA FAST CO2 CONTROL ERROR: {e}")
            return False