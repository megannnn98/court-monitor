"""Entity-level semantic candidate retrieval (ADR 0011).

PostgreSQL is the source of truth; Qdrant only returns candidate entity ids.
Retrieval hits never carry facts, and a retrieval score is never a domain
confidence.
"""
