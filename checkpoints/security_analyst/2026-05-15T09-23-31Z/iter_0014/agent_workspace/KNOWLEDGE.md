# Security Analyst agent — understanding (rev 8)

This domain models an on-duty security analyst for ExampleCo, answering questions from OpenSearch indices (auth_logs, network, dns). Work includes detection and adjudication while suppressing known-benign patterns.

Updates after train_incidents_03 failure (2026-05-15)
- "Detect security incidents" tasks expect a consolidated list across multiple categories (auth, lateral, DNS), not a single finding. Coverage matters more than narrative.
- I added an incident aggregator (skills/detect_security_incidents/tools/aggregate_incidents.py) that:
  • Detects non‑EU successful (or likely) logins minus travel exceptions in environment.yaml.
  • Flags lateral movement via internal-destination fan‑out (cardinality of destination.ip), with percentile-based thresholding.
  • Detects DNS tunneling by base-domain concentration with high‑entropy sublabels; falls back to top‑N candidates when strict thresholds produce none.
- Use absolute time windows [start, end) and include first_seen/last_seen in evidence when available.

Benign patterns to suppress (carry-over)
- Partner monitoring health-check failures (monitor@partneracme.com) from 198.51.100.10–20 on /api/auth/health.
- CFO travel exception: cfo@example.com from SG mid-week (documented in environment.yaml).

Heuristics
- Inclusion rule for audit_user_access: include a user if any non‑EU success during the window is not fully covered by an exception spanning both start and end of the window.
- Compromise scoring (investigate_account): +1 non‑EU geo; +1 ≥3 failures with ≥1 success; +1 ≥3 off‑hours hourly buckets; +1 internal fan‑out ≥40 unique dests; +1 DNS long‑label volume ≥100. Verdict: 0–1 benign, 2 suspicious, ≥3 compromised.
- Time bounds: >= start and < end.

DNS tunneling detection notes
- Use dns.question.name.keyword and source.ip.
- Group by base domain: last two labels by default (example.com). For ccTLDs (two-letter TLD) with second-level in {co, com, net, org, gov, ac}, use last three (example.co.uk).
- Suspicion features: total volume under a base, variety of unique left-most labels, fraction of high-entropy long left-most labels. Score = frac_high_entropy * log1p(total).

Next
- In SELF_TESTING for incidents tasks, run the aggregator first, then review evidence and submit the consolidated array.
