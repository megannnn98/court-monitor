# ADR 0005: Entity Resolution Strategy

## Status

Accepted. The exact baseline below remains the fast path; fuzzy candidate
generation, scoring, a decision policy with human review ("Phase 2") are
implemented by [ADR 0012](0012-entity-resolution-v2.md).

**What is actually implemented today: an exact, deterministic matching_key
baseline (`RuleBasedPersonResolver` in `src/persons/resolver.py`).** It does
not do fuzzy matching, phonetic matching, or ML-based resolution — "Phase
2"/"Phase 3" below are future extension points, not implemented behavior.
Do not describe this resolver as "fuzzy entity resolution" — it isn't.

## Context

After implementing canonical person model (ADR 0004), we need a strategy to resolve person mentions to canonical persons. The challenge is:

1. Same person can appear with different name variations
2. Different people can have similar names
3. Need to handle typos, transliterations, abbreviations
4. Must be fast enough for batch processing
5. Must be deterministic and testable

Options considered:
- Exact matching only
- Fuzzy matching (edit distance, phonetic)
- Machine learning embeddings
- Graph-based clustering
- Hybrid approach

## Decision

We implemented a **deterministic rule-based baseline** with the following strategy:

### Phase 1: Exact Matching (Current)

1. **Normalize**: lowercase, remove punctuation, remove spaces
   - "Иванов Иван Иванович" → "ивановиваниванович"
   - "И.И. Иванов" → "иииванов"

2. **Create matching_key**: Unique identifier from normalized name

3. **Lookup**: O(1) lookup via indexed matching_key

4. **Resolve**:
   - If match found → link to existing person
   - If no match → create new person

### Phase 2: Fuzzy Matching (Future)

Add fuzzy matching as a separate strategy:
- Edit distance for typos
- Phonetic matching for similar-sounding names
- Context-based matching (same article, same event)

### Phase 3: Machine Learning (Future)

Add ML-based resolution:
- Train on labeled data
- Use embeddings for semantic similarity
- Active learning from manual reviews

## Consequences

### Positive

1. **Deterministic**
   - Same input always produces same output
   - Easy to test and debug
   - No model drift or randomness

2. **Fast**
   - O(1) lookups via indexed matching_key
   - Can handle millions of mentions
   - No expensive similarity computations

3. **Transparent**
   - Easy to understand how decisions are made
   - Can explain why two mentions were/weren't merged
   - No "black box" ML model

4. **Testable**
   - Can write comprehensive unit tests
   - Can create golden datasets
   - Can measure precision/recall

5. **Extensible**
   - Can add fuzzy matching later without breaking existing code
   - Can add ML as separate strategy
   - Can combine multiple strategies

### Negative

1. **No Fuzzy Matching**
   - May miss "Иванов" vs "Иванnaf" (typo)
   - Mitigated by creating multiple aliases
   - Future: add fuzzy matching strategy

2. **Name Normalization Complexity**
   - Russian names have complex morphology
   - "Иванов", "Иванова", "Иванову" are same person
   - Mitigated by rule-based normalizer
   - Future: ML-based normalization

3. **Manual Review Needed**
   - Some merges require human judgment
   - Mitigated by review queue
   - Future: active learning

## Implementation

### PersonResolver Protocol

```python
class PersonResolver(Protocol):
    def resolve(self, mention: EntityMention) -> Person:
        """Resolve a mention to a canonical person."""
        ...
```

### RuleBasedPersonResolver

```python
class RuleBasedPersonResolver:
    def resolve(self, mention: EntityMention) -> Person:
        # Normalize
        normalized = normalize_person_name(mention.surface_text)
        matching_key = create_matching_key(normalized)

        # Lookup
        existing = self.persistence.find_by_matching_key(matching_key)

        if existing:
            # Link to existing
            self.persistence.create_alias(existing, mention)
            return existing
        else:
            # Create new
            person = self.persistence.create_person(normalized, matching_key)
            self.persistence.create_alias(person, mention)
            return person
```

### Normalization Rules

1. **Lowercase**: Convert to lowercase
2. **Remove punctuation**: Remove dots, commas, hyphens
3. **Remove spaces**: Join all parts
4. **Handle ё → е**: Normalize Russian letter
5. **Expand abbreviations**: "И.И." → "ии"

