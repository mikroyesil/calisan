// Global variables
let nutrientSettings = {};
let phData = []; // Ensure phData is declared only once
let ecData = [];
let dosingData = {
    nutrient: [],
    ph_up: [],
    ph_down: []
};
let phEcChart;
let dosingChart;

// Create a single socket instance or use existing one from main.js
let socket = window.socket;

// If no socket exists and Socket.IO is available, create one
if (!socket && typeof io !== 'undefined') {
    socket = io(window.socketConfig || {
        transports: ['websocket'],
        upgrade: false,
        reconnection: true,
        reconnectionAttempts: 5,
        reconnectionDelay: 1000,
        timeout: 10000,
        autoConnect: true,
    });
    window.socket = socket;
}

// If still no socket, create a dummy one
if (!socket) {
    console.warn('Socket.IO not available, using dummy socket for nutrients.js');
    socket = {
        on: function(event, callback) {
            // console.log(`Dummy socket - would listen for event: ${event}`);
        },
        emit: function(event, data) {
            // console.log(`Dummy socket - would emit event: ${event}`, data);
        },
        connected: false,
        id: 'dummy',
        disconnect: function() {},
        connect: function() {}
    };
    window.socket = socket;
}

// Enhanced connection handling
socket.on('connect', () => {
    console.log('Socket.IO connected with ID:', socket.id);
    showToast('Connected to server', 'success');
    updateConnectionStatus(true);
    
    // Request initial data while preserving local settings
    const currentSettings = { ...nutrientSettings };
    socket.emit('request_initial_data');
    
    // Keep local changes if they exist
    if (Object.keys(currentSettings).length > 0) {
        nutrientSettings = { ...currentSettings };
        updateLastSavedValues();
    }
});

// Add better error handling
socket.on('connect_error', (error) => {
    console.error('Socket.IO connection error:', error);
    showToast('Connection error: ' + error.message, 'warning');
    updateConnectionStatus(false);
});

socket.on('disconnect', () => {
    console.log('Socket.IO disconnected');
    showToast('Disconnected from server', 'warning');
    updateConnectionStatus(false);
});

