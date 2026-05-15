# Security Analyst agent — understanding (rev 6)

This domain models an on-duty security analyst for ExampleCo, answering questions from OpenSearch indices (auth_logs, network, dns). Work includes detection and adjudication while suppressing known-benign patterns.

Updates after train_audit_02 failure
- Field names vary across datasets. For auth_logs, user identity may be in user.email, user.name, or user; country ISO code may appear as event.geo.country_iso_code, source.geo.country_iso_code, client.geo.country_iso_code, geo.country_iso_code, or geoip.country_iso_code; IP may be source.ip, client.ip, or related.ip.
- Our audit_user_access skill now tries multiple field combinations to avoid empty results caused by a single hard-coded path.
- Success indicators can differ; when unknown, aggregate without enforcing event.outcome=success to avoid false negatives. Prefer event.action=login + outcome=success when available.

Benign patterns to suppress (carry-over)
- Partner monitoring health-check failures (monitor@partneracme.com) from 198.51.100.10–20 on /api/auth/health.
- CFO travel exception: cfo@example.com from SG mid-week (documented in environment.yaml).

Heuristics
- Inclusion rule for audit_user_access: include a user if any non‑EU successful (or likely successful) login during the window is not fully covered by an exception that spans both the start and end of the queried window.
- Time bounds: >= start and < end.

DNS tunneling detection notes
- Use dns.question.name.keyword and source.ip.
- Group by base domain: last two labels by default (example.com). For ccTLDs (two-letter TLD) with second-level in {co, com, net, org, gov, ac}, use last three (example.co.uk). This avoids mis-bucketing .com under three labels.
- Suspicion features: total volume under a base, variety of unique left-most labels, fraction of high-entropy long left-most labels. Score = frac_high_entropy * log1p(total).
- If strict thresholds produce no matches, rank and emit top-N guarded by minimum volume and variety to avoid returning empty.

Next
- If further empties persist, consider switching to a composite aggregation to avoid terms cutoffs for very cardinal FQDNs.
