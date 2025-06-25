import logging
from flask import Blueprint, jsonify, request

# Set up Blueprint for API routes
api_blueprint = Blueprint('api', __name__)
logger = logging.getLogger(__name__)

# Controllers will be set by init_routes function
_environment_controller = None
_sensor_manager = None

def init_routes(environment_controller, sensor_manager):
    """Initialize the routes with controller references"""
    global _environment_controller, _sensor_manager
    _environment_controller = environment_controller
    _sensor_manager = sensor_manager
    return api_blueprint

@api_blueprint.route('/sensors')
def get_sensor_data():
    """API endpoint to get current sensor readings"""
    try:
        # Get sensor manager from global variable
        sensor_manager = _sensor_manager
        
        if not sensor_manager:
            logger.error("Sensor manager not found")
            return jsonify({
                'status': 'error',
                'message': 'Sensor manager not initialized',
                'temperature': 20.0,  # Fallback values
                'humidity': 65.0, 
                'co2': 800.0
            })
        
        # Read all sensor data
        readings = sensor_manager.read_all_sensors()
        logger.debug(f"Retrieved sensor readings: {readings}")
        
        # Add device states (these should be replaced with actual states from your system)
        readings['devices'] = {
            'fans': {'state': False},
            'co2': {'state': False},
            'humidifier': {'state': False},
            'dehumidifier': {'state': False}
        }
        
        return jsonify(readings)
        
    except Exception as e:
        logger.error(f"Error fetching sensor data: {str(e)}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e),
            'temperature': 20.0,  # Fallback values
            'humidity': 65.0,
            'co2': 800.0
        })