document.addEventListener('DOMContentLoaded', function() {
    // Initialize charts
    initCharts();
    
    // Fetch nutrient settings and populate the form
    fetchNutrientSettings().then(settings => {
        if (settings) {
            // Populate the "Last:" fields with the last saved settings
            const lastEc = document.getElementById('last-saved-ec');
            const lastPh = document.getElementById('last-saved-ph');

            if (settings.ec_target !== undefined) {
                lastEc.textContent = `Last: ${settings.ec_target.toFixed(1)} mS/cm`;
            }
            if (settings.ph_target !== undefined) {
                lastPh.textContent = `Last: ${settings.ph_target.toFixed(1)}`;
            }
        }
    });

    // Set up form submission
    document.getElementById('nutrient-settings-form').addEventListener('submit', function(e) {
        e.preventDefault();
        saveNutrientSettings();
    });
    
    // Set up manual control buttons
    document.getElementById('dose-nutrient-btn').addEventListener('click', function() {
        const amount = parseInt(document.getElementById('nutrient-dose-amount').value);
        doseNutrient('nutrient', amount);
    });
    
    document.getElementById('dose-ph-up-btn').addEventListener('click', function() {
        const amount = parseInt(document.getElementById('ph-up-dose-amount').value);
        doseNutrient('ph_up', amount);
    });
    
    document.getElementById('dose-ph-down-btn').addEventListener('click', function() {
        const amount = parseInt(document.getElementById('ph-down-dose-amount').value);
        doseNutrient('ph_down', amount);
    });
    
    document.getElementById('auto-dose-btn').addEventListener('click', function() {
        if (confirm('Run automatic dosing check? This will measure current levels and adjust as needed.')) {
            runAutoDose();
        }
    });
    
    document.getElementById('flush-pumps-btn').addEventListener('click', function() {
        if (confirm('Flush all pump lines? This will run each pump for 5 seconds.')) {
            flushPumps();
        }
    });
    
    // Enhanced sensor update handling
    socket.on('sensor_update', function(data) {
        console.log('Received sensor update:', data);
        if (data) {
            updateSensorDisplays(data);
            updateCharts(data);
        } else {
            console.warn('Received empty sensor data');
        }
    });
    
    // Listen for nutrient dose events
    socket.on('nutrient_dose', function(data) {
        updateDoseInfo(data);
        fetchNutrientEvents();
    });
    
    // Fetch initial nutrient events
    fetchNutrientEvents();
    
    // Update charts and events every 5 minutes
    setInterval(function() {
        fetchNutrientEvents();
    }, 300000);
    
    // Listen for nutrient settings updates
    socket.on('nutrient_settings_updated', function(settings) {
        console.log('Received nutrient settings update:', settings);
        nutrientSettings = settings;
        updateLastSavedValues();
    });

    // Enhanced initial data handling
    socket.on('initial_data', function(data) {
        console.log('Received initial data:', data);
        if (data.nutrient_settings) {
            // Merge with existing settings instead of replacing
            nutrientSettings = {
                ...nutrientSettings,
                ...data.nutrient_settings,
                // Preserve any local overrides
                ...{
                    ec_target: document.getElementById('ec-target').value || data.nutrient_settings.ec_target,
                    ph_target: document.getElementById('ph-target').value || data.nutrient_settings.ph_target
                }
            };
            updateSettingsForm();
            updateStatusDisplay();
        }
    });

    // Direct API call with fetch for immediate update
    console.log("Loading nutrient settings directly...");
    fetchNutrientSettings();

    // Add manual reconnect button
    const reconnectBtn = document.createElement('button');
    reconnectBtn.className = 'btn btn-sm btn-warning ms-2';
    reconnectBtn.innerHTML = '<i class="fas fa-sync"></i> Reconnect';
    reconnectBtn.onclick = () => {
        console.log('Manual reconnection attempt...');
        socket.connect();
    };
    document.querySelector('.card-header').appendChild(reconnectBtn);
    
    // Verify connection status immediately
    console.log('Socket.IO ready state:', socket.connected ? 'Connected' : 'Disconnected');
    updateConnectionStatus(socket.connected);
    
    // Debug connection
    if (!socket.connected) {
        console.log('Not connected, attempting to connect...');
        socket.connect();
    }
});

function initCharts() {
    // pH and EC Chart
    const phEcCtx = document.getElementById('phEcChart').getContext('2d');
    phEcChart = new Chart(phEcCtx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'pH',
                    data: [],
                    borderColor: '#eab308',
                    backgroundColor: 'rgba(234, 179, 8, 0.1)',
                    tension: 0.3,
                    yAxisID: 'y-ph',
                },
                {
                    label: 'EC (mS/cm)',
                    data: [],
                    borderColor: '#22c55e',
                    backgroundColor: 'rgba(34, 197, 94, 0.1)',
                    tension: 0.3,
                    yAxisID: 'y-ec',
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: {
                    display: true,
                    title: {
                        display: true,
                        text: 'Time'
                    }
                },
                'y-ph': {
                    type: 'linear',
                    display: true,
                    position: 'left',
                    title: {
                        display: true,
                        text: 'pH'
                    },
                    suggestedMin: 5,
                    suggestedMax: 7
                },
                'y-ec': {
                    type: 'linear',
                    display: true,
                    position: 'right',
                    title: {
                        display: true,
                        text: 'EC (mS/cm)'
                    },
                    suggestedMin: 0,
                    suggestedMax: 2,
                    grid: {
                        drawOnChartArea: false
                    }
                }
            }
        }
    });
    
    // Dosing History Chart
    const dosingCtx = document.getElementById('dosingChart').getContext('2d');
    dosingChart = new Chart(dosingCtx, {
        type: 'bar',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Nutrient (ml)',
                    data: [],
                    backgroundColor: 'rgba(74, 222, 128, 0.7)',
                    borderColor: 'rgba(74, 222, 128, 1)',
                    borderWidth: 1
                },
                {
                    label: 'pH Up (ml)',
                    data: [],
                    backgroundColor: 'rgba(59, 130, 246, 0.7)',
                    borderColor: 'rgba(59, 130, 246, 1)',
                    borderWidth: 1
                },
                {
                    label: 'pH Down (ml)',
                    data: [],
                    backgroundColor: 'rgba(239, 68, 68, 0.7)',
                    borderColor: 'rgba(239, 68, 68, 1)',
                    borderWidth: 1
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: {
                    display: true,
                    title: {
                        display: true,
                        text: 'Date'
                    }
                },
                y: {
                    display: true,
                    title: {
                        display: true,
                        text: 'Volume (ml)'
                    },
                    beginAtZero: true
                }
            }
        }
    });
}

