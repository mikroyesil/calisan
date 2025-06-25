# File: controllers/scheduler.py - Task scheduling system for vertical farm operations

import time
import datetime
import threading
import logging

logger = logging.getLogger(__name__)

class Scheduler:
    """
    Manages scheduled tasks for all controllers in the vertical farm system.
    Handles timing for recurring tasks and ensures they execute at the appropriate times.
    """
    
    def __init__(self, light_controller=None, nutrient_controller=None, environment_controller=None, watering_controller=None, sensor_manager=None):
        """Initialize the scheduler with controller references"""
        self.light_controller = light_controller
        self.nutrient_controller = nutrient_controller
        self.environment_controller = environment_controller
        self.watering_controller = watering_controller
        self.sensor_manager = sensor_manager  # Store sensor manager reference
        
        # Task schedule dictionaries
        self.hourly_tasks = []
        self.daily_tasks = []
        self.weekly_tasks = []
        self.custom_tasks = []  # Tasks with custom intervals
        
        # Time trackers
        self.last_hourly_check = 0
        self.last_daily_check = 0
        self.last_weekly_check = 0
        
        # Initialize default tasks
        self._setup_default_tasks()
        
        # Add periodic light schedule check every 60 seconds
        if self.light_controller:
            self.add_custom_task(
                'light_schedule_check',
                lambda: self.light_controller.update(force_check=True),
                interval_seconds=60
            )
        logger.info("Added periodic light schedule check (every 60s)")

        self.running = False
        self.thread = None
        
        logger.info("Scheduler initialized")
    
    def _setup_default_tasks(self):
        """Set up default recurring tasks"""
        # REMOVED: All reminder tasks - they just create log noise without operational value
        # Only keep tasks that are actually needed for system operation
        pass
    
    def add_hourly_task(self, task_id, callback):
        """Add a task to run every hour"""
        self.hourly_tasks.append({
            'id': task_id,
            'callback': callback,
            'last_run': 0
        })
        logger.debug(f"Added hourly task: {task_id}")
    
    def add_daily_task(self, task_id, callback, time_str):
        """Add a task to run once per day at the specified time"""
        hour, minute = map(int, time_str.split(':'))
        self.daily_tasks.append({
            'id': task_id,
            'callback': callback,
            'hour': hour,
            'minute': minute,
            'last_run_day': 0
        })
        logger.debug(f"Added daily task: {task_id} at {time_str}")
    
    def add_weekly_task(self, task_id, callback, day_of_week, time_str):
        """Add a task to run once per week on the specified day and time
        day_of_week: 1-7, where 1 is Monday and 7 is Sunday
        """
        hour, minute = map(int, time_str.split(':'))
        self.weekly_tasks.append({
            'id': task_id,
            'callback': callback,
            'day_of_week': day_of_week,
            'hour': hour,
            'minute': minute,
            'last_run_week': 0
        })
        logger.debug(f"Added weekly task: {task_id} on day {day_of_week} at {time_str}")
    
    def add_custom_task(self, task_id, callback, interval_seconds, start_delay=0):
        """Add a task to run at a custom interval specified in seconds"""
        self.custom_tasks.append({
            'id': task_id,
            'callback': callback,
            'interval': interval_seconds,
            'last_run': time.time() - interval_seconds + start_delay
        })
        logger.debug(f"Added custom task: {task_id} with interval {interval_seconds}s")
    
    def check_scheduled_tasks(self):
        """Check if any scheduled tasks need to be run"""
        current_time = time.time()
        now = datetime.datetime.now()
        current_day = now.day
        current_weekday = now.weekday() + 1  # Convert to 1-7 format (Monday-Sunday)
        current_hour = now.hour
        current_minute = now.minute
        current_week = now.isocalendar()[1]  # ISO week number
        
        # Check hourly tasks
        current_hour_timestamp = current_time // 3600
        if current_hour_timestamp > self.last_hourly_check:
            self.last_hourly_check = current_hour_timestamp
            for task in self.hourly_tasks:
                if current_hour_timestamp > task['last_run']:
                    try:
                        logger.info(f"Running hourly task: {task['id']}")
                        task['callback']()
                        task['last_run'] = current_hour_timestamp
                    except Exception as e:
                        logger.error(f"Error running hourly task {task['id']}: {str(e)}")
        
        # Check daily tasks
        for task in self.daily_tasks:
            if (current_day != task['last_run_day'] and 
                current_hour == task['hour'] and 
                current_minute >= task['minute'] and 
                current_minute < task['minute'] + 5):  # 5-minute window to avoid missing tasks
                try:
                    logger.info(f"Running daily task: {task['id']}")
                    task['callback']()
                    task['last_run_day'] = current_day
                except Exception as e:
                    logger.error(f"Error running daily task {task['id']}: {str(e)}")
        
        # Check weekly tasks
        for task in self.weekly_tasks:
            if (current_week != task['last_run_week'] and 
                current_weekday == task['day_of_week'] and
                current_hour == task['hour'] and 
                current_minute >= task['minute'] and 
                current_minute < task['minute'] + 5):  # 5-minute window
                try:
                    logger.info(f"Running weekly task: {task['id']}")
                    task['callback']()
                    task['last_run_week'] = current_week
                except Exception as e:
                    logger.error(f"Error running weekly task {task['id']}: {str(e)}")
        
        # Check custom interval tasks
        for task in self.custom_tasks:
            if current_time - task['last_run'] >= task['interval']:
                try:
                    logger.info(f"Running custom task: {task['id']}")
                    task['callback']()
                    task['last_run'] = current_time
                except Exception as e:
                    logger.error(f"Error running custom task {task['id']}: {str(e)}")
    
    def _run(self):
        """Main scheduler loop"""
        while self.running:
            try:
                # Get sensor readings as dictionary
                sensor_data = self._get_sensor_data()
                
                # Update all controllers with sensor data
                self._update_controllers(sensor_data)
                
                # Check scheduled tasks
                self.check_scheduled_tasks()
                
                # Sleep for a shorter interval for faster response
                time.sleep(3)  # Reduced from 5 to 3 seconds
                
            except Exception as e:
                logger.error(f"Error in scheduler loop: {str(e)}")
                time.sleep(5)

    def _get_sensor_data(self):
        """Get sensor data and convert to consistent dictionary format"""
        try:
            # FIXED: Use the scheduler's sensor_manager directly instead of environment_controller's
            if self.sensor_manager:
                raw_data = self.sensor_manager.read_all_sensors()
                
                # Convert tuple to dictionary if needed
                if isinstance(raw_data, tuple):
                    return {
                        'temperature': raw_data[0] if len(raw_data) > 0 else None,
                        'humidity': raw_data[1] if len(raw_data) > 1 else None,
                        'co2': raw_data[2] if len(raw_data) > 2 else None,
                        'ph': raw_data[3] if len(raw_data) > 3 else None,
                        'ec': raw_data[4] if len(raw_data) > 4 else None
                    }
                return raw_data if raw_data else {}
            else:
                # FIXED: Return empty dict if no sensor manager available
                logger.debug("No sensor manager available for scheduler")
                return {}
            
        except Exception as e:
            logger.error(f"Error reading sensors for controller updates: {str(e)}")
            # FIXED: Return empty dict instead of None to prevent controller errors
            return {}

    def _update_controllers(self, sensor_data=None):
        """Update all controllers with current sensor data"""
        try:
            # Environment controller enabled for CO2 control (fan control remains manual)
            controllers_with_sensors = [
                (self.environment_controller, 'environment'),  # ENABLED for CO2 control
                (self.nutrient_controller, 'nutrient')
            ]
            
            # Controllers that don't need sensor data
            controllers_without_sensors = [
                (self.light_controller, 'light'),
                (self.watering_controller, 'watering')
            ]
            
            # Update controllers that use sensor data
            for controller, name in controllers_with_sensors:
                if controller and sensor_data:
                    try:
                        controller.update(sensor_data)
                        logger.debug(f"Updated {name} controller with sensor data")
                    except Exception as e:
                        logger.warning(f"Error updating {name} controller: {str(e)}")
            
            # Update controllers that don't need sensor data
            for controller, name in controllers_without_sensors:
                if controller:
                    try:
                        controller.update()
                        logger.debug(f"Updated {name} controller")
                    except Exception as e:
                        logger.warning(f"Error updating {name} controller: {str(e)}")
                        
        except Exception as e:
            logger.error(f"Error in controller update process: {str(e)}")
    
    # ADD: Missing start() method that app.py expects
    def start(self):
        """Start the scheduler thread"""
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
            logger.info("Scheduler thread started")
            return True
        return False
    
    # ADD: Missing stop() method that app.py expects  
    def stop(self):
        """Stop the scheduler thread"""
        if self.running:
            logger.info("Stopping scheduler...")
            self.running = False
            
            # FIXED: Use shorter timeout and don't block on thread join
            if self.thread and self.thread.is_alive():
                try:
                    self.thread.join(timeout=1.0)  # Reduced from 5.0 to 1.0 seconds
                    if self.thread.is_alive():
                        logger.warning("Scheduler thread did not stop gracefully within timeout")
                        # Don't force kill, just let it finish naturally
                    else:
                        logger.info("Scheduler thread stopped gracefully")
                except Exception as e:
                    logger.error(f"Error during scheduler thread stop: {e}")
            
            logger.info("Scheduler stop complete")
            return True
        return False
