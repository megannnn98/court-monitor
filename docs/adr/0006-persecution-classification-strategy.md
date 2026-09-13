# ADR 0006: Persecution Classification Strategy

## Status

Accepted

## Context

The court-monitor project needs to identify persons who are politically persecuted. The challenge is:

1. "Political persecution" is a complex, subjective concept
2. Need to classify based on extracted entities and events
3. Must be explainable (why is someone classified as political?)
4. Must be fast enough for batch processing
5. Must be testable and measurable

Options considered:
- Keyword-based classification
- ML-based classification
- Rule-based classification with multiple signals
- Manual classification only
- Hybrid approach

## Decision

We implemented a **rule-based classification system** that combines multiple signals:

### Classification Signals

1. **Political Charges**
   - Article 280 (extremism calls)
   - Article 282 (hatred incitement)
   - Article 205.2 (terrorism justification)
   - Article 207.3 (army fakes)
   - Article 212 (mass disorders)
   - Article 274.1 (state secrets misuse)
   - Article 284.2 (undesirable organizations)

2. **Political Keywords**
   - "политический" (political)
   - "политзаключенный" (political prisoner)
   - "политическое преследование" (political persecution)
   - "правозащитник" (human rights defender)
   - "Мемориал" (Memorial)
   - "ОВД-Инфо" (OVD-Info)
   - "SOTA" (SOTA)
   - "антивоенный" (anti-war)
   - "демократия" (democracy)
   - "оппозиция" (opposition)
   - "протест" (protest)
   - "свобода слова" (freedom of speech)
   - "иностранный агент" (foreign agent)
   - "нежелательная организация" (undesirable organization)
   - "ЛГБТ" (LGBT)
   - "Свидетели Иеговы" (Jehovah's Witnesses)

3. **Event Types**
   - `arrest`: Person was arrested
   - `detention`: Person was detained
   - `charge`: Person was charged
   - `sentence`: Person was sentenced
   - `case_opened`: Criminal case was opened

### Classification Algorithm

```python
def classify_person(person_id: int) -> PersecutionClassification:
    # Get all events for this person
    events = get_person_events(person_id)

    # Get all articles mentioning this person
    articles = get_person_articles(person_id)

    # Collect evidence
    evidence = []

    # Check for political charges
    for event in events:
        if event.event_type in ['charge', 'sentence']:
            article = event.attributes.get('article', '')
            if article in POLITICAL_ARTICLES:
                evidence.append(EvidenceType.POLITICAL_CHARGE)

    # Check for political keywords in articles
    for article in articles:
        text = article.text.lower()
        for keyword in POLITICAL_KEYWORDS:
            if keyword in text:
                evidence.append(EvidenceType.POLITICAL_KEYWORD)
                break

    # Compute confidence
    if not evidence:
        return PersecutionClassification(
            status='non_political',
            confidence=0.9,
            reasons=['No evidence of political persecution']
        )

    # Strong evidence
    if EvidenceType.POLITICAL_CHARGE in evidence:
        return PersecutionClassification(
            status='political',
            confidence=0.95,
            reasons=['Charged under political article'],
            evidence_types=evidence
        )

    # Moderate evidence
    if len(evidence) >= 2:
        return PersecutionClassification(
            status='political',
            confidence=0.8,
            reasons=['Multiple indicators of political persecution'],
            evidence_types=evidence
        )

    # Weak evidence
    return PersecutionClassification(
        status='uncertain',
        confidence=0.6,
        reasons=['Some indicators but not conclusive'],
        evidence_types=evidence
    )
```

## Consequences

### Positive

1. **Explainable**
   - Can provide reasons for classification
   - Can show which evidence was used
   - Easy to understand and audit

2. **Deterministic**
   - Same input always produces same output
   - Easy to test and debug
   - No model drift

3. **Fast**
   - Simple rule evaluation
   - Can classify thousands of persons per second
   - No expensive ML inference

4. **Testable**
   - Can create golden datasets
   - Can measure precision/recall
   - Can add test cases for edge cases

5. **Extensible**
   - Can add new signals easily
   - Can adjust confidence thresholds
   - Can combine with ML later

### Negative

1. **Rule Maintenance**
   - Need to maintain lists of political articles and keywords
   - Laws change, new articles added
   - Keywords may become outdated

2. **Limited Nuance**
   - Cannot capture complex contextual factors
   - May misclassify edge cases
   - Cannot learn from data

3. **Manual Review Needed**
   - Some cases require human judgment
   - Uncertain classifications need review
   - Mitigated by review queue

## Implementation

### Domain Models

```python
class PersecutionClassification(BaseModel):
    person_id: int
    status: str  # political/non_political/uncertain
    confidence: float  # 0.0 to 1.0
    reasons: list[str]
    evidence_types: list[str]
    classifier_name: str
    classifier_version: str
    classified_at: datetime

class EvidenceType(StrEnum):
    POLITICAL_CHARGE = "political_charge"
    POLITICAL_KEYWORD = "political_keyword"
    POLITICAL_EVENT = "political_event"
```

### Database Schema

```sql
CREATE TABLE persecution_classifications (
    id SERIAL PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    status VARCHAR(32) NOT NULL,
    confidence FLOAT NOT NULL,
    reasons JSONB NOT NULL DEFAULT '[]',
    evidence_types JSONB NOT NULL DEFAULT '[]',
    classifier_name VARCHAR(255) NOT NULL,
    classifier_version VARCHAR(64) NOT NULL,
    classified_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(person_id, classifier_name, classifier_version)
);

CREATE INDEX ix_persecution_classifications_person_id
    ON persecution_classifications(person_id);
CREATE INDEX ix_persecution_classifications_status
    ON persecution_classifications(status);
```

### Evaluation

Created evaluation framework:

```python
# Golden dataset
{
    "cases": [
        {
            "person_id": 1,
            "expected_status": "political",
            "expected_confidence_min": 0.8,
            "description": "Charged under article 280"
        }
    ]
}

# Metrics
- Accuracy: overall correctness
- Political precision: of all predicted political, how many correct?
- Political recall: of all actual political, how many predicted?
- Political F1: harmonic mean
```

## Alternatives Considered

### Alternative 1: Manual Classification Only

Have humans classify all persons.

**Pros**: Most accurate, can capture nuance
**Cons**: Slow, expensive, not scalable

**Rejected**: Cannot scale to thousands of persons

### Alternative 2: ML-Based Classification

Train ML model on labeled data.

**Pros**: Can learn complex patterns, improve over time
**Cons**: Requires training data, non-deterministic, hard to explain

**Rejected**: Need explainable baseline first, ML can be added later

### Alternative 3: Keyword-Only Classification

Only use keyword matching.

**Pros**: Simple, fast
**Cons**: Too many false positives/negatives

**Rejected**: Need multiple signals for accuracy

### Alternative 4: External Classification Service

Use external service (e.g., human rights databases).

**Pros**: Leverages existing expertise
**Cons**: Not all persons are in external databases, no control

**Rejected**: Need full control and customization

## Future Enhancements

### Phase 2: Context Analysis

Add context-based signals:
- Article source credibility
- Corroborating sources
- Temporal patterns
- Geographic patterns

### Phase 3: Machine Learning

Add ML-based classification:
- Train on labeled data
- Use NLP for better text understanding
- Learn from manual reviews

### Phase 4: International Standards

Align with international standards:
- UN human rights definitions
- EU asylum criteria
- US asylum criteria

## References

- Implementation: `src/persecution_classification_service.py`, `src/persecution_classifier.py`
- Tests: `tests/test_persecution_classification_service.py`, `tests/test_persecution_classifier.py`
- Evaluation: `src/persecution_evaluation.py`, `tests/test_persecution_evaluation.py`
- Related: `docs/wiki/Persecution-Classification.md`
