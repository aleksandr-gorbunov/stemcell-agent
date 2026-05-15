# Initial knowledge: ExampleCo Security Analyst domain

## What this domain is about
- I act as ExampleCo’s on-duty security analyst.
- I must answer high-level questions about activity in OpenSearch logs: detect real incidents, exclude documented benign patterns, and provide short structured outputs per task type.
- Data lives in three indices at http://localhost:9200 (no auth/SSL):
  - auth_logs: authentication events
  - network: outbound/internal connection summaries
  - dns: recursive resolver view of DNS queries from internal hosts

## Core entities and baselines
- Offices and NAT pools (public):
  - Berlin DE: 192.0.2.10–59
  - Amsterdam NL: 192.0.2.80–109
  - Tallinn EE: 192.0.2.140–159
- Internal networks:
  - All internal: 10.0.0.0/16
  - Workstations: 10.0.1.0/24 – 10.0.4.0/24
  - Infra: 10.0.0.0/24 key hosts:
    - DNS resolver 10.0.0.5 (forwards to 1.1.1.1, 8.8.8.8)
    - Backup orchestrator 10.0.0.10
    - Mail 10.0.0.20, File 10.0.0.30
    - CI/CD agent 10.0.0.40
    - DBs 10.0.0.50–54
    - App servers 10.0.0.60–62
- External partners/services:
  - PartnerAcme probes: 198.51.100.10–20
  - Authorized pen test: 198.51.100.50
  - Docker registry: 18.213.0.0/16 (CI/CD only)
  - GitHub: 140.82.112.0/20 (CI/CD only)
- Special accounts:
  - cfo@example.com, ceo@example.com, cto@example.com
  - monitor@partneracme.com (health checks)
  - sales_apac|americas|emea@example.com
  - build@example.com (CI/CD service)

## Index schemas (key fields)
- auth_logs: @timestamp, user.name, source.ip, source.geo.country, event.action (login_attempt|mfa_challenge|logout), event.outcome (success|failure), user_agent.name, url.path
- network: @timestamp, source.ip (internal), destination.ip/port, network.protocol, network.bytes_out/in (from source perspective), event.action (allow|block)
- dns: @timestamp, source.ip (internal), query.name, query.type, response.code

## Documented benign patterns (recognition rules)
1) Partner monitoring health-checks
- Expect ~20/day, any time.
- Must match ALL: source.ip in 198.51.100.10–20 AND user.name=monitor@partneracme.com AND url.path=/api/auth/health AND event.action=login_attempt AND event.outcome=failure.

2) Scheduled internal backup
- Nightly 02:00–02:30 UTC.
- Source 10.0.0.10 -> destinations 10.0.0.50–54; large bytes_in, small bytes_out.

3) CFO international travel
- cfo@example.com logins from SG, Wednesdays/Thursdays only, normal browser UA, with MFA challenges.

4) Regional sales team travel
- sales_apac: APAC countries {SG, JP, KR, AU, HK, IN, TW, TH, PH, ID, VN, MY}.
- sales_americas: Americas {US, CA, BR, MX, AR, CL, CO, PE, UY}.
- sales_emea: EU only; outside EMEA should be flagged.
- Recognition: user name + country within allowed region.

5) Authorized weekly pen test
- Sundays 14:00–16:00 UTC.
- Source IP 198.51.100.50.
- Pattern: 300–800 failed login_attempts across 50+ users. Outside window = suspicious.

6) CI/CD nightly build (weekdays only) 03:00–04:00 UTC
- Source 10.0.0.40; fans out to app (10.0.0.60–62) and DBs (10.0.0.50–54).
- External pulls from Docker (18.213.0.0/16) and GitHub (140.82.112.0/20).
- High in/out traffic during window. Same pattern from other hosts/times is not documented.

## Incident categories and signals (to use when not covered by benign rules)
- Credential stuffing: many failed login_attempts from one source IP across many user names; exclude Sunday pen test window/IP.
- Data exfiltration: unusual high bytes_out from an internal host to external IPs, esp. off-hours; exclude CI/CD window/host.
- Lateral movement: internal source talking to many new internal destinations; exclude backup (10.0.0.10 02:00–02:30) and CI/CD (10.0.0.40 03:00–04:00 weekdays).
- DNS tunneling: very high DNS volume from a host, often concentrated on one domain with high-entropy subdomains; TXT/NULL types suspicious; high NXDOMAIN ratios possible.
- Compromised account: unusual geo/IP successful login + follow-on network/DNS anomalies tied to that user/host.

## Correlation heuristics
- Tie workstation public IP seen in auth_logs (source.ip in 192.0.2.x) to internal host via network telemetry if present; otherwise, attribute by user.name and time proximity.
- Link unusual DNS lookups from host before unusual external network connections.
- For compromise chains: failed bursts -> later success for same user/IP -> new internal/external access -> data movement.

## Queries I will need (OpenSearch DSL)
- Time-bounded filters using range on @timestamp.
- Aggregations:
  - auth_logs: terms by source.ip with cardinality of user.name for failure bursts.
  - network: sum of bytes_out/by_source to spot exfil; cardinality of destination.ip for lateral movement; internal-only filters using prefix 10.
  - dns: terms by source.ip with total count and cardinality of query.name; optional wildcard on a domain.
- Exact-match term/terms for user.name, source.geo.country, url.path, event.action/outcome, source/destination IP ranges.

## Task outputs to prepare for
- detect_security_incidents: return list of incidents with type, primary_indicator (IP/user/host), one-sentence evidence excluding benign patterns.
- assess_service_health: partner_monitoring or ci_cd_pipeline status over a window.
- audit_user_access: users who logged in from outside EU without a documented exception (exclude CFO Wed/Thu SG and regional sales within their regions).
- detect_lateral_movement: same as incidents but only lateral_movement; indicator is internal source host.
- detect_dns_tunneling: only dns_tunneling; indicator is internal source host.
- investigate_compromised_account: given user, assess low/medium/high with short evidence chain.

## What to investigate/build next (in SELF_MODIFICATION)
- Create skills with small Python tools to:
  - issue OpenSearch POST /<index>/_search with JSON bodies and return parsed aggregations/hits.
  - reusable query builders for each documented benign rule (predicate functions) to filter/exclude during analysis.
  - helper: convert absolute UTC time windows and determine weekday/Sunday checks.
  - helpers to compute entropy/length stats of DNS subdomains (for tunneling assessment) from returned hits.
- Populate environment.yaml with:
  - opensearch.base_url: http://localhost:9200
- Write SKILL.md runbooks for each capability, including example DSL payloads from materials.
- Add a reference list of EU country codes to use in audit_user_access.

## Open questions / validations
- Confirm actual EU membership list used for audit (materials include a suggested set; I will codify it).
- Volume thresholds (e.g., what is “unusual” bytes_out) may need relative baselining per window; start with top-N by sum and investigate.
- How to map a user account to an internal host reliably when only public NAT IP appears in auth_logs—likely unnecessary; focus on per-user anomalies in auth_logs and per-host anomalies in network/dns.
