# Skill: sec_analytics

Purpose: End-to-end security analytics for ExampleCo logs in OpenSearch: incident detection, service health, user access audit, lateral movement, DNS tunneling, and account compromise investigation.

Tools:
- tools/analyze.py: Multi-command CLI. Reads configuration from environment.yaml and queries OpenSearch directly.

Common Arguments:
- --start ISO8601 --end ISO8601 (required for time-bounded commands)
- Output is compact JSON printed to stdout.

Subcommands:
1) incidents
   Detect security incidents in the window across auth_logs, network, dns (excludes documented benign patterns).
   Example:
   python skills/sec_analytics/tools/analyze.py incidents --start 2026-04-13T00:00:00Z --end 2026-04-15T00:00:00Z

2) health-partner
   Assess partner_monitoring health over the window using auth_logs.
   Example:
   python skills/sec_analytics/tools/analyze.py health-partner --start 2026-04-13T00:00:00Z --end 2026-04-20T00:00:00Z

3) health-cicd
   Assess ci_cd_pipeline health over the window using network and dns (weekdays 03:00-04:00 UTC expected activity from 10.0.0.40 to app/db and Docker/GitHub).
   Example:
   python skills/sec_analytics/tools/analyze.py health-cicd --start 2026-04-13T00:00:00Z --end 2026-04-20T00:00:00Z

4) audit-users
   List users who successfully logged in from outside EU and warrant investigation (applies documented exceptions for CFO and regional sales).
   Example:
   python skills/sec_analytics/tools/analyze.py audit-users --start 2026-04-13T00:00:00Z --end 2026-04-20T00:00:00Z

5) lateral
   Detect internal hosts with unusual internal fan-out (excludes backup and CI/CD windows/hosts).
   Example:
   python skills/sec_analytics/tools/analyze.py lateral --start 2026-04-13T00:00:00Z --end 2026-04-20T00:00:00Z

6) dns-tunnel
   Detect hosts with DNS tunneling indicators (high volume to a single domain with many random subdomains, high NXDOMAIN/TXT).
   Example:
   python skills/sec_analytics/tools/analyze.py dns-tunnel --start 2026-04-13T00:00:00Z --end 2026-04-20T00:00:00Z

7) investigate --user <email>
   Investigate a single account for compromise indicators.
   Example:
   python skills/sec_analytics/tools/analyze.py investigate --user user042@example.com --start 2026-04-13T00:00:00Z --end 2026-04-20T00:00:00Z

Implementation notes:
- The tool reads base_url and index names from environment.yaml.
- EU country list is sourced from environment.yaml key `eu_countries` (includes EEA/CH for safety as documented in KNOWLEDGE.md).
- Heuristics are conservative: they rank by severity; thresholds are configurable in-code defaults.
- All outputs are deterministic from the provided logs; no network calls beyond OpenSearch.