Examples:
- "Иванов Иван Иванович" → "ивановиваниванович"
- "И.И. Иванов" → "иииванов"
- "Иванов-Петров И.И." → "ивановпетровии"

### Evaluation

Created evaluation framework (see `docs/wiki/Evaluation.md`):

```python
# Golden dataset
{
    "cases": [
        {
            "mentions": ["Иванов И.И.", "Иван Иванов", "И.И. Иванов"],
            "expected_person_id": 1
        }
    ]
}

# Metrics
- Pairwise precision: of all predicted pairs, how many correct?
- Pairwise recall: of all correct pairs, how many predicted?
- Pairwise F1: harmonic mean
```

## Alternatives Considered

### Alternative 1: Fuzzy Matching Only

Use edit distance or phonetic matching for all resolution.

**Pros**: Can handle typos and variations
**Cons**: Slower, less deterministic, harder to test

**Rejected**: Start with exact matching baseline, add fuzzy as enhancement

### Alternative 2: Machine Learning Only

Use ML embeddings for all resolution.

**Pros**: Can handle semantic similarity, learn from data
**Cons**: Non-deterministic, requires training data, model drift, hard to debug

**Rejected**: Need deterministic baseline first, ML can be added later

### Alternative 3: Graph-Based Clustering

Use graph database to cluster mentions.

**Pros**: Flexible, can model complex relationships
**Cons**: New technology stack, operational complexity, overkill

**Rejected**: PostgreSQL is sufficient, graph DB adds complexity

### Alternative 4: External Identity Service

Use Wikidata or similar for canonical identities.

**Pros**: Leverages existing data, community-maintained
**Cons**: Not all persons are in external databases, no control

**Rejected**: Many persons of interest are not in public databases

## Future Enhancements

### Phase 2: Fuzzy Matching

Add fuzzy matching strategy:

```python
class FuzzyPersonResolver:
    def resolve(self, mention: EntityMention) -> Person:
        # Get candidates by exact match
        candidates = self.exact_resolver.get_candidates(mention)

        # Score by similarity
        scored = []
        for candidate in candidates:
            score = compute_similarity(mention, candidate)
            scored.append((candidate, score))

        # Return best match if above threshold
        best = max(scored, key=lambda x: x[1])
        if best[1] > threshold:
            return best[0]
        else:
            return self.create_new_person(mention)
```

Similarity metrics:
- Edit distance (Levenshtein)
- Phonetic similarity (Soundex, Metaphone)
- Context similarity (same article, same event)

### Phase 3: Machine Learning

Add ML-based resolution:

```python
class MLPersonResolver:
    def __init__(self, model_path: str):
        self.model = load_model(model_path)

    def resolve(self, mention: EntityMention) -> Person:
        # Get candidates
        candidates = self.get_candidates(mention)

        # Extract features
        features = extract_features(mention, candidates)

        # Predict
        predictions = self.model.predict(features)

        # Return best match
        best = max(predictions, key=lambda x: x.score)
        if best.score > threshold:
            return best.person
        else:
            return self.create_new_person(mention)
```

Training data:
- Labeled pairs: (mention, person) → same/different
- Features: name similarity, context, temporal, etc.

### Phase 4: Active Learning

Learn from manual reviews:

```python
class ActiveLearningResolver:
    def resolve(self, mention: EntityMention) -> Person:
        # Get candidates
        candidates = self.get_candidates(mention)

        # If uncertain, flag for review
        if self.is_uncertain(candidates):
            self.flag_for_review(mention, candidates)
            return self.create_new_person(mention)

        # Otherwise, resolve normally
        return self.resolve_normal(mention, candidates)

    def learn_from_review(self, review: Review):
        # Update model with review decision
        self.model.update(review.mention, review.person, review.decision)
```

## References

- Implementation: `src/persons/resolver.py`
- Tests: `tests/persons/test_person_resolver.py`
- Evaluation: `src/persons/er_evaluation.py`, `tests/persons/test_er_evaluation.py`
- Related ADR: ADR 0004 (Canonical Person Model)
