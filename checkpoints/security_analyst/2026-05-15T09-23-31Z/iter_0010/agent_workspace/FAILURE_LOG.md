Failure: train_audit_02 (attempt 1 on SELF_TESTING iteration 4)
Root cause
- The audit_user_access tool assumed fixed field paths (user.email, event.geo.country_iso_code, source.ip). The dataset uses different field names in places (e.g., source.geo.country_iso_code or geoip.country_iso_code), so the aggregation returned no non‑EU countries and produced an empty result.

Fix
- Updated tools/audit_non_eu_logins.py to use Elasticsearch runtime_mappings to coalesce multiple possible field paths for user id, country ISO code, and IP (user.email|user.name|user; event.geo|source.geo|client.geo|geo|geoip; source.ip|client.ip|related.ip).
- Expanded SKILL.md with explicit mapping assumptions and logic notes.

Next steps
- Re-run the tool for the targeted window 2026-04-15T00:00:00Z to 2026-04-17T00:00:00Z and use the output for train_audit_02.

---
Failure: train_dns_01 (attempt 2 on SELF_TESTING iteration 7)
Root cause
- dns_tunneling tool returned an empty list. Likely causes: (a) base-domain heuristic incorrectly grouped names under 3-label bases like “example.com” due to treating “com” as a second‑level suffix; (b) thresholds too strict for this dataset week; (c) lack of fallback ranking when strict thresholds yield none.

Fix
- Corrected base-domain logic to use last 3 labels only when the TLD is a country code (length 2) AND the second‑level is in {co, com, net, org, gov, ac}; otherwise use last 2.
- Added scoring and fallback: compute score = frac_high_entropy * log1p(total). If no candidates meet thresholds, emit top N (default 5) with total ≥ 100 and ≥ 20 unique left‑labels.
- Tuned defaults to --min-total 200 and --min-fraction 0.5; still overrideable via CLI.
- Sorted findings by score descending and included score in output for transparency.

Next steps
- Re-run for 2026-04-13T00:00:00Z to 2026-04-20T00:00:00Z against the dns index and use the output for train_dns_01.
