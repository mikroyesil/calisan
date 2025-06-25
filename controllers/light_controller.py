# controllers/light_controller.py - Basic light control system

import time
import datetime
import logging
import json

logger = logging.getLogger(__name__)

class LightController:
    def __init__(self, db, socketio, relay_controller=None):
        self.db = db
        self.socketio = socketio
        self.relay_controller = relay_controller
        self.simulation_mode = not relay_controller
        
        # Basic light zone configuration (Zone ID -> Relay Channels)
        self.light_zones = {
            1: {'relay_a': 1, 'relay_b': 2},   # Zone 1 uses relays 1,2
            2: {'relay_a': 3, 'relay_b': 4},   # Zone 2 uses relays 3,4
            3: {'relay_a': 5, 'relay_b': 6},   # Zone 3 uses relays 5,6
            4: {'relay_a': 7, 'relay_b': 8},   # Zone 4 uses relays 7,8
            5: {'relay_a': 9, 'relay_b': 10},  # Zone 5 uses relays 9,10
            6: {'relay_a': 11, 'relay_b': 12}, # Zone 6 uses relays 11,12
            7: {'relay_a': 13, 'relay_b': 14}  # Zone 7 uses relays 13,14
        }
        
        # Add compatibility attribute for app.py
        self.lights = self.light_zones  # Alias for backward compatibility
        
        # Track current states
        self.zone_states = {zone_id: False for zone_id in self.light_zones.keys()}
        
        # Load schedules from database
        self.schedules = self._load_schedules_from_db()
        
        # Last check time to prevent too frequent updates
        self.last_check = 0
        self.check_interval = 30  # Check every 30 seconds (reduced from 60 for better responsiveness)
        
        logger.info(f"Light controller initialized - {'Simulation' if self.simulation_mode else 'Hardware'} mode")
        
        # Initialize all zones to a consistent state
        self._initialize_all_zones()

    def _load_schedules_from_db(self):
        """Load schedules from database"""
        try:
            schedules = self.db.get_light_schedules()
            if not schedules:
                logger.info("No schedules found, creating default")
                return self._create_default_schedule()
            
            logger.info(f"Loaded {len(schedules)} schedules from database")
            return schedules
        except Exception as e:
            logger.error(f"Error loading schedules: {e}")
            return self._create_default_schedule()

    def _create_default_schedule(self):
        """Create a basic default schedule"""
        default = [{
            'id': 1,
            'name': 'Main Schedule',
            'start_time': '06:00',
            'end_time': '22:00',
            'enabled': True,
            'affected_zones': [1, 2, 3, 4, 5, 6, 7]
        }]
        
        # Save to database
        try:
            self.db.save_light_schedules(default)
            logger.info("Created and saved default schedule")
        except Exception as e:
            logger.error(f"Error saving default schedule: {e}")
        
        return default

    def _set_light_state(self, light_id, state):
        """Set the physical state of a light using the relay controller"""
        try:
            # Check if zone exists
            if light_id not in self.light_zones:
                logger.warning(f"Unknown light zone ID: {light_id}")
                return False
            
            # Get current state and check if change is needed
            current_state = self.zone_states.get(light_id, False)
            if current_state == state:
                logger.debug(f"Light zone {light_id} already in desired state: {state}")
                return True
            
            # Get zone configuration
            zone_config = self.light_zones[light_id]
            relay_a = zone_config['relay_a']
            relay_b = zone_config['relay_b']
            
            logger.info(f"Changing Light Zone {light_id}: {'OFF' if current_state else 'ON'} -> {'ON' if state else 'OFF'} (relays {relay_a}, {relay_b})")
            
            success = True
            
            if not self.simulation_mode and self.relay_controller and self.relay_controller.connected:
                # Control physical relays
                try:
                    success_a = self.relay_controller.set_relay(relay_a, state)
                    success_b = self.relay_controller.set_relay(relay_b, state)
                    success = success_a and success_b
                    
                    if success:
                        logger.info(f"HARDWARE: Light Zone {light_id} relays {relay_a},{relay_b} set to {'ON' if state else 'OFF'}")
                    else:
                        logger.error(f"HARDWARE: Failed to set Light Zone {light_id} relays {relay_a},{relay_b}")
                        
                except Exception as e:
                    logger.error(f"Hardware error controlling Light Zone {light_id}: {e}")
                    success = False
            else:
                # Simulation mode
                logger.info(f"SIMULATION: Light Zone {light_id} set to {'ON' if state else 'OFF'}")
                success = True
            
            if success:
                # Update internal state
                self.zone_states[light_id] = state
                
                # Emit socket event for frontend updates
                try:
                    self.socketio.emit('light_state_change', {
                        'light_id': light_id,
                        'zone_id': light_id,  # Add zone_id for compatibility
                        'state': state,
                        'timestamp': datetime.datetime.now().isoformat()
                    })
                except Exception as socket_error:
                    logger.warning(f"Socket emit error: {socket_error}")
            
            return success
                
        except Exception as e:
            logger.error(f"Error setting light zone {light_id} state: {e}")
            return False

    def update(self, sensor_data=None, force_check=False):
        """Update light schedules and control lights based on current time"""
        try:
            # Add throttling to prevent excessive updates
            now = time.time()
            if not force_check and hasattr(self, '_last_update_time'):
                time_since_last = now - self._last_update_time
                if time_since_last < 30:  # Minimum 30 seconds between updates
                    return
            
            self._last_update_time = now
            
            current_time = datetime.datetime.now().time()
            logger.info(f"Light schedule check at {current_time.strftime('%H:%M:%S')}")
            
            # Check if any schedule is active
            lights_should_be_on = False
            active_schedule = None
            
            for schedule in self.schedules:
                if not schedule.get('enabled', True):
                    continue
                
                start_time = datetime.datetime.strptime(schedule['start_time'], '%H:%M').time()
                end_time = datetime.datetime.strptime(schedule['end_time'], '%H:%M').time()
                
                # Handle schedules that cross midnight
                if start_time <= end_time:
                    # Normal schedule (e.g., 06:00 to 22:00)
                    if start_time <= current_time <= end_time:
                        lights_should_be_on = True
                        active_schedule = schedule
                        break
                else:
                    # Schedule crosses midnight (e.g., 22:00 to 06:00)
                    if current_time >= start_time or current_time <= end_time:
                        lights_should_be_on = True
                        active_schedule = schedule
                        break
            
            # Log the schedule decision
            if lights_should_be_on and active_schedule:
                logger.info(f"Lights should be ON - Active schedule: {active_schedule['start_time']}-{active_schedule['end_time']}")
            elif lights_should_be_on:
                logger.info(f"Lights should be ON - Schedule active")
            else:
                logger.info(f"Lights should be OFF - No active schedules at {current_time.strftime('%H:%M')}")
            
            # Check if ANY zone needs state change
            zones_needing_change = []
            for zone_id in self.light_zones.keys():
                current_state = self.zone_states.get(zone_id, False)
                if current_state != lights_should_be_on:
                    zones_needing_change.append(zone_id)
            
            # If any zones need changing, update ALL zones to ensure synchronization
            if zones_needing_change:
                logger.info(f"Updating ALL light zones to {'ON' if lights_should_be_on else 'OFF'}")
                logger.info(f"Zones that needed change: {zones_needing_change}")
                
                # Update all zones to the same state
                success_count = 0
                failed_zones = []
                
                for zone_id in self.light_zones.keys():
                    try:
                        if self._set_light_state(zone_id, lights_should_be_on):
                            success_count += 1
                        else:
                            failed_zones.append(zone_id)
                    except Exception as e:
                        logger.error(f"Error updating zone {zone_id}: {e}")
                        failed_zones.append(zone_id)
                
                # Log results
                total_zones = len(self.light_zones)
                if success_count == total_zones:
                    logger.info(f"✅ Successfully updated all {total_zones} light zones to {'ON' if lights_should_be_on else 'OFF'}")
                else:
                    logger.warning(f"⚠️ Light zone update partial success: {success_count}/{total_zones} successful")
                    if failed_zones:
                        logger.warning(f"Failed zones: {failed_zones}")
            else:
                logger.debug(f"All light zones already in correct state: {'ON' if lights_should_be_on else 'OFF'}")
                    
        except Exception as e:
            logger.error(f"Error in light controller update: {e}")

    def manual_control(self, zone_id, state):
        """Manual control of a zone"""
        try:
            success = self._set_light_state(zone_id, state)
            if success:
                logger.info(f"Manual control: Zone {zone_id} set to {'ON' if state else 'OFF'}")
            return success
        except Exception as e:
            logger.error(f"Error in manual control: {e}")
            return False

    def control_all_zones(self, state):
        """Control all zones at once"""
        try:
            success_count = 0
            for zone_id in self.light_zones.keys():
                if self._set_light_state(zone_id, state):
                    success_count += 1
            
            logger.info(f"Controlled all zones: {success_count}/{len(self.light_zones)} successful")
            return success_count == len(self.light_zones)
        except Exception as e:
            logger.error(f"Error controlling all zones: {e}")
            return False

    def get_all_light_schedules(self):
        """Get all light schedules including disabled ones"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM light_schedules ORDER BY id")
            rows = cursor.fetchall()
            conn.close()

            schedules = []
            for row in rows:
                # Parse affected_zones JSON
                affected_zones = []
                if row['affected_zones']:
                    try:
                        affected_zones = json.loads(row['affected_zones'])
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON in affected_zones for schedule {row['id']}")
                        affected_zones = []
                
                schedules.append({
                    'id': row['id'],
                    'name': row['schedule_name'],
                    'start_time': row['start_time'],
                    'end_time': row['end_time'],
                    'enabled': bool(row['enabled']),
                    'affected_zones': affected_zones
                })
            
            return schedules
        except Exception as e:
            logger.error(f"Error retrieving all light schedules: {e}")
            return []

    def get_light_states(self):
        """Get current state of all zones"""
        return {
            'zones': self.zone_states,
            'schedules': len(self.schedules),
            'simulation_mode': self.simulation_mode
        }

    def get_schedules(self):
        """Get current schedules - compatibility method for app.py"""
        return self.schedules

    def are_lights_on(self):
        """Determine if lights should be on based on current time and schedules"""
        try:
            current_time = datetime.datetime.now().time()
            
            # Check if any schedule is active
            for schedule in self.schedules:
                if not schedule.get('enabled', True):
                    continue
                
                start_time = datetime.datetime.strptime(schedule['start_time'], '%H:%M').time()
                end_time = datetime.datetime.strptime(schedule['end_time'], '%H:%M').time()
                
                # Handle schedules that cross midnight
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
            logger.error(f"Error determining light status: {e}")
            # Fallback to checking if any zones are on
            return any(self.zone_states.values())

    def _initialize_all_zones(self):
        """Initialize all light zones to OFF state at startup"""
        try:
            logger.info("Initializing all light zones to OFF state...")
            
            # Determine what state lights should be in based on current time
            current_time = datetime.datetime.now().time()
            lights_should_be_on = False
            
            for schedule in self.schedules:
                if not schedule.get('enabled', True):
                    continue
                
                start_time = datetime.datetime.strptime(schedule['start_time'], '%H:%M').time()
                end_time = datetime.datetime.strptime(schedule['end_time'], '%H:%M').time()
                
                # Handle schedules that cross midnight
                if start_time <= end_time:
                    # Normal schedule (e.g., 06:00 to 22:00)
                    if start_time <= current_time <= end_time:
                        lights_should_be_on = True
                        break
                else:
                    # Schedule crosses midnight (e.g., 22:00 to 06:00)
                    if current_time >= start_time or current_time <= end_time:
                        lights_should_be_on = True
                        break
            
            # Set all zones to the correct initial state
            initial_state = lights_should_be_on
            logger.info(f"Setting all zones to initial state: {'ON' if initial_state else 'OFF'} based on time {current_time.strftime('%H:%M')}")
            
            success_count = 0
            for zone_id in self.light_zones.keys():
                try:
                    # Force set the state regardless of current state tracking
                    if self._set_light_state_force(zone_id, initial_state):
                        success_count += 1
                        self.zone_states[zone_id] = initial_state
                except Exception as e:
                    logger.error(f"Error initializing zone {zone_id}: {e}")
            
            logger.info(f"Zone initialization complete: {success_count}/{len(self.light_zones)} zones set to {'ON' if initial_state else 'OFF'}")
            
        except Exception as e:
            logger.error(f"Error during zone initialization: {e}")

    def _set_light_state_force(self, light_id, state):
        """Force set light state without checking current state (used for initialization)"""
        try:
            # Check if zone exists
            if light_id not in self.light_zones:
                logger.warning(f"Unknown light zone ID: {light_id}")
                return False
            
            # Get zone configuration
            zone_config = self.light_zones[light_id]
            relay_a = zone_config['relay_a']
            relay_b = zone_config['relay_b']
            
            logger.info(f"Force setting Light Zone {light_id} to {'ON' if state else 'OFF'} (relays {relay_a}, {relay_b})")
            
            success = True
            
            if not self.simulation_mode and self.relay_controller and self.relay_controller.connected:
                # Control physical relays
                try:
                    success_a = self.relay_controller.set_relay(relay_a, state)
                    success_b = self.relay_controller.set_relay(relay_b, state)
                    success = success_a and success_b
                    
                    if success:
                        logger.info(f"INIT: Light Zone {light_id} relays {relay_a},{relay_b} force set to {'ON' if state else 'OFF'}")
                    else:
                        logger.error(f"INIT: Failed to force set Light Zone {light_id} relays {relay_a},{relay_b}")
                        
                except Exception as e:
                    logger.error(f"Hardware error during force set Light Zone {light_id}: {e}")
                    success = False
            else:
                # Simulation mode
                logger.info(f"INIT SIMULATION: Light Zone {light_id} force set to {'ON' if state else 'OFF'}")
                success = True
            
            return success
                
        except Exception as e:
            logger.error(f"Error force setting light zone {light_id} state: {e}")
            return False