"""Generic monitoring mechanics: discover, fetch, parse and persist documents.

Knows nothing of courts, cases or people; the application builds on it through the
ports in `monitor_core.ports`. Must not import any application package.
"""
