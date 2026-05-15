2026-05-15: train_incidents_03 failed
- Cause: Returned only one lateral_movement incident and omitted other real incidents (e.g., DNS tunneling and non‑EU login anomalies) present in the same window. I followed the runbook manually and did not consolidate all detectors into a single, complete output.
- Fixes:
  1) Added an automated aggregator script at skills/detect_security_incidents/tools/aggregate_incidents.py to run all three detectors (non‑EU logins, lateral movement, DNS tunneling) and emit a unified JSON list.
  2) Upgraded skills/detect_security_incidents/SKILL.md with precise usage, thresholds, and output schema to reduce format drift.
  3) Will use the aggregator in future incident tasks to ensure coverage and consistency across indices.
