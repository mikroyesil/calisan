/**
 * Environment Monitoring and Control
 * Handles fetching sensor data and updating the UI
 */

// Global state variables
let fetchInProgress = false;
let lastUpdateTime = 0;
let lastFetchDuration = 0;
let lastSuccessfulFetchTime = 0;
let consecutiveErrors = 0;
let errorBackoffTime = 5000;
let inErrorBackoff = false;
let lastSuccessfulData = null;
let apiHealthy = true;
let lastApiCheck = 0;

// Configuration
const REFRESH_INTERVAL = 10000; // 10 seconds for faster updates
const API_TIMEOUT = 10000; // 10 seconds
const MAX_CONSECUTIVE_ERRORS = 3;
const MIN_FETCH_INTERVAL = 2000; // Minimum time between fetches
const API_ENDPOINT = '/api/sensors';

// Performance optimization: Cache DOM elements to avoid repeated queries
const domCache = {};
const observedElements = new Set(); // Track which elements we're observing

function getCachedElement(id) {
    if (!domCache[id]) {
        domCache[id] = document.getElementById(id);
        
        // Performance: Observe element for changes to clear cache if removed
        if (domCache[id] && !observedElements.has(id)) {
            observedElements.add(id);
            
            // Use MutationObserver to detect when elements are removed
            if (typeof MutationObserver !== 'undefined') {
                const observer = new MutationObserver((mutations) => {
                    mutations.forEach((mutation) => {
                        if (mutation.type === 'childList') {
                            mutation.removedNodes.forEach((node) => {
                                if (node.id && domCache[node.id]) {
                                    delete domCache[node.id];
                                    observedElements.delete(node.id);
                                }
                            });
                        }
                    });
                });
                
                observer.observe(document.body, {
                    childList: true,
                    subtree: true
                });
            }
        }
    }
    return domCache[id];
}

