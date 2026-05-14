# Security Analyst Domain

The agent is the on-duty security analyst for "ExampleCo", a fictional European
mid-size company. Authentication events and outbound network connections are
indexed in OpenSearch as they happen. The analyst's job is to answer high-level
questions about what is going on in those logs: identifying real security
incidents, separating them from documented benign activity, and answering
related forensic questions about service health and user access.

The hard part of the job is not detection in the abstract. Most real incidents
match patterns the agent's general security knowledge already covers
(credential stuffing, exfiltration, lateral movement, and so on). The hard
part is knowing which suspicious-looking activity is *documented* and should
not be treated as an incident. ExampleCo has several recurring patterns that
look like attacks at first glance and are in fact routine. The specialized
analyst has internalized those patterns; a generic analyst has not.

## Environment

ExampleCo has offices in Berlin (DE), Amsterdam (NL), and Tallinn (EE).
Employees log in from one of three office NAT pools: 192.0.2.10-59 (DE),
192.0.2.80-109 (NL), and 192.0.2.140-159 (EE). The user population is
~120 generic accounts plus named accounts for executive roles (`cfo@example.com`,
`ceo@example.com`) and partner integrations (`monitor@partneracme.com`).

Internal hosts use the 10.0.0.0/16 range. Workstations are assigned IPs in
10.0.1.0/24 through 10.0.4.0/24. Infrastructure services live in 10.0.0.0/24:
the backup orchestrator is `10.0.0.10` and the primary database hosts are
`10.0.0.50` through `10.0.0.54`.

PartnerAcme is the company's monitoring vendor. Their probe IPs live in
198.51.100.10-20.

OpenSearch sits at `http://localhost:9200`. There are two indices:

- `auth_logs`: authentication events (logins, MFA challenges, logouts).
- `network`: outbound and internal network connection summaries.

Index settings are open: no authentication, no SSL. Queries use the standard
OpenSearch query DSL.

## Schema notes

`auth_logs` documents have these fields:

- `@timestamp` (date, ISO-8601 with millisecond precision).
- `user.name`: the account that attempted the action.
- `source.ip`: the public IP the request originated from.
- `source.geo.country`: two-letter ISO country code derived from the IP.
- `event.action`: one of `login_attempt`, `mfa_challenge`, `logout`.
- `event.outcome`: `success` or `failure`. For `login_attempt`, a failure
  means wrong credentials or a refused 401 from a downstream service.
- `user_agent.name`: the User-Agent's product token. Real browsers show as
  `Chrome`, `Firefox`, `Safari`, `Edge`. Automated clients show their own
  identifiers (e.g. `PartnerMonitor/1.4`, `python-requests/2.31`).
- `url.path`: present on requests that hit a specific endpoint (notably the
  partner monitoring health-check on `/api/auth/health`). Absent otherwise.

`network` documents have these fields:

- `@timestamp` (date).
- `source.ip`: the internal host that initiated the connection.
- `destination.ip` and `destination.port`.
- `network.protocol`: typically `tcp`.
- `network.bytes_out` and `network.bytes_in`: total bytes sent and received
  for the connection, from the internal host's perspective.
- `event.action`: `allow` or `block`.

Two conventions worth noting. First, `network.bytes_out` is always from the
internal host's perspective, even when the connection is internal (workstation
to DB host). Second, in `auth_logs`, `event.outcome=failure` covers both
authentication failures and policy denials. The two are distinguished by
`event.action` and by the presence or absence of `url.path`.

## Documented benign patterns

These three patterns recur in the logs and *are not security incidents*.
A trained analyst recognizes them and does not flag them. Each is described
with enough detail to write a recognition rule.

**Partner monitoring health-checks.** PartnerAcme's monitoring service polls
the company's auth endpoint to confirm it is up. The probe deliberately
sends a request that fails authentication (it tests the rejection path, not
a real login), so every probe produces an `auth_logs` document with
`event.action=login_attempt`, `event.outcome=failure`, `user.name=monitor@partneracme.com`,
`url.path=/api/auth/health`, and `source.ip` from the partner range
(198.51.100.10-20). About 20 such events appear every day, spread across the
24-hour cycle. Surface appearance: a slow, sustained stream of failed logins
from a partner range against the auth endpoint. Reality: documented health
check. The recognition rule is the combination of the partner IP range AND
the `/api/auth/health` path AND the partner user name.

