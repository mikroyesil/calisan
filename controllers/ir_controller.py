#!/usr/bin/env python3
# ir_controller.py - IR Control System for Air Conditioner via ESP32
import logging
import requests
import json
import time
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)

class IRController:
    """Controller for IR devices via ESP32 transmitter"""
    
    def __init__(self, esp32_ip: str = "192.168.1.150", esp32_port: int = 80, timeout: int = 5):
        self.esp32_ip = esp32_ip
        self.esp32_port = esp32_port
        self.timeout = timeout
        self.base_url = f"http://{esp32_ip}:{esp32_port}"
        self.connected = False
        self.last_error = None
        
        # Air conditioner state tracking
        self.ac_state = {
            'power': False,
            'temperature': 24,
            'mode': 'cool',  # cool, heat, fan, auto, dry
            'fan_speed': 'medium',  # low, medium, high, auto
            'last_command': None,
            'last_update': None
        }
        
        # IR command mappings for Airfel air conditioner
        self.airfel_commands = {
            'power_toggle': 'AIRFEL_AC_POWER',
            'temp_up': 'AIRFEL_AC_TEMP_UP',
            'temp_down': 'AIRFEL_AC_TEMP_DOWN',
            'mode_cool': 'AIRFEL_AC_MODE_COOL',
            'mode_heat': 'AIRFEL_AC_MODE_HEAT',
            'mode_fan': 'AIRFEL_AC_MODE_FAN',
            'mode_auto': 'AIRFEL_AC_MODE_AUTO',
            'mode_dry': 'AIRFEL_AC_MODE_DRY',
            'fan_low': 'AIRFEL_AC_FAN_LOW',
            'fan_medium': 'AIRFEL_AC_FAN_MED',
            'fan_high': 'AIRFEL_AC_FAN_HIGH',
            'fan_auto': 'AIRFEL_AC_FAN_AUTO',
            'swing': 'AIRFEL_AC_SWING',
            'timer': 'AIRFEL_AC_TIMER',
            'sleep': 'AIRFEL_AC_SLEEP'
        }
        
        logger.info(f"IR Controller initialized for ESP32 at {self.base_url}")
    
    def connect(self) -> bool:
        """Test connection to ESP32 IR transmitter"""
        try:
            response = requests.get(f"{self.base_url}/status", timeout=self.timeout)
            if response.status_code == 200:
                self.connected = True
                self.last_error = None
                logger.info(f"Successfully connected to ESP32 IR transmitter at {self.esp32_ip}")
                return True
            else:
                self.connected = False
                self.last_error = f"HTTP {response.status_code}"
                logger.error(f"Failed to connect to ESP32: HTTP {response.status_code}")
                return False
        except Exception as e:
            self.connected = False
            self.last_error = str(e)
            logger.error(f"Error connecting to ESP32 IR transmitter: {e}")
            return False
    
    def send_ir_command(self, command: str, device: str = "ac") -> bool:
        """Send IR command to ESP32 transmitter"""
        if not self.connected:
            if not self.connect():
                return False
        
        try:
            payload = {
                "device": device,
                "command": command,
                "timestamp": int(time.time())
            }
            
            response = requests.post(
                f"{self.base_url}/ir/send",
                json=payload,
                timeout=self.timeout,
                headers={'Content-Type': 'application/json'}
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('status') == 'success':
                    logger.info(f"IR command sent successfully: {command}")
                    return True
                else:
                    logger.error(f"ESP32 reported error: {result.get('message', 'Unknown error')}")
                    return False
            else:
                logger.error(f"Failed to send IR command: HTTP {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"Error sending IR command {command}: {e}")
            self.connected = False
            return False
    
    def set_ac_power(self, power_on: bool) -> bool:
        """Turn air conditioner on or off (Airfel uses power toggle)"""
        command = self.airfel_commands.get('power_toggle')
        
        if not command:
            logger.error("No power command found for Airfel AC")
            return False
        
        success = self.send_ir_command(command)
        if success:
            # Toggle the power state
            self.ac_state['power'] = power_on
            self.ac_state['last_command'] = command
            self.ac_state['last_update'] = time.time()
            logger.info(f"Airfel AC power toggled to {'ON' if power_on else 'OFF'}")
        
        return success
    def set_ac_temperature(self, temperature: int) -> bool:
        """Set air conditioner temperature (Airfel)"""
        if not 16 <= temperature <= 30:
            logger.error(f"Invalid temperature {temperature}. Must be between 16-30°C")
            return False

        current_temp = self.ac_state['temperature']
        
        # Calculate how many steps to reach target temperature
        temp_diff = temperature - current_temp
        success = True
        
        if temp_diff > 0:
            # Increase temperature
            command = self.airfel_commands.get('temp_up')
            if command:
                for _ in range(abs(temp_diff)):
                    if not self.send_ir_command(command):
                        success = False
                        break
                    time.sleep(0.5)  # Small delay between commands
        elif temp_diff < 0:
            # Decrease temperature
            command = self.airfel_commands.get('temp_down')
            if command:
                for _ in range(abs(temp_diff)):
                    if not self.send_ir_command(command):
                        success = False
                        break
                    time.sleep(0.5)
        
        if success:
            self.ac_state['temperature'] = temperature
            self.ac_state['last_update'] = time.time()
            logger.info(f"Airfel AC temperature set to {temperature}°C")
        
        return success
    
    def set_ac_mode(self, mode: str) -> bool:
        """Set air conditioner mode (Airfel)"""
        valid_modes = ['cool', 'heat', 'fan', 'auto', 'dry']
        if mode not in valid_modes:
            logger.error(f"Invalid mode {mode}. Must be one of: {valid_modes}")
            return False
        
        command = self.airfel_commands.get(f'mode_{mode}')
        
        if not command:
            logger.error(f"No {mode} mode command found for Airfel AC")
            return False
        
        success = self.send_ir_command(command)
        if success:
            self.ac_state['mode'] = mode
            self.ac_state['last_update'] = time.time()
            logger.info(f"Airfel AC mode set to {mode}")
        
        return success
    
    def set_ac_fan_speed(self, speed: str) -> bool:
        """Set air conditioner fan speed (Airfel)"""
        valid_speeds = ['low', 'medium', 'high', 'auto']
        if speed not in valid_speeds:
            logger.error(f"Invalid fan speed {speed}. Must be one of: {valid_speeds}")
            return False
        
        command = self.airfel_commands.get(f'fan_{speed}')
        
        if not command:
            logger.error(f"No {speed} fan speed command found for Airfel AC")
            return False
        
        success = self.send_ir_command(command)
        if success:
            self.ac_state['fan_speed'] = speed
            self.ac_state['last_update'] = time.time()
            logger.info(f"Airfel AC fan speed set to {speed}")
        
        return success
    
    def get_ac_state(self) -> Dict[str, Any]:
        """Get current air conditioner state"""
        return self.ac_state.copy()
    
    def add_custom_airfel_command(self, command_name: str, ir_code: str):
        """Add custom IR command for Airfel AC"""
        self.airfel_commands[command_name] = ir_code
        logger.info(f"Added custom Airfel command {command_name}: {ir_code}")
    
    def get_connection_status(self) -> Dict[str, Any]:
        """Get connection status and device info"""
        return {
            'connected': self.connected,
            'esp32_ip': self.esp32_ip,
            'esp32_port': self.esp32_port,
            'last_error': self.last_error,
            'brand': 'Airfel',
            'available_commands': list(self.airfel_commands.keys())
        }
