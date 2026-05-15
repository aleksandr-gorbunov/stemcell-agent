Failure: train_audit_02 (attempt 1 on SELF_TESTING iteration 4)
Root cause
- The audit_user_access tool assumed fixed field paths (user.email, event.geo.country_iso_code, source.ip). The dataset uses different field names in places (e.g., source.geo.country_iso_code or geoip.country_iso_code), so the aggregation returned no non‑EU countries and produced an empty result.

Fix
- Updated tools/audit_non_eu_logins.py to use Elasticsearch runtime_mappings to coalesce multiple possible field paths for user id, country ISO code, and IP (user.email|user.name|user; event.geo|source.geo|client.geo|geo|geoip; source.ip|client.ip|related.ip).
- Expanded SKILL.md with explicit mapping assumptions and logic notes.

Next steps
- Re-run the tool for the targeted window 2026-04-15T00:00:00Z to 2026-04-17T00:00:00Z and use the output for train_audit_02.
