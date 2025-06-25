#!/usr/bin/env python3
# app.py - Main Flask application for Vertical Farm Control System

import os
import time as time_module
import datetime
import threading
import logging
import pytz
import urllib3
from flask import Flask, render_template, request, jsonify, current_app, redirect
from flask_socketio import SocketIO, emit
from flask_cors import CORS

# Import controllers
from controllers.light_controller import LightController
from controllers.nutrient_controller import NutrientController
from controllers.environment_controller import EnvironmentController
from controllers.watering_controller import WateringController
from controllers.growing_profile_controller import GrowingProfileController

# Import utilities
from sensors.robust_sensor_manager import RobustSensorManager
from utils.database import Database
from controllers.scheduler import Scheduler  # Changed from utils.scheduler to controllers.scheduler
from utils.debug_monitor import DebugMonitor

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("farm_control.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Disable urllib3 warnings
urllib3.disable_warnings()

# Configure logging levels
logging.getLogger('engineio.server').setLevel(logging.ERROR)
logging.getLogger('socketio.server').setLevel(logging.ERROR)
logging.getLogger('werkzeug').setLevel(logging.WARNING)
logging.getLogger('pymodbus').setLevel(logging.ERROR)
logging.getLogger('urllib3').setLevel(logging.ERROR)

# Water pump channel
WATER_PUMP_RELAY_CHANNEL = 16

# Initialize Flask app
app = Flask(__name__, static_folder='static', static_url_path='/static')
CORS(app, resources={r"/*": {"origins": "*"}})

app.config.update(
    SECRET_KEY='vertical-farm-secret-key',
    DEBUG=False,
    SEND_FILE_MAX_AGE_DEFAULT=0,
    TEMPLATES_AUTO_RELOAD=True,
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,
    PROPAGATE_EXCEPTIONS=False,
    PRESERVE_CONTEXT_ON_EXCEPTION=False
)

# CRITICAL: Add template context processor that was missing
@app.context_processor
def inject_now():
    """Inject current timestamp for cache busting"""
    return {'now': lambda: datetime.datetime.now()}

# FIXED: Re-enable SocketIO with proper configuration and debugging
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='threading',
    logger=False,  # Disable logging to avoid conflicts
    engineio_logger=False,  # Disable engine.io logging
    manage_session=False,
    always_connect=False,
    ping_timeout=60,
    ping_interval=25,
    max_http_buffer_size=1000000
)

# Initialize database
db = Database()

# Initialize sensor manager
sensor_manager = RobustSensorManager(
    arduino_ip='192.168.1.107',
    arduino_port=80,
    connection_timeout=8,
    read_timeout=15,
    max_retries=2,
    query_pumps=False  # Don't query pump endpoints that don't exist on Arduino
)
logger.info(f"Initialized sensor manager for Arduino at 192.168.1.107:80 (pump queries disabled)")

# FIXED: Verify sensor manager is properly initialized before using it
if not hasattr(sensor_manager, 'logger'):
    logger.error("Sensor manager missing logger attribute - reinitializing")
    sensor_manager.logger = logging.getLogger('sensors.robust_sensor_manager')

# Initialize relay controller
def initialize_modbus_relay_controller():
    """Initialize a Modbus TCP relay controller if available"""
    try:
        from controllers.modbus_relay_controller import ModbusRelayController
        controller = ModbusRelayController(
            host='192.168.1.200',
            port=4196,
            device_id=1,
            channels=30,
            simulation_mode=False
        )
        logger.info(f"Attempting to connect to Modbus relay controller at 192.168.1.200:4196")
        
        if controller.connect():
            logger.info("Successfully connected to Modbus relay controller")
            return controller
        else:
            logger.warning("Failed to connect to Modbus relay controller, falling back to simulation mode")
            return None
    except ImportError:
        logger.warning("ModbusRelayController not available, missing pymodbus dependency")
        return None
    except Exception as e:
        logger.error(f"Error initializing Modbus relay controller: {e}")
        return None

# Initialize hardware
relay_controller = initialize_modbus_relay_controller()
if not relay_controller:
    logger.warning("Using simulation mode for relay control")

# Initialize IR controller for air conditioner
def initialize_ir_controller():
    """Initialize IR controller for ESP32-based IR transmitter"""
    try:
        from controllers.ir_controller import IRController
        controller = IRController(
            esp32_ip='192.168.1.150',  # Configure this IP for your ESP32
            esp32_port=80,
            timeout=5
        )
        logger.info(f"Attempting to connect to ESP32 IR transmitter at 192.168.1.150:80")
        
        if controller.connect():
            logger.info("Successfully connected to ESP32 IR transmitter")
            return controller
        else:
            logger.warning("Failed to connect to ESP32 IR transmitter, IR control disabled")
            return None
    except Exception as e:
        logger.error(f"Error initializing IR controller: {e}")
        return None

ir_controller = initialize_ir_controller()
if not ir_controller:
    logger.warning("IR control disabled - ESP32 not available")

# Initialize controllers with real hardware
light_controller = LightController(db, socketio, relay_controller=relay_controller)
# FIXED: Don't pass sensor_manager to nutrient_controller if Arduino doesn't have pump endpoints
nutrient_controller = NutrientController(db, socketio, sensor_manager=None)  # Use None instead of sensor_manager
environment_controller = EnvironmentController(db, socketio, sensor_manager, relay_controller=relay_controller, light_controller=light_controller, ir_controller=ir_controller)
watering_controller = WateringController(db, socketio, relay_controller=relay_controller, light_controller=light_controller)
growing_profile_controller = GrowingProfileController(db)

# Initialize scheduler
try:
    scheduler = Scheduler(
        light_controller=light_controller,
        nutrient_controller=nutrient_controller,
        environment_controller=environment_controller,
        watering_controller=watering_controller,
        sensor_manager=sensor_manager  # Pass the properly initialized sensor manager
    )
    logger.info("Scheduler initialized with all controllers and sensor manager")
except Exception as e:
    logger.error(f"Failed to initialize scheduler: {e}")
    raise

# Initialize debug monitor
debug_monitor = DebugMonitor()

# Register API routes blueprint
from routes.api_routes import init_routes
api_blueprint = init_routes(environment_controller, sensor_manager)
app.register_blueprint(api_blueprint, url_prefix='/api')

# MANDATORY error handlers to prevent WSGI errors
@app.errorhandler(Exception)
def handle_all_exceptions(e):
    """Catch ALL exceptions to prevent WSGI errors"""
    logger.error(f"Unhandled exception: {e}", exc_info=True)
    try:
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500
    except:
        return "Internal Server Error", 500

@app.errorhandler(404)
def handle_404(e):
    return jsonify({'status': 'error', 'message': 'Not found'}), 404

@app.errorhandler(500)
def handle_500(e):
    return jsonify({'status': 'error', 'message': 'Internal error'}), 500

# Web routes
@app.route('/')
def index():
    try:
        return render_template('dashboard.html')
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        return "Dashboard temporarily unavailable", 500

@app.route('/lights')
def lights():
    try:
        return render_template('lights.html')
    except Exception as e:
        logger.error(f"Lights page error: {e}")
        return "Lights page temporarily unavailable", 500

@app.route('/environment')
def environment():
    try:
        return render_template('environment.html')
    except Exception as e:
        logger.error(f"Environment page error: {e}")
        return "Environment page temporarily unavailable", 500

@app.route('/nutrients')
def nutrients():
    try:
        return render_template('nutrients.html')
    except Exception as e:
        logger.error(f"Nutrients page error: {e}")
        return "Nutrients page temporarily unavailable", 500