// Optimized fetch with better error handling and performance monitoring
function fetchSensorData() {
    // Skip if already fetching or in error backoff
    if (inErrorBackoff || fetchInProgress) {
        return;
    }
    
    // Rate limiting - prevent requests that are too close together
    const now = Date.now();
    if (now - lastSuccessfulFetchTime < MIN_FETCH_INTERVAL) {
        console.log(`Rate limiting - skipping fetch (${now - lastSuccessfulFetchTime}ms since last fetch)`);
        return;
    }
    
    fetchInProgress = true;
    const fetchStartTime = Date.now();
    console.log(`[${new Date().toISOString()}] Starting sensor data fetch...`);
    
    // Show loading indicator
    updateConnectionStatus('fetching');
    
    // Set up timeout handling
    const controller = new AbortController();
    const timeoutId = setTimeout(() => {
        controller.abort();
        console.warn(`Fetch timeout after ${API_TIMEOUT/1000} seconds`);
    }, API_TIMEOUT);

    // Fetch with abort controller for better timeout handling
    fetch(API_ENDPOINT, {
        method: 'GET',
        headers: {
            'Accept': 'application/json',
            'Cache-Control': 'no-cache',
            'X-Requested-With': 'XMLHttpRequest'
        },
        signal: controller.signal,
        cache: 'no-store'
    })
    .then(response => {
        clearTimeout(timeoutId);
        console.log(`[${new Date().toISOString()}] Network response received (${Date.now() - fetchStartTime}ms), status: ${response.status}`);
        
        if (!response.ok) {
            throw new Error(`Server returned ${response.status}: ${response.statusText}`);
        }
        return response.json();
    })
    .then(data => {
        lastFetchDuration = Date.now() - fetchStartTime;
        console.log(`[${new Date().toISOString()}] Sensor data fetch completed in ${lastFetchDuration}ms`);
        
        // Extract sensor data from response structure
        const sensorData = data.data || data;
        
        // Save data and reset error states
        lastSuccessfulData = sensorData;
        consecutiveErrors = 0;
        inErrorBackoff = false;
        apiHealthy = true;
        lastApiCheck = Date.now();
        lastSuccessfulFetchTime = Date.now();
        
        // Update UI with the extracted sensor data
        updateSensorDisplays(sensorData);
        lastUpdateTime = Date.now();
        fetchInProgress = false;
        
        // Performance warning if fetch is slow
        if (lastFetchDuration > 3000) {
            console.warn(`⚠️ Slow sensor fetch: ${lastFetchDuration}ms`);
            showToast(`Sensor data updated (slow response: ${Math.round(lastFetchDuration/100)/10}s)`, "warning");
        }
        
        updateConnectionStatus('connected');
        
        // Cache data for offline use
        try {
            localStorage.setItem('env_sensor_cache', JSON.stringify({
                ...sensorData,
                cached_at: Date.now()
            }));
        } catch (e) {
            console.error("Error caching sensor data:", e);
        }
    })
    .catch(error => {
        clearTimeout(timeoutId);
        
        const fetchTime = Date.now() - fetchStartTime;
        console.error(`[${new Date().toISOString()}] Error fetching sensor data (${fetchTime}ms): ${error.message}`);
        fetchInProgress = false;
        consecutiveErrors++;
        apiHealthy = false;
        
        // Handle specific error types
        if (error.name === 'AbortError') {
            console.error("🚨 API request timed out");
            updateConnectionStatus('timeout');
            showToast("Sensor API timeout. Check server connection.", "danger");
        } else if (error.message.includes('Failed to fetch')) {
            console.error("🚨 Network error. Check if the API server is running.");
            updateConnectionStatus('error');
            showToast("Network connection error to sensor API.", "danger");
        } else {
            updateConnectionStatus('error');
            showToast(`Sensor API error: ${error.message}`, "danger");
        }
        
        // Error backoff logic
        if (consecutiveErrors >= MAX_CONSECUTIVE_ERRORS) {
            inErrorBackoff = true;
            errorBackoffTime = Math.min(errorBackoffTime * 1.5, 60000); // Max 1 minute
            
            console.log(`Entering error backoff for ${errorBackoffTime/1000}s after ${consecutiveErrors} consecutive errors`);
            
            setTimeout(() => {
                inErrorBackoff = false;
                console.log("Exiting error backoff, resuming normal fetching");
            }, errorBackoffTime);
            
            // Use cached data if available
            if (lastSuccessfulData) {
                console.log("Using cached data due to API errors");
                updateSensorDisplays({...lastSuccessfulData, cached: true});
                updateConnectionStatus('cached');
            } else {
                showToast(`Connection to sensor system lost. Retrying in ${errorBackoffTime/1000}s.`, "warning");
            }
        }
    });
}

// Optimized sensor display updates using cached DOM elements and batching
function updateSensorDisplays(data) {
    if (!data || Object.keys(data).length === 0) {
        console.warn("No sensor data to display");
        showNoDataMessage();
        return;
    }

    console.log("Updating sensor displays with data:", data);

    // Performance: Batch DOM updates in requestAnimationFrame for smooth rendering
    requestAnimationFrame(() => {
        // Prepare all updates first to minimize DOM queries
        const updates = [
            {
                element: getCachedElement('current-temperature'),
                value: data.temperature,
                formatter: val => `${parseFloat(val).toFixed(1)}°C`
            },
            {
                element: getCachedElement('current-humidity'), 
                value: data.humidity,
                formatter: val => `${Math.round(val)}%`
            },
            {
                element: getCachedElement('current-co2'),
                value: data.co2,
                formatter: val => `${Math.round(val)} ppm`
            }
        ];
        
        // Apply all text updates at once
        updates.forEach(({element, value, formatter}) => {
            if (element && value !== undefined && value !== null) {
                try {
                    const numValue = typeof value === 'string' ? parseFloat(value) : value;
                    if (!isNaN(numValue)) {
                        element.textContent = formatter(numValue);
                    } else {
                        element.textContent = "Error";
                    }
                } catch (e) {
                    console.error(`Error updating element:`, e);
                    element.textContent = "Error";
                }
            }
        });
        
        // Update status badges in batch
        updateStatusBadge('temp-status', data.temperature, 18, 25);
        updateStatusBadge('humidity-status', data.humidity, 50, 80);
        updateStatusBadge('co2-status', data.co2, 400, 1200);
        
        // Show cached data indicator if needed
        if (data.cached) {
            updateConnectionStatus('cached');
            const cacheAge = Date.now() - (data.cached_at || 0);
            if (cacheAge > 60000) { // Show age if > 1 minute
                showToast(`Using cached data (${Math.round(cacheAge/60000)} min old)`, "warning");
            }
        }
    });
}

