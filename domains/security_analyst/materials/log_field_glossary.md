# Log field glossary

Field-level notes for `auth_logs` and `network`. Cross-reference the
`Schema notes` section in `DESCRIPTION.md` for the conventions, and the
`Documented benign patterns` section for cases where specific values matter.

## auth_logs

`@timestamp`
: ISO-8601 UTC with millisecond precision. Sortable as a string; range
  queries against it accept any prefix granularity (a date, a date and
  hour, or a full timestamp).

`user.name`
: Email-style account identifier. Generic employees follow the pattern
  `user<NNN>@example.com`. Named roles (`cfo@example.com`, `ceo@example.com`)
  and partner integrations (`monitor@partneracme.com`) are distinct from
  the generic pool.

`source.ip`
: The public IP the request came from. Office NAT pools are in 192.0.2.x.
  Partner monitoring traffic comes from 198.51.100.10-20. Anything outside
  these is either a documented exception (CFO travel uses
  `203.0.113.30` from SG) or unknown and potentially adversarial.

`source.geo.country`
: ISO 3166-1 alpha-2. EU countries for ExampleCo are DE, NL, EE. Non-EU
  countries on this field are always worth examining; whether they are
  "documented exception" or "potential incident" depends on the user and
  the day-of-week pattern (see DESCRIPTION).

`event.action`
: Three values: `login_attempt` (the user submitted credentials),
  `mfa_challenge` (a second-factor prompt was issued and answered), and
  `logout`. Health-check probes from the partner monitor appear as
  `login_attempt` because the probe really does try to authenticate (and
  is expected to fail).

`event.outcome`
: `success` or `failure`. Failures on `login_attempt` mean either wrong
  credentials, or a 401 from the auth endpoint (the partner probe always
  produces this), or rate-limiting. The distinction between these cases is
  not in the data directly; the analyst must infer from context (which
  account, which path, which IP).

`user_agent.name`
: Browser names indicate real user activity. `PartnerMonitor/<version>`
  identifies the documented partner probe. `python-requests`, `curl`,
  `Go-http-client`, and other non-browser strings indicate automation,
  which is fine for some flows (the partner probe is one) and suspicious
  for others (rapid failed logins under any of these).

`url.path`
: Present only on a subset of events. The notable case is
  `/api/auth/health` for the partner probe. Absent on ordinary user
  logins (they hit the standard `/api/login`-equivalent flow which is not
  recorded as a separate path).

## network

`@timestamp`
: Same as in auth_logs.

`source.ip`
: The internal host that initiated the connection. All ExampleCo internal
  hosts are in 10.0.0.0/16. The backup orchestrator is `10.0.0.10`;
  workstations live in 10.0.1.0/24 through 10.0.4.0/24.

`destination.ip`
: Either internal (10.0.0.0/16) or external. Common known external
  destinations are in 203.0.113.0/24; partner destinations are in
  198.51.100.0/24. Anything else is unknown and warrants attention
  proportional to volume.

`destination.port`
: 443 for HTTPS to most known external services. 80 for legacy HTTP. 5432
  for PostgreSQL traffic (used by the backup orchestrator pulling from DB
  hosts). Other ports are unusual and worth a closer look.

`network.protocol`
: Typically `tcp`. UDP would be unusual for the traffic patterns we model
  here.

`network.bytes_out`
: Bytes sent from `source.ip` to `destination.ip`. The "outbound" direction
  is always from the perspective of the internal host. For the backup
  orchestrator pulling from DB hosts, the *interesting* direction is
  `bytes_in` (the DB sending data back to the orchestrator), not
  `bytes_out`.

`network.bytes_in`
: Bytes returned from `destination.ip` to `source.ip`. Large `bytes_in`
  with small `bytes_out` is the signature of a pull (downloading data).
  Large `bytes_out` with small `bytes_in` is the signature of a push
  (uploading or exfiltrating data).

`event.action`
: `allow` (the connection was permitted) or `block` (the firewall denied
  it). Blocks are unusual; a sudden burst of blocks from one source is
  worth examining even if no allows accompany them.
