# Summarize Skill

Summarize articles, documents, and long content for the family.

## When to Use

- "Summarize this article: [URL]"
- "Give me the key points from this"
- "What does this email say?"
- "TLDR this"

## How to Summarize

1. Fetch the content using `web_fetch` if given a URL
2. Extract the key points
3. Present a brief summary (3-5 bullet points for articles, 1-2 sentences for short content)
4. Offer to go deeper: "Want me to explain any of these points?"

## Format

**Brief (default):**
- 3-5 bullet points
- Under 100 words

**Detailed (when asked):**
- Section-by-section breakdown
- Key quotes or data points
- 200-400 words

## Guidelines

- Lead with the most important point
- Skip filler and repetitive content
- For news articles, note the source and date
- For recipes, extract ingredients and steps separately
- For product reviews, give the verdict first, then pros/cons
