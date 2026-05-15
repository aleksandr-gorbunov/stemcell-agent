Skill: audit_user_access

Purpose
- Identify users who successfully logged in from outside the EU within a given window, excluding documented exceptions.

Field normalization via runtime_mappings
- This skill defines runtime fields to coalesce common ECS variants so queries do not depend on exact mappings:
  * user_r (keyword): coalesces user.email, user.name, user
  * country_r (keyword): coalesces event.geo.country_iso_code, source.geo.country_iso_code, client.geo.country_iso_code, geo.country_iso_code, geoip.country_iso_code
  * ip_r (ip): coalesces source.ip, client.ip, related.ip

Success signal
- Uses a broad should-clause (event.outcome=success, event.action=login, authentication.type=success, auth.result=success) with minimum_should_match=0. This keeps compatibility across schemas while still biasing toward login-success events.

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
  exceptions.vpn_cidrs: list of CIDR strings considered corporate VPN egress

Logic
- Aggregate by user_r; collect countries and IPs via runtime fields.
- Determine non-EU countries by exclusion from policy.eu_country_codes.
- Exclude users if BOTH of the following are true:
  1) All non-EU countries are fully covered by an allowlisted travel window for the entire analysis window (both start and end timestamps inside the exception window for that user+country), and/or
  2) All observed IPs for the user bucket fall within exceptions.vpn_cidrs (corporate VPN egress).
- If either condition is not satisfied, include the user.

Notes
- If no country is resolvable, events are ignored for inclusion logic.
- Time bounds: inclusive start (>=) and exclusive end (<) are used.
