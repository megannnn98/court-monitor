"""Natural-language research workflow (LangGraph orchestration).

The workflow turns a natural-language query into the existing
`ResearchRequest` and executes it through the deterministic
`ResearchService`. The LLM interprets intent only; facts come from domain
services.
"""
