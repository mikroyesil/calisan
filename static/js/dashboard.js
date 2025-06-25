/**
 * dashboard.js - Dashboard page functionality for Vertical Farm Control System
 */

// Single initialization point
document.addEventListener('DOMContentLoaded', function() {
    // Use existing socket from main.js
    const socket = window.socket;

    // Set up dashboard-specific socket handlers
    socket.on('sensor_update', updateSensorValues);
    socket.on('initial_data', handleInitialData);

    // Initialize charts only once
    initializeDashboardCharts();
    setupDashboardControls();
    loadRecentEvents(5);
});

function updateSensorValues(data) {
    const sensors = {
        'temperature': { suffix: '°C', precision: 1 },
        'humidity': { suffix: '%', precision: 1 },
        'co2': { suffix: 'ppm', precision: 0 },
        'ph': { suffix: '', precision: 2 },
        'ec': { suffix: 'mS/cm', precision: 2 }
    };

    for (const [sensor, config] of Object.entries(sensors)) {
        const element = document.getElementById(`${sensor}-value`);
        if (element && data[sensor] !== undefined) {
            const value = Number(data[sensor]).toFixed(config.precision);
            element.textContent = value + config.suffix;
        }
    }

    // Update charts if available
    if (window.charts) {
        updateDashboardCharts(data);
    }
}

function initializeDashboardCharts() {
    const commonOptions = {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: {
            legend: { position: 'top' }
        }
    };

    // Initialize charts if they don't exist and elements are present
    ['tempHumidity', 'phEc', 'co2'].forEach(chartType => {
        const ctx = document.getElementById(`${chartType}Chart`);
        if (ctx && !window.charts[chartType]) {
            window.charts[chartType] = createDashboardChart(ctx, chartType, commonOptions);
        }
    });
}

function createDashboardChart(ctx, type, baseOptions) {
    const config = window.chartConfigs[type];
    if (!config || !config.datasets) {
        console.error(`Missing chart configuration for ${type}`);
        return null;
    }

    return new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: config.datasets.map(dataset => ({
                ...dataset,
                data: [],
                tension: 0.4,
                backgroundColor: 'rgba(255,255,255,0.1)'
            }))
        },
        options: {
            ...baseOptions,
            scales: getScalesConfig(type)
        }
    });
}

function getScalesConfig(type) {
    switch (type) {
        case 'tempHumidity':
            return {
                'y-temp': {
                    type: 'linear',
                    position: 'left',
                    min: 15,
                    max: 30
                },
                'y-humidity': {
                    type: 'linear',
                    position: 'right',
                    min: 40,
                    max: 80
                }
            };
        case 'phEc':
            return {
                'y-ph': {
                    type: 'linear',
                    position: 'left',
                    min: 5.5,
                    max: 7.0
                },
                'y-ec': {
                    type: 'linear',
                    position: 'right',
                    min: 0,
                    max: 2.0
                }
            };
        case 'co2':
            return {
                y: {
                    min: 400,
                    max: 1200
                }
            };
        default:
            return {};
    }
}

// Update charts with new data
function updateDashboardCharts(data) {
    const now = new Date();
    const timeLabel = now.getHours().toString().padStart(2, '0') + ':' + 
                     now.getMinutes().toString().padStart(2, '0');

    // Helper function to update chart data
    function updateChartData(chart, newData, maxPoints = 20) {
        if (!chart) return;

        chart.data.labels.push(timeLabel);
        chart.data.datasets.forEach((dataset, i) => {
            dataset.data.push(newData[i]);
        });

        if (chart.data.labels.length > maxPoints) {
            chart.data.labels.shift();
            chart.data.datasets.forEach(dataset => dataset.data.shift());
        }

        chart.update('none');
    }

    // Update Temperature & Humidity Chart
    if (window.charts.tempHumidity && data.temperature !== undefined && data.humidity !== undefined) {
        updateChartData(window.charts.tempHumidity, [data.temperature, data.humidity]);
    }

    // Update pH & EC Chart
    if (window.charts.phEc && data.ph !== undefined && data.ec !== undefined) {
        updateChartData(window.charts.phEc, [data.ph, data.ec]);
    }

    // Update CO2 Chart
    if (window.charts.co2 && data.co2 !== undefined) {
        updateChartData(window.charts.co2, [data.co2]);
    }
}

