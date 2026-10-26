/**
 * Weather Widget
 * Displays current weather and forecast.
 *
 * Wave 4b (2026-10-19): the card no longer paints placeholder numbers
 * ("23°C Sunny", a fake Mon-Thu forecast, "12 km/h") until data lands - it
 * shows a loading state, then the reading from GET /api/weather/current and
 * /api/weather/forecast, or says the weather is unavailable. Both routes read
 * the location from the signed-in member's own preferences (routers/weather.py
 * ignores query parameters), so the card sends no user_id and no coordinates,
 * and no longer asks the browser for its location for nothing. The forecast is
 * fetched on load and on the 5-minute update, not on every resize.
 * Version: 2.0.0
 */

class WeatherWidget extends WidgetModule {
    constructor() {
        super('weather', {
            version: '2.0.0',
            defaultSize: 'size-medium',
            updateInterval: 300000 // Update every 5 minutes
        });
        this._hasReading = false;
        this._hasForecast = false;
        this._resizeObserver = null;
        this._onClick = null;
        this._onSettingsUpdate = null;
    }

    getTemplate() {
        return `
            <div class="widget-content weather-content">
                <div class="weather-unified">
                    <div class="weather-hero">
                        <div class="weather-icon-large" aria-hidden="true"></div>
                        <div class="weather-temp" aria-busy="true">…</div>
                        <div class="weather-condition" role="status">Loading weather…</div>
                    </div>
                    <div class="weather-forecast-compact" id="weather-forecast" aria-label="Forecast">
                        <div class="forecast-state" role="status">Loading forecast…</div>
                    </div>
                    <div class="weather-details-compact" id="weather-details" style="display: none; margin-top: 0.3rem;">
                        <div class="detail-compact"><span>💨 Wind</span><span class="detail-value">—</span></div>
                        <div class="detail-compact"><span>💧 Humidity</span><span class="detail-value">—</span></div>
                        <div class="detail-compact"><span>🌡️ Feels Like</span><span class="detail-value">—</span></div>
                        <div class="detail-compact"><span>👁️ Visibility</span><span class="detail-value">—</span></div>
                    </div>
                </div>
            </div>
        `;
    }

    init(element) {
        super.init(element);
        this.element = element;

        // Click toggles between the forecast and the details.
        this._onClick = () => {
            const hero = element.querySelector('.weather-hero');
            const detailsSection = element.querySelector('#weather-details');
            const forecast = element.querySelector('.weather-forecast-compact');
            if (!hero || !detailsSection) return;
            if (!hero.classList.contains('weather-collapsed')) {
                hero.classList.add('weather-collapsed');
                detailsSection.style.display = 'grid';
                if (forecast) forecast.style.display = 'none';
                setTimeout(() => {
                    detailsSection.querySelectorAll('.detail-compact').forEach((detail, index) => {
                        setTimeout(() => {
                            detail.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
                            detail.style.opacity = '1';
                            detail.style.transform = 'translateY(0)';
                        }, index * 50);
                    });
                }, 50);
            } else {
                hero.classList.remove('weather-collapsed');
                detailsSection.style.display = 'none';
                if (forecast) forecast.style.display = 'grid';
            }
        };
        element.addEventListener('click', this._onClick);

        this.loadWeather();
        this.loadForecast();

        if (typeof ResizeObserver !== 'undefined') {
            this._resizeObserver = new ResizeObserver(() => this.updateResponsiveLayout(element));
            this._resizeObserver.observe(element);
        }
        this.updateResponsiveLayout(element);

        // React to widget settings changes
        this.applyPrefs(element);
        this._onSettingsUpdate = (e) => {
            if (!e.detail || e.detail.type !== 'weather') return;
            if (e.detail.widget && e.detail.widget !== element) return;
            this.applyPrefs(element);
            this.loadWeather();
            this.loadForecast();
        };
        window.addEventListener('widget-settings:update', this._onSettingsUpdate);
    }

    destroy() {
        super.destroy();
        if (this._resizeObserver) this._resizeObserver.disconnect();
        this._resizeObserver = null;
        if (this._onSettingsUpdate) window.removeEventListener('widget-settings:update', this._onSettingsUpdate);
        this._onSettingsUpdate = null;
        if (this.element && this._onClick) this.element.removeEventListener('click', this._onClick);
        this._onClick = null;
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
        element.classList.remove('weather-compact', 'weather-normal', 'weather-expanded');
        if (width < 280) {
            element.classList.add('weather-compact');
        } else if (width >= 400) {
            element.classList.add('weather-expanded');
        } else {
            element.classList.add('weather-normal');
        }
    }

