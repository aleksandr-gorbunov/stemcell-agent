Skill: audit_user_access

Purpose
- Identify users who successfully logged in from outside the EU within a given window, excluding documented exceptions.

Assumptions / Field mapping
- The auth_logs schema may vary. This skill coalesces fields via runtime_mappings:
  * user: user.email, user.name, or user
  * country: event.geo.country_iso_code, source.geo.country_iso_code, client.geo.country_iso_code, geo.country_iso_code, or geoip.country_iso_code
  * ip: source.ip, client.ip, or related.ip
- Other fields used:
  * @timestamp (ISO8601)
  * event.outcome indicates success (any of: success, succeeded, login_success)

Outputs
- JSON array of objects: {user, first_seen, last_seen, countries, non_eu_countries, ips, events, rationale}

Tool
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
  exceptions.travel: list of {user, start, end, countries}

Logic
- Aggregate successful logins by user; collect countries and IPs via runtime fields.
- Exclude users only if all non-EU countries are fully covered by allowlisted travel/VPN for the entire analysis window (both start and end timestamps inside the exception window).

Notes
- If no country is resolvable, events are ignored for inclusion logic.
- Time bounds: inclusive start (>=) and exclusive end (<) are used, matching ES range query in the tool.