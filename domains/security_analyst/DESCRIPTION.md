# Security Analyst Domain

The agent is the on-duty security analyst for "ExampleCo", a fictional European
mid-size company. Authentication events, outbound network connections, and DNS
queries are indexed in OpenSearch as they happen. The analyst's job is to
answer high-level questions about what is going on in those logs: identifying
real security incidents, separating them from documented benign activity,
auditing user access, and investigating suspected account compromise.

The hard part of the job is not detection in the abstract. Most real incidents
match patterns the agent's general security knowledge already covers
(credential stuffing, exfiltration, lateral movement, DNS tunneling, account
compromise). The hard part is knowing which suspicious-looking activity is
*documented* and should not be treated as an incident, and following enough
context to give a useful answer instead of just a single-signal alarm. The
specialized analyst has internalized ExampleCo's documented patterns and its
operational baselines; a generic analyst has not.

## Environment

ExampleCo has offices in Berlin (DE), Amsterdam (NL), and Tallinn (EE).
Employees log in from one of three office NAT pools: 192.0.2.10-59 (DE),
192.0.2.80-109 (NL), and 192.0.2.140-159 (EE). The user population is ~120
generic accounts plus named accounts.

Named accounts and what they are:

- `cfo@example.com`: Chief Financial Officer. Documented travel pattern below.
- `ceo@example.com`: Chief Executive Officer. Travels rarely, always with advance notice.
- `cto@example.com`: Chief Technology Officer. Mostly office-based.
- `monitor@partneracme.com`: Partner monitoring service account. Documented pattern below.
- `sales_apac@example.com`, `sales_americas@example.com`, `sales_emea@example.com`:
  Regional sales team. Constantly traveling. Documented pattern below.
- `build@example.com`: CI/CD service account. Documented pattern below.

Internal hosts use the 10.0.0.0/16 range. Workstations are assigned IPs in
10.0.1.0/24 through 10.0.4.0/24. Infrastructure services live in 10.0.0.0/24:

- DNS resolver: `10.0.0.5`. Forwards external lookups to `1.1.1.1` and `8.8.8.8` only.
- Backup orchestrator: `10.0.0.10`
- Mail server: `10.0.0.20`
- File server: `10.0.0.30`
- CI/CD agent: `10.0.0.40`
- Primary database hosts: `10.0.0.50` through `10.0.0.54`
- Application servers: `10.0.0.60` through `10.0.0.62`

External partners and services:

- PartnerAcme monitoring probes: `198.51.100.10-20`
- Authorized pen-test consultancy: `198.51.100.50` (single IP)
- Docker registry public IPs: `18.213.0.0/16` (used by CI/CD only)
- GitHub public IPs: `140.82.112.0/20` (used by CI/CD only)

OpenSearch sits at `http://localhost:9200`. There are three indices:

- `auth_logs`: authentication events (logins, MFA challenges, logouts).
- `network`: outbound and internal network connection summaries.
- `dns`: DNS queries made by internal hosts.

Index settings are open: no authentication, no SSL. Queries use the standard
OpenSearch query DSL.

## Schema notes

`auth_logs` documents:

- `@timestamp` (date, ISO-8601 with millisecond precision)
- `user.name`: the account that attempted the action
- `source.ip`: the public IP the request originated from
- `source.geo.country`: two-letter ISO country code derived from the IP
- `event.action`: one of `login_attempt`, `mfa_challenge`, `logout`
- `event.outcome`: `success` or `failure`
- `user_agent.name`: the User-Agent's product token (e.g. `Chrome`, `Firefox`,
  `PartnerMonitor/1.4`, `python-requests/2.31`)
- `url.path`: present on requests that hit a specific endpoint (notably the
  partner monitoring health-check on `/api/auth/health`)

`network` documents:

- `@timestamp` (date)
- `source.ip`: the internal host that initiated the connection
- `destination.ip` and `destination.port`
- `network.protocol`: typically `tcp`
- `network.bytes_out` and `network.bytes_in`: total bytes sent and received
  for the connection, from the internal host's perspective
- `event.action`: `allow` or `block`

`dns` documents:

- `@timestamp` (date)
- `source.ip`: the internal host that issued the query
- `query.name`: the fully-qualified domain name being queried
- `query.type`: `A`, `AAAA`, `TXT`, `CNAME`, `MX`, `NULL`
- `response.code`: `NOERROR`, `NXDOMAIN`, `SERVFAIL`

