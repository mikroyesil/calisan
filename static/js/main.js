/**
 * main.js - Main JavaScript for Vertical Farm Control System
 * HTTP-only version with Socket.IO disabled for reliability
 */

// DISABLE Socket.IO completely to avoid 404 errors
let socket;
let mainInitialized = false; // Prevent multiple initializations

function initializeSocket() {
    if (mainInitialized) return socket;
    
    console.log('Socket.IO disabled - using HTTP-only communication');
    
    // Create dummy socket for compatibility
    socket = {
        on: function(event, callback) {
            // Silently ignore socket events in HTTP-only mode
        },
        emit: function(event, data) {
            // Silently ignore socket emits in HTTP-only mode
        },
        connected: false,
        disconnect: function() {},
        connect: function() {}
    };
    
    mainInitialized = true;
    return socket;
}

// Initialize socket immediately
socket = initializeSocket();

// Make socket globally available
window.socket = socket;
window.charts = {};

// Global variables
let sensorData = {};
let chartTimeLabels = [];
const MAX_DATA_POINTS = 50;

// Single chart configuration object
window.chartConfigs = {
    tempHumidity: {
        datasets: [
            {
                label: 'Temperature (°C)',
                yAxisID: 'y-temp',
                borderColor: '#ff6384',
                tension: 0.4
            },
            {
                label: 'Humidity (%)',
                yAxisID: 'y-humidity',
                borderColor: '#36a2eb',
                tension: 0.4
            }
        ]
    },
    phEc: {
        datasets: [
            {
                label: 'pH',
                yAxisID: 'pH',
                borderColor: '#2196F3',
                tension: 0.4
            },
            {
                label: 'EC',
                yAxisID: 'EC',
                borderColor: '#4CAF50',
                tension: 0.4
            }
        ],
        options: {
            scales: {
                pH: {
                    type: 'linear',
                    position: 'left',
                    min: 5,
                    max: 7,
                    title: { display: true, text: 'pH' }
                },
                EC: {
                    type: 'linear',
                    position: 'right',
                    min: 0,
                    max: 2,
                    title: { display: true, text: 'EC (mS/cm)' }
                }
            }
        }
    },
    co2: {
        datasets: [{
            label: 'CO₂ (ppm)',
            borderColor: '#7e57c2',
            tension: 0.4
        }]
    }
};

// Declare charts globally
let phEcChart = null;

// Setup socket event listeners
function setupSocketListeners() {
    socket.on('connect', function() {
        console.log('Connected to server via Socket.IO');
        showToast('System connected', 'success');
        updateConnectionStatus(true);
        hideLoadingSpinners();
    });

    socket.on('disconnect', function(reason) {
        console.log('Disconnected from server:', reason);
        showToast('System disconnected - attempting to reconnect...', 'warning');
        updateConnectionStatus(false);
        showOfflineState();
        startReconnectAttempts();
    });

    socket.on('reconnect', (attemptNumber) => {
        console.log('Reconnected to server after', attemptNumber, 'attempts');
        showToast('System reconnected', 'success');
        updateConnectionStatus(true);
    });

    socket.on('reconnect_error', (error) => {
        console.error('Reconnection error:', error);
        showToast('Reconnection failed - check network connection', 'danger');
    });

    socket.on('sensor_update', function(data) {
        sensorData = data;
        updateSensorDisplays(data);
        updateCharts(data);
    });

    // Add other socket event listeners
    socket.on('nutrient_settings', updateNutrientSettings);
    socket.on('environment_settings', updateEnvironmentSettings);
    socket.on('watering_settings', updateWateringSettings);
    socket.on('light_schedules', updateLightSchedules);
    socket.on('error', data => showToast(data.message, 'error'));
}