function fetchNutrientSettings() {
    console.log('Fetching nutrient settings...');
    
    return fetchAPI('/api/nutrient-settings')
        .then(data => {
            console.log('Settings received:', data);
            if (data) {
                nutrientSettings = data;
                
                // Update form fields
                const ecTarget = document.getElementById('ec-target');
                const phTarget = document.getElementById('ph-target');
                const phTolerance = document.getElementById('ph-tolerance');
                const ecTolerance = document.getElementById('ec-tolerance');
                const autoNutrient = document.getElementById('auto-nutrient');
                const autoPh = document.getElementById('auto-ph');
                
                if (data.ec_target !== undefined) {
                    ecTarget.value = data.ec_target.toFixed(1);
                }
                if (data.ph_target !== undefined) {
                    phTarget.value = data.ph_target.toFixed(1);
                }
                if (data.ph_tolerance !== undefined) {
                    phTolerance.value = data.ph_tolerance.toFixed(2);
                }
                if (data.ec_tolerance !== undefined) {
                    ecTolerance.value = data.ec_tolerance.toFixed(2);
                }
                if (data.auto_nutrient !== undefined) {
                    autoNutrient.checked = data.auto_nutrient;
                }
                if (data.auto_ph !== undefined) {
                    autoPh.checked = data.auto_ph;
                }
                
                // Update displays
                updateLastSavedValues();
                updateStatusDisplay();
                
                console.log('Form updated with fetched settings');
            }
            return data;
        })
        .catch(error => {
            console.error('Error fetching settings:', error);
            showToast('Failed to load settings', 'danger');
        });
}

function saveNutrientSettings() {
    const settings = {
        ec_target: parseFloat(document.getElementById('ec-target').value),
        ph_target: parseFloat(document.getElementById('ph-target').value),
        ph_tolerance: parseFloat(document.getElementById('ph-tolerance').value),
        ec_tolerance: parseFloat(document.getElementById('ec-tolerance').value),
        auto_nutrient: document.getElementById('auto-nutrient').checked,
        auto_ph: document.getElementById('auto-ph').checked
    };
    
    // Validate input values
    if (isNaN(settings.ec_target) || isNaN(settings.ph_target) || 
        isNaN(settings.ph_tolerance) || isNaN(settings.ec_tolerance)) {
        showToast('Please enter valid numbers for all fields', 'danger');
        return;
    }
    updateLastSavedValues();
    
    fetchAPI('/api/nutrient-settings', 'POST', settings)
        .then(response => {
            console.log('Save response:', response);
            if (response.status === 'success') {
                if (response.settings) {
                    nutrientSettings = response.settings;
                }
                updateLastSavedValues();
                updateSettingsForm();
                updateStatusDisplay();
                showToast('Settings saved successfully', 'success');
            } else {
                // Revert to previous settings on failure
                nutrientSettings = previousSettings;
                updateLastSavedValues();
                updateSettingsForm();
                showToast(response.message || 'Failed to save settings', 'danger');
            }
        })
        .catch(error => {
            console.error('Error saving settings:', error);
            // Revert to previous settings on error
            nutrientSettings = previousSettings;
            updateLastSavedValues();
            updateSettingsForm();
            showToast('Failed to save settings: Network error', 'danger');
        });
}

