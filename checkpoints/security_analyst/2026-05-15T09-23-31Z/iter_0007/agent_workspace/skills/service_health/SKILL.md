Skill: service_health

Purpose
- Assess the health/uptime of log-producing integrations by looking for event gaps, sharp drops, and error-rate spikes over a time window.

When to use
- Tasks asking to assess health of a specific integration over a period (e.g., `partner_monitoring` via auth_logs; `ci_cd_pipeline` via network+dns).

Assumptions about data
- Each index has `@timestamp` in ISO8601.
- Optional fields `pipeline`, `integration`, `source`, or `service.name` may tag the integration. If not provided in the task, pass a filter value via `--filter-kv` (key=value), or rely on index-only signal.
- Error/success may be indicated by fields like `event.outcome` (success/failure), `status` (ok/error), or HTTP `response.status_code` in network logs.

Outputs
- JSON with hourly counts, zero-hour percentage, largest consecutive gap, anomaly flags, and a concise summary string.

Tools
- tools/assess_health.py

Usage
- Single index:
  python skills/service_health/tools/assess_health.py \
    --index auth_logs \
    --start 2026-04-13T00:00:00Z --end 2026-04-20T00:00:00Z \
    --filter-kv integration=partner_monitoring

- Multiple indices (provide multiple --index):
  python skills/service_health/tools/assess_health.py \
    --index network --index dns \
    --start 2026-04-13T00:00:00Z --end 2026-04-20T00:00:00Z \
    --filter-kv pipeline=ci_cd_pipeline

Behavior
- Pull hourly histogram (date_histogram) per index, merge series, and compute:
  * hours_total, hours_with_events, hours_zero
  * pct_zero_hours
  * longest_zero_streak_hours
  * mean and stddev of hourly counts; flag dips below 20% of mean for >=2 consecutive hours
  * error_rate if failure indicators exist
- Prints JSON to stdout.

Environment
- Reads environment.yaml for:
  es.base_url (default http://localhost:9200)

Notes
- Keeps queries light by using date_histogram aggregation; does not fetch raw events.
- All thresholds are conservative and printed alongside metrics for transparency.