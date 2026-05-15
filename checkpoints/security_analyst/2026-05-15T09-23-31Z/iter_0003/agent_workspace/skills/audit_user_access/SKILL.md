Skill: audit_user_access

Purpose
- Identify users who successfully logged in from outside the EU within a given window, excluding documented exceptions.

Assumptions
- auth_logs index has fields:
  * @timestamp (ISO8601)
  * user.email or user.name
  * event.outcome in {success,failure}
  * source.ip or client.ip
  * geo.country_iso_code OR event.geo.country_iso_code (ISO-3166-1 alpha-2)
- Exceptions (allowlisted travel/VPN) are provided via environment.yaml.

Outputs
- JSON array of objects: {user, first_seen, last_seen, countries, ips, events, rationale}

Tools
- tools/audit_non_eu_logins.py

Usage
python skills/audit_user_access/tools/audit_non_eu_logins.py \
  --start 2026-04-13T00:00:00Z --end 2026-04-20T00:00:00Z \
  --index auth_logs

Environment
- environment.yaml:
  es.base_url: http://localhost:9200
  es.indices.auth: auth_logs
  policy.eu_country_codes: ["AT","BE",...]
  exceptions.travel: list of entries {user: "user@example.com", start: "2026-04-10T00:00:00Z", end: "2026-04-25T00:00:00Z", countries: ["US","GB"]}

Notes
- If country field is missing, host is skipped (cannot determine geo).
- A user is included if any successful login in window has a non-EU country not fully covered by an exception.
- The script collapses by user and returns concise rationale strings.