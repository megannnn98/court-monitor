"""Deterministic research planning and source routing.

The planner decides from the request, the source registry and the database
result whether the database is enough or a source refresh is recommended.
It never runs ingestion and never asks an LLM.
"""
