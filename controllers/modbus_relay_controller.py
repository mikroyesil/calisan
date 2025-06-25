"""
Modbus Relay Controller - Implementation for Waveshare Modbus POE ETH Relay 30CH
https://www.waveshare.com/wiki/Modbus_POE_ETH_Relay_30CH
"""

import time
import logging
import datetime
import socket
import threading
import traceback
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

# Keep the original pymodbus imports for compatibility, but add fallback methods
try:
    from pymodbus.client import ModbusTcpClient
    from pymodbus.exceptions import ModbusException
    from pymodbus.pdu import ExceptionResponse
    MODBUS_AVAILABLE = True
except ImportError:
    logger.warning("pymodbus not installed - install with: pip install pymodbus")
    MODBUS_AVAILABLE = False


class ModbusRelayController:
    """Controller for Modbus TCP relay controllers like Waveshare 30CH"""
    
    def __init__(self, host='192.168.1.200', port=4196, device_id=1, channels=30, 
                 simulation_mode=False, connection_timeout=2, read_timeout=1):
        """
        Initialize Modbus relay controller
        
        Args:
            host (str): IP address of the relay controller
            port (int): Modbus TCP port (default: 4196 for Waveshare custom configuration)
            device_id (int): Modbus unit ID/slave address
            channels (int): Number of relay channels (default: 30 for Waveshare)
            simulation_mode (bool): If True, don't attempt real hardware connection
            connection_timeout (int): Timeout in seconds for connection attempts
            read_timeout (int): Timeout in seconds for read operations
        """
        self.host = host
        self.port = port
        self.device_id = device_id
        self.channels = channels
        self.simulation_mode = simulation_mode
        self.connection_timeout = connection_timeout
        self.read_timeout = read_timeout
        
        # Connection state tracking
        self.connected = False
        self.client = None
        self.last_connection_attempt = 0
        self.connection_cooldown = 30  # seconds between connection attempts
        self.last_error = None
        self.in_cooldown = False
        self.cooldown_until = None
        
        # Local cache of relay states
        self._relay_states = [False] * channels
        
        # Add a lock for thread safety
        self.lock = threading.Lock()
        
        # Map of light zones to relay channels for the vertical farm system
        # This mapping is used by the light controller to control specific lights
        self.light_zone_mapping = {
            # Format: light_id: {'section': channel}
            1: {'a': 1, 'b': 2},    # Light Zone 1 uses channels 1 & 2
            2: {'a': 3, 'b': 4},    # Light Zone 2 uses channels 3 & 4
            3: {'a': 5, 'b': 6},    # Light Zone 3 uses channels 5 & 6
            4: {'a': 7, 'b': 8},    # Light Zone 4 uses channels 7 & 8
            5: {'a': 9, 'b': 10},   # Light Zone 5 uses channels 9 & 10
            6: {'a': 11, 'b': 12},  # Light Zone 6 uses channels 11 & 12
            7: {'a': 13, 'b': 14},  # Light Zone 7 uses channels 13 & 14
        }
        
        # Try to connect immediately if not in simulation mode
        if not simulation_mode:
            if not MODBUS_AVAILABLE:
                logger.error("Cannot initialize Modbus client - pymodbus not installed")
                self.simulation_mode = True
            else:
                self.connect()
    
    def connect(self) -> bool:
        """Connect to the Modbus TCP relay controller"""
        if self.simulation_mode:
            logger.info("Simulation mode active - not connecting to physical relay controller")
            return False
        
        # Check if we're in cooldown period
        now = time.time()
        if self.in_cooldown and self.cooldown_until and now < self.cooldown_until:
            remaining = int(self.cooldown_until - now)
            logger.warning(f"Connection in cooldown for {remaining} more seconds")
            return False
            
        self.in_cooldown = False
        self.last_connection_attempt = now
        
        # Try to connect using the direct socket method first (most reliable for Waveshare)
        try:
            logger.info(f"Attempting direct socket connection to relay at {self.host}:{self.port}")
            
            # Create a new socket connection
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.connection_timeout)
            sock.connect((self.host, self.port))
            
            # For Waveshare relays, a successful socket connection is enough to consider connected
            self.connected = True
            sock.close()
            
            logger.info(f"Successfully connected to relay at {self.host}:{self.port} via direct socket")
            return True
            
        except Exception as e:
            logger.warning(f"Direct socket connection failed: {e}, trying ModbusTcpClient")
            
            # If direct socket method fails, try with ModbusTcpClient as fallback
            if MODBUS_AVAILABLE:
                try:
                    # Close any existing connection
                    if self.client:
                        self.client.close()
                    
                    # Create new client
                    logger.info(f"Connecting to Modbus relay at {self.host}:{self.port}")
                    self.client = ModbusTcpClient(
                        host=self.host,
                        port=self.port,
                        timeout=self.connection_timeout
                    )
                    
                    # Try to connect
                    connected = self.client.connect()
                    if not connected:
                        raise ConnectionError(f"Failed to connect to {self.host}:{self.port}")
                    
                    # Connection successful
                    self.connected = True
                    self.last_error = None
                    logger.info(f"Successfully connected to Modbus relay at {self.host}:{self.port}")
                    return True
                    
                except Exception as e:
                    self.last_error = str(e)
                    self.connected = False
                    
                    # Start cooldown period for connection attempts
                    self.in_cooldown = True
                    self.cooldown_until = now + self.connection_cooldown
                    
                    logger.error(f"Failed to connect to Modbus relay: {e}")
                    return False
            else:
                logger.error("Modbus library not available and direct socket failed")
                return False
    
    def disconnect(self) -> bool:
        """Disconnect from the Modbus relay controller"""
        if self.client:
            self.client.close()
            self.connected = False
            logger.info("Disconnected from Modbus relay controller")
            return True
        return False
    
    def get_all_relay_states(self) -> Dict[int, bool]:
        """Get a dictionary of all relay states {channel: state}"""
        states = {}
        # In a real implementation, we would read from the device here
        # But for reliability, just return our cached states
        for channel in range(min(self.channels, len(self._relay_states))):
            states[channel] = self._relay_states[channel]
            
        return states
    
    def read_actual_fan_states(self, fan_channels: List[int]) -> Dict[int, bool]:
        """Read actual hardware states for fan channels - simplified implementation"""
        hardware_states = {}
        
        # Return current cached states (updated whenever relays are controlled)
        for channel in fan_channels:
            if 0 <= channel < len(self._relay_states):
                hardware_states[channel] = self._relay_states[channel]
                
        return hardware_states
    
    def read_hardware_relay_states(self, channels: List[int]) -> Dict[int, bool]:
        """Read hardware relay states - simplified implementation using cached states"""
        hardware_states = {}
        
        # Return current cached states (updated whenever relays are controlled)
        for channel in channels:
            if 0 <= channel < len(self._relay_states):
                hardware_states[channel] = self._relay_states[channel]
        
        return hardware_states
    
    def get_relay(self, channel) -> bool:
        """Get the state of a specific relay channel"""
        # For backward compatibility with the existing light controller
        # which uses 'light_1a', 'light_2b' style channel names
        if isinstance(channel, str) and channel.startswith('light_'):
            try:
                # Extract zone number and section from format "light_1a"
                zone_str = channel[6:-1]  # Remove "light_" and last character
                section = channel[-1]     # Last character (a or b)
                zone = int(zone_str)
                
                # Use the mapping to find the actual channel number
                if zone in self.light_zone_mapping and section in self.light_zone_mapping[zone]:
                    channel = self.light_zone_mapping[zone][section]
                else:
                    logger.warning(f"Unknown light zone mapping: {channel}")
                    return False
            except Exception as e:
                logger.error(f"Error parsing light channel {channel}: {e}")
                return False
                
        # Convert channel number to int
        try:
            channel = int(channel)
        except (ValueError, TypeError):
            logger.error(f"Invalid channel type: {type(channel)}")
            return False
            
        # Validate channel number
        if not (0 <= channel < self.channels):
            logger.warning(f"Channel {channel} out of range (0-{self.channels-1})")
            return False
            
        # Return cached state in simulation mode
        if self.simulation_mode:
            return self._relay_states[channel]
        
        # Return cached state instead of reading from device
        # This improves reliability when checking state for UI display
        return self._relay_states[channel]
    
    def set_relay(self, channel, state) -> bool:
        """Set the state of a specific relay channel"""
        with self.lock:  # Thread safety
            # ENHANCED: Better debug logging to track what's calling this method
            caller_info = ""
            call_stack = ""
            try:
                import inspect
                frame = inspect.currentframe()
                if frame and frame.f_back:
                    caller_frame = frame.f_back
                    caller_info = f" (called from {caller_frame.f_code.co_filename}:{caller_frame.f_lineno} in {caller_frame.f_code.co_name})"
                    
                    # Get full call stack for fan channels (17-24)
                    if isinstance(channel, int) and 17 <= channel <= 24:
                        stack = inspect.stack()
                        call_stack = "\nCall stack for fan relay:\n"
                        for i, frame_info in enumerate(stack[1:4]):  # Show top 3 callers
                            call_stack += f"  {i+1}. {frame_info.filename}:{frame_info.lineno} in {frame_info.function}\n"
            except:
                pass
            
            # ADDED: Special logging for fan channels to track automatic control
            if isinstance(channel, int) and 17 <= channel <= 24:
                logger.info(f"🌀 FAN CONTROL: Channel {channel} -> {'ON' if state else 'OFF'}{caller_info}{call_stack}")
            elif isinstance(channel, int) and channel == 16:
                logger.info(f"🚰 WATER PUMP: Channel {channel} -> {'ON' if state else 'OFF'}{caller_info}")
            else:
                logger.debug(f"set_relay called: channel={channel}, state={state}{caller_info}")
            
            # Handle string channel names (same as get_relay)
            if isinstance(channel, str) and channel.startswith('light_'):
                try:
                    zone_str = channel[6:-1]
                    section = channel[-1]
                    zone = int(zone_str)
                    
                    if zone in self.light_zone_mapping and section in self.light_zone_mapping[zone]:
                        original_channel = channel
                        channel = self.light_zone_mapping[zone][section]
                        logger.debug(f"Mapped light channel {original_channel} to relay {channel}")
                    else:
                        logger.warning(f"Unknown light zone mapping: {channel}")
                        return False
                except Exception as e:
                    logger.error(f"Error parsing light channel {channel}: {e}")
                    return False
            
            # Convert channel and state to appropriate types
            try:
                channel = int(channel)
                state = bool(state)
            except (ValueError, TypeError):
                logger.error(f"Invalid channel or state type: {type(channel)}, {type(state)}")
                return False
                
            # Validate channel number
            if not (0 <= channel < self.channels):
                logger.warning(f"Channel {channel} out of range (0-{self.channels-1})")
                return False
                
            # Update cache immediately even in simulation mode
            old_state = self._relay_states[channel]
            self._relay_states[channel] = state
            
            # ADDED: Only log actual state changes to reduce noise
            if old_state != state:
                logger.info(f"Relay {channel} state changed: {old_state} -> {state}{caller_info}")
            else:
                logger.debug(f"Relay {channel} state unchanged: {state}")
            
            # In simulation mode, just return success
            if self.simulation_mode:
                return True
                
            # If not connected, try to connect
            if not self.connected:
                if not self.connect():
                    return False
            
            try:
                # First try the direct socket method with exact commands - most reliable for Waveshare
                result = self._send_direct_command(channel, state)
                if result:
                    # CHANGED: Only log if state actually changed
                    if old_state != state:
                        logger.info(f"Relay {channel} set to {'ON' if state else 'OFF'} using direct command")
                    return True
                
                # If socket method fails, try with ModbusTcpClient
                logger.warning(f"Direct command failed, trying ModbusTcpClient for relay {channel}")
                if MODBUS_AVAILABLE and self.client:
                    # FIXED: Apply same hardware offset for ModbusTcpClient
                    hardware_channel = channel - 1
                    response = self.client.write_coil(hardware_channel, state)
                    if not hasattr(response, 'isError') or not response.isError():
                        logger.info(f"Relay {channel} set to {'ON' if state else 'OFF'} using ModbusTcpClient (hardware address {hardware_channel})")
                        return True
                
                # Both methods failed
                logger.error(f"Failed to set relay {channel} to {state} with all methods")
                return False
                
            except Exception as e:
                logger.error(f"Error setting relay {channel} to {state}: {e}")
                self.connected = False
                self.last_error = str(e)
                return False
    
    def _send_direct_command(self, channel, state):
        """Send direct command to relay using the most reliable method for Waveshare"""
        try:
            # Create a new socket
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(self.connection_timeout)
            
            try:
                # Connect to the relay
                s.connect((self.host, self.port))
                
                # Format command using Modbus RTU format
                # Function code 5 (0x05) for write single coil
                # ON = 0xFF00, OFF = 0x0000
                # FIXED: Subtract 1 from channel because hardware uses 1-based addressing
                # Software channel 16 should control hardware relay 16, not 17
                hardware_channel = channel - 1
                cmd = bytearray([
                    0x01,                    # Unit ID
                    0x05,                    # Function code (write single coil)
                    0x00, hardware_channel,  # Coil address (high byte, low byte) - hardware offset corrected
                    0xFF if state else 0x00, # Value (high byte)
                    0x00,                    # Value (low byte)
                ])
                
                # Calculate CRC16 (specific to Modbus RTU)
                crc = self._calculate_modbus_crc(cmd)
                cmd.extend(crc)
                
                # Send the command
                logger.info(f"🔧 MODBUS DEBUG: Software Channel {channel} -> Hardware Address {hardware_channel} -> Command: {' '.join(f'{b:02x}' for b in cmd)}")
                s.send(cmd)
                
                # Give device time to process
                time.sleep(0.1)
                
                # Success
                return True
                
            finally:
                s.close()
                
        except Exception as e:
            logger.error(f"Error in direct command for relay {channel}: {e}")
            return False
    
    def _calculate_modbus_crc(self, data):
        """Calculate Modbus RTU CRC16"""
        crc = 0xFFFF
        for b in data:
            crc ^= b
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc = crc >> 1
        return bytearray([crc & 0xFF, (crc >> 8) & 0xFF])
    
    def get_connection_status(self) -> Dict[str, Any]:
        """Get the current connection status"""
        if self.simulation_mode:
            return {
                "connected": False,
                "host": self.host,
                "port": self.port,
                "last_error": "Running in simulation mode",
                "in_cooldown": False,
                "cooldown_until": None,
                "simulation_mode": True,
                "current_time": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            
        # Try to reconnect if not connected and not in cooldown
        now = time.time()
        can_retry = not self.connected and not self.in_cooldown and (now - self.last_connection_attempt) > self.connection_cooldown
        
        if can_retry:
            logger.info("Attempting automatic reconnection during status check")
            self.connect()
        
        return {
            "connected": self.connected,
            "host": self.host,
            "port": self.port,
            "device_id": self.device_id,
            "channels": self.channels,
            "last_error": self.last_error,
            "in_cooldown": self.in_cooldown,
            "cooldown_until": datetime.datetime.fromtimestamp(self.cooldown_until).strftime('%Y-%m-%d %H:%M:%S') if self.cooldown_until else None,
            "simulation_mode": self.simulation_mode,
            "current_time": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
