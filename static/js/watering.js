/**
 * watering.js - Watering Control Page JavaScript
 * Handles watering system controls, settings, and real-time updates
 */

// Global variables
let wateringSettings = {};
let pumpState = false;
let manualCountdown = null;
let socketConnected = false;
let fallbackPollingInterval = null;
let isInitialized = false; // Prevent multiple initializations

// Countdown variables
let countdownInterval = null;
let countdownEndTime = null;

// FIXED: Improved Socket.IO initialization and error handling
function initializeWateringSocket() {
    if (isInitialized) {
        console.log('Watering socket already initialized, skipping...');
        return;
    }
    
    console.log('HTTP-only communication initialized (Socket.IO disabled)');
    
    // Create a dummy socket object that does nothing to prevent errors
    if (typeof window.socket === 'undefined') {
        window.socket = {
            connected: false,
            on: function(event, callback) {
                // Silently ignore socket events in HTTP-only mode
            },
            emit: function(event, data) {
                // Silently ignore socket emits in HTTP-only mode
            },
            disconnect: function() {},
            connect: function() {}
        };
    }
    
    // Start HTTP polling immediately for real-time updates
    startFallbackPolling();
    isInitialized = true;
}

function startFallbackPolling() {
    if (fallbackPollingInterval) return; // Already running
    
    console.log('Starting HTTP polling for pump state updates');
    
    // Poll pump status every 5 seconds (reduced frequency)
    fallbackPollingInterval = setInterval(async () => {
        try {
            const response = await makeRequest('/api/pump/status');
            if (response && response.data) {
                const currentState = response.data.state || false;
                if (currentState !== pumpState) {
                    console.log('Pump state changed via HTTP polling:', currentState);
                    updatePumpStatus(currentState, false);
                }
                
                // Update other status information
                updateStatusDisplay(response.data);
            }
        } catch (error) {
            console.log('HTTP polling error:', error);
        }
    }, 5000); // Poll every 5 seconds (reduced from 3)
}

function stopFallbackPolling() {
    if (fallbackPollingInterval) {
        console.log('Stopping HTTP polling');
        clearInterval(fallbackPollingInterval);
        fallbackPollingInterval = null;
    }
}