function updateLastSavedValues() {
    const lastEc = document.getElementById('last-saved-ec');
    const lastPh = document.getElementById('last-saved-ph');
    if (nutrientSettings) {
        if (nutrientSettings.ec_target !== undefined) {
            lastEc.textContent = `Last: ${nutrientSettings.ec_target.toFixed(1)} mS/cm`;
        }
        if (nutrientSettings.ph_target !== undefined) {
            lastPh.textContent = `Last: ${nutrientSettings.ph_target.toFixed(1)}`;
        }
    }
}

function updateSettingsForm() {
    if (!nutrientSettings) return;
    
    // Update form fields
    document.getElementById('ec-target').value = nutrientSettings.ec_target;
    document.getElementById('ph-target').value = nutrientSettings.ph_target;
    document.getElementById('ph-tolerance').value = nutrientSettings.ph_tolerance;
    document.getElementById('auto-nutrient').checked = nutrientSettings.auto_nutrient;
    document.getElementById('auto-ph').checked = nutrientSettings.auto_ph;
    
    // Safety limits
    document.getElementById('daily-nutrient-limit').value = nutrientSettings.safety_lockout?.max_daily_nutrient_ml || 100;
    document.getElementById('daily-ph-up-limit').value = nutrientSettings.safety_lockout?.max_daily_ph_up_ml || 50;
    document.getElementById('daily-ph-down-limit').value = nutrientSettings.safety_lockout?.max_daily_ph_down_ml || 50;
}

function updateSensorDisplays(data) {
    console.log('Updating sensor displays with:', data);
    // Update pH
    if (data.ph !== undefined) {
        const phElement = document.getElementById('current-ph');
        phElement.textContent = data.ph.toFixed(1);
        
        const phStatus = document.getElementById('ph-status');
        if (data.ph < 5.5 || data.ph > 6.5) {
            phStatus.textContent = 'Warning';
            phStatus.className = 'badge bg-warning';
        } else {
            phStatus.textContent = 'Normal';
            phStatus.className = 'badge bg-success';
        }
    }
    
    // Update EC
    if (data.ec !== undefined) {
        const ecElement = document.getElementById('current-ec');
        ecElement.textContent = `${data.ec.toFixed(2)} mS/cm`;
        
        const ecStatus = document.getElementById('ec-status');
        if (data.ec < 0.8 || data.ec > 1.6) {
            ecStatus.textContent = 'Warning';
            ecStatus.className = 'badge bg-warning';
        } else {
            ecStatus.textContent = 'Normal';
            ecStatus.className = 'badge bg-success';
        }
    }
    
    // Update water temperature
    if (data.temperature !== undefined) {
        const tempElement = document.getElementById('current-water-temp');
        tempElement.textContent = `${data.temperature.toFixed(1)}°C`;
        
        const tempStatus = document.getElementById('water-temp-status');
        if (data.temperature < 18 || data.temperature > 24) {
            tempStatus.textContent = 'Warning';
            tempStatus.className = 'badge bg-warning';
        } else {
            tempStatus.textContent = 'Normal';
            tempStatus.className = 'badge bg-success';
        }
    }
}

function updateCharts(data) {
    // Add current time to labels
    const now = new Date();
    const timeString = now.getHours().toString().padStart(2, '0') + ':' + 
                      now.getMinutes().toString().padStart(2, '0');
    
    // Update pH/EC chart
    if (data.ph !== undefined) {
        phEcChart.data.labels.push(timeString);
        phEcChart.data.datasets[0].data.push(data.ph);
        
        // Limit the number of data points
        if (phEcChart.data.labels.length > 24) {
            phEcChart.data.labels.shift();
            phEcChart.data.datasets[0].data.shift();
        }
    }
    
    if (data.ec !== undefined) {
        // If we already added a label for pH, don't add another one
        if (data.ph === undefined) {
            phEcChart.data.labels.push(timeString);
        }
        
        phEcChart.data.datasets[1].data.push(data.ec);
        
        // Limit the number of data points
        if (phEcChart.data.datasets[1].data.length > 24) {
            phEcChart.data.datasets[1].data.shift();
        }
    }
    
    phEcChart.update();
}

