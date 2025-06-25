"""
Debug monitor for tracking actions that might cause unintended behavior
"""
import time
import logging
import threading
from collections import deque

logger = logging.getLogger(__name__)

class DebugMonitor:
    """
    Monitors and tracks actions that might cause conflicts or race conditions
    """
    def __init__(self, max_history=100):
        self.action_history = deque(maxlen=max_history)
        self.relay_actions = {}  # Tracks actions by channel
        self.lock = threading.Lock()
        logger.info("Debug monitor initialized")
    
    def track_relay_action(self, channel, state, source, details=None):
        """
        Track a relay control action to detect potential conflicts
        
        Args:
            channel: Relay channel number
            state: New state (True/False)
            source: Component initiating the action (e.g., "scheduler", "manual", "modbus_reconnect")
            details: Additional details about the action
        """
        timestamp = time.time()
        
        with self.lock:
            action = {
                'timestamp': timestamp,
                'time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp)),
                'channel': channel,
                'state': state,
                'source': source,
                'details': details
            }
            
            self.action_history.append(action)
            
            # Track by channel
            if channel not in self.relay_actions:
                self.relay_actions[channel] = deque(maxlen=20)
            
            self.relay_actions[channel].append(action)
            
            # Check for rapid toggles
            self._check_for_conflicts(channel)
    
    def _check_for_conflicts(self, channel):
        """Check for potential conflicts or rapid toggles on a channel"""
        if channel not in self.relay_actions or len(self.relay_actions[channel]) < 2:
            return
            
        actions = list(self.relay_actions[channel])
        if len(actions) < 2:
            return
            
        # Get the two most recent actions
        current = actions[-1]
        previous = actions[-2]
        
        # Check time difference
        time_diff = current['timestamp'] - previous['timestamp']
        
        # Alert if state changed too quickly
        if current['state'] != previous['state'] and time_diff < 10.0:
            logger.warning(
                f"POTENTIAL CONFLICT: Channel {channel} toggled from "
                f"{'ON' if previous['state'] else 'OFF'} to {'ON' if current['state'] else 'OFF'} "
                f"in just {time_diff:.2f}s. "
                f"Previous: {previous['source']}, Current: {current['source']}"
            )
    
    def get_channel_history(self, channel):
        """Get action history for a specific channel"""
        with self.lock:
            if channel in self.relay_actions:
                return list(self.relay_actions[channel])
            return []
    
    def get_recent_actions(self, limit=20):
        """Get the most recent actions across all channels"""
        with self.lock:
            return list(self.action_history)[-limit:]
