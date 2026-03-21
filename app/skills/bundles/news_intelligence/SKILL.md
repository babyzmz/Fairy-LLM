---
name: news-intelligence
description: Retrieves, filters, and summarizes the latest news articles and headlines. Use when the user asks for current events, daily briefings, or news on a specific topic.
allowed-tools: 
---

# News Intelligence Skill

## When to use
- The user asks "what's the news today?", "give me a daily briefing", or "any updates on [topic]?".
- The user wants a summary of recent events in a specific category (e.g., technology, finance, sports).

## When NOT to use
- The user is asking for historical facts or deep research on a static topic (use web-research).
- The user is asking to analyze a specific, provided document (use document-editing).

## Core workflow
1. **Identify Intent**: Determine if the user wants general headlines or news about a specific topic/category.
2. **Fetch News**: (Note: This skill relies on the underlying system's NewsService capability, not explicit tool calls). Request the relevant news data from the system.
3. **Filter & Curate**: Select the most important, relevant, and diverse articles from the retrieved data. Avoid duplicates.
4. **Summarize**: Write concise, engaging summaries for each selected article.
5. **Format**: Present the news in a structured, easy-to-read format (e.g., a bulleted list or a categorized briefing).

## Tool usage policy
- This skill does not currently expose specific external tools to the model. It relies on the runtime environment to inject news data or provide a native news fetching capability.

## Failure handling
- **No news found**: If the topic is too niche or there are no recent updates, inform the user and suggest broadening the topic.
- **Service unavailable**: If the underlying news service fails, apologize to the user and suggest trying again later or using the `web-research` skill as a fallback.

## Output contract
- Provide a clear, structured news briefing.
- Include headlines and brief summaries.
- Whenever possible, include the source name and a link to the full article.

## Examples

**Task**: "Give me the top tech news for today."
**Action**:
1. Fetch top headlines in the 'technology' category.
2. Select the top 3-5 distinct stories.
3. Present them as a bulleted list with summaries and links.

**Task**: "Any news about Apple's new product?"
**Action**:
1. Fetch news specifically querying for "Apple new product".
2. Summarize the most recent and relevant articles found.
