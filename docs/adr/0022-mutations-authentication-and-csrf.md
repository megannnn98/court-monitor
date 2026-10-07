# ADR 0022: Authentication and CSRF for actions in the console

## Status

Accepted, 2026-10-07: option A, implemented in `web/csrf.py`. Option C stays the plan for
when the console has to be reachable without an SSH tunnel; the open questions below
decide it then. Unblocks moving the console's actions («Работа», «обработано», Excel
exports, starting and stopping steps, manual decisions) to the React console and
`/api/v1`.

## Context

The API has no authentication, authorization or CSRF protection. ADR 0014 binds every
published port to `127.0.0.1` and says the API "must not be exposed beyond the host
except through a reverse proxy that adds them"; that is a deployment constraint, not a
solved problem. The server deployment (`var/deploy-irina.sh`) keeps the same binding.

**The legacy UI is already exposed to cross-site request forgery.** It has 36 POST
routes, all plain HTML forms (`application/x-www-form-urlencoded`), with no CSRF token
and no `Origin` check. The absence of CORS does not protect them: a cross-site form POST
is a "simple request", sent without a preflight; the attacker cannot read the answer,
but the side effect happens. Any page the operator opens in the same browser can submit
a form to `http://127.0.0.1:8001/ui/management/purge` (irreversible deletion of junk
publications), `/ui/management/run` (paid model steps), `/ui/airtable/sync`, the
decisions of the review pages, and so on. Binding to `127.0.0.1` keeps other machines
out; it does not keep out the operator's own browser. Chrome's Private Network Access
checks narrow this for some requests, Firefox and Safari do not; it cannot be relied on.

The React console adds a second client of the same actions. Whatever is chosen must
cover both: the legacy forms stay until each page is moved.

Facts that shape the choice:
- one or two operators, no roles: everyone who can open the console may do everything;
- access today is local (`127.0.0.1:8001`) or to the server through SSH
  (ASSUMPTION: a tunnel, `ssh -L 8001:127.0.0.1:8001`; to be confirmed by the owner);
- the React build is served by Vite in development; in production it is not served yet
  (a separate decision: from FastAPI on the same origin, or from the reverse proxy).

## Options

### A. Same-origin check on every unsafe request (no login)

A middleware rejects `POST`, `PUT`, `PATCH`, `DELETE` unless the request comes from the
console's own origin: `Sec-Fetch-Site: same-origin`, or, when a browser does not send
it, an `Origin` (else `Referer`) that is in an allowlist (`http://127.0.0.1:8001`,
`http://localhost:8001`, the Vite dev origin, the server's public origin if any). A
request with none of the three headers (curl, scripts) is let through: CSRF needs a
browser, and the CLI does not go through HTTP.

- Closes the existing CSRF hole for the legacy forms and the React console at once, with
  no change to either: browsers send these headers on their own.
- Authentication stays where it is now: whoever reaches the port (localhost, SSH
  tunnel) is trusted.
- Cost: about a day: the middleware, its allowlist setting, tests for each header case.
- Risk: a misconfigured allowlist refuses the operator's own forms; the error must say
  which origin was refused.

### B. A + login with a session cookie and a CSRF token

A login page (one shared password or a user per operator, hashed in the database), a
session cookie (`HttpOnly`, `Secure` behind TLS, `SameSite=Lax`), and a synchronizer CSRF
token in every legacy form and in a header from React.

- Needed once the console is reachable by anyone who should not use it, without a tunnel.
- Cost: three to five days, most of it threading the token through 36 legacy forms.
- Adds state to keep (sessions, password reset) for one or two people.

### C. A + authentication at a reverse proxy

Caddy or nginx in front of the API with basic auth or an OAuth2 proxy, TLS included;
the application keeps A for CSRF and trusts whoever the proxy lets in.

- No login code in the application; the proxy is configuration.
- Cost: about a day of infrastructure on the server; nothing changes locally.
- Basic auth is shared by everyone and cannot be logged out of; an OAuth2 proxy needs an
  identity provider.

### D. A bearer token in a header

React sends `Authorization: Bearer …`; a cross-site form cannot set a header, so CSRF is
impossible by construction.

- Does not fit the legacy forms, which cannot send a header: they would stay exposed or
  need B anyway.
- The token has to live in the browser (`localStorage`), readable by any script that
  gets onto the page.

## Recommendation

**A now, C when the console has to be reachable without an SSH tunnel.** A closes the
hole that exists today, unblocks the React actions under the same rule, and costs a day;
it does not pretend to be authentication. C adds authentication without login code when
it is actually needed. B is worth its cost only if operators need separate accounts.
D does not cover the legacy forms.

Regardless of the option, React's mutating calls go through one helper that sends
`Content-Type: application/json`: a cross-site form cannot send JSON without a
preflight, which adds a second line of defence for the new routes.

## Consequences

- A request is served when its method is safe, when `Sec-Fetch-Site` is `same-origin`,
  when its `Origin` (else the origin of its `Referer`) has the host the request came to
  (`Host`), or when that origin is in `ALLOWED_ORIGINS`; a request with none of the three
  headers is not a browser's and passes. The own-host rule covers an SSH tunnel on any
  local port and the Vite proxy without configuration.
- `ALLOWED_ORIGINS` (comma-separated) replaces the default list
  (`http://127.0.0.1:8001`, `http://localhost:8001`, `http://127.0.0.1:5173`,
  `http://localhost:5173`). A reverse proxy that rewrites `Host` needs its public origin
  there.
- A refused request gets 403 with the app's error body (`error.code` `csrf_refused`,
  `error.message` `cross-origin request refused: <origin>`, the request id), before the
  route runs, and is logged as `event=csrf_refused` with the method, path and origin.
  The React client shows `error.message`.
- Checked in a real browser (headless Chromium): a form auto-submitted from a page on
  another port got 403; the same form from the console's own page reached the route.
- Tests: same-origin form, foreign `Origin`, foreign `Referer` without `Origin`,
  `Sec-Fetch-Site: cross-site`, no headers at all (allowed), each legacy POST route
  still answering from its own page.
- Then the first actions move to `/api/v1` (`POST`), starting with «обработано» on
  «Результат».

## Open questions for the owner

1. How does the operator reach the server's console today: SSH tunnel, VPN, or a public
   address?
2. Will anyone need the console without a tunnel in the next months (then plan C)?
3. Is one shared identity enough, or must the console know which operator decided what
   (then B, or C with an OAuth2 proxy)?
