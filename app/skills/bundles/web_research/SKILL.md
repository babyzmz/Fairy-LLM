# Web Research Skill

This skill enables Fairy to conduct open-web research:
search multiple sources, extract content, cross-validate, and produce cited answers.

## When to use
- User asks a research question requiring current web information
- User asks for comparison, verification, or deep explanation
- Task is NOT a structured realtime-data query (use realtime_lookup for that)

## Capabilities
- Multi-source web search
- Page content extraction
- Source ranking and trust scoring
- Evidence synthesis with citations
- Conflict detection

## Architecture
- Agent: app.agents.web_research.WebResearchAgent
- Stateless: conversation state stays in app.core.conversation_state
- Tools: search_web, fetch_page
