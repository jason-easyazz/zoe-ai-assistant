/**
 * Weather Widget
 * Displays current weather and forecast
 * Version: 1.0.0
 */

class WeatherWidget extends WidgetModule {
    constructor() {
        super('weather', {
            version: '1.1.0', // Updated for night/day icons and click behavior
            defaultSize: 'size-medium',
            updateInterval: 300000 // Update every 5 minutes
        });
    }
    
    getTemplate() {
        return `
            <div class="widget-content weather-content">
                <!-- Unified responsive layout - always shows all info -->
                <div class="weather-unified">
                    <!-- Current conditions -->
                    <div class="weather-hero">
                        <div class="weather-icon-large">☀️</div>
                        <div class="weather-temp">23°C</div>
                        <div class="weather-condition">Sunny</div>
                    </div>
                    
                    <!-- Forecast - 4 days to save space -->
                    <div class="weather-forecast-compact" id="weather-forecast">
                        <div class="forecast-compact">
                            <div class="forecast-day-name">Mon</div>
                            <div class="forecast-icon">☀️</div>
                            <div class="forecast-temps">
                                <span class="forecast-high">25°</span>
                                <span class="forecast-low">18°</span>
                            </div>
                        </div>
                        <div class="forecast-compact">
                            <div class="forecast-day-name">Tue</div>
                            <div class="forecast-icon">⛅</div>
                            <div class="forecast-temps">
                                <span class="forecast-high">22°</span>
                                <span class="forecast-low">16°</span>
                            </div>
                        </div>
                        <div class="forecast-compact">
                            <div class="forecast-day-name">Wed</div>
                            <div class="forecast-icon">🌧️</div>
                            <div class="forecast-temps">
                                <span class="forecast-high">19°</span>
                                <span class="forecast-low">14°</span>
                            </div>
                        </div>
                        <div class="forecast-compact">
                            <div class="forecast-day-name">Thu</div>
                            <div class="forecast-icon">☀️</div>
                            <div class="forecast-temps">
                                <span class="forecast-high">26°</span>
                                <span class="forecast-low">19°</span>
                            </div>
                        </div>
                    </div>
                    
                    <!-- Collapsible details section - shown when clicked -->
                    <div class="weather-details-compact" id="weather-details" style="display: none; margin-top: 0.3rem;">
                        <div class="detail-compact"><span>💨 Wind</span><span class="detail-value">12 km/h</span></div>
                        <div class="detail-compact"><span>💧 Humidity</span><span class="detail-value">65%</span></div>
                        <div class="detail-compact"><span>🌡️ Feels Like</span><span class="detail-value">21°C</span></div>
                        <div class="detail-compact"><span>👁️ Visibility</span><span class="detail-value">10km</span></div>
                    </div>
                </div>
            </div>
        `;
    }
    
    init(element) {
        super.init(element);
        
        // Add click handler to shrink hero and show details
        element.addEventListener('click', () => {
            const hero = element.querySelector('.weather-hero');
            const detailsSection = element.querySelector('#weather-details');
            const forecast = element.querySelector('.weather-forecast-compact');
            
            if (hero && detailsSection) {
                const isExpanded = !hero.classList.contains('weather-collapsed');
                
                if (isExpanded) {
                    // Collapse: shrink hero, show details, hide forecast
                    hero.classList.add('weather-collapsed');
                    detailsSection.style.display = 'grid';
                    if (forecast) forecast.style.display = 'none';
                    
                    // Trigger fade-in animation after brief delay
                    setTimeout(() => {
                        const details = detailsSection.querySelectorAll('.detail-compact');
                        details.forEach((detail, index) => {
                            setTimeout(() => {
                                detail.style.transition = `opacity 0.3s ease, transform 0.3s ease`;
                                detail.style.opacity = '1';
                                detail.style.transform = 'translateY(0)';
                            }, index * 50); // Staggered animation
                        });
                    }, 50);
                } else {
                    // Expand: restore hero, hide details, show forecast
                    hero.classList.remove('weather-collapsed');
                    detailsSection.style.display = 'none';
                    if (forecast) forecast.style.display = 'grid';
                }
            }
        });
        
        // Load weather data
        this.loadWeather();
        
        // Watch for resize and update layout dynamically
        const resizeObserver = new ResizeObserver(() => {
            this.updateResponsiveLayout(element);
        });
        resizeObserver.observe(element);
        
        // Initial layout update
        this.updateResponsiveLayout(element);
    }
    
    updateResponsiveLayout(element) {
        const width = element.offsetWidth;
        const height = element.offsetHeight;
        
        // Add responsive classes for additional styling if needed
        element.classList.remove('weather-compact', 'weather-normal', 'weather-expanded');
        
        if (width < 280) {
            element.classList.add('weather-compact');
        } else if (width >= 400) {
            element.classList.add('weather-expanded');
        } else {
            element.classList.add('weather-normal');
        }
        
        // Always load forecast - it's always visible now
        this.loadForecast();
    }
    
