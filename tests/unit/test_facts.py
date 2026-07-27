"""Unit tests: ExtractedFactDTO validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from court_monitor.domain.facts import ExtractedFactDTO
from court_monitor.domain.models import VerificationStatus


def _base(**kw):
    base = {
        "entity": "case",
        "field": "criminal_article",
        "value": "205.1",
        "extraction_method": "regex:article",
    }
    base.update(kw)
    return ExtractedFactDTO(**base)


def test_defaults_inferred_and_zero_confidence():
    dto = _base()
    assert dto.verification_status == VerificationStatus.inferred
    assert dto.confidence == 0.0


def test_confidence_bounds_enforced():
    with pytest.raises(ValidationError):
        _base(confidence=1.5)
    with pytest.raises(ValidationError):
        _base(confidence=-0.1)


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        _base(rogue_field="nope")


def test_verification_status_from_string():
    dto = _base(verification_status="confirmed")
    assert dto.verification_status == VerificationStatus.confirmed


def test_invalid_verification_status_rejected():
    with pytest.raises(ValidationError):
        _base(verification_status="totally-confirmed")