// Set up dashboard controls
function setupDashboardControls() {
    // Light control buttons
    const allLightsOn = document.getElementById('all-lights-on');
    if (allLightsOn) {
        allLightsOn.addEventListener('click', function() {
            for (let i = 1; i <= 7; i++) {
                sendManualControl('light', i, true);
            }
            showToast('All lights turned on', 'success');
        });
    }
    
    const allLightsOff = document.getElementById('all-lights-off');
    if (allLightsOff) {
        allLightsOff.addEventListener('click', function() {
            for (let i = 1; i <= 7; i++) {
                sendManualControl('light', i, false);
            }
            showToast('All lights turned off', 'success');
        });
    }
    
    // Nutrient control buttons
    const doseNutrients = document.getElementById('dose-nutrients');
    if (doseNutrients) {
        doseNutrients.addEventListener('click', function() {
            sendManualControl('nutrient', 'nutrient', 5);
            showToast('Dosing nutrients (5ml)', 'success');
        });
    }
    
    const dosePh = document.getElementById('dose-ph');
    if (dosePh) {
        dosePh.addEventListener('click', function() {
            // Determine if we need pH up or down based on current pH
            if (sensorData.ph < 6.0) {
                sendManualControl('nutrient', 'ph_up', 3);
                showToast('Dosing pH up solution (3ml)', 'success');
            } else {
                sendManualControl('nutrient', 'ph_down', 3);
                showToast('Dosing pH down solution (3ml)', 'success');
            }
        });
    }
    
    // Watering control buttons
    const startWatering = document.getElementById('start-watering');
    if (startWatering) {
        startWatering.addEventListener('click', function() {
            sendWateringCommand('start', 1);
            showToast('Starting watering cycle (1 minute)', 'success');
        });
    }
    
    const stopWatering = document.getElementById('stop-watering');
    if (stopWatering) {
        stopWatering.addEventListener('click', function() {
            sendWateringCommand('stop');
            showToast('Stopping watering cycle', 'success');
        });
    }
}

// Send manual control command
function sendManualControl(type, id, state) {
    const data = { type: type };
    
    if (type === 'light') {
        data.id = id;
        data.state = state;
    } else if (type === 'nutrient') {
        data.pump_id = id;
        data.duration = state; // For nutrients, state is actually duration
    } else if (type === 'environment') {
        data.device_id = id;
        data.state = state;
    }
    
    fetchAPI('/api/manual-control', 'POST', data);
}

// Send watering command
function sendWateringCommand(command, duration = null) {
    const data = {
        type: 'watering',
        command: command
    };
    
    if (duration !== null) {
        data.duration = duration;
    }
    
    fetchAPI('/api/manual-control', 'POST', data);
}

// Update dashboard sections with settings
function updateEnvironmentControls(settings) {
    const humidityRange = document.getElementById('humidity-range');
    if (humidityRange) {
        humidityRange.textContent = `${settings.humidity_min}% - ${settings.humidity_max}%`;
    }
    
    const co2Status = document.getElementById('co2-control-status');
    if (co2Status) {
        co2Status.textContent = settings.co2_control ? 'Active' : 'Inactive';
        co2Status.className = `badge ${settings.co2_control ? 'bg-success' : 'bg-secondary'}`;
    }
    
    const humidityStatus = document.getElementById('humidity-control-status');
    if (humidityStatus) {
        humidityStatus.textContent = settings.humidity_control ? 'Active' : 'Inactive';
        humidityStatus.className = `badge ${settings.humidity_control ? 'bg-success' : 'bg-secondary'}`;
    }
}

function updateNutrientControls(settings) {
    const targetEc = document.getElementById('target-ec');
    if (targetEc) {
        targetEc.textContent = settings.ec_target;
    }
    
    const targetPh = document.getElementById('target-ph');
    if (targetPh) {
        targetPh.textContent = settings.ph_target;
    }
    
    const nutrientStatus = document.getElementById('nutrient-status');
    if (nutrientStatus) {
        nutrientStatus.textContent = settings.auto_nutrient && settings.auto_ph 
            ? 'Auto-dosing is active' 
            : (settings.auto_nutrient 
                ? 'Auto EC dosing active, manual pH' 
                : (settings.auto_ph 
                    ? 'Auto pH adjustment active, manual EC' 
                    : 'Manual dosing mode'));
    }
}

