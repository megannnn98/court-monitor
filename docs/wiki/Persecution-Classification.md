# Persecution Classification

## Overview

The persecution classification system analyzes extracted entities and events to determine whether a person is politically persecuted. This is the core business logic of the court-monitor project.

## Architecture

### Classification Service

The `PersecutionClassificationService` uses a rule-based approach to classify persons:

1. **Collect Evidence**: Gather all events and articles associated with a person
2. **Analyze Patterns**: Check for indicators of political persecution
3. **Compute Confidence**: Assign confidence score based on evidence strength
4. **Generate Reasons**: Provide human-readable explanations

### Evidence Types

The classifier looks for several types of evidence:

#### Political Charges

Legal articles commonly used for political persecution:
- **280**: Public calls for extremism
- **282**: Incitement of hatred
- **207.3**: "Fakes" about the army
- **205.2**: Justification of terrorism
- **212**: Mass disorder organization
- **274.1**: Misuse of state funds
- **284.2**: Cooperation with "undesirable" organization

#### Political Keywords

Text analysis for political terminology:
- "политический" (political)
- "политзаключенный" (political prisoner)
- "политическое преследование" (political persecution)
- "правозащитник" (human rights defender)
- "Мемориал" (Memorial - human rights org)
- "ОВД-Инфо" (OVD-Info - human rights org)
- "антивоенный" (anti-war)
- "демократия" (democracy)
- "оппозиция" (opposition)
- "протест" (protest)
- "свобода слова" (freedom of speech)
- "иностранный агент" (foreign agent)
- "нежелательная организация" (undesirable organization)
- "ЛГБТ" (LGBT)
- "Свидетели Иеговы" (Jehovah's Witnesses)

#### Event Patterns

Types of events that indicate persecution:
- `arrest`: Detention by authorities
- `detention`: Temporary holding
- `charge`: Formal criminal charges
- `sentence`: Court conviction
- `case_opened`: Criminal case initiation

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

### Database Schema

```sql
CREATE TABLE persecution_classifications (
    id SERIAL PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    status VARCHAR(32) NOT NULL,  -- 'political', 'non_political', 'uncertain'
    confidence FLOAT NOT NULL,
    reasons JSONB NOT NULL DEFAULT '[]',
    evidence_types JSONB NOT NULL DEFAULT '[]',
    classifier_name VARCHAR(255) NOT NULL,
    classifier_version VARCHAR(64) NOT NULL,
    classified_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(person_id, classifier_name, classifier_version)
);

CREATE INDEX ix_persecution_classifications_person_id ON persecution_classifications(person_id);
CREATE INDEX ix_persecution_classifications_status ON persecution_classifications(status);
```

## CLI Commands

### classify-persecution

Classify persons for political persecution:

```bash
# Classify all persons
court-monitor classify-persecution

# Classify specific person
court-monitor classify-persecution --person-id 123

# Limit to N persons
court-monitor classify-persecution --limit 100
```

Output:
```
Classified 150 persons: 45 political persecution
```

### list-candidates

List politically persecuted persons absent from Rosfinmonitoring:

```bash
# List candidates from latest snapshot
court-monitor list-candidates --snapshot-id 5

# Filter by confidence
court-monitor list-candidates --snapshot-id 5 --min-confidence 0.8

# Output to file
court-monitor list-candidates --snapshot-id 5 --output-path candidates.json
```

Output:
```json
{
  "snapshot_id": 5,
  "candidates": [
    {
      "person_id": 42,
      "canonical_name": "Иванов Иван Иванович",
      "normalized_name": "иванов иван иванович",
      "persecution_status": "political",
      "persecution_confidence": 0.95,
      "persecution_reasons": ["Charged under political article 280"],
      "rosfinmonitoring_status": "not_in_list",
      "rosfinmonitoring_match_confidence": null,
      "event_count": 3,
      "alias_count": 5,
      "last_event_date": "2026-09-01T12:00:00Z"
    }
  ],
  "total_count": 1,
  "query_timestamp": "2026-09-13T15:30:00Z"
}
```

## API Endpoints

### GET /persons/{id}/persecution

Get persecution classification for a person:

```bash
curl http://localhost:8000/persons/42/persecution
```

Response:
```json
{
  "id": 1,
  "person_id": 42,
  "status": "political",
  "confidence": 0.95,
  "reasons": ["Charged under political article 280"],
  "evidence_types": ["political_charge", "political_keyword"],
  "classifier_name": "rule-based-persecution-classifier",
  "classifier_version": "1.0.0"
}
```

### GET /candidates

List politically persecuted persons absent from Rosfinmonitoring:

```bash
curl "http://localhost:8000/candidates?snapshot_id=5&min_confidence=0.8&limit=100"
```

## Evaluation

Classification quality is measured using standard metrics:

```bash
court-monitor evaluate-persecution --dataset tests/fixtures/persecution_golden_dataset.json
```

Metrics:
- **Accuracy**: Overall correctness of classifications
- **Political Precision**: Of all predicted as political, how many are correct?
- **Political Recall**: Of all actual political cases, how many did we predict?
- **Political F1**: Harmonic mean of precision and recall

Example output:
```json
{
  "total_cases": 100,
  "correct_classifications": 92,
  "accuracy": 0.92,
  "political_precision": 0.94,
  "political_recall": 0.89,
  "political_f1": 0.91
}
```

## Future Improvements

### Machine Learning

- Train classifier on labeled data
- Use NLP for better text understanding
- Learn from manual review decisions

### Context Analysis

- Analyze article source credibility
- Check for corroborating sources
- Consider temporal patterns

### International Standards

- Align with UN human rights definitions
- Cross-reference with international databases
- Support multiple jurisdictions