@app.route('/watering')
def watering():
    try:
        return render_template('watering.html')
    except Exception as e:
        logger.error(f"Watering page error: {e}")
        return "Watering page temporarily unavailable", 500

@app.route('/settings')
def settings():
    try:
        return render_template('settings.html')
    except Exception as e:
        logger.error(f"Settings page error: {e}")
        return "Settings page temporarily unavailable", 500

@app.route('/profiles')
def profiles():
    try:
        return render_template('profiles.html')
    except Exception as e:
        logger.error(f"Profiles page error: {e}")
        return "Profiles page temporarily unavailable", 500

@app.route('/logs')
def logs():
    try:
        return render_template('logs.html')
    except Exception as e:
        logger.error(f"Logs page error: {e}")
        return "Logs page temporarily unavailable", 500

# API endpoints with proper hardware integration
@app.route('/api/relay-states', methods=['GET'])
def get_relay_states():
    try:
        states = {}
        
        # Try to get actual hardware states
        if relay_controller and relay_controller.connected:
            try:
                physical_states = relay_controller.get_all_relay_states()
                if physical_states:
                    states = physical_states.copy()
            except Exception as e:
                logger.error(f"Hardware error reading relay states: {e}")
        
        # If hardware failed, use controller states
        if not states:
            # Light controller now uses channels 1-14
            for channel in range(1, 15):
                zone_id = ((channel - 1) // 2) + 1
                if zone_id in light_controller.light_zones:
                    states[channel] = light_controller.zone_states.get(zone_id, False)
                else:
                    states[channel] = False
            
            # Channel 0 unused
            states[0] = False
            
            # Water pump
            states[WATER_PUMP_RELAY_CHANNEL] = getattr(watering_controller, 'pump_state', False)
            
            # Remaining channels
            for channel in range(15, 30):
                if channel != WATER_PUMP_RELAY_CHANNEL:  # Don't overwrite water pump state
                    states[channel] = False
        
        return jsonify({
            "status": "success",
            "states": {str(k): v for k, v in states.items()},  # Ensure all keys are strings
            "connected": relay_controller.connected if relay_controller else False,
            "simulation_mode": not bool(relay_controller and relay_controller.connected),
            "timestamp": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }), 200
        
    except Exception as e:
        logger.error(f"Error in relay states endpoint: {e}")
        return jsonify({"status": "error", "message": "Failed to get states"}), 500

@app.route('/api/relay-control', methods=['POST'])
def relay_control():
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"status": "error", "message": "No data"}), 400

        channel = int(data.get('channel', -1))
        state = bool(data.get('state', False))

        if not (0 <= channel <= 29):
            return jsonify({"status": "error", "message": "Invalid channel"}), 400

        success = False
        
        # Try hardware control first
        if relay_controller and relay_controller.connected:
            try:
                success = relay_controller.set_relay(channel, state)
                if success:
                    logger.info(f"HARDWARE: Relay {channel} set to {'ON' if state else 'OFF'}")
                else:
                    logger.error(f"HARDWARE: Failed to set relay {channel}")
            except Exception as e:
                logger.error(f"Hardware relay control error: {e}")
        
        # Update controller states and emit events safely
        if channel == WATER_PUMP_RELAY_CHANNEL:
            watering_controller.pump_state = state
            # Safe SocketIO emit with error handling
            try:
                socketio.emit('pump_state_change', {
                    'state': state,
                    'time': datetime.datetime.now().strftime("%H:%M:%S")
                })
            except Exception as socket_error:
                logger.warning(f"Socket emit error (pump_state_change): {socket_error}")
        else:
            # Update light controller - channels 1-14 are for lights
            if 1 <= channel <= 14:
                zone_id = ((channel - 1) // 2) + 1
                if zone_id in light_controller.light_zones:
                    light_controller.zone_states[zone_id] = state

        status_msg = "HARDWARE" if (relay_controller and relay_controller.connected) else "SIMULATION"
        logger.info(f"{status_msg}: Relay {channel} -> {'ON' if state else 'OFF'}")

        return jsonify({
            "status": "success",
            "message": f"Relay {channel} {'ON' if state else 'OFF'}",
            "channel": channel,
            "state": state
        }), 200

    except Exception as e:
        logger.error(f"Relay control error: {e}")
        return jsonify({"status": "error", "message": "Control failed"}), 500

@app.route('/api/relay-status/<int:channel>', methods=['GET'])
def relay_status(channel):
    """Get status of a specific relay channel - simple indicator"""
    try:
        if not (0 <= channel <= 29):
            return jsonify({"status": "error", "message": "Invalid channel"}), 400

        state = False
        
        # Get state from relay controller
        if relay_controller:
            try:
                state = relay_controller.get_relay(channel)
            except:
                # Fallback to cached state
                if hasattr(relay_controller, '_relay_states') and 0 <= channel < len(relay_controller._relay_states):
                    state = relay_controller._relay_states[channel]
        
        return jsonify({
            "status": "success",
            "channel": channel,
            "state": state
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": "Status check failed"}), 500
@app.route('/api/light-schedule/simple', methods=['GET', 'POST'])
def simple_light_schedule():
    try:
        if request.method == 'POST':
            data = request.get_json(force=True)
            if not data:
                return jsonify({"status": "error", "message": "No data"}), 400
            
            schedule = {
                'id': 1,
                'name': 'Main Schedule',
                'start_time': data.get('start_time', '06:00'),
                'end_time': data.get('end_time', '22:00'),
                'enabled': data.get('enabled', True),
                'affected_zones': data.get('affected_zones', [1, 2, 3, 4, 5, 6, 7])
            }
            
            try:
                success = db.save_light_schedules([schedule])
                if success:
                    light_controller.schedules = [schedule]
                    return jsonify({'status': 'success', 'message': 'Schedule saved'})
                else:
                    return jsonify({'status': 'error', 'message': 'Failed to save'}), 500
            except Exception as db_error:
                logger.error(f"Database error: {db_error}")
                return jsonify({'status': 'error', 'message': 'Database error'}), 500
        
        else:
            # GET request
            schedules = light_controller.get_schedules()
            if schedules:
                return jsonify({'status': 'success', 'schedule': schedules[0]})
            else:
                return jsonify({
                    'status': 'success',
                    'schedule': {'start_time': '06:00', 'end_time': '22:00', 'enabled': True}
                })
                
    except Exception as e:
        logger.error(f"Schedule error: {e}")
        return jsonify({"status": "error", "message": "Schedule error"}), 500

@app.route('/api/watering/settings', methods=['GET', 'POST'])
def watering_settings():
    try:
        if request.method == 'POST':
            data = request.get_json(force=True)
            if not data:
                return jsonify({"status": "error", "message": "No data provided"}), 400
            
            # Log the received data for debugging
            logger.info(f"Received watering settings data: {data}")
            
            try:
                success = db.save_watering_settings(
                    enabled=data.get('enabled', True),
                    cycle_minutes_per_hour=float(data.get('cycle_minutes_per_hour', 5.0)),
                    active_hours_start=int(data.get('active_hours_start', 6)),
                    active_hours_end=int(data.get('active_hours_end', 20)),
                    cycle_seconds_on=int(data.get('cycle_seconds_on', 30)),
                    cycle_seconds_off=int(data.get('cycle_seconds_off', 300)),
                    day_cycle_seconds_on=int(data.get('day_cycle_seconds_on', data.get('cycle_seconds_on', 30))),
                    day_cycle_seconds_off=int(data.get('day_cycle_seconds_off', data.get('cycle_seconds_off', 300))),
                    night_cycle_seconds_on=int(data.get('night_cycle_seconds_on', data.get('cycle_seconds_on', 20))),
                    night_cycle_seconds_off=int(data.get('night_cycle_seconds_off', data.get('cycle_seconds_off', 600))),
                    daily_limit=float(data.get('daily_limit', 60.0)),
                    manual_watering_duration=int(data.get('manual_watering_duration', 10)),
                    max_continuous_run=int(data.get('max_continuous_run', 5)),
                    updated_at=int(time_module.time())
                )
                
                if success:
                    logger.info("Watering settings saved successfully to database")
                    
                    # CRITICAL FIX: Update the running watering controller with new settings
                    try:
                        update_success = watering_controller.update_settings(data)
                        if update_success:
                            logger.info("Watering controller updated successfully with new settings")
                            
                            # Return the updated settings so UI can reflect changes immediately
                            current_settings = watering_controller.get_settings()
                            return jsonify({
                                'status': 'success', 
                                'message': 'Settings saved and applied', 
                                'data': current_settings
                            })
                        else:
                            logger.error("Failed to update watering controller with new settings")
                            return jsonify({'status': 'error', 'message': 'Settings saved to database but failed to apply to controller'}), 500
                    except Exception as controller_error:
                        logger.error(f"Error updating watering controller: {controller_error}")
                        return jsonify({'status': 'error', 'message': f'Settings saved to database but controller update failed: {str(controller_error)}'}), 500
                else:
                    logger.error("Database save operation failed")
                    return jsonify({'status': 'error', 'message': 'Failed to save to database'}), 500
            except Exception as save_error:
                logger.error(f"Error saving watering settings: {save_error}")
                return jsonify({'status': 'error', 'message': f'Database error: {str(save_error)}'}), 500
        
        else:
            # GET request
            try:
                # Try to get settings from controller first (includes runtime state)
                if watering_controller:
                    current_settings = watering_controller.get_settings()
                    logger.info(f"Retrieved watering settings from controller: {current_settings}")
                    return jsonify({'status': 'success', 'data': current_settings})
                else:
                    # Fallback to database only
                    settings = db.get_watering_settings()
                    logger.info(f"Retrieved watering settings from database: {settings}")
                    
                    if settings:
                        return jsonify({'status': 'success', 'data': settings})
                    else:
                        # Return default settings if none found
                        default_settings = {
                            'enabled': True,
                            'cycle_minutes_per_hour': 5.0,
                            'active_hours_start': 6,
                            'active_hours_end': 20,
                            'cycle_seconds_on': 30,
                            'cycle_seconds_off': 300,
                            'day_cycle_seconds_on': 30,
                            'day_cycle_seconds_off': 300,
                            'night_cycle_seconds_on': 20,
                            'night_cycle_seconds_off': 600,
                            'daily_limit': 60.0,
                            'manual_watering_duration': 10,
                            'max_continuous_run': 5
                        }
                        logger.info("No settings found, returning defaults")
                        return jsonify({'status': 'success', 'data': default_settings})
                    
            except Exception as get_error:
                logger.error(f"Error getting watering settings: {get_error}")
                return jsonify({'status': 'error', 'message': f'Failed to get settings: {str(get_error)}'}), 500
                
    except Exception as e:
        logger.error(f"Error in watering settings endpoint: {e}")
        return jsonify({"status": "error", "message": f"Watering settings error: {str(e)}"}), 500

@app.route('/api/watering-settings', methods=['GET', 'POST'])
def watering_settings_hyphen():
    """Handle watering settings with hyphen URL (frontend compatibility)"""
    # Simply call the main watering_settings function to avoid code duplication
    return watering_settings()

@app.route('/api/environment/settings', methods=['GET', 'POST'])
def environment_settings():
    try:
        if request.method == 'POST':
            data = request.get_json(force=True)
            if not data:
                return jsonify({"status": "error", "message": "No data provided"}), 400
            
            # Handle basic environment settings
            basic_settings = {
                'temp_day': float(data.get('temp_day', 25.0)),
                'temp_night': float(data.get('temp_night', 20.0)),
                'humidity_min': float(data.get('humidity_min', 50.0)),
                'humidity_max': float(data.get('humidity_max', 70.0)),
                'co2_target': float(data.get('co2_target', 600.0))
            }
            
            # Handle CO2-specific settings for environment controller
            co2_settings = {}
            if 'co2_mode' in data:
                co2_settings['co2_mode'] = data['co2_mode']
            if 'co2_day_target' in data:
                co2_settings['co2_day_target'] = int(data['co2_day_target'])
            if 'co2_night_target' in data:
                co2_settings['co2_night_target'] = int(data['co2_night_target'])
            if 'co2_tolerance' in data:
                co2_settings['co2_tolerance'] = int(data['co2_tolerance'])
            if 'co2_day_start' in data:
                co2_settings['co2_day_start'] = int(data['co2_day_start'])
            if 'co2_day_end' in data:
                co2_settings['co2_day_end'] = int(data['co2_day_end'])
            
            try:
                # Save basic settings to database
                success = db.save_environment_settings(basic_settings)
                
                # Update environment controller with CO2 settings
                controller_success = True
                if co2_settings and environment_controller:
                    logger.info(f"Updating environment controller with CO2 settings: {co2_settings}")
                    controller_success = environment_controller.update_settings(co2_settings)
                    
                    # Save the controller's updated settings to database too
                    if controller_success:
                        environment_controller.save_settings()
                
                if success and controller_success:
                    logger.info("Environment settings saved successfully (database + controller)")
                    return jsonify({'status': 'success', 'message': 'Settings saved and applied to hardware'})
                else:
                    error_msg = "Database save failed" if not success else "Controller update failed"
                    logger.error(f"Environment settings save failed: {error_msg}")
                    return jsonify({'status': 'error', 'message': f'Failed to save settings: {error_msg}'}), 500
                    
            except Exception as save_error:
                logger.error(f"Error saving environment settings: {save_error}")
                return jsonify({'status': 'error', 'message': f'Save error: {str(save_error)}'}), 500
        
        else:
            # GET request - combine database settings with controller settings
            try:
                # Get basic settings from database
                db_settings = db.get_environment_settings()
                
                # Get CO2 settings from environment controller
                controller_settings = environment_controller.get_settings() if environment_controller else {}
                
                combined_settings = {
                    'temp_day': db_settings[1] if db_settings and len(db_settings) > 1 else 25.0,
                    'temp_night': db_settings[2] if db_settings and len(db_settings) > 2 else 20.0,
                    'humidity_min': db_settings[3] if db_settings and len(db_settings) > 3 else 50.0,
                    'humidity_max': db_settings[4] if db_settings and len(db_settings) > 4 else 70.0,
                    'co2_target': db_settings[5] if db_settings and len(db_settings) > 5 else 600.0,
                    # Add CO2 controller settings
                    'co2_mode': controller_settings.get('co2_mode', 'auto'),
                    'co2_day_target': controller_settings.get('co2_day_target', 1200),
                    'co2_night_target': controller_settings.get('co2_night_target', 800),
                    'co2_tolerance': controller_settings.get('co2_tolerance', 50),
                    'co2_day_start': controller_settings.get('co2_day_start', 6),
                    'co2_day_end': controller_settings.get('co2_day_end', 22),
                    'co2_state': controller_settings.get('co2_state', False),
                    'co2_hardware_connected': controller_settings.get('co2_hardware_connected', False)
                }
                
                return jsonify({
                    'status': 'success',
                    'settings': combined_settings
                })
                
            except Exception as get_error:
                logger.error(f"Error getting environment settings: {get_error}")
                return jsonify({'status': 'error', 'message': 'Failed to get settings'}), 500
                
    except Exception as e:
        logger.error(f"Error in environment settings endpoint: {e}")
        return jsonify({"status": "error", "message": "Environment settings error"}), 500

@app.route('/api/environment/status', methods=['GET'])
def environment_status():
    try:
        sensor_data = {
            'temperature': 22.5,
            'humidity': 65.0,
            'co2': 580.0,
            'connected': False
        }
        
        if sensor_manager:
            try:
                current_readings = sensor_manager.read_all_sensors()
                if current_readings:
                    sensor_data.update({
                        'temperature': current_readings.get('temperature', 22.5),
                        'humidity': current_readings.get('humidity', 65.0),
                        'co2': current_readings.get('co2', 580.0),
                        'connected': True
                    })
            except Exception as sensor_error:
                logger.warning(f"Error reading sensors: {sensor_error}")
        
        return jsonify({
            'status': 'success',
            'data': sensor_data,
            'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
        
    except Exception as e:
        logger.error(f"Error in environment status: {e}")
        return jsonify({"status": "error", "message": "Status error"}), 500

@app.route('/api/nutrient/settings', methods=['GET', 'POST'])
def nutrient_settings():
    try:
        if request.method == 'POST':
            data = request.get_json(force=True)
            if not data:
                return jsonify({"status": "error", "message": "No data provided"}), 400
            
            settings = {
                'ec_target': float(data.get('ec_target', 1.5)),
                'ph_target': float(data.get('ph_target', 6.0))
            }
            
            try:
                success = db.save_nutrient_settings(settings)
                if success:
                    return jsonify({'status': 'success', 'message': 'Settings saved'})
                else:
                    return jsonify({'status': 'error', 'message': 'Failed to save'}), 500
            except Exception as save_error:
                logger.error(f"Error saving nutrient settings: {save_error}")
                return jsonify({'status': 'error', 'message': 'Database error'}), 500
        
        else:
            try:
                settings = db.get_nutrient_settings()
                if settings:
                    return jsonify({
                        'status': 'success',
                        'settings': {
                            'ec_target': settings[1] if len(settings) > 1 else 1.5,
                            'ph_target': settings[2] if len(settings) > 2 else 6.0
                        }
                    })
                else:
                    return jsonify({
                        'status': 'success',
                        'settings': {'ec_target': 1.5, 'ph_target': 6.0}
                    })
            except Exception as get_error:
                logger.error(f"Error getting nutrient settings: {get_error}")
                return jsonify({'status': 'error', 'message': 'Failed to get settings'}), 500
                
    except Exception as e:
        logger.error(f"Error in nutrient settings endpoint: {e}")
        return jsonify({"status": "error", "message": "Nutrient settings error"}), 500

@app.route('/health')
def health_check():
    try:
        return jsonify({
            'status': 'healthy',
            'timestamp': datetime.datetime.utcnow().isoformat(),
            'hardware_connected': relay_controller.connected if relay_controller else False
        }), 200
    except Exception as e:
        logger.error(f"Health check error: {e}")
        return jsonify({'status': 'error'}), 500

# Socket.IO handlers - RE-ENABLED WITH PROPER ERROR HANDLING
@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    try:
        logger.info(f"Socket client connected from {request.remote_addr}")
        emit('connection_status', {
            'connected': True, 
            'timestamp': datetime.datetime.now().isoformat(),
            'server_time': datetime.datetime.now().strftime('%H:%M:%S')
        })
        return True
    except Exception as e:
        logger.error(f"Error in connect handler: {e}")
        return False

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    try:
        logger.info(f"Socket client disconnected from {request.remote_addr}")
        return True
    except Exception as e:
        logger.error(f"Error in disconnect handler: {e}")
        return False

@socketio.on('request_initial_data')
def handle_request_initial_data():
    """Handle requests for initial data"""
    try:
        initial_data = {
            'timestamp': datetime.datetime.now().isoformat(),
            'hardware_connected': relay_controller.connected if relay_controller else False,
            'server_status': 'running',
            'pump_state': getattr(watering_controller, 'pump_state', False)
        }
        emit('initial_data', initial_data)
        logger.info(f"Initial data sent to client: {initial_data}")
    except Exception as e:
        logger.error(f"Error sending initial data: {e}")

@socketio.on('ping')
def handle_ping():
    """Handle ping requests from client"""
    try:
        emit('pong', {'timestamp': datetime.datetime.now().isoformat()})
    except Exception as e:
        logger.error(f"Error in ping handler: {e}")

# ADD: New watering control endpoint
@app.route('/api/watering/control', methods=['POST'])
def watering_control():
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"status": "error", "message": "No data provided"}), 400
        
        action = data.get('action')
        duration = int(data.get('duration', 10))
        
        logger.info(f"Watering control request: action={action}, duration={duration}")
        
        if action == 'start_manual':
            success = False
            
            if relay_controller and relay_controller.connected:
                try:
                    success = relay_controller.set_relay(WATER_PUMP_RELAY_CHANNEL, True)
                    if success:
                        logger.info(f"HARDWARE: Water pump started for {duration} seconds")
                    else:
                        logger.error("HARDWARE: Failed to start water pump")
                except Exception as e:
                    logger.error(f"Hardware water pump control error: {e}")
            
            # Always update controller state regardless of hardware
            watering_controller.pump_state = True
            
            # Emit socket event
            try:
                socketio.emit('pump_state_change', {
                    'state': True,
                    'manual': True,
                    'duration': duration,
                    'time': datetime.datetime.now().strftime("%H:%M:%S")
                })
            except Exception as socket_error:
                logger.warning(f"Socket emit error: {socket_error}")
            
            status_msg = "HARDWARE" if (relay_controller and relay_controller.connected) else "SIMULATION"
            logger.info(f"{status_msg}: Manual watering started for {duration}s")
            
            return jsonify({
                "status": "success",
                "message": f"Manual watering started for {duration} seconds",
                "hardware": bool(relay_controller and relay_controller.connected)
            }), 200  # FIXED: Added missing closing parenthesis here
            
        elif action == 'stop':
            success = False
            
            if relay_controller and relay_controller.connected:
                try:
                    success = relay_controller.set_relay(WATER_PUMP_RELAY_CHANNEL, False)
                    if success:
                        logger.info("HARDWARE: Water pump stopped")
                except Exception as e:
                    logger.error(f"Hardware water pump stop error: {e}")
            
            # Always update controller state
            watering_controller.pump_state = False
            
            # Emit socket event
            try:
                socketio.emit('pump_state_change', {
                    'state': False,
                    'manual': False,
                    'time': datetime.datetime.now().strftime("%H:%M:%S")
                })
            except Exception as socket_error:
                logger.warning(f"Socket emit error: {socket_error}")
            
            status_msg = "HARDWARE" if (relay_controller and relay_controller.connected) else "SIMULATION"
            logger.info(f"{status_msg}: Watering stopped")
            
            return jsonify({
                "status": "success",
                "message": "Watering stopped",
                "hardware": bool(relay_controller and relay_controller.connected)
            }), 200
        
        else:
            return jsonify({"status": "error", "message": "Invalid action"}), 400
            
    except Exception as e:
        logger.error(f"Error in watering control: {e}")
        return jsonify({"status": "error", "message": "Control error"}), 500

@app.route('/api/watering/status', methods=['GET'])
def watering_status():
    try:
        return jsonify({
            'status': 'success',
            'data': {
                'pump_active': getattr(watering_controller, 'pump_state', False),
                'manual_active': getattr(watering_controller, 'pump_state', False),
                'last_watering': getattr(watering_controller, 'last_watering_time', None),
                'daily_usage': getattr(watering_controller, 'daily_usage', 0.0)
            },
            'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }), 200
    except Exception as e:
        logger.error(f"Error in watering status: {e}")
        return jsonify({"status": "error", "message": "Status error"}), 500

# ADD: Manual control endpoint for unified device control
@app.route('/api/manual-control', methods=['POST'])
def manual_control():
    """Unified manual control endpoint for all device types"""
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"status": "error", "message": "No data provided"}), 400
        
        device_type = data.get('type')
        logger.info(f"Manual control request: {data}")
        logger.info(f"Device type received: '{device_type}'")
        logger.info(f"All data keys: {list(data.keys())}")
        
        if device_type == 'light':
            light_id = data.get('id')
            state = data.get('state')
            if light_id is None or state is None:
                return jsonify({"status": "error", "message": "Missing light id or state"}), 400
            
            success = light_controller.manual_control(light_id, state)
            return jsonify({"status": "success" if success else "error"}), 200
            
        elif device_type == 'watering':
            command = data.get('command')
            duration = data.get('duration', 10)
            
            if command == 'start':
                success = False
                if relay_controller and relay_controller.connected:
                    try:
                        success = relay_controller.set_relay(WATER_PUMP_RELAY_CHANNEL, True)
                    except Exception as e:
                        logger.error(f"Hardware water pump control error: {e}")
                
                watering_controller.pump_state = True
                
                socketio.emit('pump_state_change', {
                    'state': True,
                    'manual': True,
                    'duration': duration,
                    'time': datetime.datetime.now().strftime("%H:%M:%S")
                })
                return jsonify({"status": "success", "message": f"Watering started for {duration}s"}), 200
                
            elif command == 'stop':
                success = False
                if relay_controller and relay_controller.connected:
                    try:
                        success = relay_controller.set_relay(WATER_PUMP_RELAY_CHANNEL, False)
                    except Exception as e:
                        logger.error(f"Hardware water pump stop error: {e}")
                
                watering_controller.pump_state = False
                
                socketio.emit('pump_state_change', {
                    'state': False,
                    'manual': False,
                    'time': datetime.datetime.now().strftime("%H:%M:%S")
                })
                return jsonify({"status": "success", "message": "Watering stopped"}), 200
            else:
                return jsonify({"status": "error", "message": "Invalid watering command"}), 400
            
        elif device_type == 'nutrient':
            pump_id = data.get('pump_id')
            duration = data.get('duration', 5)
            
            if pump_id in ['nutrient', 'ph_up', 'ph_down']:
                success = nutrient_controller.manual_control(pump_id, duration)
                return jsonify({"status": "success" if success else "error"}), 200
            else:
                return jsonify({"status": "error", "message": "Invalid pump_id"}), 400
                
        elif device_type == 'environment':
            device_id = data.get('device_id')
            state = data.get('state')
            
            if device_id and state is not None:
                success = environment_controller.manual_control(device_id, state)
                return jsonify({"status": "success" if success else "error"}), 200
            else:
                return jsonify({"status": "error", "message": "Missing device_id or state"}), 400
                
        elif device_type == 'air_conditioner':
            if not environment_controller:
                return jsonify({"status": "error", "message": "Environment controller not available"}), 503
                
            try:
                command = data.get('command')
                if not command:
                    return jsonify({"status": "error", "message": "Missing command parameter"}), 400
                
                if command == 'power':
                    power_state = data.get('state')
                    if power_state is None:
                        return jsonify({"status": "error", "message": "Missing state parameter"}), 400
                    
                    success = environment_controller.set_ac_power(bool(power_state))
                    if success:
                        return jsonify({
                            "status": "success",
                            "message": f"AC power {'ON' if power_state else 'OFF'}",
                            "data": {"power": bool(power_state)}
                        }), 200
                    else:
                        return jsonify({"status": "error", "message": "Failed to control AC power"}), 500
                        
                elif command == 'temperature':
                    temperature = data.get('temperature')
                    if temperature is None:
                        return jsonify({"status": "error", "message": "Missing temperature parameter"}), 400
                    
                    try:
                        temp_val = int(temperature)
                        if not (16 <= temp_val <= 30):
                            return jsonify({"status": "error", "message": "Temperature must be between 16-30°C"}), 400
                    except (ValueError, TypeError):
                        return jsonify({"status": "error", "message": "Invalid temperature value"}), 400
                        
                    success = environment_controller.set_ac_temperature(temp_val)
                    if success:
                        return jsonify({
                            "status": "success",
                            "message": f"AC temperature set to {temp_val}°C",
                            "data": {"temperature": temp_val}
                        }), 200
                    else:
                        return jsonify({"status": "error", "message": "Failed to set AC temperature"}), 500
                        
                elif command == 'mode':
                    mode = data.get('mode')
                    if not mode:
                        return jsonify({"status": "error", "message": "Missing mode parameter"}), 400
                    
                    valid_modes = ['cool', 'heat', 'fan', 'auto', 'dry']
                    if mode not in valid_modes:
                        return jsonify({
                            "status": "error", 
                            "message": f"Invalid mode. Must be one of: {', '.join(valid_modes)}"
                        }), 400
                        
                    success = environment_controller.set_ac_mode(mode)
                    if success:
                        return jsonify({
                            "status": "success",
                            "message": f"AC mode set to {mode}",
                            "data": {"mode": mode}
                        }), 200
                    else:
                        return jsonify({"status": "error", "message": "Failed to set AC mode"}), 500
                        
                elif command == 'fan_speed':
                    fan_speed = data.get('fan_speed')
                    if not fan_speed:
                        return jsonify({"status": "error", "message": "Missing fan_speed parameter"}), 400
                    
                    valid_speeds = ['low', 'medium', 'high', 'auto']
                    if fan_speed not in valid_speeds:
                        return jsonify({
                            "status": "error", 
                            "message": f"Invalid fan speed. Must be one of: {', '.join(valid_speeds)}"
                        }), 400
                        
                    success = environment_controller.set_ac_fan_speed(fan_speed)
                    if success:
                        return jsonify({
                            "status": "success",
                            "message": f"AC fan speed set to {fan_speed}",
                            "data": {"fan_speed": fan_speed}
                        }), 200
                    else:
                        return jsonify({"status": "error", "message": "Failed to set AC fan speed"}), 500
                        
                else:
                    return jsonify({
                        "status": "error", 
                        "message": f"Unknown command: {command}. Valid commands: power, temperature, mode, fan_speed"
                    }), 400
                    
            except Exception as e:
                logger.error(f"Error controlling air conditioner: {e}")
                return jsonify({"status": "error", "message": "Internal server error"}), 500
        
        else:
            return jsonify({"status": "error", "message": f"Unknown device type: {device_type}"}), 400
            
    except Exception as e:
        logger.error(f"Error in manual control: {e}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500

# ADD: Pump status endpoint
@app.route('/api/pump/status', methods=['GET'])
def pump_status():
    """Get current pump status including state, daily total, and manual mode info"""
    try:
        # Get current cycle information from watering controller
        current_cycle_info = None
        if watering_controller:
            try:
                cycle_on, cycle_off, cycle_type = watering_controller._get_current_cycle_settings()
                current_cycle_info = {
                    'cycle_type': cycle_type,
                    'cycle_seconds_on': cycle_on,
                    'cycle_seconds_off': cycle_off,
                    'lights_on': cycle_type == 'day' if cycle_type in ['day', 'night'] else None
                }
            except Exception as e:
                logger.warning(f"Error getting current cycle info for pump status: {e}")
                current_cycle_info = {
                    'cycle_type': 'main',
                    'cycle_seconds_on': getattr(watering_controller, 'cycle_seconds_on', 30),
                    'cycle_seconds_off': getattr(watering_controller, 'cycle_seconds_off', 270),
                    'lights_on': None
                }
        
        return jsonify({
            'status': 'success',
            'data': {
                'state': getattr(watering_controller, 'pump_state', False),
                'daily_total': getattr(watering_controller, 'daily_run_minutes', 0.0),
                'manual_mode': getattr(watering_controller, 'manual_mode', False),
                'manual_end_time': getattr(watering_controller, 'manual_end_time', None),
                'last_watering_time': getattr(watering_controller, 'last_watering_time', None),
                'connected': relay_controller.connected if relay_controller else False,
                'current_cycle': current_cycle_info  # Add current cycle information
            },
            'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }), 200
    except Exception as e:
        logger.error(f"Error getting pump status: {e}")
        return jsonify({"status": "error", "message": "Failed to get pump status"}), 500

# ADD: Nutrient settings endpoint (alias for frontend compatibility)
@app.route('/api/nutrient-settings', methods=['GET', 'POST'])
def nutrient_settings_alias():
    """Alias for /api/nutrient/settings for frontend compatibility"""
    return nutrient_settings()

# ADD: Events endpoint for dosing history and other events
@app.route('/api/events', methods=['GET'])
def get_events():
    """Get system events including dosing history"""
    try:
        event_type = request.args.get('type')
        limit = int(request.args.get('limit', 50))
        
        # For now, return mock events data - you can connect this to a real events system later
        events = []
        if event_type == 'nutrient_dose':
            # Mock nutrient dosing events
            import time
            current_time = time.time()
            for i in range(min(limit, 10)):
                events.append({
                    'id': i + 1,
                    'timestamp': current_time - (i * 3600),  # Each event 1 hour apart
                    'event_type': 'nutrient_dose',
                    'details': {
                        'pump': ['nutrient', 'ph_up', 'ph_down'][i % 3],
                        'duration': 5 + (i % 3),
                        'triggered_by': 'manual' if i % 2 else 'auto'
                    }
                })
        
        return jsonify({
            'status': 'success',
            'events': events,
            'total': len(events)
        }), 200
    except Exception as e:
        logger.error(f"Error getting events: {e}")
        return jsonify({"status": "error", "message": "Failed to get events"}), 500

# ADD: Reset environment endpoint
@app.route('/api/reset-environment', methods=['POST'])
def reset_environment():
    """Reset environment controls to default state"""
    try:
        success = environment_controller.manual_control('reset', None)
        return jsonify({
            "status": "success" if success else "error",
            "message": "Environment reset to defaults" if success else "Failed to reset environment"
        }), 200
    except Exception as e:
        logger.error(f"Error resetting environment: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ADD: Sensors endpoint (alias for environment status)
@app.route('/api/sensors', methods=['GET'])
def sensors():
    """Get current sensor data (alias for environment status)"""
    return environment_status()

# ADD: Sensor data endpoint (another alias for frontend compatibility)
@app.route('/api/sensor-data', methods=['GET'])
def sensor_data():
    """Get current sensor data (alias for environment status)"""
    return environment_status()

# ADD: Circulation fan control endpoint - ENSURE it's before catch-all route
@app.route('/api/environment/fan-control', methods=['POST'])
def environment_fan_control():
    """Control circulation fans on channels 17-24"""
    try:
        logger.info("🌀 Fan control endpoint called")
        
        data = request.get_json(force=True)
        if not data:
            logger.error("🌀 No data provided to fan control endpoint")
            return jsonify({"status": "error", "message": "No data provided"}), 400
        
        fan_mode = data.get('fan_mode', 'intermittent')
        fan_on_minutes = int(data.get('fan_on_minutes', 5))
        fan_off_minutes = int(data.get('fan_off_minutes', 10))
        
        logger.info(f"🌀 Fan control request: mode={fan_mode}, on={fan_on_minutes}min, off={fan_off_minutes}min")
        
        # Update environment controller settings
        if environment_controller:
            settings_data = {
                'fan_mode': fan_mode,
                'fan_on_minutes': fan_on_minutes,
                'fan_off_minutes': fan_off_minutes
            }
            
            logger.info(f"🌀 Updating environment controller with: {settings_data}")
            success = environment_controller.update_settings(settings_data)
            if success:
                logger.info(f"🌀 ✅ Environment controller updated successfully")
                return jsonify({
                    "status": "success",
                    "message": f"Circulation fans set to {fan_mode} mode",
                    "settings": settings_data
                }), 200
            else:
                logger.error("🌀 ❌ Failed to update environment controller fan settings")
                return jsonify({"status": "error", "message": "Failed to update fan settings"}), 500
        else:
            logger.error("🌀 ❌ Environment controller not available")
            return jsonify({"status": "error", "message": "Environment controller not available"}), 500
            
    except Exception as e:
        logger.error(f"🌀 ❌ Error in fan control endpoint: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/environment/fan-status', methods=['GET'])
def environment_fan_status():
    """Get current status of individual circulation fans"""
    try:
        if environment_controller:
            # Get cached states from environment controller
            cached_fan_states = getattr(environment_controller, 'fan_states', {})
            fan_mode = getattr(environment_controller, 'fan_mode', 'unknown')
            fan_mapping = getattr(environment_controller, 'fan_mapping', {})
            
            # Get actual hardware relay states for channels 17-24
            actual_fan_states = {}
            if relay_controller:
                try:
                    # Get the channels we need to read (17-24 for circulation fans)
                    fan_channels = list(fan_mapping.values())  # Should be [17, 18, 19, 20, 21, 22, 23, 24]
                    
                    # Read actual relay states from hardware using fan-specific method
                    hardware_states = relay_controller.read_actual_fan_states(fan_channels)
                    
                    if hardware_states:
                        # Map fan device names to their actual hardware states
                        for device_name, channel in fan_mapping.items():
                            actual_state = hardware_states.get(channel, False)
                            actual_fan_states[device_name] = actual_state
                    else:
                        actual_fan_states = cached_fan_states
                        
                except Exception as e:
                    actual_fan_states = cached_fan_states
            else:
                actual_fan_states = cached_fan_states
            
            return jsonify({
                "status": "success",
                "fan_states": actual_fan_states,
                "cached_states": cached_fan_states,
                "fan_mode": fan_mode,
                "fan_mapping": fan_mapping,
                "hardware_read": len(actual_fan_states) > 0
            }), 200
        else:
            return jsonify({"status": "error", "message": "Environment controller not available"}), 500
            
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# CRITICAL: Fix all route handlers to ensure they ALWAYS return a response
@app.before_request
def before_request():
    """Ensure request has proper headers"""
    pass

@app.after_request
def after_request(response):
    """Ensure response has proper headers - fixed for passthrough mode"""
    try:
        # Only check responses that are not in passthrough mode
        if hasattr(response, 'direct_passthrough') and response.direct_passthrough:
            return response
            
        # Only check JSON responses with status 200
        if (response.status_code == 200 and 
            response.content_type and 
            'application/json' in response.content_type):
            
            # Safely check if response has data
            try:
                data = response.get_data()
                if not data or data == b'':
                    logger.warning("Empty JSON response detected, fixing...")
                    response.data = b'{"status": "success"}'
                    response.headers['Content-Type'] = 'application/json'
            except (AttributeError, RuntimeError):
                # Response is in passthrough mode or cannot be read
                pass
                
        return response
    except Exception as e:
        logger.error(f"Error in after_request: {e}")
        return response

# Add a catch-all route for missing API endpoints - MOVED AFTER all specific routes
@app.route('/api/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE'])
def catch_all_api(path):
    """Catch any undefined API endpoints"""
    logger.warning(f"Undefined API endpoint requested: /api/{path}")
    return jsonify({
        "status": "error", 
        "message": f"API endpoint '/api/{path}' not found",
        "available_endpoints": [
            "/api/relay-states",
            "/api/relay-control", 
            "/api/light-schedule/simple",
            "/api/watering/settings",
            "/api/watering-settings",
            "/api/watering/control",
            "/api/watering/status",
            "/api/environment/settings",
            "/api/environment/status",
            "/api/environment/fan-control",  # This should now work
            "/api/nutrient/settings",
            "/api/manual-control",
            "/api/pump/status",
            "/api/nutrient-settings",
            "/api/events",
            "/api/reset-environment",
            "/api/sensor-data",
            "/api/sensors",
            "/health"
        ]
    }), 404

# Main entry point - FIXED SERVER STARTUP CODE
if __name__ == '__main__':
    try:
        import socket as socket_module
        
        # FIXED: Use consistent port configuration
        port = int(os.environ.get('PORT', 5002))
        host = os.environ.get('HOST', '0.0.0.0')
        
        logger.info("Starting Vertical Farm Control System...")
        
        # Check if port is available
        try:
            test_socket = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)
            test_socket.bind((host, port))
            test_socket.close()
            logger.info(f"Port {port} is available")
        except OSError as e:
            logger.error(f"Port {port} is already in use: {e}")
            print(f"ERROR: Port {port} is already in use. Please stop other services or use a different port.")
            exit(1)
        
        # Start the scheduler
        try:
            if scheduler.start():
                logger.info("Scheduler started successfully - cyclic watering now active")
            else:
                logger.error("Failed to start scheduler - cyclic watering will not work")
        except Exception as scheduler_error:
            logger.error(f"Error starting scheduler: {scheduler_error}")
            raise
        
        logger.info(f"Server will be accessible at http://{host}:{port}")
        logger.info(f"Socket.IO endpoint will be available at http://{host}:{port}/socket.io/")
        print(f"\nServer starting at http://{host}:{port}")
        print(f"Socket.IO endpoint: http://{host}:{port}/socket.io/")
        print("Press Ctrl+C to stop the server\n")
        
        # FIXED: Use socketio.run with proper configuration
        socketio.run(
            app,
            host=host,
            port=port,
            debug=False,
            allow_unsafe_werkzeug=True,
            use_reloader=False,
            log_output=True  # Enable request logging
        )
        
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down gracefully...")
        print("\n🛑 Shutting down server...")
    except Exception as e:
        logger.error(f"Failed to start server: {e}", exc_info=True)
        print(f"ERROR: Failed to start server: {e}")
    finally:
        # IMPROVED: Better shutdown handling
        try:
            if scheduler and hasattr(scheduler, 'stop'):
                logger.info("Stopping scheduler...")
                scheduler.stop()
                logger.info("Scheduler stopped")
        except KeyboardInterrupt:
            # If user presses Ctrl+C again during shutdown, exit immediately
            logger.warning("Force shutdown requested")
            print("🚨 Force shutdown - exiting immediately")
            import sys
            sys.exit(0)
        except Exception as stop_error:
            logger.error(f"Error stopping scheduler: {stop_error}")
        
        logger.info("Server shutdown complete")
        print("✅ Server shutdown complete")

@app.route('/test-socketio')
def test_socketio():
    """Test endpoint to verify Socket.IO is working"""
    try:
        # Try to emit a test event
        socketio.emit('test_event', {'message': 'Socket.IO is working', 'timestamp': datetime.datetime.now().isoformat()})
        return jsonify({
            'status': 'success',
            'message': 'Socket.IO test successful',
            'socketio_configured': True
        })
    except Exception as e:
        logger.error(f"Socket.IO test failed: {e}")
        return jsonify({
            'status': 'error',
            'message': f'Socket.IO test failed: {str(e)}',
            'socketio_configured': False
        }), 500

# Air Conditioner status endpoint (robust)
@app.route('/api/air-conditioner/status', methods=['GET'])
def air_conditioner_status():
    """Get current air conditioner status with comprehensive error handling"""
    try:
        if not environment_controller:
            return jsonify({
                "status": "error", 
                "message": "Environment controller not available"
            }), 503
        
        # Check if IR controller is available
        if not hasattr(environment_controller, 'ir_controller') or not environment_controller.ir_controller:
            return jsonify({
                "status": "error",
                "message": "IR controller not configured"
            }), 503
        
        ac_status = environment_controller.get_ac_status()
        
        if not ac_status:
            return jsonify({
                "status": "error",
                "message": "Failed to retrieve AC status"
            }), 500
        
        # Ensure the status has all required fields with defaults
        validated_status = {
            'settings': {
                'brand': 'Airfel',  # Fixed to Airfel
                'power': ac_status.get('settings', {}).get('power', False),
                'temperature': ac_status.get('settings', {}).get('temperature', 24),
                'mode': ac_status.get('settings', {}).get('mode', 'cool'),
                'fan_speed': ac_status.get('settings', {}).get('fan_speed', 'medium')
            },
            'relay_state': ac_status.get('relay_state', False),
            'relay_channel': ac_status.get('relay_channel', 15),
            'ir_controller': {
                'connected': ac_status.get('ir_controller', {}).get('connected', False),
                'last_error': ac_status.get('ir_controller', {}).get('last_error'),
                'esp32_ip': ac_status.get('ir_controller', {}).get('esp32_ip', 'Unknown')
            }
        }
        
        return jsonify({
            'status': 'success',
            'data': validated_status,
            'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }), 200
        
    except Exception as e:
        logger.error(f"Error getting AC status: {e}")
        return jsonify({
            "status": "error", 
            "message": "Internal server error while retrieving AC status"
        }), 500

# Air Conditioner configuration endpoint (simplified for Airfel only)
@app.route('/api/air-conditioner/configure', methods=['POST'])
def configure_air_conditioner():
    """Configure air conditioner settings (Airfel brand only)"""
    try:
        if not environment_controller:
            return jsonify({
                "status": "error", 
                "message": "Environment controller not available"
            }), 503
        
        data = request.get_json(force=True)
        if not data:
            return jsonify({
                "status": "error", 
                "message": "No configuration data provided"
            }), 400
        
        # Since we only support Airfel, we can configure other settings
        updated_settings = {}
        
        # Optional temperature setting
        if 'temperature' in data:
            try:
                temp = int(data['temperature'])
                if 16 <= temp <= 30:
                    success = environment_controller.set_ac_temperature(temp)
                    if success:
                        updated_settings['temperature'] = temp
                    else:
                        return jsonify({
                            "status": "error",
                            "message": "Failed to set temperature"
                        }), 500
                else:
                    return jsonify({
                        "status": "error",
                        "message": "Temperature must be between 16-30°C"
                    }), 400
            except (ValueError, TypeError):
                return jsonify({
                    "status": "error",
                    "message": "Invalid temperature value"
                }), 400
        
        # Optional mode setting
        if 'mode' in data:
            mode = data['mode']
            valid_modes = ['cool', 'heat', 'fan', 'auto', 'dry']
            if mode in valid_modes:
                success = environment_controller.set_ac_mode(mode)
                if success:
                    updated_settings['mode'] = mode
                else:
                    return jsonify({
                        "status": "error",
                        "message": "Failed to set mode"
                    }), 500
            else:
                return jsonify({
                    "status": "error",
                    "message": f"Invalid mode. Must be one of: {', '.join(valid_modes)}"
                }), 400
        
        # Optional fan speed setting
        if 'fan_speed' in data:
            fan_speed = data['fan_speed']
            valid_speeds = ['low', 'medium', 'high', 'auto']
            if fan_speed in valid_speeds:
                success = environment_controller.set_ac_fan_speed(fan_speed)
                if success:
                    updated_settings['fan_speed'] = fan_speed
                else:
                    return jsonify({
                        "status": "error",
                        "message": "Failed to set fan speed"
                    }), 500
            else:
                return jsonify({
                    "status": "error",
                    "message": f"Invalid fan speed. Must be one of: {', '.join(valid_speeds)}"
                }), 400
        
        if updated_settings:
            return jsonify({
                "status": "success",
                "message": f"AC configured successfully",
                "data": {
                    "brand": "Airfel",
                    "updated_settings": updated_settings
                }
            }), 200
        else:
            return jsonify({
                "status": "info",
                "message": "No settings to update",
                "data": {"brand": "Airfel"}
            }), 200
            
    except Exception as e:
        logger.error(f"Error configuring AC: {e}")
        return jsonify({
            "status": "error", 
            "message": "Internal server error while configuring AC"
        }), 500

# ADD: CO2 Control endpoint with hardcoded presets
@app.route('/api/co2/control', methods=['GET', 'POST'])
def co2_control():
    """CO2 control endpoint with hardcoded presets"""
    try:
        if request.method == 'POST':
            data = request.get_json(force=True)
            if not data:
                return jsonify({"status": "error", "message": "No data provided"}), 400
            
            action = data.get('action')
            logger.info(f"🌱 CO2 control request: {data}")
            
            if not environment_controller:
                return jsonify({"status": "error", "message": "Environment controller not available"}), 500
            
            if action == 'manual_on':
                success = environment_controller.manual_control('co2_injector', True)
                return jsonify({
                    "status": "success" if success else "error",
                    "message": f"CO2 injector {'activated' if success else 'failed to activate'}",
                    "co2_state": success
                }), 200
                
            elif action == 'manual_off':
                success = environment_controller.manual_control('co2_injector', False)
                return jsonify({
                    "status": "success" if success else "error",
                    "message": f"CO2 injector {'deactivated' if success else 'failed to deactivate'}",
                    "co2_state": False
                }), 200
                
            elif action == 'set_auto':
                environment_controller.co2_mode = 'auto'
                environment_controller.save_settings()
                return jsonify({
                    "status": "success",
                    "message": "CO2 set to automatic mode",
                    "co2_mode": "auto"
                }), 200
                
            elif action == 'set_preset':
                preset = data.get('preset', 'normal')
                
                # Hardcoded CO2 presets
                presets = {
                    'low': {'day': 400, 'night': 300, 'tolerance': 30},
                    'normal': {'day': 800, 'night': 500, 'tolerance': 50},
                    'high': {'day': 1200, 'night': 800, 'tolerance': 50},
                    'max': {'day': 1500, 'night': 1000, 'tolerance': 75},
                    'seedling': {'day': 600, 'night': 400, 'tolerance': 40},
                    'flowering': {'day': 1400, 'night': 900, 'tolerance': 60}
                }
                
                if preset not in presets:
                    return jsonify({
                        "status": "error", 
                        "message": f"Invalid preset. Available: {list(presets.keys())}"
                    }), 400
                
                preset_settings = presets[preset]
                co2_settings = {
                    'co2_day_target': preset_settings['day'],
                    'co2_night_target': preset_settings['night'],
                    'co2_tolerance': preset_settings['tolerance'],
                    'co2_mode': 'auto'
                }
                
                success = environment_controller.update_settings(co2_settings)
                if success:
                    environment_controller.save_settings()
                    logger.info(f"🌱 CO2 preset '{preset}' applied: {preset_settings}")
                    
                return jsonify({
                    "status": "success" if success else "error",
                    "message": f"CO2 preset '{preset}' {'applied' if success else 'failed'}",
                    "preset": preset,
                    "settings": preset_settings
                }), 200
                
            else:
                return jsonify({
                    "status": "error", 
                    "message": f"Unknown action: {action}. Valid: manual_on, manual_off, set_auto, set_preset"
                }), 400
                
        else:
            # GET request - return current CO2 status and available presets
            if not environment_controller:
                return jsonify({"status": "error", "message": "Environment controller not available"}), 500
                
            settings = environment_controller.get_settings()
            presets = {
                'low': {'day': 400, 'night': 300, 'tolerance': 30, 'description': 'Low CO2 (400/300 PPM)'},
                'normal': {'day': 800, 'night': 500, 'tolerance': 50, 'description': 'Normal CO2 (800/500 PPM)'},
                'high': {'day': 1200, 'night': 800, 'tolerance': 50, 'description': 'High CO2 (1200/800 PPM)'},
                'max': {'day': 1500, 'night': 1000, 'tolerance': 75, 'description': 'Maximum CO2 (1500/1000 PPM)'},
                'seedling': {'day': 600, 'night': 400, 'tolerance': 40, 'description': 'Seedling stage (600/400 PPM)'},
                'flowering': {'day': 1400, 'night': 900, 'tolerance': 60, 'description': 'Flowering stage (1400/900 PPM)'}
            }
            
            return jsonify({
                'status': 'success',
                'current_settings': {
                    'co2_mode': settings.get('co2_mode', 'auto'),
                    'co2_day_target': settings.get('co2_day_target', 1200),
                    'co2_night_target': settings.get('co2_night_target', 800),
                    'co2_tolerance': settings.get('co2_tolerance', 50),
                    'co2_state': settings.get('co2_state', False),
                    'hardware_connected': settings.get('co2_hardware_connected', False)
                },
                'available_presets': presets
            }), 200
            
    except Exception as e:
        logger.error(f"Error in CO2 control endpoint: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/environment/update', methods=['POST'])
def trigger_environment_update():
    """Trigger an immediate environment controller update for testing"""
    try:
        if environment_controller:
            logger.info("Manual environment controller update triggered via API")
            environment_controller.update()
            return jsonify({
                'status': 'success', 
                'message': 'Environment controller update triggered',
                'timestamp': time_module.time()
            })
        else:
            logger.warning("Environment controller update requested but controller not available")
            return jsonify({
                'status': 'error', 
                'message': 'Environment controller not available'
            }), 503
            
    except Exception as e:
        logger.error(f"Error triggering environment controller update: {e}")
        return jsonify({
            'status': 'error', 
            'message': f'Update failed: {str(e)}'
        }), 500

# Add periodic environment control task
def environment_control_task():
    """Periodic task to get sensor data and update environment controller"""
    try:
        if environment_controller and sensor_manager:
            # Get current sensor data
            sensor_data = sensor_manager.read_all_sensors()
            if sensor_data:
                logger.debug(f"🌱 Environment control task: CO2={sensor_data.get('co2', 'N/A')} PPM")
                environment_controller.update(sensor_data)
            else:
                logger.warning("🌱 Environment control task: No sensor data available")
    except Exception as e:
        logger.error(f"🌱 Environment control task error: {e}")

# Add environment control task to run every 30 seconds
scheduler.add_custom_task(
    'environment_control',
    environment_control_task,
    interval_seconds=5
)
logger.info("Added periodic environment control task (every 5s)")