function updateDoseInfo(data) {
    // Update last dose info
    const lastDoseTime = document.getElementById('last-dose-time');
    lastDoseTime.textContent = data.time;
    
    const lastDoseInfo = document.getElementById('last-dose-info');
    let pumpName = 'Unknown';
    let badgeClass = 'bg-secondary';
    
    if (data.pump === 'nutrient') {
        pumpName = 'Nutrient';
        badgeClass = 'bg-primary';
    } else if (data.pump === 'ph_up') {
        pumpName = 'pH Up';
        badgeClass = 'bg-success';
    } else if (data.pump === 'ph_down') {
        pumpName = 'pH Down';
        badgeClass = 'bg-danger';
    }
    
    lastDoseInfo.textContent = `${pumpName} ${data.amount_ml}ml`;
    lastDoseInfo.className = `badge ${badgeClass}`;
    
    // Update pump status temporarily
    updatePumpStatus(data.pump, true);
    setTimeout(() => {
        updatePumpStatus(data.pump, false);
    }, 5000);
    
    // Update usage data
    fetchNutrientSettings();
}

function updatePumpStatus(pump, isActive) {
    let statusElement;
    if (pump === 'nutrient') {
        statusElement = document.getElementById('nutrient-pump-status');
    } else if (pump === 'ph_up') {
        statusElement = document.getElementById('ph-up-pump-status');
    } else if (pump === 'ph_down') {
        statusElement = document.getElementById('ph-down-pump-status');
    } else {
        return;
    }
    
    if (isActive) {
        statusElement.innerHTML = '<span class="badge bg-success">Active</span>';
    } else {
        statusElement.innerHTML = '<span class="badge bg-secondary">Inactive</span>';
    }
}

function updateStatusDisplay() {
    // Update auto control status
    const autoStatus = document.getElementById('auto-control-status');
    if (nutrientSettings.auto_nutrient && nutrientSettings.auto_ph) {
        autoStatus.innerHTML = '<span class="badge bg-success">Fully Automatic</span>';
    } else if (nutrientSettings.auto_nutrient || nutrientSettings.auto_ph) {
        autoStatus.innerHTML = '<span class="badge bg-warning">Partially Automatic</span>';
    } else {
        autoStatus.innerHTML = '<span class="badge bg-secondary">Manual Only</span>';
    }
    
    // Update daily usage
    if (nutrientSettings.daily_totals) {
        // Nutrient
        const nutrientUsed = document.getElementById('nutrient-used');
        const nutrientLimit = nutrientSettings.safety_lockout?.max_daily_nutrient_ml || 100;
        const nutrientPercentage = (nutrientSettings.daily_totals.nutrient / nutrientLimit) * 100;
        
        nutrientUsed.textContent = `${nutrientSettings.daily_totals.nutrient.toFixed(1)} / ${nutrientLimit} ml`;
        document.getElementById('nutrient-progress').style.width = `${nutrientPercentage}%`;
        
        // pH Up
        const phUpUsed = document.getElementById('ph-up-used');
        const phUpLimit = nutrientSettings.safety_lockout?.max_daily_ph_up_ml || 50;
        const phUpPercentage = (nutrientSettings.daily_totals.ph_up / phUpLimit) * 100;
        
        phUpUsed.textContent = `${nutrientSettings.daily_totals.ph_up.toFixed(1)} / ${phUpLimit} ml`;
        document.getElementById('ph-up-progress').style.width = `${phUpPercentage}%`;
        
        // pH Down
        const phDownUsed = document.getElementById('ph-down-used');
        const phDownLimit = nutrientSettings.safety_lockout?.max_daily_ph_down_ml || 50;
        const phDownPercentage = (nutrientSettings.daily_totals.ph_down / phDownLimit) * 100;
        
        phDownUsed.textContent = `${nutrientSettings.daily_totals.ph_down.toFixed(1)} / ${phDownLimit} ml`;
        document.getElementById('ph-down-progress').style.width = `${phDownPercentage}%`;
    }
}