Conventions worth noting. First, `network.bytes_out` is always from the
internal host's perspective, even when the connection is internal (workstation
to DB host). Second, in `auth_logs`, `event.outcome=failure` covers both
authentication failures and policy denials; the two are distinguished by
`event.action` and by the presence or absence of `url.path`. Third, the `dns`
index records the recursive resolver's view: queries appear as if originating
from the internal host that asked, not from the resolver itself.

## Documented benign patterns

These six patterns recur in the logs and *are not security incidents*. A
trained analyst recognizes them and does not flag them. Each is described with
enough detail to write a recognition rule.

### Partner monitoring health-checks

PartnerAcme's monitoring service polls the company's auth endpoint to confirm
it is up. The probe deliberately sends a request that fails authentication, so
every probe produces an `auth_logs` document with `event.action=login_attempt`,
`event.outcome=failure`, `user.name=monitor@partneracme.com`,
`url.path=/api/auth/health`, and `source.ip` from the partner range
(198.51.100.10-20). About 20 such events appear every day, spread across the
24-hour cycle.

Recognition rule: the combination of the partner IP range AND the
`/api/auth/health` path AND the partner user name.

### Scheduled internal backup

Every night between 02:00 and 02:30 UTC, the backup orchestrator at
`10.0.0.10` opens connections to every primary database host (`10.0.0.50`
through `10.0.0.54`) and streams large volumes of data. Each connection moves
tens to hundreds of MB on `network.bytes_in` and a few MB on `network.bytes_out`
(the backup pulls, so `bytes_in` is large).

Recognition rule: source host `10.0.0.10` AND time window 02:00-02:30 UTC AND
the destination set being precisely the documented DB hosts.

### CFO international travel

The CFO (`cfo@example.com`) routinely travels mid-week. Logins from outside
the EU during these trips are expected. The current pattern is travel on
Wednesdays and Thursdays, with successful logins from
`source.geo.country=SG`. The user agent is a normal browser, MFA challenges
accompany each login.

Recognition rule: user name `cfo@example.com` AND country `SG` AND day-of-week
in {Wednesday, Thursday}.

### Regional sales team travel

Three sales accounts travel constantly. Their non-EU activity is expected and
should not be flagged, but the expected geography differs per account:

- `sales_apac@example.com`: logins from any APAC country (SG, JP, KR, AU, HK,
  IN, TW, TH, PH, ID, VN, MY) at any time, any weekday.
- `sales_americas@example.com`: logins from any Americas country (US, CA, BR,
  MX, AR, CL, CO, PE, UY) at any time, any weekday.
- `sales_emea@example.com`: covers EMEA only. Logins from any EU country are
  fine. A login from outside EMEA is NOT documented and should be flagged.

Recognition rule: the user name + a country in their documented region. A
`sales_apac` login from the US, or a `sales_emea` login from APAC, is *not*
covered by this exception.

### Authorized weekly pen test

Every Sunday between 14:00 and 16:00 UTC, the authorized security consultancy
runs an external pen test from `198.51.100.50`. The test produces a burst of
failed login attempts across many user accounts (typically 300-800 failures
hitting 50+ distinct users in the window). This looks identical to a
credential-stuffing attack at first glance.

Recognition rule: source IP `198.51.100.50` AND time window Sunday 14:00-16:00
UTC. Outside that window, traffic from this IP is *not* documented and should
be treated as suspicious.

### CI/CD nightly build

The CI/CD agent at `10.0.0.40` runs scheduled jobs every weekday between 03:00
and 04:00 UTC. During this window it:

- Fans out internally to deploy to application servers (`10.0.0.60`-`62`) and
  to apply database migrations (`10.0.0.50`-`54`)
- Pulls container images from Docker registry (`18.213.0.0/16` range)
- Pulls source from GitHub (`140.82.112.0/20` range)
- Generates high outbound and inbound traffic during the window

Recognition rule: source host `10.0.0.40` AND time window weekday 03:00-04:00
UTC. Outside this window or from a different source, the same traffic
patterns are *not* documented.

## Incident guidance (general)

When something does not match a documented pattern, apply general security
reasoning. The incident categories that arise in this environment:

- **Credential stuffing / password spraying**: many failed login attempts from
  a single source IP across many user names. The hallmark is breadth in
  targets and concentration in source. Distinguish from the authorized pen
  test by checking IP and timing.
- **Data exfiltration**: unusual outbound volume from an internal host to an
  external destination, especially outside business hours. Magnitude,
  time-of-day, and whether the destination is documented all matter.
  Distinguish from CI/CD by source host and timing.
