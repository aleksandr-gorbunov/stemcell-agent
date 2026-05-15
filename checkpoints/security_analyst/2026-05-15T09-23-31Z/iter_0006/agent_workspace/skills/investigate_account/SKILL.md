Skill: investigate_account

Purpose
- Investigate a user account for compromise indicators across auth_logs, network, and dns within a window.

Method
1) Pull auth successes/failures for the user; summarize IPs, countries, MFA outcomes, off-hours activity (00:00-06:00 local assumed UTC if tz unknown).
2) From successful login IPs in-window, pivot to network+dns by source.ip to capture unusual internal fan-out and potential tunneling domains.
3) Emit a JSON object with key evidence and a verdict {benign|suspicious|compromised} with reasons.

Tool
- tools/investigate_user.py

Usage
python skills/investigate_account/tools/investigate_user.py \
  --user user042@example.com \
  --start 2026-04-13T00:00:00Z --end 2026-04-20T00:00:00Z

Environment
- es.base_url
- policy.eu_country_codes (used for geo-anomaly)

Output
- JSON object keyed by user with fields: auth_summary, geo_anomalies, network_anomalies, dns_anomalies, verdict, reasons.