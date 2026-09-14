"""Read-only platform boundary (ADR 0014) for external integrations such as MCP.

External callers get typed application results — `ResearchResponse`,
`ResearchReport`, monitoring views — never SQLAlchemy sessions or ORM rows.
"""