// Update sensor displays if they exist on the current page
function updateSensorDisplays(data) {
    // Temperature
    const tempElement = document.getElementById('current-temperature');
    if (tempElement && data.temperature) {
        tempElement.textContent = `${data.temperature.toFixed(1)}°C`;
        
        const tempStatus = document.getElementById('temp-status');
        if (tempStatus) {
            if (data.temperature < 15 || data.temperature > 24) {
                tempStatus.textContent = 'Warning';
                tempStatus.className = 'badge bg-warning';
            } else {
                tempStatus.textContent = 'Normal';
                tempStatus.className = 'badge bg-success';
            }
        }
    }
    
    // Humidity
    const humidityElement = document.getElementById('current-humidity');
    if (humidityElement && data.humidity) {
        humidityElement.textContent = `${data.humidity.toFixed(0)}%`;
        
        const humidityStatus = document.getElementById('humidity-status');
        if (humidityStatus) {
            if (data.humidity < 50 || data.humidity > 80) {
                humidityStatus.textContent = 'Warning';
                humidityStatus.className = 'badge bg-warning';
            } else {
                humidityStatus.textContent = 'Normal';
                humidityStatus.className = 'badge bg-success';
            }
        }
    }
    
    // pH
    const phElement = document.getElementById('current-ph');
    if (phElement && data.ph) {
        phElement.textContent = data.ph.toFixed(1);
        
        const phStatus = document.getElementById('ph-status');
        if (phStatus) {
            if (data.ph < 5.5 || data.ph > 6.5) {
                phStatus.textContent = 'Warning';
                phStatus.className = 'badge bg-warning';
            } else {
                phStatus.textContent = 'Normal';
                phStatus.className = 'badge bg-success';
            }
        }
    }
    
    // EC
    const ecElement = document.getElementById('current-ec');
    if (ecElement && data.ec) {
        ecElement.textContent = `${data.ec.toFixed(2)} mS/cm`;
        
        const ecStatus = document.getElementById('ec-status');
        if (ecStatus) {
            if (data.ec < 0.8 || data.ec > 1.6) {
                ecStatus.textContent = 'Warning';
                ecStatus.className = 'badge bg-warning';
            } else {
                ecStatus.textContent = 'Normal';
                ecStatus.className = 'badge bg-success';
            }
        }
    }
    
    // CO2
    const co2Element = document.getElementById('current-co2');
    if (co2Element && data.co2) {
        co2Element.textContent = `${data.co2.toFixed(0)} ppm`;
        
        const co2Progress = document.getElementById('co2-progress');
        if (co2Progress) {
            const co2Percentage = Math.min(100, (data.co2 / 1500) * 100);
            co2Progress.style.width = `${co2Percentage}%`;
            
            if (data.co2 < 600 || data.co2 > 1400) {
                co2Progress.className = 'progress-bar bg-warning';
            } else {
                co2Progress.className = 'progress-bar bg-primary';
            }
        }
    }
}

// Utility function to format timestamp
function formatTimestamp(timestamp) {
    const date = new Date(timestamp * 1000);
    const hours = date.getHours().toString().padStart(2, '0');
    const minutes = date.getMinutes().toString().padStart(2, '0');
    return `${hours}:${minutes}`;
}

// Utility function to format date
function formatDate(timestamp) {
    const date = new Date(timestamp * 1000);
    return date.toLocaleDateString();
}

