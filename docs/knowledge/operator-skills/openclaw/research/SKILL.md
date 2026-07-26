# Research Skill

<!-- metadata.when: user asks to look up, research, find information, or needs current web data -->


You can search the web and fetch information for the family.

## When to Use

- "What's the weather going to be this weekend?"
- "Find me a recipe for chicken tikka masala"
- "What time does the library close?"
- "Look up reviews for the new Marvel movie"

## Available Tools

- `web_search` - Search the web for current information
- `web_fetch` - Fetch and read a specific URL
- **Browser tools** (`browser_navigate`, `browser_snapshot`, `browser_evaluate`, screenshots) — use when the site needs JavaScript, a login, or you must **see** the page. See the `browser` skill for when to prefer browser over `web_fetch`.

## Guidelines

- Summarize findings concisely -- don't dump raw search results
- For recipes, extract the key ingredients and steps
- For local info (store hours, events), note the source
- If results seem outdated, mention that
- For product comparisons, give a clear recommendation with reasoning
