"""Source adapters: how documents enter the system.

Adapters are transport-only — they MUST NOT make business decisions or touch
the review/matching layers. They yield :class:`FetchResult` values consumed by
the pipeline service.
"""
