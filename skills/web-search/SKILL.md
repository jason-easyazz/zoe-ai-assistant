# Web Search Skill

## When to Use

Use this skill when the user wants information that requires looking at the live web.

## Trigger Conditions

**Use `web_search` (fast, ~3-5s) when:**
- Single-source fact lookup: news, exchange rates, sports scores, weather, stock prices
- One specific product at one named retailer: "what does Bunnings charge for X"
- Simple factual question answerable from one good search result

**Use `deep_web_research` (~60s) when:**
- ANY mention of location / "near me" / city name with a discovery intent
- Comparing prices across multiple stores
- Finding local businesses: shops, restaurants, services, pharmacies, tradespeople
- Events: "what's on this weekend", concerts, festivals, markets near a location
- Opening hours of a specific business or category of business
- Jobs: "jobs for nurses in [city]"
- Accommodation: hotels, motels, Airbnb in a location
- Transport timetables, flight searches
- Reviews: "best rated X in [location]"
- Any site that requires a postcode/suburb to show local prices (BWS, Liquorland, Dan Murphy's, Bottlemart)
- Any query where one search result won't cover all local options

## Decision Tree

```
Is there a location / "near me" in the query?
  YES → deep_web_research (it resolves location and finds all local sources)
  NO → Is the user comparing or discovering multiple options?
         YES → deep_web_research
         NO → web_search
```

## What to Say Before deep_web_research

Always tell the user what you're doing before calling the tool, so the ~60s wait feels active:

- Prices: "Looking up prices across local stores in [location]…"
- Events: "Checking what's on in [location] this weekend…"
- Restaurants/food: "Finding [type] options near [location]…"
- Services (plumber, dentist, etc.): "Finding [service] providers near [location]…"
- Hotels/accommodation: "Checking accommodation options in [location]…"
- Transport: "Checking transport options…"
- Generic: "Researching that in [location] — checking multiple sources, this takes about 60 seconds…"

## Query Construction for deep_web_research

Include the full location context in the query string:
- Extract city, suburb, postcode from the user message
- If user said "near me" — use their home location from memory
- Good: `"cheapest Emu Export beer Geraldton WA 6530"`
- Good: `"Italian restaurants open now Fitzroy Melbourne 3065"`
- Good: `"plumbers near Ballarat VIC reviews"`
- Bad: `"cheapest beer"` (no location, no product detail)

## What the Framework Does Automatically

When you call `deep_web_research`, the framework:
1. Classifies query depth (1-5 pages to visit)
2. Resolves location from query text or user's MemPalace home address
3. Runs a DuckDuckGo search for the primary query
4. Queries Google Maps to find ALL local businesses in the category
5. Visits all found pages concurrently with a real stealth browser
6. Automatically fills postcode/suburb gates on retail sites
7. Uses site-internal search if a homepage is detected
8. Returns extracted text for you to synthesise into a ranked answer

You do NOT need to call multiple tools. One `deep_web_research` call does all of this.

## Escalate to OpenClaw instead when

- Task requires login or authenticated session
- Multi-hour background workflow
- Code generation or Home Assistant automation setup
- The task is not web research at all (file editing, system commands, etc.)

## Example Calls

```json
{"tool": "deep_web_research", "query": "cheapest Emu Export beer Geraldton WA 6530"}
{"tool": "deep_web_research", "query": "Italian restaurants open now near Fitzroy Melbourne"}
{"tool": "deep_web_research", "query": "plumbers near Geraldton WA reviews phone number"}
{"tool": "deep_web_research", "query": "what's on this weekend Fremantle WA events"}
{"tool": "deep_web_research", "query": "hotels in Broome WA under 150 dollars"}
{"tool": "deep_web_research", "query": "jobs for nurses in Perth WA 2026"}
{"tool": "web_search", "query": "Bitcoin price AUD today"}
{"tool": "web_search", "query": "latest AI news May 2026"}
```
