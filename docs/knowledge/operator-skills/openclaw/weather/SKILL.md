# Weather Skill

<!-- metadata.when: user asks about weather, temperature, forecast, climate, rain, sun -->


You provide weather information using the family's saved location preferences.

## When to Use

Activate when someone asks about:
- Current weather: "what's the weather", "is it hot outside", "how's the weather"
- Forecast: "will it rain tomorrow", "weather this week", "weekend forecast"
- Clothing advice: "do I need a jacket", "should I bring an umbrella"
- Activity planning: "is it good weather for a BBQ", "can we go to the beach"

## Tools

### Current weather
```
mcporter-safe call zoe-data.weather_current
```

With city override:
```
mcporter-safe call zoe-data.weather_current city="Sydney"
```

### Forecast
```
mcporter-safe call zoe-data.weather_forecast
```

With more periods:
```
mcporter-safe call zoe-data.weather_forecast days=8
```

## Guidelines

- Always include temperature and conditions in your response
- Convert descriptions to natural language: "scattered clouds" -> "partly cloudy"
- For clothing advice, factor in temperature AND conditions (rain, wind)
- When someone has calendar events, combine weather with their schedule: "It might rain during your BBQ at 3pm"
- Use Celsius (metric) -- the location is Australia
- If weather data unavailable, suggest checking weather preferences in settings
