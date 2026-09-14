"""Deterministic research reports over `ResearchResponse`.

Facts come from `ResearchService`; this package only presents them as
claims with citations and decides which results need human review. It never
changes a status and never asks an LLM to phrase facts.
"""
