# ADR 0020: AI reviews ER decisions, a deterministic policy decides

## Status

Accepted, 2026-09-20. Adds a review step and an audit table; changes no ER rule
(ADR 0012) and no candidate definition (ADR 0006, ADR 0007).

## Context

ER v2 sends a mention to a human whenever the name alone cannot identify a person:
a medium score, several plausible candidates, initials, a name without corroboration.
On the working corpus that is a large share of the mentions, and the queue is the
bottleneck of the pipeline — a person waits for a reviewer, and until then the mention
is unlinked and the person misses from `/ui/candidates`.

Most of those decisions are not hard for a reader of the two articles: the same case
number, the same court, the same organisation, an obvious namesake in another region.
That reading is what a language model can do, and it is exactly what the deterministic
features cannot: the extractor has no birth date, no region, no case number as fields.

The danger is the opposite of the benefit. Merging two people who share a name is the
worst error this system can make — it attributes a prosecution to the wrong person —
and a model's confidence is not evidence. A model must not be able to cause it.

## Decision

### The model reviews, the policy decides

`EntityMatchReviewer` (a Protocol in `persons/resolution/ai_review.py`) answers one
question per candidate: same person, different person, or uncertain, with its own
confidence and the quotes it relied on. It returns a value object. It never writes to
the database, never merges persons and never chooses an action.

`EntityReviewPolicy` (`ai_policy.py`) turns the answers into one ER action, in code
whose branches are readable and tested:

- `same_person` at or above `ENTITY_REVIEW_AUTO_THRESHOLD` (default 0.90), exactly one
  such candidate, no conflicting identity feature on it, and no blocking reason on the
  decision → `link_to_person`;
- every candidate `different_person` at or above the threshold, and the incoming name is
  complete → `create_new_person`;
- anything else — `uncertain`, a lower confidence, two candidates read as the same
  person, a conflict, an incomplete name — → the human queue;
- an invalid answer, a timeout, exhausted retries or an API error → the human queue,
  recorded as `failed`. There is no silent fallback that links anybody.

Blocking reasons are the ones where ER v2 itself refuses to guess:
`multiple_exact_name_matches`, `possible_duplicate_persons`, `known_distinct_persons`.
Telling namesakes apart and merging two canonical persons stay human decisions;
`merge_persons` is not in the policy's vocabulary at all.

### The action goes through the existing review service

`AutomatedEntityReviewService` applies the chosen action with
`PersonResolutionReviewService.apply` — the same code path a human uses, with the same
alias promotion, event linking, locks and state checks. The audit row and the action are
committed in one transaction; if the action cannot be applied, both roll back and the
failure is recorded, so an applied link always has provenance and a rejected one never
leaves a half-written state.

### What the model is shown

Only the two sides of one pending decision: the mention's name and normalized form, the
candidate's canonical name and aliases, the deterministic comparison (matched and
conflicting features, the score, why a review was asked), the candidate's event types,
and short quotes around the mentions — 600 characters each, at most four per side, at
most three candidates. No corpus, no full articles, no retrieval results.

The system prompt states that publication text is untrusted data, that a shared full
name is not enough, that a differing patronymic, birth date, region or case number is a
conflict, that missing context means `uncertain`, and that inventing facts is forbidden.
The answer is validated against a Pydantic model; free text is a review failure.

### Provenance

`person_resolution_ai_reviews` keeps one answered row per (decision, input hash, model,
prompt version): the decision, confidence, explanation, supporting and conflicting
evidence, every candidate's answer, provider, model, prompt version, the provider calls
it cost, duration, the outcome, the reason a human is needed, and the action that was
applied. The unique index is partial (`WHERE outcome <> 'failed'`), so a rerun of an
answered review is a no-op while a review that failed — a timeout, a rate limit, an
unparseable answer — is tried again on the next run. A new
`ENTITY_REVIEW_PROMPT_VERSION` reviews again and keeps the earlier row as history.

The model is called with no transaction open: the decision is read in a short
transaction, the provider answers outside any, and the action is applied in a second
transaction that locks the row and re-checks that the decision is still pending and
still unreviewed. Holding a row lock across a provider call would block a human on the
same decision and keep a pooled connection for as long as the provider takes.

Logs carry identifiers and the decision only — no prompt, no answer text, no article
text, no API key. The full structured answer lives in the audit table.

### Disabled by default

`ENTITY_REVIEW_PROVIDER=none` is the default: nothing is built, the monitoring stage
reports `not_configured`, and every pending decision goes to a human exactly as before.
Turning it on is a configuration change, and the threshold is configuration too.

## Consequences

- The human queue shrinks to the cases a reader cannot settle from two quotes, plus
  every technical failure.
- A wrong automatic link is now possible where before there was none. It is bounded by
  the threshold, by the ER conflicts the policy refuses to override, and by the audit
  row that says which model, which prompt and which evidence produced it. It is not
  bounded by the model being right.
- The provider is replaceable: another `EntityMatchReviewer` needs no change in the
  policy, the service or the domain.
- The quality of the reviews is not measured yet. The labelled pairs in
  `tests/fixtures/entity_review_cases.json` fix what the policy does with a given
  answer, not how often a model answers well; an evaluation against the golden corpus
  (ADR 0012's `evaluate-er`) is the next step before the threshold is lowered.

## Alternatives considered

- **Let the model apply the decision** (write the link itself, or answer with an
  action). Rejected: the model would then be the decision point, and a prompt injection
  inside an article would reach the database.
- **Feed the whole articles.** Rejected: more tokens, more injection surface, and the
  question is answered by the sentences around the mentions.
- **A single call for all candidates.** Rejected: one answer per pair keeps the contract
  small, makes "two candidates read as the same person" visible to the policy, and keeps
  the audit row per candidate.
- **LangGraph.** Rejected: one call, one policy, one action — an application service is
  enough, and the research workflow's graph brings no benefit here.
