# Failure Log

Entry date: 2026-05-15

Task: train_compromise_01
Symptom: Investigate tool returned low likelihood with empty evidence for user042@example.com; attempt failed.
Root cause: The investigate subcommand relied on (a) country codes present in successes and (b) a simplistic "failure then immediate success" heuristic. If a success came from a non-office public IP without a country code, or failures occurred in a short burst but not immediately followed by a success on the next event, no auth-side signal was triggered. Post-login anomalies were therefore gated off and not considered.
Fixes implemented:
- Added detection of successful logins from non-office public IPs (using allowlists.offices_nat_ranges) as an auth-side indicator.
- Upgraded failure-burst logic: detect >=5 failures within 15 minutes preceding a success (sliding window), not only a single failure immediately before success.
- Evidence strings now include specific timestamps and IPs for these new signals.
- Documented these behaviors in skills/sec_analytics/SKILL.md.
Next steps: Consider optional post-login host attribution window to link internal hosts to office NATs; add --post-window minutes when needed.