function doseNutrient(pumpId, amount) {
    fetchAPI('/api/manual-control', 'POST', {
        type: 'nutrient',
        pump_id: pumpId,
        duration_ml: amount
    })
    .then(data => {
        if (data.status === 'success') {
            showToast(`Dosing ${pumpId.replace('_', ' ')} for ${amount}ml`, 'success');
        } else {
            showToast('Failed to dose nutrient', 'danger');
        }
    });
}

function runAutoDose() {
    fetchAPI('/api/manual-control', 'POST', {
        type: 'nutrient',
        pump_id: 'auto_dose',
        duration: 0
    })
    .then(data => {
        if (data.status === 'success') {
            showToast('Automatic dosing check initiated', 'success');
        } else {
            showToast('Failed to start automatic dosing', 'danger');
        }
    });
}

function flushPumps() {
    // Flush each pump in sequence
    showToast('Flushing nutrient pump...', 'info');
    doseNutrient('nutrient', 5);
    
    setTimeout(() => {
        showToast('Flushing pH up pump...', 'info');
        doseNutrient('ph_up', 5);
    }, 6000);
    
    setTimeout(() => {
        showToast('Flushing pH down pump...', 'info');
        doseNutrient('ph_down', 5);
    }, 12000);
}

function fetchNutrientEvents() {
    fetchAPI('/api/events?type=nutrient_dose&limit=10')
        .then(data => {
            updateNutrientEvents(data);
            updateDosingChart(data);
        });
}

function updateNutrientEvents(events) {
    const eventsContainer = document.getElementById('nutrient-events');
    eventsContainer.innerHTML = '';
    
    if (!events || events.length === 0) {
        eventsContainer.innerHTML = '<tr><td colspan="5" class="text-center">No recent nutrient events</td></tr>';
        return;
    }
    
    events.forEach(event => {
        const row = document.createElement('tr');
        
        // Format timestamp
        const timeCell = document.createElement('td');
        timeCell.textContent = formatTimestamp(event.timestamp);
        
        // Event type
        const typeCell = document.createElement('td');
        let typeClass = 'bg-secondary';
        let typeText = 'Unknown';
        
        if (event.details && event.details.pump) {
            if (event.details.pump === 'nutrient') {
                typeClass = 'bg-primary';
                typeText = 'Nutrient';
            } else if (event.details.pump === 'ph_up') {
                typeClass = 'bg-success';
                typeText = 'pH Up';
            } else if (event.details.pump === 'ph_down') {
                typeClass = 'bg-danger';
                typeText = 'pH Down';
            }
        }
        
        typeCell.innerHTML = `<span class="badge ${typeClass}">${typeText}</span>`;
        
        // Details
        const detailsCell = document.createElement('td');
        if (event.details && event.details.amount_ml) {
            detailsCell.textContent = `${event.details.amount_ml.toFixed(1)}ml dosed`;
        } else {
            detailsCell.textContent = 'Unknown';
        }
        
        // Before/After values
        const beforeCell = document.createElement('td');
        const afterCell = document.createElement('td');
        
        if (event.details) {
            if (event.details.before_ph && event.details.before_ec) {
                beforeCell.textContent = `pH ${event.details.before_ph.toFixed(1)}, EC ${event.details.before_ec.toFixed(2)}`;
            } else {
                beforeCell.textContent = '-';
            }
            
            if (event.details.after_ph && event.details.after_ec) {
                afterCell.textContent = `pH ${event.details.after_ph.toFixed(1)}, EC ${event.details.after_ec.toFixed(2)}`;
            } else {
                afterCell.textContent = '-';
            }
        } else {
            beforeCell.textContent = '-';
            afterCell.textContent = '-';
        }
        
        row.appendChild(timeCell);
        row.appendChild(typeCell);
        row.appendChild(detailsCell);
        row.appendChild(beforeCell);
        row.appendChild(afterCell);
        eventsContainer.appendChild(row);
    });
}