    update() {
        this.loadWeather();
    }
    
    async loadWeather() {
        try {
            const userId = localStorage.getItem('user_id') || 'default';
            
            // Check if should use current device location
            const prefsResponse = await fetch(`/api/weather/preferences?user_id=${userId}`);
            const prefs = await prefsResponse.json();
            
            if (prefs.use_current_location && navigator.geolocation) {
                // Get current device location
                navigator.geolocation.getCurrentPosition(
                    async (position) => {
                        const lat = position.coords.latitude;
                        const lon = position.coords.longitude;
                        
                        // Fetch weather for current coordinates with cache-busting
                        const cacheBuster = `&_t=${Date.now()}`;
                        const response = await fetch(
                            `/api/weather/current?user_id=${userId}&lat=${lat}&lon=${lon}${cacheBuster}`
                        );
                        if (response.ok) {
                            const data = await response.json();
                            this.updateWeatherDisplay(data);
                        }
                    },
                    async (error) => {
                        console.warn('Geolocation failed, using saved location:', error);
                        // Fallback to saved location if geolocation fails
                        const cacheBuster = `?_t=${Date.now()}`;
                        const response = await fetch(`/api/weather/current?user_id=${userId}${cacheBuster}`);
                        if (response.ok) {
                            const data = await response.json();
                            this.updateWeatherDisplay(data);
                        }
                    },
                    {
                        enableHighAccuracy: false,
                        timeout: 10000,
                        maximumAge: 300000 // Cache for 5 minutes
                    }
                );
            } else {
                // Use saved location with cache-busting timestamp
                const cacheBuster = `?_t=${Date.now()}`;
                const response = await fetch(`/api/weather/current?user_id=${userId}${cacheBuster}`);
                if (response.ok) {
                    const data = await response.json();
                    this.updateWeatherDisplay(data);
                }
            }
        } catch (error) {
            console.error('Failed to load weather:', error);
            // Keep default display
        }
    }
    
    updateWeatherDisplay(data) {
        if (!data) return;
        
        const element = this.element;
        if (!element) return;
        
        // Get temperature with unit symbol
        const tempUnit = data.temperature_unit === 'fahrenheit' ? '°F' : '°C';
        const temp = `${data.temperature}${tempUnit}`;
        
        // Map condition to emoji (time-aware)
        const conditionEmoji = this.getWeatherEmoji(data.condition);
        
        // Update all size variants
        element.querySelectorAll('.weather-temp').forEach(el => {
            el.textContent = temp;
        });
        
        element.querySelectorAll('.weather-condition').forEach(el => {
            el.textContent = data.description;
        });
        
        element.querySelectorAll('.weather-icon-large').forEach(el => {
            el.textContent = conditionEmoji;
        });
        
        // Update hidden details section
        const detailsSection = element.querySelector('#weather-details');
        if (detailsSection) {
            const windEl = detailsSection.querySelector(':nth-child(1) .detail-value');
            const humidityEl = detailsSection.querySelector(':nth-child(2) .detail-value');
            const feelsLikeEl = detailsSection.querySelector(':nth-child(3) .detail-value');
            const visEl = detailsSection.querySelector(':nth-child(4) .detail-value');
            
            if (windEl) windEl.textContent = `${data.wind_speed} km/h`;
            if (humidityEl) humidityEl.textContent = `${data.humidity}%`;
            if (feelsLikeEl) feelsLikeEl.textContent = `${data.temperature}°C`;
            if (visEl) visEl.textContent = '10km';
        }
        
        // Update background class (with time-aware styling)
        this.updateWeatherBackground(data.condition);
        
        // Apply time-based theme
        const hour = new Date().getHours();
        const isNight = hour >= 19 || hour < 6;
        if (isNight) {
            element.classList.add('weather-nighttime');
        } else {
            element.classList.remove('weather-nighttime');
        }
        
        // Load forecast for large widget
        if (element.classList.contains('size-large')) {
            this.loadForecast();
        }
    }
    
    getWeatherEmoji(condition) {
        const hour = new Date().getHours();
        const isNight = hour >= 19 || hour < 6; // 7pm to 6am is night
        
        // Base condition map
        const conditionMap = {
            'clear': isNight ? '🌙' : '☀️',
            'sunny': isNight ? '🌙' : '☀️',
            'partly-cloudy': isNight ? '☁️🌙' : '⛅',
            'cloudy': '☁️',
            'overcast': '☁️',
            'rain': '🌧️',
            'drizzle': '🌦️',
            'thunderstorm': '⛈️',
            'snow': '❄️',
            'fog': '🌫️'
        };
        
        let emoji = conditionMap[condition.toLowerCase()] || '☀️';
        
        // If night and not explicitly night-related, use moon
        if (isNight && condition.toLowerCase().includes('partly')) {
            emoji = '☁️🌙';
        }
        
        return emoji;
    }
    