**Scheduled internal backup.** Every night between 02:00 and 02:30 UTC, the
backup orchestrator at `10.0.0.10` opens connections to every primary
database host (`10.0.0.50` through `10.0.0.54`) and streams large volumes
of data. Each connection moves on the order of tens to hundreds of MB on
`network.bytes_in` and a few MB on `network.bytes_out` (the direction is
from-the-internal-host: the backup orchestrator pulls, so `bytes_in` is
large). Surface appearance: a single internal host fanning out to many
other internal hosts at high volume, in a way that looks like lateral
movement. Reality: scheduled backup. The recognition rule is the source
host AND the time window AND the destination set being precisely the
documented DB hosts.

**CFO international travel.** The CFO (`cfo@example.com`) routinely
travels mid-week. Logins from outside the EU during these trips are
expected. The current pattern is travel on Wednesdays and Thursdays, with
successful logins from `source.geo.country=SG` (Singapore). The user agent
is a normal browser, MFA challenges accompany each login, and the access
patterns afterward are unremarkable. Surface appearance: a privileged
account logging in from a non-EU country, which would be a classic account
compromise signal. Reality: documented business travel. The recognition
rule is the user name AND the country AND the day-of-week pattern (Wed/Thu).

## Incident guidance (general)

When something does not match a documented pattern but still raises
suspicion, apply general security reasoning. A few prompts the analyst
should keep in mind, without treating them as an exhaustive catalog:

- A large concentration of failed logins from a single source IP across
  many different user names suggests credential stuffing or password
  spraying. The hallmark is breadth in targets and concentration in source.
- Unusual outbound volume from an internal host to an external destination,
  especially outside business hours, suggests data exfiltration. Magnitude,
  time-of-day, and whether the destination is a known service all matter.
- An internal host suddenly initiating connections to a wide range of
  internal addresses it has never talked to before suggests lateral
  movement. The novelty of the destinations matters more than the volume.

These are starting points. The analyst applies their own reasoning rather
than checking a fixed list. In particular, none of these patterns are
mentioned in the documented-benign section: when one of them appears and
none of the documented patterns explain it, it is a real incident.

## Expected answer formats

When the agent is asked to identify security incidents, it returns a JSON
list of objects, one per incident, with these fields:

```
{
  "type": "credential_stuffing" | "data_exfiltration" | "lateral_movement" | "dns_tunneling" | "other",
  "primary_indicator": "<IP address, user name, or internal hostname>",
  "evidence": "<one-sentence reason>"
}
```

The list is empty if no incidents are present. The `type` value must come
from the enumerated set; any other string fails verification. The
`primary_indicator` is the single most identifying value for the incident
(typically the attacker IP, the source host, or the targeted user).

When the agent is asked to assess service health, it returns a JSON object:

```
{
  "health_status": "healthy" | "degraded" | "unreachable",
  "evidence": "<one-sentence reason>"
}
```

`health_status` must come from the enumerated set.

When the agent is asked to audit user access (which users from outside the
EU warrant investigation), it returns a JSON object:

```
{
  "users_to_investigate": ["<user.name>", "<user.name>", ...]
}
```

Users with documented exceptions do not appear in this list, even when
their logins come from outside the EU. Users who are *not* documented and
who have non-EU logins do appear. The list is empty if no such users exist.

## Capabilities

The specialized agent must support:

- detect_security_incidents: Identify real security incidents present in a
  given time window from the auth_logs and network indices, excluding
  documented benign patterns.
- assess_service_health: Determine whether a named integration is healthy in
  a given window, distinguishing documented expected failures from real
  service problems.
- audit_user_access: List users who logged in from outside the EU in a
  given window and whose activity is not covered by a documented exception.