function updateDosingChart(events) {
    // Group events by day
    const dailyData = {};
    
    events.forEach(event => {
        if (!event.details || !event.details.pump || !event.details.amount_ml) {
            return;
        }
        
        const date = new Date(event.timestamp * 1000);
        const dateStr = `${date.getFullYear()}-${(date.getMonth() + 1).toString().padStart(2, '0')}-${date.getDate().toString().padStart(2, '0')}`;
        
        if (!dailyData[dateStr]) {
            dailyData[dateStr] = {
                nutrient: 0,
                ph_up: 0,
                ph_down: 0
            };
        }
        
        dailyData[dateStr][event.details.pump] += event.details.amount_ml;
    });
    
    // Convert to arrays for the chart
    const labels = Object.keys(dailyData).sort();
    const nutrientData = [];
    const phUpData = [];
    const phDownData = [];
    
    labels.forEach(date => {
        nutrientData.push(dailyData[date].nutrient);
        phUpData.push(dailyData[date].ph_up);
        phDownData.push(dailyData[date].ph_down);
    });
    
    // Update the chart
    dosingChart.data.labels = labels;
    dosingChart.data.datasets[0].data = nutrientData;
    dosingChart.data.datasets[1].data = phUpData;
    dosingChart.data.datasets[2].data = phDownData;
    dosingChart.update();
}

// Utility functions
function formatTimestamp(timestamp) {
    const date = new Date(timestamp * 1000);
    const hours = date.getHours().toString().padStart(2, '0');
    const minutes = date.getMinutes().toString().padStart(2, '0');
    return `${hours}:${minutes}`;
}

// Add connection status indicator
function updateConnectionStatus(connected) {
    const statusIndicator = document.createElement('div');
    statusIndicator.id = 'connection-status';
    statusIndicator.className = `connection-status ${connected ? 'connected' : 'disconnected'}`;
    statusIndicator.innerHTML = `
        <span class="status-dot"></span>
        <span class="status-text">${connected ? 'Connected' : 'Disconnected'}</span>
    `;
    
    // Replace existing status indicator or add new one
    const existing = document.getElementById('connection-status');
    if (existing) {
        existing.replaceWith(statusIndicator);
    } else {
        document.querySelector('.card-header').appendChild(statusIndicator);
    }
}

// Use fetchAPI from main.js if available, otherwise define it here
if (typeof fetchAPI !== 'function') {
    // Fallback fetchAPI implementation
    async function fetchAPI(url, method = 'GET', data = null, retryCount = 0) {
        const API_CONFIG = {
            maxRetries: 3,
            retryDelay: 1000,
            timeout: 5000
        };

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

            if (error.name === 'AbortError') {
                showToast('Request timeout - retrying...', 'warning');
            } else if (error instanceof TypeError) {
                showToast('Network error - check connection', 'warning');
            } else {
                showToast(`API Error: ${error.message}`, 'danger');
            }

            if (retryCount < API_CONFIG.maxRetries) {
                await new Promise(resolve => setTimeout(resolve, API_CONFIG.retryDelay * (retryCount + 1)));
                return fetchAPI(url, method, data, retryCount + 1);
            }

            throw new Error(`Failed after ${API_CONFIG.maxRetries} retries`);
        }
    }
}

// Define showToast if it doesn't exist (from main.js)
if (typeof showToast !== 'function') {
    // Fallback showToast implementation
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
        
        // Initialize and show toast using Bootstrap if available
        if (typeof bootstrap !== 'undefined') {
            const toastInstance = new bootstrap.Toast(toast, {
                delay: 3000
            });
            toastInstance.show();
        } else {
            // Fallback if Bootstrap JS is not available
            toast.style.display = 'block';
            setTimeout(() => {
                toast.remove();
            }, 3000);
        }
        
        // Remove from DOM after hidden
        toast.addEventListener('hidden.bs.toast', function() {
            toast.remove();
        });
    }
}