    update() {
        this.loadWeather();
        this.loadForecast();
    }

    async loadWeather() {
        try {
            const response = await fetch(`/api/weather/current?_t=${Date.now()}`, { credentials: 'same-origin' });
            if (!response.ok) throw new Error('HTTP ' + response.status);
            this.updateWeatherDisplay(await response.json());
        } catch (error) {
            console.warn('Weather unavailable:', error);
            // Keep a real earlier reading; never fall back to invented numbers.
            if (!this._hasReading) this.showUnavailable();
        }
    }

    showUnavailable() {
        const element = this.element;
        if (!element) return;
        element.querySelectorAll('.weather-temp').forEach(el => {
            el.textContent = '—';
            el.setAttribute('aria-busy', 'false');
        });
        element.querySelectorAll('.weather-condition').forEach(el => { el.textContent = 'Weather unavailable'; });
        element.querySelectorAll('.weather-icon-large').forEach(el => { el.textContent = ''; });
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
        if (rawTemp === undefined || rawTemp === null || rawTemp === '') {
            if (!this._hasReading) this.showUnavailable();
            return;
        }
        this._hasReading = true;
        const unit = (data.temperature_unit === 'fahrenheit' || data.unit === 'fahrenheit') ? '°F' : '°C';
        const tempDisplay = `${Math.round(rawTemp)}${unit}`;
        const feelsDisplay = (rawFeels === undefined || rawFeels === null || rawFeels === '')
            ? '—' : `${Math.round(rawFeels)}${unit}`;

        // The backend sends description like "partly cloudy" + icon like "02n".
        // Use the icon's day/night suffix when available for better emoji accuracy.
        const isNightIcon = typeof data.icon === 'string' && /n$/.test(data.icon);
        const conditionEmoji = this.getWeatherEmoji(data.condition || data.description, isNightIcon);
        const conditionText = data.description || data.condition || '';

        element.querySelectorAll('.weather-temp').forEach(el => {
            el.textContent = tempDisplay;
            el.setAttribute('aria-busy', 'false');
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
            if (visEl) visEl.textContent = data.visibility ? `${Math.round(data.visibility)}km` : '—';
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
            const r = await fetch(`/api/weather/forecast?days=4&_t=${Date.now()}`, { credentials: 'same-origin' });
            if (!r.ok) throw new Error('HTTP ' + r.status);
            this.updateForecastDisplay(await r.json());
        } catch (error) {
            console.warn('Forecast unavailable:', error);
            if (!this._hasForecast) this.showForecastState('Forecast unavailable');
        }
    }

    showForecastState(text) {
        const box = this.element && this.element.querySelector('.weather-forecast-compact');
        if (!box) return;
        box.innerHTML = '';
        const msg = document.createElement('div');
        msg.className = 'forecast-state';
        msg.setAttribute('role', 'status');
        msg.textContent = text;
        box.appendChild(msg);
    }

    updateForecastDisplay(data) {
        const forecastContainer = this.element?.querySelector('.weather-forecast-compact');
        if (!forecastContainer || !data) return;

        // Backend returns { hourly: [...], daily: [...] }.
        // Also tolerate older shape { forecast: [...] }.
        const daily = Array.isArray(data.daily)
            ? data.daily
            : (Array.isArray(data.forecast) ? data.forecast : []);
        if (!daily.length) {
            if (!this._hasForecast) this.showForecastState('No forecast yet');
            return;
        }
        this._hasForecast = true;

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
        const deg = (v) => {
            const n = Number(v);
            return (v === undefined || v === null || v === '' || !isFinite(n)) ? '—' : Math.round(n) + '°';
        };

        forecastContainer.innerHTML = daily.slice(0, 4).map((day) => {
            // Backend returns { day: "YYYY-MM-DD", high, low, description }.
            // Also tolerate older shapes (date/time, temp_max, temperature_max, ...).
            const d = new Date(day.day || day.date || day.time || Date.now());
            const dayName = isNaN(d.getTime()) ? '' : d.toLocaleDateString('en-US', { weekday: 'short' });
            const emoji = pickEmoji(day.description || day.condition);
            const hi = day.high ?? day.temp_max ?? day.temperature_max ?? day.temp ?? day.temperature;
            const lo = day.low  ?? day.temp_min ?? day.temperature_min ?? day.temp ?? day.temperature;
            return `
                <div class="forecast-compact">
                    <div class="forecast-day-name">${dayName}</div>
                    <div class="forecast-icon" aria-hidden="true">${emoji}</div>
                    <div class="forecast-temps">
                        <span class="forecast-high">${deg(hi)}</span>
                        <span class="forecast-low">${deg(lo)}</span>
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