// Show a toast notification
function showToast(message, type = 'info') {
    // Create toast container if it doesn't exist
    let toastContainer = document.getElementById('toast-container');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.id = 'toast-container';
        toastContainer.className = 'toast-container position-fixed bottom-0 end-0 p-3';
        document.body.appendChild(toastContainer);
    }
    
    // Create toast element
    const toastId = 'toast-' + Date.now();
    const toast = document.createElement('div');
    toast.className = `toast align-items-center text-white bg-${type} border-0`;
    toast.id = toastId;
    toast.setAttribute('role', 'alert');
    toast.setAttribute('aria-live', 'assertive');
    toast.setAttribute('aria-atomic', 'true');
    
    // Toast content
    toast.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">
                ${message}
            </div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>
    `;
    
    // Add to container
    toastContainer.appendChild(toast);
    
    // Initialize and show toast
    const toastInstance = new bootstrap.Toast(toast, {
        delay: 3000
    });
    toastInstance.show();
    
    // Remove from DOM after hidden
    toast.addEventListener('hidden.bs.toast', function() {
        toast.remove();
    });
}

// Add retry configuration
const API_CONFIG = {
    maxRetries: 3,
    retryDelay: 1000,
    timeout: 5000
};

async function fetchAPI(url, method = 'GET', data = null, retryCount = 0) {
    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), API_CONFIG.timeout);

        const options = {
            method: method,
            headers: {
                'Content-Type': 'application/json'
            },
            signal: controller.signal
        };
        
        if (data && (method === 'POST' || method === 'PUT')) {
            options.body = JSON.stringify(data);
        }
        
        const response = await fetch(url, options);
        clearTimeout(timeoutId);

        if (!response.ok) {
            throw new Error(`API error: ${response.status}`);
        }
        
        return await response.json();

    } catch (error) {
        console.error(`API error (attempt ${retryCount + 1}):`, error);

        // Handle specific error types
        if (error.name === 'AbortError') {
            showToast('Request timeout - retrying...', 'warning');
        } else if (error instanceof TypeError) {
            showToast('Network error - check connection', 'warning');
        } else {
            showToast(`API Error: ${error.message}`, 'danger');
        }

        // Implement retry logic
        if (retryCount < API_CONFIG.maxRetries) {
            await new Promise(resolve => setTimeout(resolve, API_CONFIG.retryDelay * (retryCount + 1)));
            return fetchAPI(url, method, data, retryCount + 1);
        }

        // If all retries failed, try fallback
        const fallbackData = await tryFallbackOperation(url, method, data);
        if (fallbackData) {
            return fallbackData;
        }

        throw new Error(`Failed after ${API_CONFIG.maxRetries} retries`);
    }
}

async function tryFallbackOperation(url, method, data) {
    // Try to get cached data for GET requests
    if (method === 'GET') {
        const cachedData = await getCachedData(url);
        if (cachedData) {
            showToast('Using cached data', 'info');
            return cachedData;
        }
    }

    // Try alternative endpoint if available
    if (url.includes('/api/')) {
        const fallbackUrl = url.replace('/api/', '/api/fallback/');
        try {
            const response = await fetch(fallbackUrl, {
                method: method,
                headers: { 'Content-Type': 'application/json' },
                body: data ? JSON.stringify(data) : null
            });
            if (response.ok) {
                return await response.json();
            }
        } catch (error) {
            console.error('Fallback operation failed:', error);
        }
    }

    return null;
}

// Add caching helper
async function getCachedData(url) {
    if ('caches' in window) {
        try {
            const cache = await caches.open('api-cache');
            const cachedResponse = await cache.match(url);
            if (cachedResponse) {
                return await cachedResponse.json();
            }
        } catch (error) {
            console.error('Cache retrieval failed:', error);
        }
    }
    return null;
}

// Usage example:
// fetchAPI('/api/sensor-data')
//     .then(data => {
//         if (data) {
//             updateSensorDisplays(data);
//         }
//     })
//     .catch(error => {
//         console.error('Final error:', error);
//         showToast('Failed to fetch sensor data', 'danger');
//     });

// Load recent events
function loadRecentEvents(limit = 5) {
    const eventsContainer = document.getElementById('recent-events');
    if (!eventsContainer) return;
    
    fetchAPI(`/api/events?limit=${limit}`)
        .then(events => {
            if (!events || events.length === 0) {
                eventsContainer.innerHTML = '<tr><td colspan="3" class="text-center">No recent events</td></tr>';
                return;
            }
            
            eventsContainer.innerHTML = '';
            events.forEach(event => {
                const row = document.createElement('tr');
                
                // Format timestamp
                const timeCell = document.createElement('td');
                timeCell.textContent = formatTimestamp(event.timestamp);
                
                // Event type
                const typeCell = document.createElement('td');
                typeCell.textContent = event.event_type;
                
                // Details
                const detailsCell = document.createElement('td');
                if (typeof event.details === 'object') {
                    detailsCell.textContent = Object.entries(event.details)
                        .map(([key, value]) => `${key}: ${value}`)
                        .join(', ');
                } else {
                    detailsCell.textContent = event.details;
                }
                
                row.appendChild(timeCell);
                row.appendChild(typeCell);
                row.appendChild(detailsCell);
                eventsContainer.appendChild(row);
            });
        });
}

// Enhanced chart initialization
function initializeCharts() {
    // Clean up existing chart if any
    if (phEcChart) {
        phEcChart.destroy();
        phEcChart = null;
    }

    const phEcCtx = document.getElementById('ph-ec-chart');
    if (phEcCtx) {
        try {
            phEcChart = new Chart(phEcCtx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: window.chartConfigs.phEc.datasets.map(dataset => ({
                        ...dataset,
                        data: []
                    }))
                },
                options: {
                    ...window.chartConfigs.phEc.options,
                    animation: false,
                    responsive: true,
                    maintainAspectRatio: false
                }
            });
        } catch (error) {
            console.error('Failed to initialize chart:', error);
            showToast('Failed to initialize charts', 'danger');
        }
    }
}

// Update charts with null check
function updateCharts(data) {
    if (!phEcChart) return; // Exit if chart not initialized

    const now = new Date();
    const timeString = now.getHours().toString().padStart(2, '0') + ':' + 
                      now.getMinutes().toString().padStart(2, '0');
    
    try {
        // Update pH/EC chart
        if (data.ph !== undefined || data.ec !== undefined) {
            // Add new labels only if we're adding data
            phEcChart.data.labels.push(timeString);
            
            // Update pH data
            if (data.ph !== undefined) {
                phEcChart.data.datasets[0].data.push(parseFloat(data.ph));
            } else {
                const previousValue = phEcChart.data.datasets[0].data[phEcChart.data.datasets[0].data.length - 1];
                phEcChart.data.datasets[0].data.push(previousValue || null);
            }
            
            // Update EC data
            if (data.ec !== undefined) {
                phEcChart.data.datasets[1].data.push(parseFloat(data.ec));
            } else {
                const previousValue = phEcChart.data.datasets[1].data[phEcChart.data.datasets[1].data.length - 1];
                phEcChart.data.datasets[1].data.push(previousValue || null);
            }
            
            // Maintain data point limit
            if (phEcChart.data.labels.length > MAX_DATA_POINTS) {
                phEcChart.data.labels.shift();
                phEcChart.data.datasets.forEach(dataset => dataset.data.shift());
            }
            
            phEcChart.update('none'); // Update without animation for better performance
        }
    } catch (error) {
        console.error('Error updating charts:', error);
    }
}

// Add fallback HTTP polling function
function startHttpPolling() {
    const pollInterval = setInterval(async () => {
        if (socket.connected) {
            clearInterval(pollInterval);
            return;
        }
        
        try {
            const response = await fetch('/api/sensor-data');
            if (response.ok) {
                const data = await response.json();
                updateSensorDisplays(data);
                updateCharts(data);
            }
        } catch (error) {
            console.error('Polling error:', error);
        }
    }, 10000); // Poll every 10 seconds
}

// Add connection status indicator
function updateConnectionStatus(connected) {
    const statusIndicator = document.getElementById('connection-status');
    if (statusIndicator) {
        statusIndicator.className = `status-indicator ${connected ? 'connected' : 'disconnected'}`;
        statusIndicator.title = connected ? 'Connected' : 'Disconnected';
    }
}

// Document ready - single event listener
let domReadyFired = false;
document.addEventListener('DOMContentLoaded', function() {
    if (domReadyFired) return; // Prevent duplicate execution
    domReadyFired = true;
    
    console.log('main.js: DOM fully loaded - HTTP-only mode');
    
    // Initialize charts if they exist on the page
    initializeCharts();

    // Add error boundary
    window.addEventListener('error', function(event) {
        console.error('Global error:', event.error);
        showToast('An error occurred. Please refresh the page.', 'error');
    });
});

// Enhanced UI update functions
function updateConnectionStatus(connected) {
    const statusIcon = document.getElementById('connection-status');
    const statusText = document.getElementById('connection-text');
    
    if (statusIcon && statusText) {
        statusIcon.className = connected ? 'connected' : 'disconnected';
        statusText.textContent = connected ? 'Connected' : 'Disconnected';
        statusText.style.color = connected ? '#4CAF50' : '#F44336';
    }
}

function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `alert alert-${type} alert-dismissible fade show`;
    toast.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    
    const container = document.getElementById('alert-container');
    if (container) {
        container.appendChild(toast);
        setTimeout(() => toast.remove(), 5000);
    }
}

function hideLoadingSpinners() {
    document.querySelectorAll('.loading-spinner').forEach(spinner => {
        spinner.remove();
    });
}

function showOfflineState() {
    document.querySelectorAll('.sensor-value').forEach(value => {
        value.innerHTML = '<span class="text-muted">Offline</span>';
    });
}

// Add missing update functions
function updateNutrientSettings(data) {
    if (!data) return;
    
    const ecTarget = document.getElementById('ec-target');
    if (ecTarget) ecTarget.value = data.ec_target;
    
    const phTarget = document.getElementById('ph-target');
    if (phTarget) phTarget.value = data.ph_target;
}

function updateEnvironmentSettings(data) {
    if (!data) return;
    
    const co2Target = document.getElementById('co2-target');
    if (co2Target) co2Target.value = data.co2_target;
    
    const humidityMin = document.getElementById('humidity-min');
    if (humidityMin) humidityMin.value = data.humidity_min;
}

function updateWateringSettings(data) {
    if (!data) return;
    
    const cycleMinutes = document.getElementById('cycle-minutes');
    if (cycleMinutes) cycleMinutes.value = data.cycle_minutes;
}

function updateLightSchedules(data) {
    if (!data) return;
    
    // Update light schedules if the page has them
    if (typeof renderScheduleTable === 'function') {
        renderScheduleTable(data);
    }
}

// Global utility functions for the entire application

// Function to safely bind event handlers to elements
function safeBind(selector, event, handler) {
    const elements = document.querySelectorAll(selector);
    if (elements.length === 0) {
        console.warn(`No elements found for selector: ${selector}`);
        return false;
    }
    
    elements.forEach(element => {
        // Remove existing handler first to prevent duplicates
        element.removeEventListener(event, handler);
        // Add the new handler
        element.addEventListener(event, handler);
    });
    
    console.log(`Bound ${elements.length} elements with selector: ${selector}`);
    return true;
}

// Helper function to show a toast message
function showToast(message, type = 'info') {
    const toastContainer = document.getElementById('toastContainer');
    if (!toastContainer) {
        console.error('Toast container not found');
        return;
    }

    const toastId = 'toast-' + Date.now();
    const toastHtml = `
        <div id="${toastId}" class="toast" role="alert" aria-live="assertive" aria-atomic="true">
            <div class="toast-header bg-${type} text-white">
                <strong class="me-auto">Notification</strong>
                <button type="button" class="btn-close btn-close-white" data-bs-dismiss="toast"></button>
            </div>
            <div class="toast-body">${message}</div>
        </div>
    `;
    
    toastContainer.insertAdjacentHTML('beforeend', toastHtml);
    const toastElement = document.getElementById(toastId);
    const toast = new bootstrap.Toast(toastElement, { delay: 3000 });
    toast.show();
    
    toastElement.addEventListener('hidden.bs.toast', function() {
        this.remove();
    });
}

// Check if the page has loaded properly
document.addEventListener('DOMContentLoaded', function() {
    console.log('main.js: DOM fully loaded');
});

// Expose these utilities globally
window.safeBind = safeBind;
window.showToast = showToast;