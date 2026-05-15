# Log field glossary

Field-level notes for `auth_logs`, `network`, and `dns`. Cross-reference the
`Schema notes` section in `DESCRIPTION.md` for the conventions, and the
`Documented benign patterns` section for cases where specific values matter.

## auth_logs

`@timestamp`
: ISO-8601 UTC with millisecond precision. Sortable as a string; range
  queries against it accept any prefix granularity (a date, a date and
  hour, or a full timestamp).

`user.name`
: Email-style account identifier. Generic employees follow the pattern
  `user<NNN>@example.com`. Named roles (`cfo@example.com`, `ceo@example.com`,
  `cto@example.com`), regional sales (`sales_apac@example.com`,
  `sales_americas@example.com`, `sales_emea@example.com`), partner
  integration (`monitor@partneracme.com`), and CI/CD service (`build@example.com`)
  are distinct from the generic pool.

`source.ip`
: The public IP the request came from. Office NAT pools are in 192.0.2.x.
  Partner monitoring traffic comes from 198.51.100.10-20. The authorized
  pen-test consultancy uses `198.51.100.50`. Anything else is either a
  documented exception (sales travel, CFO travel) or unknown and potentially
  adversarial.

`source.geo.country`
: ISO 3166-1 alpha-2. EU countries for ExampleCo offices are DE, NL, EE.
  EU countries more broadly include AT, BE, BG, HR, CY, CZ, DK, FI, FR, GR,
  HU, IE, IT, LV, LT, LU, MT, PL, PT, RO, SK, SI, SE. Non-EU countries on
  this field are always worth examining; whether they are "documented
  exception" or "potential incident" depends on the user, the region, and
  the day-of-week pattern.

`event.action`
: Three values: `login_attempt` (the user submitted credentials),
  `mfa_challenge` (a second-factor prompt was issued and answered), and
  `logout`. Health-check probes from the partner monitor appear as
  `login_attempt`. Pen-test traffic also presents as `login_attempt` with
  `event.outcome=failure` across many users.

`event.outcome`
: `success` or `failure`. Failures on `login_attempt` mean wrong credentials,
  a 401 from the auth endpoint, or rate-limiting. The distinction must be
  inferred from context (which account, which path, which IP, when).

`user_agent.name`
: Browser names indicate real user activity. `PartnerMonitor/<version>`
  identifies the documented partner probe. `python-requests`, `curl`,
  `Go-http-client`, and other non-browser strings indicate automation:
  expected for the partner probe and the pen test, suspicious otherwise.

`url.path`
: Present only on a subset of events. The notable case is
  `/api/auth/health` for the partner probe. Absent on ordinary user logins.

## network

`@timestamp`
: Same as in auth_logs.

`source.ip`
: The internal host that initiated the connection. All ExampleCo internal
  hosts are in 10.0.0.0/16. Workstations live in 10.0.1.0/24 through
  10.0.4.0/24. Infrastructure roles are in 10.0.0.0/24 (see DESCRIPTION).

`destination.ip`
: Either internal (10.0.0.0/16) or external. Common known external
  destinations: Docker registry (`18.213.0.0/16`), GitHub
  (`140.82.112.0/20`). Other external IPs warrant attention proportional to
  volume.

`destination.port`
: 443 for HTTPS to most external services. 80 for legacy HTTP. 5432 for
  PostgreSQL (the backup orchestrator pulls from DB hosts on this port).
  CI/CD deployment also uses 5432 against the DBs during migrations. Other
  ports are unusual.

`network.protocol`
: Typically `tcp`. UDP appears for DNS resolver traffic to upstream DNS
  servers and is documented.

`network.bytes_out`
: Bytes sent from `source.ip` to `destination.ip`. Always from the internal
  host's perspective. Large `bytes_out` with small `bytes_in` is the
  signature of a push (uploading or exfiltrating).

`network.bytes_in`
: Bytes returned to `source.ip`. Large `bytes_in` with small `bytes_out` is
  the signature of a pull. The backup orchestrator pulling from DB hosts
  shows large `bytes_in`; CI/CD pulling container images also shows large
  `bytes_in`.

`event.action`
: `allow` or `block`. Blocks are unusual; a burst of blocks from one source
  warrants examination.

## dns

`@timestamp`
: Same as in auth_logs.

`source.ip`
: The internal host that issued the query. The recursive resolver at
  `10.0.0.5` records each query as if originating from the host that asked,
  not from the resolver itself.

`query.name`
: The fully-qualified domain name being queried. Normal queries hit
  recognizable hostnames (`github.com`, `registry-1.docker.io`,
  `update.microsoft.com`, etc.). Tunneling presents as queries to a single
  domain with random-looking subdomains, e.g.
  `axc8z2k9p1.tunnel.example.org`.

`query.type`
: Standard DNS record types. `A` and `AAAA` are address lookups (the bulk of
  normal traffic). `TXT` and `NULL` types are sometimes used for data
  exfiltration via DNS because they carry arbitrary payloads.

`response.code`
: `NOERROR` (resolved), `NXDOMAIN` (no such name), `SERVFAIL` (resolver
  problem). A spike in `NXDOMAIN` for queries against a single domain is
  consistent with DNS-based command-and-control or beaconing.

## Cross-index correlation

Many incidents become visible only when fields are correlated across indices.
A few useful joins:

- `auth_logs.user.name + auth_logs.source.ip` and `network.source.ip`: when
  a workstation IP can be tied to a user via authentication events, network
  activity from that IP can be attributed to the user.
- `network.source.ip + network.destination.ip + dns.source.ip + dns.query.name`:
  unusual destinations a host visited often appear in DNS first (the host
  resolves a name before connecting).
- `auth_logs (failure burst) + auth_logs (later success) + network (unusual
  activity)`: the canonical compromised-account chain.
