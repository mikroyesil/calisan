# filepath: /Users/batuhancakir1/src/beta/routes/light_routes.py
from flask import Blueprint, jsonify, request
from datetime import datetime
import logging
import json
import traceback

logger = logging.getLogger(__name__)

# Create the blueprint outside the function to avoid registration issues
light_routes = Blueprint('light_routes', __name__)
# Store references to controllers
_light_controller = None
_db = None

def init_routes(light_controller, db):
    global _light_controller, _db
    _light_controller = light_controller
    _db = db
    return light_routes

@light_routes.route('/api/light-schedules', methods=['GET'])
def get_schedules_api():
    try:
        schedules = _db.get_light_schedules()
        return jsonify({'status': 'success', 'data': schedules})
    except Exception as e:
        logger.error(f"Error getting schedules via API: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500

@light_routes.route('/api/light-schedules', methods=['POST'])
def add_schedule_api():
    try:
        logger.info(f"Received POST request to create schedule, data: {request.json}")
        schedule_data = request.json
        if not schedule_data:
            return jsonify({'status': 'error', 'error': 'No data provided'}), 400
        
        required_fields = ['name', 'start_time', 'end_time', 'affected_zones']
        if not all(field in schedule_data for field in required_fields):
            missing = [field for field in required_fields if field not in schedule_data]
            return jsonify({
                'status': 'error', 
                'error': f'Missing required fields: {", ".join(missing)}',
                'provided': schedule_data
            }), 400

        # Make sure affected_zones is a list (not a string)
        if isinstance(schedule_data['affected_zones'], str):
            try:
                schedule_data['affected_zones'] = json.loads(schedule_data['affected_zones'])
            except json.JSONDecodeError:
                return jsonify({'status': 'error', 'error': 'affected_zones must be a JSON array or a valid JSON string representing an array'}), 400

        # Copy name to schedule_name for consistency with database
        if 'name' in schedule_data and not 'schedule_name' in schedule_data:
            schedule_data['schedule_name'] = schedule_data['name']

        new_schedule_id = _db.add_light_schedule(schedule_data)
        if new_schedule_id:
            _light_controller.schedules = _db.get_light_schedules()
            _light_controller.update(force_check=True)
            logger.info(f"Successfully created new schedule with ID: {new_schedule_id}")
            return jsonify({
                'status': 'success', 
                'message': 'Schedule added successfully', 
                'id': new_schedule_id,
                'data': _db.get_light_schedules()  # Return all schedules for UI refresh
            }), 201
        else:
            logger.error(f"Failed to add schedule to database: {schedule_data}")
            return jsonify({'status': 'error', 'error': 'Failed to add schedule to database'}), 500
    except Exception as e:
        logger.error(f"Error adding schedule via API: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'status': 'error', 'error': str(e)}), 500

@light_routes.route('/api/light-schedules/<int:schedule_id>', methods=['PUT'])
def update_schedule_api(schedule_id):
    try:
        logger.info(f"Received PUT request to update schedule {schedule_id}, data: {request.json}")
        schedule_data = request.json
        if not schedule_data:
            logger.error(f"No data provided in request for schedule update {schedule_id}")
            return jsonify({'status': 'error', 'error': 'No data provided'}), 400

        # Make sure affected_zones is a list (not a string)
        if 'affected_zones' in schedule_data and isinstance(schedule_data['affected_zones'], str):
            try:
                schedule_data['affected_zones'] = json.loads(schedule_data['affected_zones'])
                logger.debug(f"Parsed affected_zones from JSON string: {schedule_data['affected_zones']}")
            except json.JSONDecodeError:
                logger.error(f"Failed to parse affected_zones as JSON: {schedule_data['affected_zones']}")
                return jsonify({'status': 'error', 'error': 'affected_zones must be a JSON array or a valid JSON string representing an array'}), 400
        
        # Always ensure affected_zones is a list of integers
        if 'affected_zones' in schedule_data:
            try:
                # Convert any strings to integers if needed
                schedule_data['affected_zones'] = [int(zone) for zone in schedule_data['affected_zones']]
                logger.debug(f"Normalized affected_zones to integers: {schedule_data['affected_zones']}")
            except (ValueError, TypeError) as e:
                logger.error(f"Error converting affected_zones to integers: {e}")
                return jsonify({'status': 'error', 'error': f'Invalid zone values in affected_zones: {str(e)}'}), 400

        # Copy name to schedule_name for consistency with database
        if 'name' in schedule_data and not 'schedule_name' in schedule_data:
            schedule_data['schedule_name'] = schedule_data['name']
            logger.debug(f"Copied name to schedule_name: {schedule_data['schedule_name']}")

        logger.info(f"Updating schedule {schedule_id} with data: {schedule_data}")
        success = _db.update_light_schedule(schedule_id, schedule_data)
        
        if success:
            logger.info(f"Successfully updated schedule {schedule_id}")
            # Reload schedules into the light controller
            updated_schedules = _db.get_light_schedules()
            _light_controller.schedules = updated_schedules
            
            # Force immediate application of schedules
            if hasattr(_light_controller, 'force_apply_schedules'):
                apply_success = _light_controller.force_apply_schedules()
                if apply_success:
                    logger.info(f"Successfully force-applied updated schedule {schedule_id}")
                else:
                    logger.warning(f"Failed to force-apply updated schedule {schedule_id}")
            else:
                # Fall back to regular update if force_apply_schedules doesn't exist
                _light_controller.update(force_check=True)
                logger.info(f"Applied updated schedule {schedule_id} using standard update")
                
            # Verify light states after update
            light_states = _light_controller.get_light_states()
            logger.info(f"Current light states after schedule update: {light_states}")
            
            return jsonify({
                'status': 'success', 
                'message': 'Schedule updated and applied successfully',
                'id': schedule_id,
                'data': updated_schedules  # Return all schedules for UI refresh
            })
        else:
            logger.error(f"Failed to update schedule {schedule_id} - not found or database error")
            return jsonify({'status': 'error', 'error': 'Failed to update schedule or schedule not found'}), 404
    except Exception as e:
        logger.error(f"Error updating schedule {schedule_id}: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'status': 'error', 'error': str(e)}), 500

@light_routes.route('/api/light-schedules/<int:schedule_id>', methods=['DELETE'])
def delete_schedule_api(schedule_id):
    try:
        logger.info(f"Deleting schedule with ID: {schedule_id}")
        success = _db.delete_light_schedule(schedule_id)
        if success:
            _light_controller.schedules = _db.get_light_schedules()
            _light_controller.update(force_check=True)
            return jsonify({
                'status': 'success', 
                'message': 'Schedule deleted successfully',
                'data': _db.get_light_schedules()  # Return all schedules for UI refresh
            })
        else:
            logger.error(f"Failed to delete schedule {schedule_id} - not found")
            return jsonify({'status': 'error', 'error': 'Schedule not found'}), 404
    except Exception as e:
        logger.error(f"Error deleting schedule {schedule_id}: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500

@light_routes.route('/api/lights/<int:light_id>', methods=['POST'])
def control_light_api(light_id):
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        if 'state' not in data:
            return jsonify({'error': 'Missing light_id or state in request'}), 400
            
        state = data['state']
        success = _light_controller.manual_control(light_id, state)
        logger.info(f"Manual light control: Zone {light_id} -> {state}, success: {success}")
        
        # Return all light states
        return jsonify({'status': 'success' if success else 'error'})
    except Exception as e:
        logger.error(f"Error controlling light via API: {e}")
        return jsonify({'error': str(e)}), 500

@light_routes.route('/api/lights/all/<string:state>', methods=['POST'])
def control_all_lights_api(state):
    try:
        logger.info(f"Manual control all lights: {state}")
        if state.lower() == 'on':
            _light_controller.all_on()
            success = True
        elif state.lower() == 'off':
            _light_controller.all_off()
            success = True
        else:
            return jsonify({'error': 'Invalid state. Use "on" or "off"'}), 400
        
        return jsonify({'status': 'success' if success else 'error'})
    except Exception as e:
        logger.error(f"Error controlling all lights via API: {e}")
        return jsonify({'error': str(e)}), 500

@light_routes.route('/api/lights', methods=['GET'])
def get_light_states_api():
    try:
        states = _light_controller.get_all_light_states()
        return jsonify(states)
    except Exception as e:
        logger.error(f"Error getting light states via API: {e}")
        return jsonify({'error': str(e)}), 500
        
@light_routes.route('/api/light-schedules/force-sync', methods=['POST'])
def force_sync_schedules():
    """Force synchronization between schedules in database and physical relay states"""
    try:
        logger.info("API request to force synchronization of light schedules with relays")
        
        # First reload schedules from database to ensure we have the latest
        _light_controller.schedules = _db.get_light_schedules()
        logger.info(f"Reloaded {len(_light_controller.schedules)} schedules from database")
        
        # Check if we have the new force_apply_schedules function
        if hasattr(_light_controller, 'force_apply_schedules'):
            logger.info("Using force_apply_schedules method")
            success = _light_controller.force_apply_schedules()
        else:
            # Fall back to regular update method
            logger.info("Using regular update method with force_check=True")
            success = _light_controller.update(force_check=True)
            
        # Get current light states to return in response
        light_states = _light_controller.get_light_states()
        
        if success:
            # Log the current state of each light
            for light_id, state in light_states.items():
                logger.info(f"Light {light_id} ({state['name']}) is {'ON' if state['state'] else 'OFF'}")
                
            return jsonify({
                'status': 'success',
                'message': 'Light schedules successfully synchronized with relays',
                'light_states': light_states
            })
        else:
            logger.error("Force synchronization failed")
            return jsonify({
                'status': 'error', 
                'message': 'Failed to synchronize schedules with relays',
                'light_states': light_states
            }), 500
            
    except Exception as e:
        logger.error(f"Error forcing schedule synchronization: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500