    async loadForecast() {
        try {
            const userId = localStorage.getItem('user_id') || 'default';
            
            // Check if using current location for forecast too
            const prefsResponse = await fetch(`/api/weather/preferences?user_id=${userId}`);
            const prefs = await prefsResponse.json();
            
            if (prefs.use_current_location && navigator.geolocation) {
                // Use device location for forecast (4 days)
                navigator.geolocation.getCurrentPosition(
                    async (position) => {
                        const lat = position.coords.latitude;
                        const lon = position.coords.longitude;
                        const cacheBuster = `&_t=${Date.now()}`;
                        const response = await fetch(`/api/weather/forecast?days=4&user_id=${userId}&lat=${lat}&lon=${lon}${cacheBuster}`);
                        if (response.ok) {
                            const data = await response.json();
                            this.updateForecastDisplay(data.forecast);
                        }
                    },
                    async () => {
                        // Fallback to saved location
                        const cacheBuster = `&_t=${Date.now()}`;
                        const response = await fetch(`/api/weather/forecast?days=4&user_id=${userId}${cacheBuster}`);
                        if (response.ok) {
                            const data = await response.json();
                            this.updateForecastDisplay(data.forecast);
                        }
                    },
                    { maximumAge: 300000 }
                );
            } else {
                // Use saved location with cache-busting (4 days)
                const cacheBuster = `&_t=${Date.now()}`;
                const response = await fetch(`/api/weather/forecast?days=4&user_id=${userId}${cacheBuster}`);
                if (response.ok) {
                    const data = await response.json();
                    this.updateForecastDisplay(data.forecast);
                }
            }
        } catch (error) {
            console.error('Failed to load forecast:', error);
        }
    }
    
    updateForecastDisplay(forecast) {
        const forecastContainer = this.element?.querySelector('.weather-forecast-compact');
        if (!forecastContainer || !forecast) return;
        
        forecastContainer.innerHTML = forecast.slice(0, 4).map((day, index) => {
            const date = new Date(day.date);
            const dayName = date.toLocaleDateString('en-US', { weekday: 'short' });
            // For forecast, always use day icons (not time-specific)
            const conditionMap = {
                'clear': '☀️',
                'sunny': '☀️',
                'partly-cloudy': '⛅',
                'cloudy': '☁️',
                'overcast': '☁️',
                'rain': '🌧️',
                'drizzle': '🌦️',
                'thunderstorm': '⛈️',
                'snow': '❄️',
                'fog': '🌫️'
            };
            const emoji = conditionMap[day.condition.toLowerCase()] || '☀️';
            
            return `
                <div class="forecast-compact">
                    <div class="forecast-day-name">${dayName}</div>
                    <div class="forecast-icon">${emoji}</div>
                    <div class="forecast-temps">
                        <span class="forecast-high">${Math.round(day.temperature_max || day.temperature)}°</span>
                        <span class="forecast-low">${Math.round(day.temperature_min || day.temperature)}°</span>
                    </div>
                </div>
            `;
        }).join('');
    }
    
    updateWeatherBackground(condition) {
        const widget = this.element;
        if (!widget) return;
        
        // Remove all weather classes
        widget.classList.remove('weather-sunny', 'weather-cloudy', 'weather-rainy', 
                                 'weather-stormy', 'weather-snowy', 'weather-partly-cloudy');
        
        // Add appropriate class based on condition
        const conditionLower = condition.toLowerCase();
        
        if (conditionLower.includes('sun') || conditionLower.includes('clear')) {
            widget.classList.add('weather-sunny');
        } else if (conditionLower.includes('rain') || conditionLower.includes('drizzle')) {
            widget.classList.add('weather-rainy');
        } else if (conditionLower.includes('storm') || conditionLower.includes('thunder')) {
            widget.classList.add('weather-stormy');
        } else if (conditionLower.includes('snow') || conditionLower.includes('sleet')) {
            widget.classList.add('weather-snowy');
        } else if (conditionLower.includes('partly') || conditionLower.includes('scattered')) {
            widget.classList.add('weather-partly-cloudy');
        } else if (conditionLower.includes('cloud') || conditionLower.includes('overcast')) {
            widget.classList.add('weather-cloudy');
        } else {
            // Default to partly cloudy
            widget.classList.add('weather-partly-cloudy');
        }
    }
}

// Expose to global scope for WidgetManager
window.WeatherWidget = WeatherWidget;

// Register widget
if (typeof WidgetRegistry !== 'undefined') {
    WidgetRegistry.register('weather', new WeatherWidget());
}