// Helper function to update an element with sensor data
function updateElement(elementId, value, formatter) {
    const element = getCachedElement(elementId);
    if (element && value !== undefined && value !== null) {
        try {
            const numValue = typeof value === 'string' ? parseFloat(value) : value;
            if (!isNaN(numValue)) {
                element.textContent = formatter(numValue);
            } else {
                element.textContent = "Error";
            }
        } catch (e) {
            console.error(`Error updating element ${elementId}:`, e);
            element.textContent = "Error";
        }
    }
}

// Helper function when no data is available
function showNoDataMessage() {
    const elements = ['current-temperature', 'current-humidity', 'current-co2'];
    elements.forEach(id => {
        const element = getCachedElement(id);
        if (element) element.textContent = 'No Data';
    });
    
    // Update status badges to show no data
    const statusElements = ['temp-status', 'humidity-status', 'co2-status'];
    statusElements.forEach(id => {
        const element = getCachedElement(id);
        if (element) {
            element.className = 'badge bg-secondary';
            element.textContent = 'No Data';
        }
    });
}

// Helper function to update status badges
function updateStatusBadge(elementId, value, min, max) {
    const element = getCachedElement(elementId);
    if (!element || value === undefined || value === null) return;
    
    const numValue = typeof value === 'string' ? parseFloat(value) : value;
    if (isNaN(numValue)) return;
    
    let status, className, text;
    
    if (numValue < min) {
        status = 'low';
        className = 'badge bg-info';
        text = 'Low';
    } else if (numValue > max) {
        status = 'high';
        className = 'badge bg-warning';
        text = 'High';
    } else {
        status = 'good';
        className = 'badge bg-success';
        text = 'Good';
    }
    
    element.className = className;
    element.textContent = text;
}