// Enhanced pump control functions with better error handling
async function startPump() {
    try {
        const duration = parseInt(document.getElementById('manual-duration').value) || 1;
        console.log(`Starting manual watering for ${duration} minutes`);
        
        // Disable button immediately to prevent double-clicks
        const startBtn = document.getElementById('pump-start-btn');
        if (startBtn) {
            startBtn.disabled = true;
            startBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Starting...';
        }
        
        const data = {
            action: 'start_manual',
            duration: duration
        };
        
        const response = await makeRequest('/api/watering/control', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        
        if (response && response.status === 'success') {
            showToast('Manual watering started', 'success');
            updatePumpStatus(true, true);
            startManualCountdown(duration * 60); // Convert to seconds
            
            // Auto-stop after duration
            setTimeout(async () => {
                if (pumpState) {
                    await stopPump();
                }
            }, duration * 60 * 1000);
        } else {
            showToast('Failed to start watering: ' + (response?.message || 'Unknown error'), 'error');
        }
    } catch (error) {
        console.error('Error starting pump:', error);
        showToast('Error starting watering', 'error');
    } finally {
        // Re-enable button
        const startBtn = document.getElementById('pump-start-btn');
        if (startBtn) {
            startBtn.disabled = false;
            startBtn.innerHTML = '<i class="fas fa-play"></i> Start';
        }
    }
}

async function stopPump() {
    try {
        console.log('Stopping manual watering');
        
        // Disable button immediately
        const stopBtn = document.getElementById('pump-stop-btn');
        if (stopBtn) {
            stopBtn.disabled = true;
            stopBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Stopping...';
        }
        
        const data = {
            action: 'stop'
        };
        
        const response = await makeRequest('/api/watering/control', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        
        if (response && response.status === 'success') {
            showToast('Watering stopped', 'info');
            updatePumpStatus(false, false);
            stopManualCountdown();
        } else {
            showToast('Failed to stop watering: ' + (response?.message || 'Unknown error'), 'error');
        }
    } catch (error) {
        console.error('Error stopping pump:', error);
        showToast('Error stopping watering', 'error');
    } finally {
        // Re-enable button
        const stopBtn = document.getElementById('pump-stop-btn');
        if (stopBtn) {
            stopBtn.disabled = false;
            stopBtn.innerHTML = '<i class="fas fa-stop"></i> Stop';
        }
    }
}

function updatePumpStatus(state, manual = false) {
    pumpState = state;
    
    const badge = document.getElementById('pump-badge');
    const display = document.getElementById('pump-status-display');
    const startBtn = document.getElementById('pump-start-btn');
    const stopBtn = document.getElementById('pump-stop-btn');
    
    if (badge && display) {
        if (state) {
            badge.textContent = 'On';
            badge.className = 'badge bg-success';
            display.textContent = manual ? 'Manual Active' : 'Active';
            display.className = 'text-success';
        } else {
            badge.textContent = 'Off';
            badge.className = 'badge bg-secondary';
            display.textContent = 'Inactive';
            display.className = '';
        }
    }
    
    // Update button states
    if (startBtn && stopBtn) {
        startBtn.disabled = state;
        stopBtn.disabled = !state;
    }
}

function updateStatusDisplay(data) {
    // Update daily usage
    const dailyUsage = document.getElementById('daily-water-usage');
    if (dailyUsage && data.daily_total !== undefined) {
        const dailyTotal = data.daily_total || 0;
        const dailyLimit = wateringSettings.daily_limit || 60;
        dailyUsage.textContent = `${dailyTotal.toFixed(1)} / ${dailyLimit} minutes`;
    }
    
    // Update last run time
    const lastRun = document.getElementById('last-run-time');
    if (lastRun && data.last_watering_time) {
        const lastTime = new Date(data.last_watering_time);
        lastRun.textContent = lastTime.toLocaleTimeString();
    }
    
    // Update current cycle display
    updateActiveCycleDisplay(data.current_cycle);
    
    // Update connection status indicator
    const connectionStatus = document.getElementById('socket-status');
    if (connectionStatus) {
        connectionStatus.style.background = '#28a745';
        connectionStatus.textContent = 'HTTP: Connected';
    }
}

// Function to update active cycle display
function updateActiveCycleDisplay(currentCycle) {
    const activeCycleBadge = document.getElementById('active-cycle-badge');
    const activeCycleDetails = document.getElementById('active-cycle-details');
    
    if (!activeCycleBadge || !activeCycleDetails) {
        return; // Elements not found
    }
    
    if (!currentCycle) {
        // No cycle information available
        activeCycleBadge.innerHTML = '<i class="fas fa-cog"></i> Unknown';
        activeCycleBadge.className = 'badge bg-secondary me-2';
        activeCycleDetails.textContent = 'Cycle info unavailable';
        return;
    }
    
    const cycleType = currentCycle.cycle_type || 'unknown';
    const cycleOn = currentCycle.cycle_seconds_on || 0;
    const cycleOff = currentCycle.cycle_seconds_off || 0;
    const lightsOn = currentCycle.lights_on;
    
    // Update badge based on cycle type
    if (cycleType === 'day') {
        activeCycleBadge.innerHTML = '<i class="fas fa-sun"></i> Day Mode';
        activeCycleBadge.className = 'badge bg-warning text-dark me-2';
        activeCycleDetails.textContent = `${cycleOn}s ON, ${cycleOff}s OFF (Lights ON)`;
    } else if (cycleType === 'night') {
        activeCycleBadge.innerHTML = '<i class="fas fa-moon"></i> Night Mode';
        activeCycleBadge.className = 'badge bg-primary me-2';
        activeCycleDetails.textContent = `${cycleOn}s ON, ${cycleOff}s OFF (Lights OFF)`;
    } else if (cycleType === 'main' || cycleType === 'fallback') {
        activeCycleBadge.innerHTML = '<i class="fas fa-cog"></i> Standard';
        activeCycleBadge.className = 'badge bg-secondary me-2';
        activeCycleDetails.textContent = `${cycleOn}s ON, ${cycleOff}s OFF (Fallback)`;
    } else {
        activeCycleBadge.innerHTML = '<i class="fas fa-question"></i> Unknown';
        activeCycleBadge.className = 'badge bg-secondary me-2';
        activeCycleDetails.textContent = 'Cycle type unknown';
    }
}

function startManualCountdown(seconds) {
    const container = document.getElementById('countdown-container');
    const timer = document.getElementById('countdown-timer');
    
    if (!container || !timer) return;
    
    container.style.display = 'block';
    
    let remaining = seconds;
    
    const updateTimer = () => {
        const minutes = Math.floor(remaining / 60);
        const secs = remaining % 60;
        timer.textContent = `${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
        
        if (remaining <= 0) {
            stopManualCountdown();
            return;
        }
        
        remaining--;
    };
    
    updateTimer();
    manualCountdown = setInterval(updateTimer, 1000);
}

function stopManualCountdown() {
    if (manualCountdown) {
        clearInterval(manualCountdown);
        manualCountdown = null;
    }
    
    const container = document.getElementById('countdown-container');
    if (container) {
        container.style.display = 'none';
    }
}

// Load watering settings from server
async function loadWateringSettings(forceRefresh = false) {
    try {
        const url = forceRefresh ? '/api/watering-settings?force_refresh=true' : '/api/watering-settings';
        console.log('Fetching watering settings from server...', forceRefresh ? '(Forced)' : '');
        
        const response = await makeRequest(url);
        
        if (response && response.status === 'success' && response.data) {
            console.log('Received watering settings:', response);
            wateringSettings = response.data;
            updateWateringControls(wateringSettings);
        } else {
            console.error('Failed to load watering settings:', response);
            showToast('Failed to load watering settings', 'error');
        }
    } catch (error) {
        console.error('Error loading watering settings:', error);
        showToast('Error loading watering settings', 'error');
    }
}

// Update UI controls with loaded settings
function updateWateringControls(settings) {
    console.log('Updating watering controls with settings:', settings);
    
    // Update form fields - fallback/legacy settings
    const secondsOn = document.getElementById('seconds-on');
    const secondsOff = document.getElementById('seconds-off');
    
    // Update day/night cycle fields
    const daySecondsOn = document.getElementById('day-seconds-on');
    const daySecondsOff = document.getElementById('day-seconds-off');
    const nightSecondsOn = document.getElementById('night-seconds-on');
    const nightSecondsOff = document.getElementById('night-seconds-off');
    
    // Update other settings
    const activeStart = document.getElementById('active-hours-start');
    const activeEnd = document.getElementById('active-hours-end');
    const dailyLimit = document.getElementById('max-daily-minutes');
    const maxRun = document.getElementById('max-continuous-run');
    const manualDuration = document.getElementById('manual-duration-setting');
    const systemToggle = document.getElementById('watering-system-toggle');
    
    // Set fallback values
    if (secondsOn) secondsOn.value = settings.cycle_seconds_on || 30;
    if (secondsOff) secondsOff.value = settings.cycle_seconds_off || 300;
    
    // Set day/night cycle values with fallbacks to main cycle settings
    if (daySecondsOn) daySecondsOn.value = settings.day_cycle_seconds_on || settings.cycle_seconds_on || 30;
    if (daySecondsOff) daySecondsOff.value = settings.day_cycle_seconds_off || settings.cycle_seconds_off || 300;
    if (nightSecondsOn) nightSecondsOn.value = settings.night_cycle_seconds_on || settings.cycle_seconds_on || 20;
    if (nightSecondsOff) nightSecondsOff.value = settings.night_cycle_seconds_off || settings.cycle_seconds_off || 600;
    
    // Set other values
    if (activeStart) activeStart.value = settings.active_hours_start || 6;
    if (activeEnd) activeEnd.value = settings.active_hours_end || 20;
    if (dailyLimit) dailyLimit.value = settings.daily_limit || 60;
    if (maxRun) maxRun.value = settings.max_continuous_run || 5;
    if (manualDuration) manualDuration.value = settings.manual_watering_duration || 1;
    if (systemToggle) systemToggle.checked = settings.enabled !== false;
    
    // Update schedule display
    const scheduleDisplay = document.getElementById('watering-schedule-display');
    if (scheduleDisplay) {
        scheduleDisplay.textContent = (settings.cycle_minutes_per_hour || 5.0).toFixed(1);
    }
    
    // Update current cycle display if available
    if (settings.current_cycle) {
        updateActiveCycleDisplay(settings.current_cycle);
    }
    
    // Calculate and display estimated daily usage
    recalculateCyclePattern();
}

// FIXED: Debounced save function to prevent multiple saves
let saveTimeout = null;
async function saveWateringSettings() {
    // Clear any existing timeout
    if (saveTimeout) {
        clearTimeout(saveTimeout);
    }
    
    // Debounce the save operation
    saveTimeout = setTimeout(async () => {
        try {
            console.log('Saving watering settings...');
            
            const data = {
                enabled: document.getElementById('watering-system-toggle').checked,
                cycle_seconds_on: parseInt(document.getElementById('seconds-on').value) || 30,
                cycle_seconds_off: parseInt(document.getElementById('seconds-off').value) || 300,
                day_cycle_seconds_on: parseInt(document.getElementById('day-seconds-on').value) || 30,
                day_cycle_seconds_off: parseInt(document.getElementById('day-seconds-off').value) || 300,
                night_cycle_seconds_on: parseInt(document.getElementById('night-seconds-on').value) || 20,
                night_cycle_seconds_off: parseInt(document.getElementById('night-seconds-off').value) || 600,
                active_hours_start: parseInt(document.getElementById('active-hours-start').value) || 6,
                active_hours_end: parseInt(document.getElementById('active-hours-end').value) || 20,
                daily_limit: parseFloat(document.getElementById('max-daily-minutes').value) || 60,
                max_continuous_run: parseInt(document.getElementById('max-continuous-run').value) || 5,
                manual_watering_duration: parseInt(document.getElementById('manual-duration-setting').value) || 1
            };
            
            // Calculate cycle_minutes_per_hour
            const totalCycleTime = data.cycle_seconds_on + data.cycle_seconds_off;
            data.cycle_minutes_per_hour = totalCycleTime > 0 ? (data.cycle_seconds_on / totalCycleTime) * 60 : 0;
            
            console.log('Sending settings data:', data);
            
            const response = await makeRequest('/api/watering-settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
            
            if (response && response.status === 'success') {
                showToast('Watering settings saved successfully', 'success');
                wateringSettings = response.data || data;
                updateWateringControls(wateringSettings);
            } else {
                showToast('Failed to save settings: ' + (response?.message || 'Unknown error'), 'error');
            }
        } catch (error) {
            console.error('Error saving watering settings:', error);
            showToast('Error saving watering settings', 'error');
        }
    }, 500); // 500ms debounce
}

// Load relay states
async function loadRelayStates() {
    try {
        const response = await makeRequest('/api/relay-states');
        
        if (response && response.status === 'success' && response.states) {
            // Update pump state from relay channel 16
            const pumpRelayState = response.states[16] || false;
            if (pumpRelayState !== pumpState) {
                updatePumpStatus(pumpRelayState, false);
            }
        }
    } catch (error) {
        console.log('Error loading relay states:', error);
    }
}

// Calculate cycle pattern display with day/night awareness
function recalculateCyclePattern() {
    console.log('Recalculating cycle pattern with day/night settings');
    
    // Get day/night cycle values
    const daySecondsOn = parseInt(document.getElementById('day-seconds-on')?.value) || 30;
    const daySecondsOff = parseInt(document.getElementById('day-seconds-off')?.value) || 300;
    const nightSecondsOn = parseInt(document.getElementById('night-seconds-on')?.value) || 20;
    const nightSecondsOff = parseInt(document.getElementById('night-seconds-off')?.value) || 600;
    
    // Get fallback values
    const secondsOn = parseInt(document.getElementById('seconds-on')?.value) || 30;
    const secondsOff = parseInt(document.getElementById('seconds-off')?.value) || 300;
    
    const activeStart = parseInt(document.getElementById('active-hours-start')?.value) || 6;
    const activeEnd = parseInt(document.getElementById('active-hours-end')?.value) || 20;
    const dailyLimit = parseFloat(document.getElementById('max-daily-minutes')?.value) || 60;
    
    // Calculate day cycle minutes per hour
    const dayTotalCycleTime = daySecondsOn + daySecondsOff;
    const dayMinutesPerHour = dayTotalCycleTime > 0 ? (daySecondsOn / dayTotalCycleTime) * 60 : 0;
    
    // Calculate night cycle minutes per hour
    const nightTotalCycleTime = nightSecondsOn + nightSecondsOff;
    const nightMinutesPerHour = nightTotalCycleTime > 0 ? (nightSecondsOn / nightTotalCycleTime) * 60 : 0;
    
    // Calculate fallback minutes per hour
    const totalCycleTime = secondsOn + secondsOff;
    const minutesPerHour = totalCycleTime > 0 ? (secondsOn / totalCycleTime) * 60 : 0;
    
    console.log(`Day cycle (${daySecondsOn}s ON, ${daySecondsOff}s OFF) = ${dayMinutesPerHour.toFixed(2)} min/hour`);
    console.log(`Night cycle (${nightSecondsOn}s ON, ${nightSecondsOff}s OFF) = ${nightMinutesPerHour.toFixed(2)} min/hour`);
    console.log(`Fallback (${secondsOn}s ON, ${secondsOff}s OFF) = ${minutesPerHour.toFixed(2)} min/hour`);
    
    // Calculate active hours
    let activeHours = activeEnd - activeStart;
    if (activeHours <= 0) activeHours = 24; // 24/7 operation
    
    // For daily estimation, assume day/night cycles are used equally (simplified)
    // This is an approximation since we don't know the exact light schedule
    const averageMinutesPerHour = (dayMinutesPerHour + nightMinutesPerHour) / 2;
    const estimatedDaily = averageMinutesPerHour * activeHours;
    
    console.log(`Active hours: ${activeStart}:00 to ${activeEnd}:00 (${activeHours} hours)`);
    console.log(`Estimated daily usage: ${estimatedDaily.toFixed(1)} minutes (average of day/night cycles)`);
    
    // Update displays
    const estimatedElement = document.getElementById('estimated-daily');
    if (estimatedElement) {
        estimatedElement.textContent = estimatedDaily.toFixed(1);
    }
    
    const scheduleDisplay = document.getElementById('watering-schedule-display');
    if (scheduleDisplay) {
        scheduleDisplay.textContent = minutesPerHour.toFixed(1);
    }
    
    // Show warning if estimated usage exceeds daily limit
    const warningElement = document.getElementById('daily-limit-warning');
    if (warningElement) {
        if (estimatedDaily > dailyLimit) {
            warningElement.textContent = `Warning: Estimated daily usage (${estimatedDaily.toFixed(1)} min) exceeds daily limit (${dailyLimit} min)`;
            warningElement.classList.remove('d-none');
        } else {
            warningElement.classList.add('d-none');
        }
    }
}

// Enhanced cycle debug info with day/night awareness
function updateCycleDebugInfo() {
    // Get day/night cycle values
    const daySecondsOn = parseInt(document.getElementById('day-seconds-on')?.value) || 30;
    const daySecondsOff = parseInt(document.getElementById('day-seconds-off')?.value) || 300;
    const nightSecondsOn = parseInt(document.getElementById('night-seconds-on')?.value) || 20;
    const nightSecondsOff = parseInt(document.getElementById('night-seconds-off')?.value) || 600;
    
    // Get fallback values
    const secondsOn = parseInt(document.getElementById('seconds-on')?.value) || 30;
    const secondsOff = parseInt(document.getElementById('seconds-off')?.value) || 300;
    
    // Calculate cycles
    const dayTotalCycle = daySecondsOn + daySecondsOff;
    const dayCyclesPerHour = dayTotalCycle > 0 ? (3600 / dayTotalCycle) : 0;
    
    const nightTotalCycle = nightSecondsOn + nightSecondsOff;
    const nightCyclesPerHour = nightTotalCycle > 0 ? (3600 / nightTotalCycle) : 0;
    
    const totalCycle = secondsOn + secondsOff;
    const cyclesPerHour = totalCycle > 0 ? (3600 / totalCycle) : 0;
    
    const debugElement = document.getElementById('cycle-debug-info');
    if (debugElement) {
        debugElement.innerHTML = `
            <strong>Day/Night Cycle Details:</strong><br>
            <div class="row">
                <div class="col-md-4">
                    <small class="text-warning"><i class="fas fa-sun"></i> <strong>Day Mode:</strong><br>
                    • Cycle: ${dayTotalCycle}s (${(dayTotalCycle/60).toFixed(1)} min)<br>
                    • Water: ${daySecondsOn}s, Rest: ${daySecondsOff}s<br>
                    • Cycles/hour: ${dayCyclesPerHour.toFixed(1)}</small>
                </div>
                <div class="col-md-4">
                    <small class="text-primary"><i class="fas fa-moon"></i> <strong>Night Mode:</strong><br>
                    • Cycle: ${nightTotalCycle}s (${(nightTotalCycle/60).toFixed(1)} min)<br>
                    • Water: ${nightSecondsOn}s, Rest: ${nightSecondsOff}s<br>
                    • Cycles/hour: ${nightCyclesPerHour.toFixed(1)}</small>
                </div>
                <div class="col-md-4">
                    <small class="text-secondary"><i class="fas fa-cog"></i> <strong>Fallback:</strong><br>
                    • Cycle: ${totalCycle}s (${(totalCycle/60).toFixed(1)} min)<br>
                    • Water: ${secondsOn}s, Rest: ${secondsOff}s<br>
                    • Cycles/hour: ${cyclesPerHour.toFixed(1)}</small>
                </div>
            </div>`;
    }
}

// Additional functionality for today's usage display
function updateTodayUsage() {
    const todayMinutes = document.getElementById('today-minutes');
    const maxMinutes = document.getElementById('max-minutes');
    const todayProgress = document.getElementById('today-progress');
    
    if (todayMinutes && maxMinutes && todayProgress) {
        const dailyTotal = wateringSettings.daily_usage || 0;
        const dailyLimit = wateringSettings.daily_limit || 60;
        
        todayMinutes.textContent = dailyTotal.toFixed(1);
        maxMinutes.textContent = dailyLimit;
        
        const percentage = Math.min(100, (dailyTotal / dailyLimit) * 100);
        todayProgress.style.width = `${percentage}%`;
        
        // Change color based on usage
        if (percentage > 90) {
            todayProgress.className = 'progress-bar bg-danger';
        } else if (percentage > 75) {
            todayProgress.className = 'progress-bar bg-warning';
        } else {
            todayProgress.className = 'progress-bar bg-primary';
        }
    }
}

// Additional functionality for watering history
function updateWateringHistory() {
    const historyElement = document.getElementById('watering-history');
    if (!historyElement) return;
    
    // Mock data for now - you can replace this with real API call
    const currentTime = new Date();
    const mockHistory = [];
    
    // Generate some mock history data
    for (let i = 0; i < 5; i++) {
        const timeOffset = i * 60 * 60 * 1000; // 1 hour apart
        const eventTime = new Date(currentTime.getTime() - timeOffset);
        const isManual = i === 2; // Make one manual entry
        
        mockHistory.push({
            time: eventTime.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
            type: isManual ? 'Manual' : 'Auto',
            duration: isManual ? '2m' : '30s',
            trigger: isManual ? 'User' : 'Schedule',
            status: 'Complete'
        });
    }
    
    historyElement.innerHTML = mockHistory.map(item => `
        <tr>
            <td>${item.time}</td>
            <td><span class="badge bg-${item.type === 'Manual' ? 'primary' : 'secondary'}">${item.type}</span></td>
            <td>${item.duration}</td>
            <td>${item.trigger}</td>
            <td><span class="badge bg-success">${item.status}</span></td>
        </tr>
    `).join('');
}

// Enhanced error handling for fetch requests
async function makeRequest(url, options = {}) {
    try {
        console.log(`Making request to ${url}`, options);
        
        const defaultOptions = {
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            },
            cache: 'no-store'
        };
        
        const finalOptions = { ...defaultOptions, ...options };
        if (finalOptions.headers && options.headers) {
            finalOptions.headers = { ...defaultOptions.headers, ...options.headers };
        }
        
        const response = await fetch(url, finalOptions);
        console.log(`Response received from ${url}, status:`, response.status);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const contentType = response.headers.get('content-type');
        if (contentType && contentType.includes('application/json')) {
            return await response.json();
        } else {
            const text = await response.text();
            console.warn(`Non-JSON response from ${url}:`, text);
            return { status: 'error', message: 'Invalid response format' };
        }
    } catch (error) {
        console.error(`Request to ${url} failed:`, error);
        return { status: 'error', message: error.message };
    }
}

// Improved toast notification function
function showToast(message, type = 'info') {
    console.log(`Toast: ${type} - ${message}`);
    
    // Create toast element
    const toast = document.createElement('div');
    toast.className = `alert alert-${type === 'error' ? 'danger' : type} alert-dismissible fade show`;
    toast.style.cssText = 'position: fixed; top: 20px; right: 20px; z-index: 9999; min-width: 250px;';
    toast.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    
    document.body.appendChild(toast);
    
    // Auto-remove after 5 seconds
    setTimeout(() => {
        if (toast.parentNode) {
            toast.parentNode.removeChild(toast);
        }
    }, 5000);
}

// FIXED: Debounced event listeners to prevent multiple attachments
let listenersAttached = false;
function attachCalculationListeners() {
    if (listenersAttached) return; // Prevent multiple attachments
    
    const inputs = [
        'seconds-on', 'seconds-off', 
        'day-seconds-on', 'day-seconds-off',
        'night-seconds-on', 'night-seconds-off',
        'active-hours-start', 'active-hours-end', 'max-daily-minutes'
    ];
    inputs.forEach(id => {
        const element = document.getElementById(id);
        if (element) {
            // Use debounced recalculation
            element.addEventListener('input', debounce(recalculateCyclePattern, 300));
            element.addEventListener('change', debounce(recalculateCyclePattern, 300));
            element.addEventListener('input', debounce(updateCycleDebugInfo, 300));
            element.addEventListener('change', debounce(updateCycleDebugInfo, 300));
        }
    });
    listenersAttached = true;
}

// Debounce utility function
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// FIXED: Single DOM Ready initialization to prevent duplicates
let domInitialized = false;
document.addEventListener('DOMContentLoaded', function() {
    if (domInitialized) {
        console.log('DOM already initialized, skipping...');
        return;
    }
    
    console.log('DOM Ready - Initializing Watering Page');
    domInitialized = true;
    
    try {
        // Initialize HTTP-only communication (no Socket.IO)
        initializeWateringSocket();
        
        // Attach event handlers ONCE
        const saveBtn = document.getElementById('save-settings-btn');
        if (saveBtn) {
            console.log('Attaching save settings button handler');
            saveBtn.addEventListener('click', function(e) {
                e.preventDefault();
                saveWateringSettings();
            });
        }
        
        // Attach pump control handlers ONCE
        const startBtn = document.getElementById('pump-start-btn');
        const stopBtn = document.getElementById('pump-stop-btn');
        
        if (startBtn && !startBtn.hasAttribute('data-listener-attached')) {
            startBtn.addEventListener('click', function(e) {
                e.preventDefault();
                startPump();
            });
            startBtn.setAttribute('data-listener-attached', 'true');
            console.log('Start pump button handler attached');
        }
        
        if (stopBtn && !stopBtn.hasAttribute('data-listener-attached')) {
            stopBtn.addEventListener('click', function(e) {
                e.preventDefault();
                stopPump();
            });
            stopBtn.setAttribute('data-listener-attached', 'true');
            console.log('Stop pump button handler attached');
        }
        
        // Attach calculation listeners ONCE
        attachCalculationListeners();
        
        // Load initial data
        loadWateringSettings(true); // Force refresh
        loadRelayStates();
        
        // Update additional UI components
        updateCycleDebugInfo();
        updateTodayUsage();
        updateWateringHistory();
        
        // Set up periodic updates with longer intervals
        setInterval(() => {
            loadWateringSettings();
            updateTodayUsage();
            updateWateringHistory();
        }, 60000); // Update every 60 seconds (increased from 30)
        
        setInterval(loadRelayStates, 30000); // Update relay states every 30 seconds (increased from 10)
        
        console.log('Watering page initialized successfully');
        
    } catch (error) {
        console.error('Error during watering page initialization:', error);
        showToast('Error initializing watering page', 'error');
    }
});

// Cleanup on page unload
window.addEventListener('beforeunload', function() {
    stopFallbackPolling();
    stopManualCountdown();
});
