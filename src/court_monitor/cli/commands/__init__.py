"""Command groups, registered onto one flat Typer app.

Each module exposes ``register(app)`` and attaches its commands with their
existing names. Grouping is for readers, not for the command line: splitting
into Typer sub-apps would have renamed every command (``court-monitor db
doctor`` instead of ``court-monitor doctor``) and broken every caller.
"""

from __future__ import annotations