- **Lateral movement**: an internal host suddenly initiating connections to
  many internal addresses it has never talked to before, or to a fan-out of
  destinations broader than its normal access pattern. Distinguish from the
  backup orchestrator and the CI/CD agent during their documented windows.
- **DNS tunneling**: an internal host making an unusual volume of DNS queries,
  or queries with high-entropy subdomain labels indicating data encoded in
  the query name. Normal hosts make on the order of a few dozen to a couple
  hundred DNS queries per day. Tunneling presents as thousands of queries to
  a single domain with random-looking subdomains.
- **Compromised account**: a chain of events affecting one user account: a
  successful login from a previously-unseen geography or IP, followed by
  activity that deviates from the user's baseline (accessing hosts they
  don't normally touch, transferring data out, attempting privilege
  escalation). A single anomalous login alone is not compromise; the chain is.

These are starting points, not an exhaustive catalog. The analyst applies
their own reasoning. When one of these patterns appears and no documented
exception explains it, it is a real incident.

## Expected answer formats

### detect_security_incidents

Returns a JSON list, one object per incident, possibly empty:

```
[
  {
    "type": "credential_stuffing" | "data_exfiltration" | "lateral_movement" | "dns_tunneling" | "compromised_account" | "other",
    "primary_indicator": "<IP address, user name, or internal hostname>",
    "evidence": "<one-sentence reason>"
  }
]
```

There may be zero, one, or several real incidents in a window. The list is
empty if nothing is happening.

### assess_service_health

Returns a JSON object:

```
{
  "health_status": "healthy" | "degraded" | "unreachable",
  "evidence": "<one-sentence reason>"
}
```

The integration to assess is named in the task instruction (`partner_monitoring`
or `ci_cd_pipeline`).

- `partner_monitoring`: healthy when the documented probe pattern is present
  at expected volume. Degraded if the probe is failing differently than
  documented or if the probe count is far below expectations. Unreachable if
  no probe activity at all.
- `ci_cd_pipeline`: healthy when the documented nightly window shows the
  expected deployment + registry + GitHub traffic. Degraded if the window is
  truncated or partial (e.g. some weekdays missing). Unreachable if no
  CI/CD activity in the period.

### audit_user_access

Returns a JSON object:

```
{
  "users_to_investigate": ["<user.name>", ...]
}
```

Users with documented exceptions (CFO travel pattern, regional sales team
within their documented region) do not appear in this list. Users who logged
in from outside the EU without a documented exception covering that
specific combination of user + country *do* appear.

### detect_lateral_movement

Same shape as `detect_security_incidents` but the list contains only
`lateral_movement` typed entries (possibly empty). The `primary_indicator`
is the internal source host.

### detect_dns_tunneling

Same shape as `detect_security_incidents` but the list contains only
`dns_tunneling` typed entries (possibly empty). The `primary_indicator` is
the internal source host.

### investigate_compromised_account

Returns a JSON object:

```
{
  "user": "<user.name>",
  "compromise_likelihood": "low" | "medium" | "high",
  "evidence": ["<short statement>", "<short statement>", ...]
}
```

The `user` field echoes the user the task asked about. `compromise_likelihood`
reflects the analyst's assessment based on the chain of evidence (a single
unusual login is "low" or "medium" without further activity; a chain of
unusual login + abnormal host access + data movement is "high"). The
`evidence` list contains short factual statements derived from the logs
(unusual login IP/country, hosts accessed that aren't in the user's pattern,
volumes of data moved, etc.). The verifier checks that key substrings from
the expected evidence are present.

## Capabilities

The specialized agent must support:

- detect_security_incidents: Identify real security incidents present in a
  given time window across the auth_logs, network, and dns indices,
  excluding documented benign patterns. May find zero, one, or several
  incidents per window.
- assess_service_health: Determine whether a named integration is healthy in
  a given window, distinguishing documented expected behaviour from real
  service problems.
- audit_user_access: List users who logged in from outside the EU in a given
  window and whose activity is not covered by a documented exception.
- detect_lateral_movement: Identify internal hosts whose connection pattern
  in a given window shows unusual fan-out to internal destinations they do
  not normally touch, excluding documented infrastructure activity.
- detect_dns_tunneling: Identify internal hosts whose DNS query pattern in a
  given window indicates tunneling (high volume to one domain with
  random-looking subdomains).
- investigate_compromised_account: Given a user name and a window, return an
  assessment of whether that account shows signs of compromise, with a
  likelihood judgement and a chain of evidence drawn from the logs.