@api_blueprint.route('/environment/settings', methods=['GET'])
def get_environment_settings():
    """API endpoint to get current environment settings"""
    try:
        environment_controller = _environment_controller
        
        if not environment_controller:
            logger.error("Environment controller not found")
            return jsonify({
                'status': 'error',
                'message': 'Environment controller not initialized'
            })
        
        settings = environment_controller.get_settings()
        logger.debug(f"Retrieved environment settings: {settings}")
        
        return jsonify({
            'status': 'success',
            'settings': settings
        })
        
    except Exception as e:
        logger.error(f"Error fetching environment settings: {str(e)}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        })

@api_blueprint.route('/environment/settings', methods=['POST'])
def update_environment_settings():
    """API endpoint to update environment settings"""
    try:
        environment_controller = _environment_controller
        
        if not environment_controller:
            logger.error("Environment controller not found")
            return jsonify({
                'status': 'error',
                'message': 'Environment controller not initialized'
            })
        
        data = request.get_json()
        if not data:
            return jsonify({
                'status': 'error',
                'message': 'No data provided'
            })
        
        # Update the settings
        success = environment_controller.update_settings(data)
        
        if success:
            # Save to database
            environment_controller.save_settings()
            logger.info(f"Environment settings updated: {data}")
            
            return jsonify({
                'status': 'success',
                'message': 'Settings updated successfully',
                'settings': environment_controller.get_settings()
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to update settings'
            })
        
    except Exception as e:
        logger.error(f"Error updating environment settings: {str(e)}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        })

# Manual control endpoint removed - now handled in main app.py with comprehensive device support

@api_blueprint.route('/co2/settings', methods=['GET'])
def get_co2_settings():
    """API endpoint to get current CO2 settings"""
    try:
        environment_controller = _environment_controller
        
        if not environment_controller:
            logger.error("Environment controller not found")
            return jsonify({
                'status': 'error',
                'message': 'Environment controller not initialized'
            })
        
        settings = environment_controller.get_settings()
        
        # Extract CO2-specific settings
        co2_settings = {
            'co2_mode': settings.get('co2_mode', 'auto'),
            'co2_day_target': settings.get('co2_day_target', 1200),
            'co2_night_target': settings.get('co2_night_target', 800),
            'co2_tolerance': settings.get('co2_tolerance', 25),
            'co2_day_start': settings.get('co2_day_start', 6),
            'co2_day_end': settings.get('co2_day_end', 22),
            'co2_state': settings.get('co2_state', False)
        }
        
        logger.debug(f"Retrieved CO2 settings: {co2_settings}")
        
        return jsonify({
            'status': 'success',
            'settings': co2_settings
        })
        
    except Exception as e:
        logger.error(f"Error fetching CO2 settings: {str(e)}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        })

@api_blueprint.route('/co2/settings', methods=['POST'])
def update_co2_settings():
    """API endpoint to update CO2 settings"""
    try:
        environment_controller = _environment_controller
        
        if not environment_controller:
            logger.error("Environment controller not found")
            return jsonify({
                'status': 'error',
                'message': 'Environment controller not initialized'
            })
        
        data = request.get_json()
        if not data:
            return jsonify({
                'status': 'error',
                'message': 'No data provided'
            })
        
        # Validate CO2 settings
        valid_fields = ['co2_mode', 'co2_day_target', 'co2_night_target', 
                       'co2_tolerance', 'co2_day_start', 'co2_day_end']
        
        co2_data = {}
        for field in valid_fields:
            if field in data:
                co2_data[field] = data[field]
        
        if not co2_data:
            return jsonify({
                'status': 'error',
                'message': 'No valid CO2 settings provided'
            })
        
        # Update the settings
        success = environment_controller.update_settings(co2_data)
        
        if success:
            # Save to database
            environment_controller.save_settings()
            logger.info(f"CO2 settings updated: {co2_data}")
            
            # Get updated settings
            updated_settings = environment_controller.get_settings()
            co2_settings = {
                'co2_mode': updated_settings.get('co2_mode'),
                'co2_day_target': updated_settings.get('co2_day_target'),
                'co2_night_target': updated_settings.get('co2_night_target'),
                'co2_tolerance': updated_settings.get('co2_tolerance'),
                'co2_day_start': updated_settings.get('co2_day_start'),
                'co2_day_end': updated_settings.get('co2_day_end'),
                'co2_state': updated_settings.get('co2_state')
            }
            
            return jsonify({
                'status': 'success',
                'message': 'CO2 settings updated successfully',
                'settings': co2_settings
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to update CO2 settings'
            })
        
    except Exception as e:
        logger.error(f"Error updating CO2 settings: {str(e)}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        })

@api_blueprint.route('/co2/control', methods=['POST'])
def co2_manual_control():
    """API endpoint for manual CO2 control"""
    try:
        environment_controller = _environment_controller
        
        if not environment_controller:
            return jsonify({
                'status': 'error',
                'message': 'Environment controller not initialized'
            })
        
        data = request.get_json()
        if not data:
            return jsonify({
                'status': 'error',
                'message': 'No data provided'
            })
        
        state = data.get('state')
        
        if state is None:
            return jsonify({
                'status': 'error',
                'message': 'Missing state parameter'
            })
        
        # Use manual control for CO2 injector
        success = environment_controller.manual_control('co2_injector', bool(state))
        
        if success:
            # Save settings to persist the manual mode
            environment_controller.save_settings()
            
            action = "activated" if state else "deactivated"
            logger.info(f"Manual CO2 control: injector {action}")
            
            return jsonify({
                'status': 'success',
                'message': f'CO2 injector {action}',
                'co2_state': bool(state)
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to control CO2 injector'
            })
        
    except Exception as e:
        logger.error(f"Error with CO2 manual control: {str(e)}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        })

@api_blueprint.route('/co2/status', methods=['GET'])
def get_co2_status():
    """API endpoint to get current CO2 status"""
    try:
        environment_controller = _environment_controller
        sensor_manager = _sensor_manager
        
        if not environment_controller:
            return jsonify({
                'status': 'error',
                'message': 'Environment controller not initialized'
            })
        
        # Get current settings
        settings = environment_controller.get_settings()
        
        # Get current sensor reading
        current_co2 = 0
        if sensor_manager:
            try:
                readings = sensor_manager.read_all_sensors()
                current_co2 = readings.get('co2', 0)
            except Exception as e:
                logger.warning(f"Could not get CO2 sensor reading: {e}")
        
        # Determine current cycle (day/night) based on light schedule
        is_day_cycle = environment_controller._is_lights_on_period()
        target_co2 = settings.get('co2_day_target', 1200) if is_day_cycle else settings.get('co2_night_target', 800)
        cycle_type = "day" if is_day_cycle else "night"
        
        co2_status = {
            'co2_state': settings.get('co2_state', False),
            'co2_mode': settings.get('co2_mode', 'auto'),
            'current_co2': current_co2,
            'target_co2': target_co2,
            'cycle_type': cycle_type,
            'is_day_cycle': is_day_cycle,
            'co2_tolerance': settings.get('co2_tolerance', 25),
            'arduino_ip': '192.168.1.140',
            'arduino_port': 32100
        }
        
        logger.debug(f"CO2 status: {co2_status}")
        
        return jsonify({
            'status': 'success',
            'co2_status': co2_status
        })
        
    except Exception as e:
        logger.error(f"Error fetching CO2 status: {str(e)}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        })
