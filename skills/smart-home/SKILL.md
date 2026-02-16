---
name: smart-home
description: Control Home Assistant devices (lights, switches, thermostats, sensors)
version: 1.0.0
author: zoe-team
api_only: true
priority: 10
triggers:
  - "turn on"
  - "turn off"
  - "dim"
  - "brightness"
  - "set temperature"
  - "thermostat"
  - "lock"
  - "unlock"
  - "open"
  - "close"
  - "garage"
  - "fan"
  - "sensor"
  - "temperature"
  - "humidity"
allowed_endpoints:
  - "POST /api/homeassistant/control"
  - "GET /api/homeassistant/entities"
  - "GET /api/homeassistant/state"
  - "GET /api/homeassistant/states"
tags:
  - home-automation
  - iot
---
# Smart Home Control

## When to Use
User wants to control smart home devices: lights, switches, thermostats, fans, locks, garage doors, or check sensor readings.

## How to Handle

1. First identify the device(s) the user is referring to
2. Determine the action: turn_on, turn_off, toggle, set_brightness, set_temperature
3. Call the control endpoint with the appropriate entity_id and action

## API Endpoints

### List Available Entities
GET http://localhost:8000/api/homeassistant/entities

### Get Entity State
GET http://localhost:8000/api/homeassistant/state?entity_id={entity_id}

### Control Device
POST http://localhost:8000/api/homeassistant/control
```json
{
  "entity_id": "light.living_room",
  "action": "turn_on",
  "parameters": {"brightness": 255}
}
```

## Examples
- "Turn on living room lights" -> POST control {entity_id: "light.living_room", action: "turn_on"}
- "Dim bedroom to 50%" -> POST control {entity_id: "light.bedroom", action: "turn_on", parameters: {brightness: 128}}
- "What's the temperature?" -> GET state {entity_id: "sensor.temperature"}
- "Lock the front door" -> POST control {entity_id: "lock.front_door", action: "lock"}

## Important Notes
- Always confirm the action with the user before executing destructive operations
- For locks: always confirm before unlocking
- Report sensor values in user-friendly format (e.g., "It's 22 degrees Celsius")
