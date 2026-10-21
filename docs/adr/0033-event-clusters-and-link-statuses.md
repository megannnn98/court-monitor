# ADR 0033: Event clusters, their statuses, and the rollback of a link

## Status

Accepted, 2026-10-21. Builds on [ADR 0032](0032-criminal-case-resolution.md) (the criminal
cases) and [ADR 0031](0031-data-ownership-and-package-apis.md) (one owner per table).

## Context

The database knew the events of a publication, the people they are about and the criminal
cases the events belong to (ADR 0032). It did not know **which publications tell the same
event**: three outlets writing about one detention were three events with three links to
one case, and nothing said so. The console and the investigations ask that question
constantly — «what else was written about this detention?».

The experiment behind the stage (`feat/clustering-experiment`) looked for a ready-made
algorithm and found none: the Taranis clustering is inapplicable out of the box (it drops
78 % of the corpus), the streaming algorithms depend on the order of arrival and join
independent cases, and there is no independent GOLD. So the stage is built on the same
ground as ADR 0032: conservative rules, every decision explainable, every doubt resolved
to «apart».

Two things were decided along the way and are written down here, because they are
decisions and not implementation:

* what a cluster is, and what may not be joined;
* what the statuses mean, and how a wrong decision is taken back.

## Decision

### The clusters

**A cluster is one event told by several publications.** The unit is the event, keyed by
`evidence_key` (the article, the step and the words), as in ADR 0032: the ids of events
and of groups change on every rebuild, the key does not.

**New tables** (`cases.storage.models`, owner `court-cases`, one migration):

- `event_clusters`: the cluster, its **anchor** (the event it was created for), the
  publication of the anchor, the step, the day, the people of the anchor
  (`entity_groups.key`), the status, the score, the reasons, the clusters the anchor
  matched but was not joined to, the algorithm version.
- `event_cluster_members`: one row per event, with the cluster it is in, its role (`core`,
  `member`, `candidate`), the status, the score, the reasons and the candidates. A row
  without a cluster is an event that joined nothing and has candidates only.

**Matching** (`cases.clusters.matching`, pure): a publication joins a cluster when it
matches the **anchor** directly, nothing contradicts, and the pair carries enough
independent evidence. A missing value counts neither for nor against.

**The person and the step are never enough.** The independent features (the court, the
body, the place, the article of the Criminal Code and its part, the very same words)
are counted: one of them is needed on the same day, two a day apart or with no day
at all. The very same words count as two. A pair without enough of them is a
**candidate**, never a join: two detentions of one person on one day that share
nothing else are two detentions, and a series of administrative arrests of one
activist must not be glued into one event. The score of a join runs from 0.67 (the
same person, the same step, no day, the article and the place) up; the threshold of
0.60 cuts nothing the rule allows. The rules are versioned: this decision is
`1.1.0` (`1.0.0` had no such condition and joined too much).

The contradictions forbid a join whatever the score:

* different people (a cluster is about one person);
* different steps (a cluster is one step);
* the same step on different days;
* the same step under two different articles of the Criminal Code;
* courts that have nothing in common.

**A chain never joins two publications.** A-B-C does not become one cluster: C is compared
with the anchor, not with B. Two clusters that match are an ambiguity: the event opens a
cluster of its own, the candidates are recorded, and nothing is merged.

**Candidates come from the database, not from the model.** The lookup takes the group, the
step, the window of days and the words of the text; the words are looked for through the
ingestion's public API (`ingestion.fulltext.articles_holding`), never in a table of another
package.

**The optional e5 signal** (`cases.clusters.embeddings`) is **off by default** and only
ranks: it adds a small part of the score and is never enough on its own. The vectors are
never written to the database; at most a cache file outside it keeps the vectors of the
quotes that were compared.

**This is not the vector retrieval ADR 0028 removed.** That one stored the vectors of
the publications in tables (`investigation_windows` and their vectors) and used pgvector to
search them; the retrieval was a read path of the investigations, and the tables were
dropped because nothing used them. Here there are no vector tables, no pgvector and no
retrieval: the e5 vectors are computed in the process, only for the pairs the rules have
already narrowed, and only as a tie-breaker of a ranking. Nothing in the database depends
on them, and the stage works with the signal off.

### The statuses and the rollback

Every link (a cluster's membership and a case's link) carries a status:

| status | meaning |
|---|---|
| `candidate` | the rules did not settle it: a person has to look at it. An article without a name is always a candidate. |
| `confirmed` | the rules joined it, and nobody has said otherwise. |
| `rejected` | it was rolled back. |

**A rollback deactivates the link and keeps the decision.** The row stays, with
`active = false`, the status `rejected`, and who decided, when and why
(`decided_by`, `decided_at`, `decision_note`). Nothing is deleted: the events, the groups
and the cases are untouched, and the next run does not undo the decision — a deactivated
link is left alone.

**This narrows ADR 0032**, which excluded the manual editing of the cases. A person may
now take one link back, with a reason, and the decision is kept with it. What is still
excluded: creating a case by hand, moving an event to another case, and merging two cases.

### The order of arrival

**The order of arrival matters between the batches** (as ADR 0032 already admits for the
cases): the first run of a batch of publications sees the world as it was then. Inside a
batch the events are ordered by the day of the event, so the same publications always give
the same clusters.

**A repeated run changes nothing**: an event that already has a membership is skipped, an
event processing has re-extracted is found again by its `evidence_key` and tied back to
its cluster, and no cluster id is given twice.

## Consequences

* The console can answer «what else was written about this event?», and the investigations
  can start from a cluster instead of from one publication.
* The clusters are conservative: the price of a missed join (a publication left in a
  cluster of its own) is much lower than the price of a wrong join (two independent
  events in one cluster, or two cases merged).
* A cluster is not a case and a case is not a cluster: the steps of one case are several
  clusters, and the case ties them together. The two levels answer different questions.
* The quality of the rules is **not measured**: there is no independent GOLD. The stage
  is judged by the scenarios of the task and by what it refuses to join.

## Alternatives

* **A ready-made clustering** (Taranis, DBSTREAM, TextClust): none is applicable here —
  see the experiment's report; the streaming ones join independent cases.
* **Embeddings as the main signal**: refused. The similarity of the words cannot tell one
  event from another (a namesake and a same-charge pair look alike), and the vectors would
  have to live somewhere, which ADR 0028 already settled.
* **A cluster per person**: refused. One person has many events, and the cluster is one
  event, not one person.
* **Transitive joining (A-B-C)**: refused. It is how two independent cases end up in one
  cluster.
* **Merging the clusters of one case into one**: refused. The steps of a case are
  different events; the case is the link between them, not their union.