// Optimized toast notification system
function showToast(message, type = 'info', duration = 5000) {
    const toastContainer = getCachedElement('toastContainer') || createToastContainer();
    
    const toast = document.createElement('div');
    toast.className = `toast align-items-center text-white bg-${type} border-0`;
    toast.setAttribute('role', 'alert');
    toast.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">${message}</div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
        </div>
    `;
    
    toastContainer.appendChild(toast);
    
    // Initialize and show toast
    const bsToast = new bootstrap.Toast(toast, { delay: duration });
    bsToast.show();
    
    // Clean up after hiding
    toast.addEventListener('hidden.bs.toast', () => {
        if (toast.parentNode) {
            toast.parentNode.removeChild(toast);
        }
    });
}

function createToastContainer() {
    const container = document.createElement('div');
    container.id = 'toastContainer';
    container.className = 'toast-container position-fixed bottom-0 end-0 p-3';
    document.body.appendChild(container);
    domCache.toastContainer = container;
    return container;
}

// Connection status helper with cached element
function updateConnectionStatus(status) {
    const element = getCachedElement('sensor-connection-status');
    if (!element) return;
    
    const statusMap = {
        fetching: { class: 'badge bg-warning', text: 'Fetching...' },
        connected: { class: 'badge bg-success', text: 'Connected' },
        timeout: { class: 'badge bg-danger', text: 'Timeout' },
        error: { class: 'badge bg-danger', text: 'Error' },
        cached: { class: 'badge bg-secondary', text: 'Cached Data' }
    };
    
    const config = statusMap[status] || { class: 'badge bg-secondary', text: status };
    element.className = config.class;
    element.textContent = config.text;
}

// Load cached data on startup for faster initial display
function loadCachedData() {
    try {
        const cached = localStorage.getItem('env_sensor_cache');
        if (cached) {
            const data = JSON.parse(cached);
            const cacheAge = Date.now() - (data.cached_at || 0);
            
            // Only use cache if it's less than 5 minutes old
            if (cacheAge < 300000) {
                console.log(`Loading cached sensor data (${Math.round(cacheAge/1000)}s old)`);
                updateSensorDisplays({...data, cached: true});
                return true;
            }
        }
    } catch (e) {
        console.error("Error loading cached data:", e);
    }
    return false;
}

// CO2 Status Management with performance optimizations
let co2StatusTimer = null;
let lastCO2StatusUpdate = 0;
const CO2_STATUS_THROTTLE = 5000; // Throttle CO2 status updates to max every 5 seconds

function fetchCO2Status() {
    // Throttle CO2 status updates for better performance
    const now = Date.now();
    if (now - lastCO2StatusUpdate < CO2_STATUS_THROTTLE) {
        console.log('CO2 status update throttled');
        return;
    }
    
    lastCO2StatusUpdate = now;
    
    fetch('/api/co2/status')
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                updateCO2StatusDisplay(data.co2_status);
            } else {
                console.warn('Failed to fetch CO2 status:', data.message);
            }
        })
        .catch(error => {
            console.error('Error fetching CO2 status:', error);
        });
}

function updateCO2StatusDisplay(co2Status) {
    // Performance: Batch all CO2 status updates in single animation frame
    requestAnimationFrame(() => {
        // Update current CO2 reading in status card
        updateElement('co2-current-reading', co2Status.current_co2, val => `${Math.round(val)} ppm`);
        
        // Update status mode
        const statusElement = getCachedElement('co2-status-mode');
        if (statusElement) {
            statusElement.textContent = co2Status.co2_mode || 'Auto';
        }
        
        // Update cycle badge
        const cycleElement = getCachedElement('co2-cycle-badge');
        if (cycleElement) {
            const isDay = co2Status.is_day_cycle;
            cycleElement.textContent = isDay ? 'Day' : 'Night';
            cycleElement.className = isDay ? 
                'badge bg-warning text-dark fs-6 px-3 py-2' : 
                'badge bg-dark fs-6 px-3 py-2';
        }
        
        // Update active target
        updateElement('co2-active-target', co2Status.target_co2, val => `${Math.round(val)} ppm`);
        
        // Update light status indicators
        const lightIcon = getCachedElement('light-status-icon');
        const lightText = getCachedElement('light-status-text');
        
        if (lightIcon && lightText) {
            if (co2Status.is_day_cycle) {
                lightIcon.textContent = '☀️';
                lightText.textContent = 'Lights ON';
            } else {
                lightIcon.textContent = '🌙';
                lightText.textContent = 'Lights OFF';
            }
        }
        
        // Update main CO2 status in sensor readings
        const mainCo2Status = getCachedElement('co2-status');
        if (mainCo2Status) {
            const isActive = co2Status.co2_state;
            mainCo2Status.className = isActive ? 'badge bg-success' : 'badge bg-secondary';
            mainCo2Status.textContent = isActive ? 'Active' : 'Inactive';
        }
    });
}

function startCO2StatusRefresh() {
    fetchCO2Status(); // Initial fetch
    
    if (co2StatusTimer) {
        clearInterval(co2StatusTimer);
    }
    
    co2StatusTimer = setInterval(fetchCO2Status, 30000); // Every 30 seconds
}

function stopCO2StatusRefresh() {
    if (co2StatusTimer) {
        clearInterval(co2StatusTimer);
        co2StatusTimer = null;
    }
}

// Circulation fan control functions
function setCirculationFanMode(mode) {
    console.log(`Setting circulation fan mode to: ${mode}`);
    
    const fanOnMinutes = getCachedElement('fan-on-minutes');
    const fanOffMinutes = getCachedElement('fan-off-minutes');
    
    fetch('/api/environment/fan-control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
            fan_mode: mode,
            fan_on_minutes: parseInt(fanOnMinutes?.value || 5),
            fan_off_minutes: parseInt(fanOffMinutes?.value || 10)
        })
    })
    .then(response => response.json())
    .then(data => {
        console.log('Fan control response:', data);
        if (data.status === 'success') {
            showToast(`Circulation fans set to ${mode} mode`, "success");
            updateFanStatusDisplay(mode);
        } else {
            showToast(`Failed to set fan mode: ${data.message}`, "danger");
        }
    })
    .catch(error => {
        console.error('Error setting fan mode:', error);
        showToast('Error controlling circulation fans', "danger");
    });
}

function updateFanStatusDisplay(mode) {
    const fanStatus = getCachedElement('circulation-fan-status');
    if (fanStatus) {
        let statusHtml = '';
        if (mode === 'continuous') {
            statusHtml = '<span class="badge bg-success">Continuous</span>';
        } else if (mode === 'intermittent') {
            statusHtml = '<span class="badge bg-warning">Intermittent</span>';
        } else if (mode === 'off') {
            statusHtml = '<span class="badge bg-secondary">Off</span>';
        }
        fanStatus.innerHTML = statusHtml;
    }
}

// Diagnostic and utility functions
function diagnoseAPI() {
    const startTime = Date.now();
    console.log("Running API connection diagnostics...");
    
    showToast("Testing API connection...", "info");
    
    fetch(`${API_ENDPOINT}?test=1`, {
        method: 'GET',
        headers: { 'Accept': 'application/json' },
        cache: 'no-store'
    })
    .then(response => {
        const responseTime = Date.now() - startTime;
        console.log(`API responded in ${responseTime}ms with status ${response.status}`);
        
        if (response.ok) {
            showToast(`API connection successful (${responseTime}ms)`, "success");
            return response.json();
        } else {
            showToast(`API returned error ${response.status} (${responseTime}ms)`, "danger");
            throw new Error(`Server error: ${response.status}`);
        }
    })
    .then(data => {
        console.log("API diagnostic data:", data);
        const totalTime = Date.now() - startTime;
        
        if (totalTime > 3000) {
            showToast(`API is responding slowly (${totalTime}ms). Check server load.`, "warning", 8000);
        }
    })
    .catch(error => {
        console.error("API diagnostic error:", error);
        showToast(`API test failed: ${error.message}`, "danger");
    });
}

// Main initialization
document.addEventListener('DOMContentLoaded', function() {
    console.log("Environment monitoring system initializing...");
    
    // Load cached data first for faster perceived performance
    const hasCachedData = loadCachedData();
    
    // Start fetching real data after a short delay if we have cached data
    const initialDelay = hasCachedData ? 1000 : 100;
    setTimeout(() => {
        fetchSensorData();
        startCO2StatusRefresh();
    }, initialDelay);
    
    // Set up regular refresh interval
    setInterval(() => {
        if (!inErrorBackoff) {
            fetchSensorData();
        }
    }, REFRESH_INTERVAL);
    
    // Set up event listeners for manual controls
    const refreshBtn = getCachedElement('manual-refresh-sensors');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            showToast("Manual refresh requested", "info");
            fetchSensorData();
        });
    }
    
    const diagBtn = getCachedElement('show-diagnostics-btn');
    if (diagBtn) {
        diagBtn.addEventListener('click', diagnoseAPI);
    }
    
    console.log(`Environment system initialized. Refresh interval: ${REFRESH_INTERVAL/1000}s`);
});

// Export functions for global access and debugging
window.environmentUtils = {
    fetchSensorData,
    updateSensorDisplays,
    setCirculationFanMode,
    updateFanStatusDisplay,
    diagnoseAPI,
    getCachedElement,
    getPerformanceStats: () => ({
        lastFetchDuration,
        consecutiveErrors,
        apiHealthy,
        lastUpdateTime,
        inErrorBackoff,
        cacheSize: Object.keys(domCache).length
    }),
    clearCache: () => {
        localStorage.removeItem('env_sensor_cache');
        Object.keys(domCache).forEach(key => delete domCache[key]);
        observedElements.clear();
        showToast("Cache cleared", "info");
    }
};

// Expose key functions globally for backwards compatibility
window.setCirculationFanMode = setCirculationFanMode;
window.updateFanStatusDisplay = updateFanStatusDisplay;
window.fetchSensorData = fetchSensorData;