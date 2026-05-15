# Security Analyst agent — initial understanding

This domain models an on-duty security analyst for ExampleCo, answering high-level questions from live-ish telemetry stored in OpenSearch. The work is both detection and adjudication: separating true incidents from documented benign patterns that regularly appear in the data.

## Environment sketch
- Offices: Berlin (DE), Amsterdam (NL), Tallinn (EE); office NAT pools: DE 192.0.2.10–59, NL 192.0.2.80–109, EE 192.0.2.140–159.
- Internal addressing: 10.0.0.0/16. Workstations 10.0.1.0/24–10.0.4.0/24. Infra in 10.0.0.0/24: DNS 10.0.0.5, Backup 10.0.0.10, Mail 10.0.0.20, File 10.0.0.30, CI/CD 10.0.0.40, DBs 10.0.0.50–54, App 10.0.0.60–62.
- External partners/services: PartnerAcme probes 198.51.100.10–20; authorized pen-test 198.51.100.50; Docker registry 18.213.0.0/16; GitHub 140.82.112.0/20.
- OpenSearch: http://localhost:9200. Indices: auth_logs, network, dns. No auth/SSL.

## Index schemas (key fields)
- auth_logs: @timestamp, user.name, source.ip, source.geo.country, event.action {login_attempt|mfa_challenge|logout}, event.outcome {success|failure}, user_agent.name, url.path.
- network: @timestamp, source.ip (internal), destination.ip/port, network.protocol, network.bytes_out/in (from source host perspective), event.action {allow|block}.
- dns: @timestamp, source.ip (true origin host), dns.question.name (keyword), dns.question.type, dns.response.code.

Conventions: bytes_out/in are from the internal host view; dns shows the querying host; failures in auth_logs can be true auth failures or policy blocks; url.path is present on certain requests (notably /api/auth/health).

## Documented benign patterns to suppress
1) Partner monitoring health-checks: user monitor@partneracme.com + url.path=/api/auth/health + source.ip in 198.51.100.10–20; event.action=login_attempt; outcome=failure; ~20/day, 24h spread.
2) Scheduled internal backup: 10.0.0.10 -> 10.0.0.50–54 between 02:00–02:30 UTC; large bytes_in, small bytes_out.
3) CFO international travel: cfo@example.com from SG on Wednesdays/Thursdays; normal browser; MFA present.
4) Regional sales travel: sales_apac (APAC countries), sales_americas (Americas), sales_emea (EU only; outside EMEA is not documented).
5) Authorized weekly pen test: Sundays 14:00–16:00 UTC from 198.51.100.50; many failed login_attempts across many users.
6) CI/CD nightly build: 10.0.0.40 on weekdays 03:00–04:00 UTC; internal fan-out to app+DBs; pulls from Docker/GitHub; high traffic.

These recognition rules must be applied before incident verdicts.

## Incident categories and typical signals
- Credential stuffing: many failures from one external IP across many users; distinguish from Sunday pen test by IP+time.
- Data exfiltration: unusual high bytes_out from an internal host to external IPs, esp. off-hours; exclude CI/CD.
- Lateral movement: sudden broad internal fan-out to many internal destinations from one source; exclude backup/CI windows and known infra roles.
- DNS tunneling: very high DNS volume from a host, many unique high-entropy subdomains to one domain; TXT/NULL usage can strengthen signal.
- Compromised account: chain for one user — unusual geo/IP success + MFA context + unusual host access and/or data movement.

## Expected tasks / outputs
- detect_security_incidents: list of incidents with type, primary_indicator, one-line evidence.
- assess_service_health: partner_monitoring or ci_cd_pipeline — healthy/degraded/unreachable with evidence.
- audit_user_access: users who logged in from outside EU without a documented exception.
- detect_lateral_movement: same shape as incidents but only lateral_movement, keyed by internal source host.
- detect_dns_tunneling: only dns_tunneling, keyed by internal source host.
- investigate_compromised_account: per-user likelihood and short evidence bullets.

## Heuristics and pitfalls (added after train_dns_01 failure)
- When aggregating DNS names, use the bucket doc_count to measure total query volume, not the number of unique FQDNs. Thresholds (e.g., 500) refer to total queries.
- Entropy should be computed on left-most labels; use unique labels for the fraction to avoid overweighting repeats of the same random token.
- Field mapping: prefer dns.question.name.keyword for terms aggs; if missing, some indices store dns.question.name as keyword directly.

## Likely entities and facts to keep handy
- User roles: cfo, ceo, cto, regional sales accounts, build (CI), monitor (partner), generic userNNN.
- Geography: EU list for filtering; APAC/Americas country lists for sales exceptions; SG special-case for CFO mid-week.
- Time handling: all data in UTC; day-of-week checks matter; weekday vs Sunday windows.
- IP ranges and host roles as above (office NATs, partners, registries, GitHub).

## To build next
- OpenSearch query helper shared across skills.
- Encoded allowlists/exceptions for benign patterns.
- Cross-index correlation helpers.

## environment.yaml seeds
- opensearch base URL and index names present; add more ranges/services as discovered.
