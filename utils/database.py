import sqlite3
import logging
import time
import threading
import json

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path='farm_control.db'):
        self.db_path = db_path
        self.lock = threading.Lock()
        self.logger = logging.getLogger(__name__)
        self._initialize_db()
    
    def connect(self):
        """Initialize database connection"""
        try:
            self.connection = sqlite3.connect(self.db_path)
            return True
        except Exception as e:
            logger.error(f"Error connecting to database: {e}")
            return False
            
    def disconnect(self):
        """Close database connection"""
        try:
            if self.connection:
                self.connection.close()
                self.connection = None
            return True
        except Exception as e:
            logger.error(f"Error disconnecting from database: {e}")
            return False

    def _initialize_db(self):
        """Initialize the database schema"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Enable foreign key support
            cursor.execute('PRAGMA foreign_keys = ON')
            
            # Only create tables if they don't exist

            # Light schedules table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS light_schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schedule_name TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                affected_zones TEXT,
                updated_at INTEGER
            )''')
            
            # Check if affected_zones column exists in light_schedules table
            try:
                cursor.execute("SELECT affected_zones FROM light_schedules LIMIT 1")
            except sqlite3.OperationalError:
                logger.info("Adding affected_zones column to light_schedules table")
                cursor.execute("ALTER TABLE light_schedules ADD COLUMN affected_zones TEXT")
                
            # Check if name column exists in light_schedules table (for consistency with JS)
            try:
                cursor.execute("SELECT name FROM light_schedules LIMIT 1")
            except sqlite3.OperationalError:
                logger.info("Adding name column to light_schedules table as alias for schedule_name")
                cursor.execute("ALTER TABLE light_schedules ADD COLUMN name TEXT")

            # Nutrient settings table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS nutrient_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ec_target REAL NOT NULL,
                ph_target REAL NOT NULL,
                updated_at INTEGER
            )''')

            # Environment settings table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS environment_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                temp_day REAL DEFAULT 25.0,
                temp_night REAL DEFAULT 20.0,
                humidity_min REAL DEFAULT 50.0,
                humidity_max REAL DEFAULT 70.0,
                co2_target REAL DEFAULT 600.0,
                updated_at INTEGER
            )''')

            # Nutrient dosing state table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS nutrient_dosing_state (
                id INTEGER PRIMARY KEY,
                active INTEGER DEFAULT 0,
                pump_id INTEGER,
                end_time REAL,
                last_dose INTEGER
            )''')

            # Watering settings table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS watering_settings (
                id INTEGER PRIMARY KEY,
                enabled INTEGER DEFAULT 1,
                cycle_minutes_per_hour REAL,
                active_hours_start INTEGER,
                active_hours_end INTEGER,
                cycle_seconds_on INTEGER,
                cycle_seconds_off INTEGER,
                day_cycle_seconds_on INTEGER,
                day_cycle_seconds_off INTEGER,
                night_cycle_seconds_on INTEGER,
                night_cycle_seconds_off INTEGER,
                daily_limit REAL,
                manual_watering_duration INTEGER,
                updated_at INTEGER
            )
            ''')
            
            # Add new day/night cycle columns to existing table if they don't exist
            try:
                cursor.execute("SELECT day_cycle_seconds_on FROM watering_settings LIMIT 1")
            except sqlite3.OperationalError:
                logger.info("Adding day/night cycle columns to watering_settings table")
                cursor.execute("ALTER TABLE watering_settings ADD COLUMN day_cycle_seconds_on INTEGER")
                cursor.execute("ALTER TABLE watering_settings ADD COLUMN day_cycle_seconds_off INTEGER") 
                cursor.execute("ALTER TABLE watering_settings ADD COLUMN night_cycle_seconds_on INTEGER")
                cursor.execute("ALTER TABLE watering_settings ADD COLUMN night_cycle_seconds_off INTEGER")

            # Watering schedules table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS watering_schedules (
                id INTEGER PRIMARY KEY,
                start_time INTEGER,
                duration INTEGER,
                enabled INTEGER DEFAULT 1,
                last_run INTEGER DEFAULT 0,
                updated_at INTEGER
            )
            ''')

            # Settings table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at INTEGER
            )''')

            # Events table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT,
                data TEXT,
                timestamp INTEGER
            )''')

            # Growing profiles table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS growing_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                data TEXT,
                updated_at INTEGER
            )''')

            conn.commit()
            cursor.close()
            conn.close()
            logger.info("Database schema initialization completed")
            
        except Exception as e:
            logger.error(f"Database initialization error: {e}")
            raise

    def get_light_schedules(self):
        """Retrieve light schedules from database - simplified"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM light_schedules WHERE enabled = 1 ORDER BY id")
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
            
            logger.info(f"Retrieved {len(schedules)} enabled light schedules")
            return schedules
            
        except Exception as e:
            logger.error(f"Error retrieving light schedules: {e}")
            return []

    def get_connection(self):
        """Get a database connection with row factory and better timeout"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=30)  # Increase timeout
            conn.execute('PRAGMA busy_timeout = 30000')  # Set busy timeout to 30 seconds
            conn.row_factory = sqlite3.Row
            return conn
        except Exception as e:
            logger.error(f"Error getting database connection: {e}")
            raise

    def execute_query(self, query, params=None, retries=3):
        """Execute a query with retries and proper connection handling"""
        attempts = 0
        while attempts < retries:
            conn = None
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)
                conn.commit()
                return cursor
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e):
                    attempts += 1
                    time.sleep(1)  # Wait before retrying
                    continue
                raise
            except Exception as e:
                if conn:
                    conn.rollback()
                raise
            finally:
                if conn:
                    conn.close()
        raise sqlite3.OperationalError("Database is locked after multiple attempts")

    def save_light_schedules(self, schedules):
        """Save light schedules - simplified for basic functionality"""
        with self.lock:
            conn = None
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                
                # Clear existing schedules
                cursor.execute("DELETE FROM light_schedules")
                
                # Insert new schedules
                for schedule in schedules:
                    affected_zones_json = json.dumps(schedule.get('affected_zones', []))
                    
                    cursor.execute('''
                    INSERT INTO light_schedules 
                    (schedule_name, start_time, end_time, enabled, affected_zones, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ''', (
                        schedule.get('name', 'Unnamed'),
                        schedule.get('start_time', '06:00'),
                        schedule.get('end_time', '18:00'),
                        1 if schedule.get('enabled', True) else 0,
                        affected_zones_json,
                        int(time.time())
                    ))
                
                conn.commit()
                logger.info(f"Saved {len(schedules)} light schedules")
                return True
                
            except Exception as e:
                logger.error(f"Error saving light schedules: {e}")
                if conn:
                    conn.rollback()
                return False
            finally:
                if conn:
                    conn.close()

    def add_light_schedule(self, schedule_data):
        """Add a single new light schedule to the database."""
        with self.lock:
            conn = None
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                
                name = schedule_data.get('name', 'Unnamed Schedule')
                start_time = schedule_data.get('start_time', '00:00')
                end_time = schedule_data.get('end_time', '00:00')
                enabled = 1 if schedule_data.get('enabled', True) else 0
                affected_zones_list = schedule_data.get('affected_zones', [])
                affected_zones_json = json.dumps(affected_zones_list)
                updated_at = int(time.time())

                cursor.execute('''
                INSERT INTO light_schedules 
                (schedule_name, start_time, end_time, enabled, affected_zones, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ''', (name, start_time, end_time, enabled, affected_zones_json, updated_at))
                
                new_id = cursor.lastrowid
                conn.commit()
                self.logger.info(f"Added new light schedule with ID {new_id}.")
                return new_id
            except Exception as e:
                logger.error(f"Error adding light schedule: {e}")
                if conn:
                    conn.rollback()
                return None
            finally:
                if conn:
                    conn.close()

    def update_light_schedule(self, schedule_id, schedule_data):
        """Update an existing light schedule in the database."""
        with self.lock:
            conn = None
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                
                name = schedule_data.get('name', 'Unnamed Schedule')
                start_time = schedule_data.get('start_time', '00:00')
                end_time = schedule_data.get('end_time', '00:00')
                enabled = 1 if schedule_data.get('enabled', True) else 0
                affected_zones_list = schedule_data.get('affected_zones', [])
                affected_zones_json = json.dumps(affected_zones_list)
                updated_at = int(time.time())

                cursor.execute('''
                UPDATE light_schedules 
                SET schedule_name = ?, start_time = ?, end_time = ?, enabled = ?, affected_zones = ?, updated_at = ?
                WHERE id = ?
                ''', (name, start_time, end_time, enabled, affected_zones_json, updated_at, schedule_id))
                
                conn.commit()
                if cursor.rowcount == 0:
                    self.logger.warning(f"No schedule found with ID {schedule_id} to update.")
                    return False
                self.logger.info(f"Updated light schedule with ID {schedule_id}.")
                return True
            except Exception as e:
                logger.error(f"Error updating light schedule ID {schedule_id}: {e}")
                if conn:
                    conn.rollback()
                return False
            finally:
                if conn:
                    conn.close()

    def delete_light_schedule(self, schedule_id):
        """Delete a light schedule from the database."""
        with self.lock:
            conn = None
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                
                cursor.execute("DELETE FROM light_schedules WHERE id = ?", (schedule_id,))
                
                conn.commit()
                if cursor.rowcount == 0:
                    self.logger.warning(f"No schedule found with ID {schedule_id} to delete.")
                    return False
                self.logger.info(f"Deleted light schedule with ID {schedule_id}.")
                return True
            except Exception as e:
                logger.error(f"Error deleting light schedule ID {schedule_id}: {e}")
                if conn:
                    conn.rollback()
                return False
            finally:
                if conn:
                    conn.close()

    def get_nutrient_settings(self):
        """Retrieve nutrient settings from the database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM nutrient_settings ORDER BY id DESC LIMIT 1")
            settings = cursor.fetchone()
            conn.close()
            return settings
        except Exception as e:
            logger.error(f"Error retrieving nutrient settings: {e}")
            return None

    def save_nutrient_settings(self, settings):
        """Save nutrient settings to the database"""
        with self.lock:
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                cursor.execute('''
                INSERT INTO nutrient_settings (ec_target, ph_target, updated_at)
                VALUES (?, ?, ?)
                ''', (
                    settings['ec_target'],
                    settings['ph_target'],
                    int(time.time())
                ))
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                logger.error(f"Error saving nutrient settings: {e}")
                return False

    def get_environment_settings(self):
        """Retrieve environment settings from the database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM environment_settings ORDER BY id DESC LIMIT 1")
            settings = cursor.fetchone()
            conn.close()
            return settings
        except Exception as e:
            logger.error(f"Error retrieving environment settings: {e}")
            return None

    def save_environment_settings(self, settings):
        """Save environment settings to the database"""
        with self.lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute('''
                INSERT INTO environment_settings 
                (temp_day, temp_night, humidity_min, humidity_max, co2_target, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    float(settings.get('temp_day', 25.0)),
                    float(settings.get('temp_night', 20.0)),
                    float(settings.get('humidity_min', 50.0)),
                    float(settings.get('humidity_max', 70.0)),
                    float(settings.get('co2_target', 600.0)),
                    int(time.time())
                ))
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                logger.error(f"Error saving environment settings: {e}")
                return False

    def log_event(self, event_type, event_data):
        """Log an event in the database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
            INSERT INTO events (type, data, timestamp)
            VALUES (?, ?, ?)
            ''', (event_type, str(event_data), int(time.time())))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Error logging event: {e}")
            return False

    def get_recent_events(self, event_type=None, limit=20):
        """Retrieve recent events from the database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            if event_type:
                cursor.execute('''
                SELECT * FROM events WHERE type = ? 
                ORDER BY timestamp DESC LIMIT ?
                ''', (event_type, limit))
            else:
                cursor.execute('SELECT * FROM events ORDER BY timestamp DESC LIMIT ?', (limit,))
            events = cursor.fetchall()
            conn.close()
            return events
        except Exception as e:
            logger.error(f"Error retrieving recent events: {e}")
            return []

    def get_growing_profiles(self):
        """Retrieve all growing profiles from the database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM growing_profiles")
            profiles = cursor.fetchall()
            conn.close()
            return profiles
        except Exception as e:
            logger.error(f"Error retrieving growing profiles: {e}")
            return []

    def save_growing_profile(self, profile):
        """Save a growing profile to the database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
            INSERT INTO growing_profiles (name, data, updated_at)
            VALUES (?, ?, ?)
            ''', (profile['name'], str(profile['data']), int(time.time())))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Error saving growing profile: {e}")
            return False
    
    def get_growing_profile(self, profile_id):
        """Retrieve a specific growing profile from the database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM growing_profiles WHERE id = ?", (profile_id,))
            profile = cursor.fetchone()
            conn.close()
            return profile
        except Exception as e:
            logger.error(f"Error retrieving growing profile: {e}")
            return None
    
    def get_watering_settings(self):
        """Get watering settings from database with improved error handling"""
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Ensure table exists
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS watering_settings (
                    id INTEGER PRIMARY KEY,
                    enabled INTEGER DEFAULT 1,
                    cycle_minutes_per_hour REAL,
                    active_hours_start INTEGER,
                    active_hours_end INTEGER,
                    cycle_seconds_on INTEGER,
                    cycle_seconds_off INTEGER,
                    daily_limit REAL,
                    manual_watering_duration INTEGER,
                    updated_at INTEGER
                )
            ''')
            
            cursor.execute('SELECT * FROM watering_settings WHERE id = 1')
            row = cursor.fetchone()
            
            if row:
                # Convert row to dict
                settings = dict(row)
                
                # Convert enabled from integer to boolean
                if 'enabled' in settings:
                    settings['enabled'] = bool(settings['enabled'])
                
                self.logger.info(f"Retrieved watering settings from database: {settings}")
                return settings
            else:
                self.logger.info("No watering settings found in database")
                return None
                
        except sqlite3.Error as e:
            self.logger.error(f"SQLite error getting watering settings: {e}")
            return None
        except Exception as e:
            self.logger.error(f"General error getting watering settings: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def save_watering_settings(self, enabled, cycle_minutes_per_hour, active_hours_start, 
                             active_hours_end, cycle_seconds_on, cycle_seconds_off, 
                             day_cycle_seconds_on, day_cycle_seconds_off,
                             night_cycle_seconds_on, night_cycle_seconds_off,
                             daily_limit, manual_watering_duration, max_continuous_run, updated_at):
        """Save watering settings to database with day/night cycle support"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Log what we're trying to save
            logger.info(f"🗄️ SAVING watering settings: DAY(ON={day_cycle_seconds_on}s, OFF={day_cycle_seconds_off}s), NIGHT(ON={night_cycle_seconds_on}s, OFF={night_cycle_seconds_off}s)")
            
            cursor.execute("""
                INSERT OR REPLACE INTO watering_settings 
                (id, enabled, cycle_minutes_per_hour, active_hours_start, active_hours_end, 
                 cycle_seconds_on, cycle_seconds_off, day_cycle_seconds_on, day_cycle_seconds_off,
                 night_cycle_seconds_on, night_cycle_seconds_off, daily_limit, manual_watering_duration, 
                 max_continuous_run, updated_at)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (enabled, cycle_minutes_per_hour, active_hours_start, active_hours_end, 
                  cycle_seconds_on, cycle_seconds_off, day_cycle_seconds_on, day_cycle_seconds_off,
                  night_cycle_seconds_on, night_cycle_seconds_off, daily_limit, 
                  manual_watering_duration, max_continuous_run, updated_at))
            
            conn.commit()
            
            # Verify the save worked
            cursor.execute("SELECT * FROM watering_settings WHERE id = 1")
            saved_row = cursor.fetchone()
            if saved_row:
                logger.info(f"🗄️ VERIFIED save: DAY(ON={saved_row['day_cycle_seconds_on']}s, OFF={saved_row['day_cycle_seconds_off']}s), NIGHT(ON={saved_row['night_cycle_seconds_on']}s, OFF={saved_row['night_cycle_seconds_off']}s)")
            else:
                logger.error("🗄️ FAILED to verify save - no row found")
            
            conn.close()
            logger.info("🗄️ Watering settings saved successfully")
            return True
            
        except Exception as e:
            logger.error(f"🗄️ Error saving watering settings: {e}")
            return False

    def get_watering_settings(self):
        """Get watering settings from database with day/night cycle support"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM watering_settings WHERE id = 1")
            row = cursor.fetchone()
            conn.close()
            
            if row:
                settings = dict(row)
                # Convert enabled from integer to boolean if needed
                if 'enabled' in settings:
                    settings['enabled'] = bool(settings['enabled'])
                    
                # Log what we loaded including day/night settings
                day_on = settings.get('day_cycle_seconds_on')
                day_off = settings.get('day_cycle_seconds_off')
                night_on = settings.get('night_cycle_seconds_on') 
                night_off = settings.get('night_cycle_seconds_off')
                
                logger.info(f"🗄️ LOADED watering settings: DAY(ON={day_on}s, OFF={day_off}s), NIGHT(ON={night_on}s, OFF={night_off}s)")
                return settings
            else:
                logger.info("🗄️ No watering settings found in database")
                return None
                
        except Exception as e:
            logger.error(f"🗄️ Error getting watering settings: {e}")
            return None

    def save_nutrient_dosing_state(self, state):
        """Save nutrient dosing state"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
            INSERT OR REPLACE INTO nutrient_dosing_state 
            (id, active, pump_id, end_time, last_dose)
            VALUES (1, ?, ?, ?, ?)
            ''', (
                1 if state.get('active', False) else 0,
                state.get('pump_id'),
                state.get('end_time'),
                state.get('last_dose', 0)
            ))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Error saving nutrient dosing state: {e}")
            return False

    def get_nutrient_dosing_state(self):
        """Get current nutrient dosing state"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT active, pump_id, end_time FROM nutrient_dosing_state WHERE id = 1")
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return {
                    'active': bool(row[0]),
                    'pump_id': row[1],
                    'end_time': row[2]
                }
            return None
        except Exception as e:
            logger.error(f"Error retrieving nutrient dosing state: {e}")
            return None

    def log_dosing_event(self, pump_id, amount_ml, timestamp=None):
        """Log nutrient dosing events to the database
        
        Args:
            pump_id (str): ID of the pump used
            amount_ml (float): Amount of solution dispensed in ml
            timestamp (float, optional): Event timestamp, defaults to current time
        """
        if timestamp is None:
            timestamp = time.time()
            
        event_data = {
            'type': 'nutrient_dose',
            'timestamp': timestamp,
            'details': {
                'pump': pump_id,
                'amount_ml': amount_ml
            }
        }
        
        # Get current sensor data to record before/after values if available
        try:
            # This assumes there's a get_current_sensor_data method or similar
            # If not available, this part can be omitted
            sensor_data = self.get_current_sensor_data()
            if (sensor_data and 'ec' in sensor_data and 'ph' in sensor_data):
                event_data['details']['before_ph'] = sensor_data['ph']
                event_data['details']['before_ec'] = sensor_data['ec']
        except Exception:
            pass  # Ignore errors here, logging is best-effort
            
        # Insert the event into the database
        self.insert_event(event_data)
        return True
        
    def log_system_event(self, event_type, message, details=None):
        """Log system events
        
        Args:
            event_type (str): Type of event (e.g., "nutrient_reset", "error", etc.)
            message (str): Event message
            details (dict, optional): Additional event details
        """
        timestamp = time.time()
        
        event_data = {
            'type': event_type,
            'timestamp': timestamp,
            'message': message
        }
        
        if details:
            event_data['details'] = details
            
        self.insert_event(event_data)
        return True

    def get_watering_schedules(self):
        """DISABLED: Return empty list - no schedules needed"""
        logger.info("🗄️ Watering schedules disabled - using cycle settings only")
        return []

    def save_watering_schedule(self, schedule_data):
        """DISABLED: No schedules needed - use watering settings instead"""
        logger.info("🗄️ Schedule saving disabled - use cycle settings instead")
        return False
    
    def delete_watering_schedule(self, schedule_id):
        """DISABLED: No schedules needed"""
        logger.info("🗄️ Schedule deletion disabled - no schedules used")
        return False
    
    def update_watering_schedule_last_run(self, schedule_id, timestamp):
        """DISABLED: No schedules needed"""
        logger.info("🗄️ Schedule update disabled - no schedules used")
        return False

    def _ensure_connection(self):
        """Ensure that we have an active database connection.
        Creates connection if none exists or if previous connection was closed."""
        try:
            # Check if connection exists and is active
            if not hasattr(self, 'conn') or self.conn is None:
                logger.debug("Creating new database connection")
                self.conn = sqlite3.connect(self.db_path)
                self.conn.row_factory = sqlite3.Row
            else:
                # Test if connection is still active with a simple query
                try:
                    self.conn.execute("SELECT 1")
                except sqlite3.Error:
                    logger.debug("Reconnecting to database after connection lost")
                    self.conn = sqlite3.connect(self.db_path)
                    self.conn.row_factory = sqlite3.Row
        except sqlite3.Error as e:
            logger.error(f"Database connection error: {e}")
            raise

    def insert_event(self, event_data):
        """Insert an event into the database"""
        try:
            import json
            event_data_str = json.dumps(event_data)
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
            INSERT INTO events (type, data, timestamp)
            VALUES (?, ?, ?)
            ''', (
                event_data.get('type', 'general'),
                event_data_str,
                int(event_data.get('timestamp', time.time()))
            ))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Error inserting event: {e}")
            return False

    def get_current_sensor_data(self):
        """Get current sensor data from database or cache"""
        try:
            # Try to get from database
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
            SELECT value FROM settings WHERE key = 'last_sensor_readings'
            ''')
            row = cursor.fetchone()
            conn.close()
            
            if row and row[0]:
                import json
                return json.loads(row[0])
            return None
        except Exception as e:
            logger.error(f"Error getting sensor data: {e}")
            return None

    def create_default_watering_schedules(self):
        """Create default watering schedules if none exist"""
        try:
            # First check if any schedules exist
            self._ensure_connection()
            cursor = self.conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM watering_schedules")
            count = cursor.fetchone()[0]
            
            if count == 0:
                logger.info("No watering schedules found, creating defaults")
                
                # Create default schedules - morning and evening watering
                default_schedules = [
                    {
                        'start_time': 8 * 60,  # 8:00 AM (in minutes since midnight)
                        'duration': 5,         # 5 minutes
                        'enabled': True,
                        'updated_at': int(time.time())
                    },
                    {
                        'start_time': 18 * 60, # 6:00 PM
                        'duration': 5,         # 5 minutes
                        'enabled': True,
                        'updated_at': int(time.time())
                    }
                ]
                
                # Insert the default schedules
                for schedule in default_schedules:
                    cursor.execute('''
                        INSERT INTO watering_schedules
                        (start_time, duration, enabled, updated_at)
                        VALUES (?, ?, ?, ?)
                    ''', (
                        schedule['start_time'],
                        schedule['duration'],
                        1 if schedule['enabled'] else 0,
                        schedule['updated_at']
                    ))
                    
                self.conn.commit()
                logger.info("Default watering schedules created successfully")
                return True
            else:
                logger.debug(f"Found {count} existing watering schedules, not creating defaults")
                return False
        except sqlite3.Error as e:
            logger.error(f"Database error creating default watering schedules: {e}")
            return False
        except Exception as e:
            logger.error(f"Error creating default watering schedules: {e}", exc_info=True)
            return False