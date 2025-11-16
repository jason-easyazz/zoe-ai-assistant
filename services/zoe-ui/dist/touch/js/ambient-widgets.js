"use strict";

// MagicMirror-inspired modular widget system with regional positioning

const AmbientWidgets = (() => {
  const REGIONS = [
    "top-left","top-center","top-right",
    "middle-left","middle-center","middle-right",
    "bottom-left","bottom-center","bottom-right",
  ];

  class BaseWidget {
    constructor(config = {}) { this.config = config; this.el = null; }
    mount(container) { this.el = container; this.render(); }
    render() { /* override in subclasses */ }
    update(data) { this.config = { ...this.config, ...data }; this.render(); }
    unmount() { if (this.el) this.el.innerHTML = ""; this.el = null; }
  }

  class WidgetRegistry {
    constructor() { this.types = new Map(); this.instances = []; }
    register(type, ctor) { this.types.set(type, ctor); }
    create(type, config) {
      const Ctor = this.types.get(type);
      if (!Ctor) throw new Error(`Unknown widget type: ${type}`);
      return new Ctor(config);
    }
    clear() { this.instances.forEach(w => w.unmount()); this.instances = []; }
  }

  const registry = new WidgetRegistry();

  function regionSelector(region) { return `[data-region="${region}"]`; }

  function mountFromDOM() {
    const nodeList = document.querySelectorAll("[data-widget]");
    nodeList.forEach((node) => {
      const type = node.getAttribute("data-widget");
      const cfgAttr = node.getAttribute("data-config");
      const config = cfgAttr ? JSON.parse(cfgAttr) : {};
      const instance = registry.create(type, config);
      instance.mount(node);
      registry.instances.push(instance);
    });
  }

  // Enhanced MagicMirror-inspired widgets with API integration
  class ClockWidget extends BaseWidget {
    constructor(config) {
      super(config);
      this.timer = null;
    }
    mount(container) {
      super.mount(container);
      this.startClock();
    }
    startClock() {
      this.render();
      this.timer = setInterval(() => this.render(), 1000);
    }
    render() {
      if (!this.el) return;
      const now = new Date();
      const { format24h = true, showSeconds = false, showDate = true, orbStyle = true } = this.config;
      const timeStr = now.toLocaleTimeString(undefined, {
        hour12: !format24h,
        hour: '2-digit',
        minute: '2-digit',
        second: showSeconds ? '2-digit' : undefined
      });
      const dateStr = now.toLocaleDateString(undefined, {
        weekday: 'long',
        month: 'long',
        day: 'numeric'
      });

      if (orbStyle) {
        this.el.innerHTML = `
          <div class="widget widget-clock">
            <div class="clock-orb">
              <div class="clock-time">${timeStr}</div>
              ${showDate ? `<div class="clock-date">${dateStr.split(',')[0]}</div>` : ''}
            </div>
            ${showDate ? `<div class="clock-time-external">${dateStr.split(',')[1]?.trim()}</div>` : ''}
          </div>`;
      } else {
        this.el.innerHTML = `
          <div class="widget widget-clock">
            <div class="clock-time-external">${timeStr}</div>
            ${showDate ? `<div class="clock-date">${dateStr}</div>` : ''}
          </div>`;
      }
    }
    unmount() {
      if (this.timer) clearInterval(this.timer);
      super.unmount();
    }
  }

  class WeatherWidget extends BaseWidget {
    async render() {
      if (!this.el) return;
      const { location = "Auto", apiKey, lat, lon } = this.config;
      
      // Mock data if no API key, otherwise fetch real weather
      let weatherData = { temp: 72, summary: "Sunny", icon: "☀️", humidity: 65, windSpeed: 5 };
      
      if (apiKey && (lat && lon || location !== "Auto")) {
        try {
          weatherData = await this.fetchWeather(apiKey, lat, lon, location);
        } catch (e) {
          console.warn('Weather fetch failed, using mock data');
        }
      }
      
      this.el.innerHTML = `
        <div class="widget widget-weather">
          <div class="weather-main">
            <div class="weather-icon">${weatherData.icon}</div>
            <div class="weather-temp">${Math.round(weatherData.temp)}°</div>
          </div>
          <div class="weather-details">
            <div class="weather-summary">${weatherData.summary}</div>
            <div class="weather-meta">💧${weatherData.humidity}% 💨${weatherData.windSpeed}mph</div>
          </div>
        </div>`;
    }
    
    async fetchWeather(apiKey, lat, lon, location) {
      // OpenWeatherMap API integration (replace with your preferred service)
      const url = lat && lon 
        ? `https://api.openweathermap.org/data/2.5/weather?lat=${lat}&lon=${lon}&appid=${apiKey}&units=imperial`
        : `https://api.openweathermap.org/data/2.5/weather?q=${location}&appid=${apiKey}&units=imperial`;
      
      const res = await fetch(url);
      const data = await res.json();
      
      const iconMap = {
        '01d': '☀️', '01n': '🌙', '02d': '⛅', '02n': '☁️',
        '03d': '☁️', '03n': '☁️', '04d': '☁️', '04n': '☁️',
        '09d': '🌧️', '09n': '🌧️', '10d': '🌦️', '10n': '🌧️',
        '11d': '⛈️', '11n': '⛈️', '13d': '❄️', '13n': '❄️',
        '50d': '🌫️', '50n': '🌫️'
      };
      
      return {
        temp: data.main.temp,
        summary: data.weather[0].description,
        icon: iconMap[data.weather[0].icon] || '🌤️',
        humidity: data.main.humidity,
        windSpeed: Math.round(data.wind.speed)
      };
    }
  }

  class CalendarWidget extends BaseWidget {
    render() {
      if (!this.el) return;
      const { events = [] } = this.config;
      const items = events.slice(0, 4).map(e => `
        <div class="cal-event">
          <span class="cal-time">${e.time || ""}</span>
          <span class="cal-title">${e.title || "Event"}</span>
        </div>`).join("");
      this.el.innerHTML = `
        <div class="widget widget-calendar">
          <div class="widget-header">📅 Upcoming</div>
          <div class="cal-events">${items || "<div class='cal-empty'>No events today</div>"}</div>
        </div>`;
    }
  }

  class NewsWidget extends BaseWidget {
    async render() {
      if (!this.el) return;
      const { apiKey, source = "bbc-news", maxItems = 3 } = this.config;
      
      // Mock news if no API key
      let newsData = [
        { title: "Technology advances continue", source: "Tech News" },
        { title: "Weather patterns shift globally", source: "Science Daily" },
        { title: "Local community events planned", source: "Local News" }
      ];
      
      if (apiKey) {
        try {
          newsData = await this.fetchNews(apiKey, source, maxItems);
        } catch (e) {
          console.warn('News fetch failed, using mock data');
        }
      }
      
      const items = newsData.slice(0, maxItems).map(n => `
        <div class="news-item">
          <div class="news-title">${n.title}</div>
          <div class="news-source">${n.source}</div>
        </div>`).join("");
        
      this.el.innerHTML = `
        <div class="widget widget-news">
          <div class="widget-header">📰 News</div>
          <div class="news-items">${items}</div>
        </div>`;
    }
    
    async fetchNews(apiKey, source, maxItems) {
      // NewsAPI integration
      const url = `https://newsapi.org/v2/top-headlines?sources=${source}&pageSize=${maxItems}&apiKey=${apiKey}`;
      const res = await fetch(url);
      const data = await res.json();
      return data.articles.map(a => ({ title: a.title, source: a.source.name }));
    }
  }

  class MemoryWidget extends BaseWidget {
    render() {
      if (!this.el) return;
      const { items = [] } = this.config;
      const memories = items.slice(0, 2).map(m => `<div class="memory-item">✨ ${m}</div>`).join("");
      this.el.innerHTML = `
        <div class="widget widget-memory">
          <div class="widget-header">💭 Memories</div>
          <div class="memory-content">${memories || "<div class='memory-empty'>No memories yet</div>"}</div>
        </div>`;
    }
  }

  class SystemWidget extends BaseWidget {
    constructor(config) {
      super(config);
      this.timer = null;
    }
    mount(container) {
      super.mount(container);
      this.startMonitoring();
    }
    startMonitoring() {
      this.render();
      this.timer = setInterval(() => this.render(), 5000);
    }
    render() {
      if (!this.el) return;
      // Mock system stats (replace with actual Pi monitoring)
      const cpu = Math.floor(Math.random() * 40 + 10);
      const temp = Math.floor(Math.random() * 20 + 45);
      const uptime = new Date(Date.now() - Math.random() * 86400000).toLocaleTimeString();
      
      this.el.innerHTML = `
        <div class="widget widget-system">
          <div class="system-stat">🔥 ${temp}°C</div>
          <div class="system-stat">⚡ ${cpu}%</div>
          <div class="system-uptime">↑ ${uptime}</div>
        </div>`;
    }
    unmount() {
      if (this.timer) clearInterval(this.timer);
      super.unmount();
    }
  }

  // Core Zoe widgets for events, tasks, and journal
  class EventsWidget extends BaseWidget {
    async render() {
      if (!this.el) return;
      const { maxItems = 4 } = this.config;
      
      // Fetch events from Zoe's backend
      let events = [];
      try {
        const response = await TouchCommon.fetchJSON('/api/events/upcoming?limit=' + maxItems);
        events = response.events || [];
      } catch (e) {
        // Mock events if API not available
        events = [
          { title: 'Team Meeting', time: '2:00 PM', type: 'work' },
          { title: 'Doctor Appointment', time: '4:30 PM', type: 'personal' },
          { title: 'Family Dinner', time: '7:00 PM', type: 'family' }
        ];
      }
      
      const eventIcons = { work: '💼', personal: '👤', family: '👨‍👩‍👧‍👦', default: '📅' };
      const items = events.slice(0, maxItems).map(e => `
        <div class="event-item">
          <span class="event-icon">${eventIcons[e.type] || eventIcons.default}</span>
          <div class="event-details">
            <span class="event-title">${e.title}</span>
            <span class="event-time">${e.time}</span>
          </div>
        </div>`).join("");
        
      this.el.innerHTML = `
        <div class="widget widget-events">
          <div class="widget-header">📅 Events</div>
          <div class="events-list">${items || "<div class='empty-state'>No upcoming events</div>"}</div>
        </div>`;
    }
  }

  class ListsWidget extends BaseWidget {
    async render() {
      if (!this.el) return;
      const { maxItems = 5 } = this.config;
      
      // Get user ID from session (check localStorage for auth session)
      let userId = 'default';
      try {
        const sessionData = localStorage.getItem('zoe_session');
        if (sessionData) {
          const session = JSON.parse(sessionData);
          userId = session.user_info?.user_id || session.user_id || 'default';
        }
      } catch (e) {
        console.warn('Failed to get user session:', e);
      }
      
      // Fetch lists from Zoe's backend
      let lists = [];
      try {
        const response = await TouchCommon.fetchJSON(`/api/lists/tasks?user_id=${userId}`);
        // Convert tasks to list items format
        lists = (response.tasks || []).map(task => ({
          text: task.text || task.title,
          list: task.list_name || task.list_category,
          completed: task.completed || false
        }));
      } catch (e) {
        console.warn('Failed to load lists:', e);
        // Show empty state instead of mock data
        lists = [];
      }
      
      const items = lists.slice(0, maxItems).map(item => `
        <div class="list-item ${item.completed ? 'completed' : ''}">
          <div class="list-checkbox ${item.completed ? 'checked' : ''}"></div>
          <div class="list-content">
            <span class="list-text">${item.text}</span>
            <span class="list-name">${item.list}</span>
          </div>
        </div>`).join("");
        
      this.el.innerHTML = `
        <div class="widget widget-lists">
          <div class="widget-header">📝 Lists</div>
          <div class="lists-container">${items || "<div class='empty-state'>No list items</div>"}</div>
        </div>`;
    }
  }

  class JournalWidget extends BaseWidget {
    async render() {
      if (!this.el) return;
      const { maxItems = 3 } = this.config;
      
      // Fetch recent journal entries from Zoe's backend
      let entries = [];
      try {
        const response = await TouchCommon.fetchJSON('/api/journal/recent?limit=' + maxItems);
        entries = response.entries || [];
      } catch (e) {
        // Mock journal entries if API not available
        entries = [
          { title: 'Beautiful sunset walk', date: 'Today', mood: '😊' },
          { title: 'Started reading new book', date: 'Yesterday', mood: '📚' },
          { title: 'Family game night', date: '2 days ago', mood: '🎲' }
        ];
      }
      
      const items = entries.slice(0, maxItems).map(e => `
        <div class="journal-item">
          <span class="journal-mood">${e.mood || '📝'}</span>
          <div class="journal-content">
            <span class="journal-title">${e.title}</span>
            <span class="journal-date">${e.date}</span>
          </div>
        </div>`).join("");
        
      this.el.innerHTML = `
        <div class="widget widget-journal">
          <div class="widget-header">📖 Journal</div>
          <div class="journal-list">${items || "<div class='empty-state'>No recent entries</div>"}</div>
        </div>`;
    }
  }

  class NotesWidget extends BaseWidget {
    async render() {
      if (!this.el) return;
      const { maxItems = 4 } = this.config;
      
      // Fetch quick notes from Zoe's backend
      let notes = [];
      try {
        const response = await TouchCommon.fetchJSON('/api/notes/recent?limit=' + maxItems);
        notes = response.notes || [];
      } catch (e) {
        // Mock notes if API not available
        notes = [
          { text: 'Pick up dry cleaning on Tuesday', created: '2 hours ago' },
          { text: 'Remember to water the plants', created: '1 day ago' },
          { text: 'Gift idea: cooking class for Mom', created: '3 days ago' }
        ];
      }
      
      const items = notes.slice(0, maxItems).map(n => `
        <div class="note-item">
          <span class="note-text">${n.text}</span>
          <span class="note-time">${n.created}</span>
        </div>`).join("");
        
      this.el.innerHTML = `
        <div class="widget widget-notes">
          <div class="widget-header">📝 Notes</div>
          <div class="notes-list">${items || "<div class='empty-state'>No recent notes</div>"}</div>
        </div>`;
    }
  }

  // Register all widgets
  registry.register("clock", ClockWidget);
  registry.register("weather", WeatherWidget);
  registry.register("calendar", CalendarWidget);
  registry.register("news", NewsWidget);
  registry.register("memory", MemoryWidget);
  registry.register("system", SystemWidget);
  registry.register("events", EventsWidget);
  registry.register("lists", ListsWidget);
  registry.register("journal", JournalWidget);
  registry.register("notes", NotesWidget);

  function init() { mountFromDOM(); }

  return { REGIONS, BaseWidget, WidgetRegistry, registry, regionSelector, init };
})();

window.AmbientWidgets = AmbientWidgets;


