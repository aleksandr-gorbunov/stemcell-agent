Skill: detect_security_incidents

Purpose
- Provide a structured, repeatable workflow to identify real security incidents across auth_logs, network, and dns within a window.

Approach
1) Authentication anomalies: spikes in failures, success after many failures, non-EU login successes.
   - Use skills/audit_user_access first.
2) Lateral movement: internal fan-out anomalies.
   - Use skills/detect_lateral_movement.
3) DNS tunneling: high-entropy subdomain volume to a single domain per host.
   - Use skills/dns_tunneling.
4) Correlation: intersect IPs and users across findings; prioritize entities appearing in multiple detectors.

Tooling
- This skill orchestrates by runbook; it has no standalone script. Follow the steps and combine outputs manually.

Output format for tasks
- Return JSON with keys: {incidents: [ {type, entity, window, evidence: {...}} ], summary}

Usage checklist
- Run audit_non_eu_logins -> candidate users
- Run fanout_anomaly -> candidate hosts
- Run detect_dns_tunnel -> candidate hosts/domains
- Cross-link by IPs seen in auth successes
- Write concise incidents with timestamps and counts.