function updateLightControls(schedules) {
    const lightStatusSummary = document.getElementById('light-status-summary');
    if (lightStatusSummary) {
        // Count active lights
        const activeCount = schedules.filter(schedule => schedule.enabled).length;
        
        // Get current time and determine if we're in daylight period
        const now = new Date();
        const currentHour = now.getHours();
        const currentMinute = now.getMinutes();
        const currentTimeMinutes = currentHour * 60 + currentMinute;
        
        let inLightPeriod = false;
        let firstOnTime = null;
        let lastOffTime = null;
        
        schedules.forEach(schedule => {
            if (!schedule.enabled) return;
            
            const onTimeParts = schedule.on_time.split(':');
            const onTimeMinutes = parseInt(onTimeParts[0]) * 60 + parseInt(onTimeParts[1]);
            
            const offTimeParts = schedule.off_time.split(':');
            const offTimeMinutes = parseInt(offTimeParts[0]) * 60 + parseInt(offTimeParts[1]);
            
            // Determine if current time is between on and off times
            if (onTimeMinutes < offTimeMinutes) {
                // Normal case (e.g., 6:00 to 22:00)
                if (currentTimeMinutes >= onTimeMinutes && currentTimeMinutes < offTimeMinutes) {
                    inLightPeriod = true;
                }
            } else {
                // Overnight case (e.g., 22:00 to 6:00)
                if (currentTimeMinutes >= onTimeMinutes || currentTimeMinutes < offTimeMinutes) {
                    inLightPeriod = true;
                }
            }
            
            // Track earliest on time and latest off time for progress bar
            if (firstOnTime === null || onTimeMinutes < firstOnTime) {
                firstOnTime = onTimeMinutes;
            }
            if (lastOffTime === null || offTimeMinutes > lastOffTime) {
                lastOffTime = offTimeMinutes;
            }
        });
        
        // Update summary text
        lightStatusSummary.textContent = `${activeCount} of 7 zones active, ${inLightPeriod ? 'lights are ON' : 'lights are OFF'}`;
        
        // Update progress bar if we have valid times
        if (firstOnTime !== null && lastOffTime !== null) {
            const lightProgress = document.getElementById('light-schedule-progress');
            if (lightProgress) {
                if (inLightPeriod) {
                    let progressPercentage;
                    if (firstOnTime < lastOffTime) {
                        // Normal day period
                        const totalLightPeriod = lastOffTime - firstOnTime;
                        const elapsedTime = currentTimeMinutes - firstOnTime;
                        progressPercentage = (elapsedTime / totalLightPeriod) * 100;
                    } else {
                        // Overnight period
                        const totalLightPeriod = (24 * 60) - firstOnTime + lastOffTime;
                        let elapsedTime;
                        if (currentTimeMinutes >= firstOnTime) {
                            elapsedTime = currentTimeMinutes - firstOnTime;
                        } else {
                            elapsedTime = (24 * 60) - firstOnTime + currentTimeMinutes;
                        }
                        progressPercentage = (elapsedTime / totalLightPeriod) * 100;
                    }
                    
                    lightProgress.style.width = `${progressPercentage}%`;
                    lightProgress.className = 'progress-bar bg-primary';
                } else {
                    lightProgress.style.width = '0%';
                    lightProgress.className = 'progress-bar bg-secondary';
                }
            }
        }
    }
}

function updateWateringControls(settings) {
    const wateringSchedule = document.getElementById('watering-schedule');
    if (wateringSchedule) {
        wateringSchedule.textContent = `${settings.cycle_minutes_per_hour} min/hour`;
    }
    
    const wateringStatus = document.getElementById('watering-status-badge');
    if (wateringStatus) {
        wateringStatus.textContent = settings.pump_state ? 'Active' : 'Inactive';
        wateringStatus.className = `badge ${settings.pump_state ? 'bg-success' : 'bg-secondary'}`;
    }
    
    const waterUsage = document.getElementById('water-usage');
    if (waterUsage) {
        waterUsage.textContent = `${settings.daily_run_minutes}/${settings.max_daily_minutes}`;
    }
}

function updateWateringStatus(data) {
    const pumpStatus = document.getElementById('pump-status');
    if (pumpStatus && data.pump_status !== undefined) {
        pumpStatus.textContent = data.pump_status ? 'Active' : 'Inactive';
    }
}

function handleInitialData(data) {
    if (data.sensors) {
        updateSensorValues(data.sensors);
    }
    
    // Update environmental controls
    if (data.environment_settings) {
        updateEnvironmentControls(data.environment_settings);
    }
    
    // Update nutrient settings
    if (data.nutrient_settings) {
        updateNutrientControls(data.nutrient_settings);
    }
    
    // Update light settings
    if (data.light_schedules) {
        updateLightControls(data.light_schedules);
    }
    
    // Update watering settings
    if (data.watering_settings) {
        updateWateringControls(data.watering_settings);
    }
}