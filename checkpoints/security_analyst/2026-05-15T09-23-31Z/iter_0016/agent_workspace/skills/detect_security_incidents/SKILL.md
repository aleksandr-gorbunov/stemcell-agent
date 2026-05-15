Skill: detect_security_incidents

Purpose
- End-to-end incident sweep across auth_logs, network, and dns for a given window. Automates detectors and emits a unified JSON list of incidents.

When to use
- Any task that asks to "identify real security incidents" across multiple indices in a time window.

Detectors included
- non_eu_login: successful (or likely successful) logins from outside the EU, minus documented travel exceptions (from environment.yaml).
- lateral_movement: internal fan-out anomalies where a source touches unusually many unique internal destinations.
- dns_tunneling: hosts making high-volume DNS queries to a single base domain with many high-entropy sublabels.

Tool
- tools/aggregate_incidents.py

Usage
- python skills/detect_security_incidents/tools/aggregate_incidents.py --start 2026-04-13T00:00:00Z --end 2026-04-20T00:00:00Z
- Optional tuning:
  --min-unique 20 --pctl 95            # lateral movement thresholding
  --min-total-dns 200 --min-frac-dns 0.5 --topn-dns 5  # DNS tunneling

Output
- A JSON array of incident objects:
  {"type": "<non_eu_login|lateral_movement|dns_tunneling>", "entity": "<user or ip>", "window": {"start", "end"}, "evidence": {...}}

Notes
- The script adapts to schema drift by trying multiple field names.
- EU list and travel exceptions live in environment.yaml.
- If strict DNS thresholds yield none, it returns the top-N candidates with guards, so review evidence before reporting as definite incidents.
