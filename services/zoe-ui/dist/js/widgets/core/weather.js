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

        // React to widget settings changes
        this.applyPrefs(element);
        this._onSettingsUpdate = (e) => {
            if (!e.detail || e.detail.type !== 'weather') return;
            if (e.detail.widget && e.detail.widget !== element) return;
            this.applyPrefs(element);
            // Re-fetch hero + forecast so unit/forecastDays/useGeo take effect immediately.
            this.loadWeather();
            this.loadForecast();
        };
        window.addEventListener('widget-settings:update', this._onSettingsUpdate);
    }

    getPrefs() {
        try {
            const all = JSON.parse(localStorage.getItem('zoe_widget_settings') || '{}');
            return all.weather || {};
        } catch(_) { return {}; }
    }

    applyPrefs(element) {
        const p = this.getPrefs();
        const forecast = element.querySelector('.weather-forecast-compact');
        if (forecast) forecast.style.display = (p.showForecast === false) ? 'none' : '';
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
        
        // API returns `temp` (not `temperature`); we also accept `temperature` as a fallback.
        const rawTemp = (data.temp !== undefined && data.temp !== null)
            ? data.temp
            : data.temperature;
        const rawFeels = (data.feels_like !== undefined && data.feels_like !== null)
            ? data.feels_like
            : data.temperature;
        const unit = (data.temperature_unit === 'fahrenheit' || data.unit === 'fahrenheit') ? '°F' : '°C';
        const tempDisplay = (rawTemp === undefined || rawTemp === null || rawTemp === '')
            ? '—' : `${Math.round(rawTemp)}${unit}`;
        const feelsDisplay = (rawFeels === undefined || rawFeels === null || rawFeels === '')
            ? '—' : `${Math.round(rawFeels)}${unit}`;

        // The backend sends description like "partly cloudy" + icon like "02n".
        // Use the icon's day/night suffix when available for better emoji accuracy.
        const isNightIcon = typeof data.icon === 'string' && /n$/.test(data.icon);
        const conditionEmoji = this.getWeatherEmoji(data.condition || data.description, isNightIcon);
        const conditionText = data.description || data.condition || '';

        element.querySelectorAll('.weather-temp').forEach(el => {
            el.textContent = tempDisplay;
        });
        
        element.querySelectorAll('.weather-condition').forEach(el => {
            el.textContent = conditionText;
        });
        
        element.querySelectorAll('.weather-icon-large').forEach(el => {
            el.textContent = conditionEmoji;
        });
        
        const detailsSection = element.querySelector('#weather-details');
        if (detailsSection) {
            const windEl = detailsSection.querySelector(':nth-child(1) .detail-value');
            const humidityEl = detailsSection.querySelector(':nth-child(2) .detail-value');
            const feelsLikeEl = detailsSection.querySelector(':nth-child(3) .detail-value');
            const visEl = detailsSection.querySelector(':nth-child(4) .detail-value');

            // Backend wind_speed is m/s — convert to km/h for display.
            const windKmh = (data.wind_speed !== undefined && data.wind_speed !== null)
                ? Math.round(Number(data.wind_speed) * 3.6) : null;

            if (windEl) windEl.textContent = windKmh !== null ? `${windKmh} km/h` : '—';
            if (humidityEl) humidityEl.textContent = (data.humidity !== undefined && data.humidity !== null)
                ? `${data.humidity}%` : '—';
            if (feelsLikeEl) feelsLikeEl.textContent = feelsDisplay;
            if (visEl) visEl.textContent = data.visibility ? `${Math.round(data.visibility)}km` : '10km';
        }

        this.updateWeatherBackground(conditionText, isNightIcon);

        // Prefer the backend's sunrise/sunset for day/night; fall back to local clock.
        let isNight = isNightIcon;
        if (!isNight && data.sunrise && data.sunset) {
            const now = Date.now();
            const rise = new Date(data.sunrise).getTime();
            const set = new Date(data.sunset).getTime();
            if (!isNaN(rise) && !isNaN(set)) isNight = now < rise || now > set;
        } else if (!isNight) {
            const hour = new Date().getHours();
            isNight = hour >= 19 || hour < 6;
        }
        element.classList.toggle('weather-nighttime', isNight);

        if (element.classList.contains('size-large')) {
            this.loadForecast();
        }
    }
    
    getWeatherEmoji(condition, forceNight) {
        const hour = new Date().getHours();
        const isNight = forceNight !== undefined ? forceNight : (hour >= 19 || hour < 6);
        const safeCondition = (typeof condition === 'string' ? condition : '').toLowerCase();

        if (safeCondition.includes('thunder') || safeCondition.includes('storm')) return '⛈️';
        if (safeCondition.includes('snow') || safeCondition.includes('sleet') || safeCondition.includes('blizzard')) return '❄️';
        if (safeCondition.includes('drizzle')) return '🌦️';
        if (safeCondition.includes('rain') || safeCondition.includes('shower')) return '🌧️';
        if (safeCondition.includes('fog') || safeCondition.includes('mist') || safeCondition.includes('haze')) return '🌫️';
        if (safeCondition.includes('overcast')) return '☁️';
        if (safeCondition.includes('partly') || safeCondition.includes('scattered')) return isNight ? '☁️' : '⛅';
        if (safeCondition.includes('cloud')) return '☁️';
        if (safeCondition.includes('clear') || safeCondition.includes('sunny') || safeCondition.includes('sun')) {
            return isNight ? '🌙' : '☀️';
        }
        return isNight ? '🌙' : '☀️';
    }
    
    async loadForecast() {
        try {
            const userId = localStorage.getItem('user_id') || 'default';
            const prefsResponse = await fetch(`/api/weather/preferences?user_id=${userId}`);
            const prefs = prefsResponse.ok ? await prefsResponse.json() : {};

            const fetchForecast = async (query) => {
                const cacheBuster = `&_t=${Date.now()}`;
                const r = await fetch(`/api/weather/forecast?days=4&user_id=${userId}${query}${cacheBuster}`);
                if (!r.ok) return;
                const data = await r.json();
                this.updateForecastDisplay(data);
            };

            if (prefs.use_current_location && navigator.geolocation) {
                navigator.geolocation.getCurrentPosition(
                    pos => fetchForecast(`&lat=${pos.coords.latitude}&lon=${pos.coords.longitude}`),
                    () => fetchForecast(''),
                    { maximumAge: 300000 }
                );
            } else {
                await fetchForecast('');
            }
        } catch (error) {
            console.error('Failed to load forecast:', error);
        }
    }

    updateForecastDisplay(data) {
        const forecastContainer = this.element?.querySelector('.weather-forecast-compact');
        if (!forecastContainer || !data) return;

        // Backend returns { hourly: [...], daily: [...] }.
        // Also tolerate older shape { forecast: [...] }.
        const daily = Array.isArray(data.daily)
            ? data.daily
            : (Array.isArray(data.forecast) ? data.forecast : []);
        if (!daily.length) return;

        const pickEmoji = (condOrDesc) => {
            const s = (typeof condOrDesc === 'string' ? condOrDesc : '').toLowerCase();
            if (s.includes('thunder') || s.includes('storm')) return '⛈️';
            if (s.includes('snow')) return '❄️';
            if (s.includes('drizzle')) return '🌦️';
            if (s.includes('rain') || s.includes('shower')) return '🌧️';
            if (s.includes('fog') || s.includes('mist')) return '🌫️';
            if (s.includes('overcast')) return '☁️';
            if (s.includes('partly') || s.includes('scattered')) return '⛅';
            if (s.includes('cloud')) return '☁️';
            return '☀️';
        };

        forecastContainer.innerHTML = daily.slice(0, 4).map((day) => {
            // Backend returns { day: "YYYY-MM-DD", high, low, description }.
            // Also tolerate older shapes (date/time, temp_max, temperature_max, ...).
            const d = new Date(day.day || day.date || day.time || Date.now());
            const dayName = d.toLocaleDateString('en-US', { weekday: 'short' });
            const emoji = pickEmoji(day.description || day.condition);
            const hi = day.high ?? day.temp_max ?? day.temperature_max ?? day.temp ?? day.temperature;
            const lo = day.low  ?? day.temp_min ?? day.temperature_min ?? day.temp ?? day.temperature;
            return `
                <div class="forecast-compact">
                    <div class="forecast-day-name">${dayName}</div>
                    <div class="forecast-icon">${emoji}</div>
                    <div class="forecast-temps">
                        <span class="forecast-high">${hi !== undefined ? Math.round(hi) + '°' : '—'}</span>
                        <span class="forecast-low">${lo !== undefined ? Math.round(lo) + '°' : '—'}</span>
                    </div>
                </div>
            `;
        }).join('');
    }
    
    updateWeatherBackground(condition, isNight) {
        const widget = this.element;
        if (!widget) return;

        widget.classList.remove('weather-sunny', 'weather-cloudy', 'weather-rainy',
                                 'weather-stormy', 'weather-snowy', 'weather-partly-cloudy',
                                 'weather-clear-night');

        const conditionLower = (typeof condition === 'string' ? condition : '').toLowerCase();

        if (conditionLower.includes('storm') || conditionLower.includes('thunder')) {
            widget.classList.add('weather-stormy');
        } else if (conditionLower.includes('snow') || conditionLower.includes('sleet')) {
            widget.classList.add('weather-snowy');
        } else if (conditionLower.includes('rain') || conditionLower.includes('drizzle') || conditionLower.includes('shower')) {
            widget.classList.add('weather-rainy');
        } else if (conditionLower.includes('overcast')) {
            widget.classList.add('weather-cloudy');
        } else if (conditionLower.includes('partly') || conditionLower.includes('scattered')) {
            widget.classList.add('weather-partly-cloudy');
        } else if (conditionLower.includes('cloud')) {
            widget.classList.add('weather-cloudy');
        } else if (conditionLower.includes('sun') || conditionLower.includes('clear')) {
            widget.classList.add(isNight ? 'weather-clear-night' : 'weather-sunny');
        } else {
            widget.classList.add(isNight ? 'weather-clear-night' : 'weather-partly-cloudy');
        }
    }
}

// Expose to global scope for WidgetManager
window.WeatherWidget = WeatherWidget;

// Register widget
if (typeof WidgetRegistry !== 'undefined') {
    WidgetRegistry.register('weather', new WeatherWidget());
}




