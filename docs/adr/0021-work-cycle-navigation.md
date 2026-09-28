# ADR 0021: The operator UI follows one work cycle

## Status

Accepted, 2026-09-28. Implemented in four independently reversible stages.

## Context

The operator menu grew around storage domains and implementation details. It mixed
reference pages with actions, exposed both a combined queue and its individual review
pages, and called the pipeline screen `Управление`. An operator had to remember which
page came next and which pending reviews blocked useful progress.

The underlying five-step pipeline, review services and conservative person-attribution
rules are already established. Navigation should explain that workflow without changing
it or turning review decisions into automatic actions.

## Decision

The primary UI is one `Рабочий цикл` page. It presents the existing pipeline steps and
their intervening review stations in order. A step keeps its existing POST action and
confirmation. A review station links to its dedicated page and shows its current count.
Extraction failures are informational: they are visible but do not block the cycle.

The menu contains only the cycle, the final result and the overview as operational
destinations. Investigations, people, publications, officials, the run journal, logs and
the wiki form a `Справочно` group. The former `Управление` page is named
`Журнал запусков`; it remains the detailed history, error and source-selection view.

The combined queue is split into dedicated pair, role and political-status pages.
Unnamed figurants keep their dedicated page. Old queue and management URLs redirect so
saved bookmarks remain useful.

The common page hint derives its next action from current pipeline and review state.
Starting a step while the preceding review station is incomplete requires explicit
confirmation. Role and political-status ambiguity is shown as work to inspect, but this
change does not invent manual decisions that the domain does not support.

The rollout is staged:

1. simplify the menu and rename management to the run journal;
2. add the cycle page and make it home;
3. make next-action hints and review warnings state-aware;
4. split the combined queue and retain redirects from old addresses.

## Consequences

- The UI mirrors the order in which an operator can safely advance the existing system.
- Pipeline execution, ER decisions and person-attribution policy do not change.
- Counts and warnings come from existing persisted state; no production schema changes.
- During the staged rollout some old pages remain reachable before their replacement is
  introduced, but they disappear from primary navigation first.
- Route and UI tests protect both the new information architecture and legacy redirects.

## Alternatives considered

- **Keep the domain-oriented menu and add instructions.** Rejected: the next action
  would still depend on knowledge outside the page.
- **Keep one combined queue.** Rejected: unlike decisions are mixed and cannot explain
  their separate positions in the cycle.
- **Automate steps two through five.** Rejected: this navigation change is not evidence
  that review gates or operational confirmations can be removed safely.
