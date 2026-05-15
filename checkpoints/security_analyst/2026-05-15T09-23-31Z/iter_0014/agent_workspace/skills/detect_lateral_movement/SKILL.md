Skill: detect_lateral_movement

Purpose
- Identify internal hosts whose connection patterns show unusual fan-out to internal destinations during a window.

Heuristic
- For each source host, count unique internal destination IPs and total internal connections.
- Compute distribution across hosts; flag hosts above p95 unique-dst and with unique-dst >= 20 (defaults). Also flag large increases hour-over-hour.
- Internal = RFC1918 (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16) or env.internal_cidrs.

Tool
- tools/fanout_anomaly.py

Usage
python skills/detect_lateral_movement/tools/fanout_anomaly.py \
  --index network --start 2026-04-13T00:00:00Z --end 2026-04-20T00:00:00Z \
  --min-unique 20 --pctl 95

Output
- JSON array: {src_ip, unique_dsts, total_flows, pctl_threshold, rationale}

Environment
- es.base_url
- internal_cidrs (optional)