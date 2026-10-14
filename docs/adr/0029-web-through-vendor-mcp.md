# ADR 0029: The open web is reached over its vendor's own MCP server

## Status

Accepted, 2026-10-14. Amends [ADR 0027](0027-investigation-runtime-scenarios-and-retrieval.md),
whose «a source outside the database» said Tavily's REST API would be one more
`SourceSearch`.

## Context

ADR 0027 added the open web as a `SourceSearch` beside the database and Telegram's public
channels. It was reached over Tavily's REST API: one address, the words in the body, a
bearer header, and a page of results whose own text came back in the answer.

Three things had changed by the time this decision was taken.

**The transport.** The vendor now publishes an official MCP server for the same search, and
Court Monitor is an MCP *client* of it. Nothing about the abstraction changes: the object is
still a `SourceSearch`, the retriever is still `web:tavily`, the planner and the scenarios
still cannot tell what it is. What changes is what the server publishes.

**What the server publishes.** Measured against the live server on 2026-10-14
(`tavily-mcp` 4.0.4, protocol `2025-11-25`): the tools are `tavily_search`,
`tavily_extract`, `tavily_crawl`, `tavily_map` and `tavily_research`. `tavily_search` takes
`query`, `max_results`, `search_depth`, `topic`, `time_range`, the `include_*` family,
`include_domains`, `exclude_domains`, `country`, `start_date`, `end_date` and
`exact_match` — and neither `include_published_date` nor `include_usage`. A result therefore
carries no publication date and the answer reports no credits. Both losses are real and
neither is worked around.

**What one search exchanges.** Measured on the same live session:

```
POST 200  mcp-session-id=<issued>     initialize
GET  405                               the SSE stream — this server answers in JSON
POST 202                               notifications/initialized
POST 200 / 200                        tools/list, tools/call
DELETE 405                             terminate_session — this server does not end it
```

Both the 405 of the stream and the 405 of the DELETE arrive *after* the call. A client that
takes the status of the last response it saw would read 200 where a rate-limited call got
429, and would never retry. That is the whole reason the status is read off the HTTP client
rather than off the exception.

## Decision

**Court Monitor speaks MCP to the vendor's own server and no longer calls its REST API.**
The endpoint is the constant `https://mcp.tavily.com/mcp`; the key travels in an
`Authorization: Bearer` header and nowhere else — not in the address, not in a log, not in
a `repr`, not in an exception, not in a stored run.

**The tool and its arguments are read from what the server publishes**, from
`list_tools()` on every search, and a parameter its schema does not list is not sent. The
schema of a vendor is not remembered in our code; it is asked for.

**A session is opened and closed inside the one search that uses it.** The SDK's transport
and `ClientSession` each hold an anyio task group, and the graph runs every search in a task
of its own, so a session cannot be entered in one and left in another. Reusing a session
across searches is possible — a call may be made from any task — but needs a task of its
own to own it, an explicit shutdown and a reconnection after the server drops the session.
It buys one handshake and one tool listing, measured at about 0.8 s of a 2–3 s search. That
is not taken here; it is the first thing to reconsider if the search gets slower.

**A rate limit is retried, and a refusal is not.** The HTTP client of a search records the
status of a failed POST and nothing else; 408, 409, 425, 429 and 5xx are asked again once,
and a 401 or a 403 is not. The status is the only thing that tells them apart: the SDK folds
every HTTP failure of the server into one JSON-RPC error and keeps the status to itself.

**A search is bounded even when the server goes quiet.** The SDK's POST task swallows a
dropped connection and leaves the request unanswered, so every session carries a read
timeout — the bound the run itself gives one search.

**Web text is data, never instruction.** A page that asks for another article in its own
words has still only the article it lays at that person's door.

**A lead is not a fact.** A charge lead from outside the database names a text worth
reading and nothing more; it does not close the follow-up for that fact until a grounded
extractor has confirmed a charge from it. A charge the database's own search found keeps
closing the follow-up as it always did.

## Consequences

- The vendor's MCP server is a production dependency. It is reached over the network from
  the API container, and the SDK is in the image.
- Web results have no publication date. The window of dates a query carries is delegated to
  the server (`start_date`/`end_date`), and a result that comes back without a date stays
  undated evidence rather than being dropped — a policy, not an accident, and it is what
  `evidence_rules._rank` sorts by.
- The run reports what the search cost in requests and results, priced by the depth it
  asked for. It cannot report credits the server did not send.
- The depth of the search is a setting (`INVESTIGATION_WEB_SEARCH_DEPTH`, `basic` by
  default). Measured on the hand-annotated set, `advanced` buys one more retrieved fact out
  of fifteen, changes no confirmed fact, costs twice the credits and adds 79% to the
  latency of a dossier. `basic` stays the default; the numbers are in the PR description.
- Anything that reads a vendor's schema is written against what that vendor published on
  the day it was measured, and the measurement is a live test rather than a comment.

## Alternatives

**Keep REST.** Rejected on the same measurement: the REST API returns `raw_content` for
about 7.5% of results at `basic` depth through the MCP server and `published_date` for
none. The REST path is not obviously better on retrieval, and it is a second protocol to
keep alive.

**Keep both.** Rejected: one vendor, two transports, and the abstraction would have to
carry the union of two schemas.

**A pooled session.** Deferred, with the measurement that would justify it.
