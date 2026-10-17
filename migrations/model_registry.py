"""Every table of the one database, composed for Alembic (ADR 0031).

Each package owns its ORM models and keeps them to itself; this registry is the one
place that imports all of them, so that `Base.metadata` holds every table when Alembic
compares it with the database. Migrations are tooling: nothing in production imports
this module, and no package imports another's models through it.
"""

from __future__ import annotations

import analysis.storage.models
import db.models.settings
import ingestion.storage.models
import investigation.storage.models
import orchestrator.storage.models
import persons.storage.models
import processing.storage.models
import results.storage.models
from db.models.base import Base

# The storage module of each owner, by package: `tests/test_data_ownership.py` checks
# that every table is registered here exactly once.
OWNERS = {
    "court-ingestion": ingestion.storage.models,
    "court-processing": processing.storage.models,
    "court-processing (ER v2)": persons.storage.models,
    "court-analysis": analysis.storage.models,
    "court-results": results.storage.models,
    "court-orchestrator": orchestrator.storage.models,
    "court-investigation": investigation.storage.models,
    "court-platform": db.models.settings,
}

metadata = Base.metadata
