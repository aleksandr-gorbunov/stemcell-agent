2026-05-15
Task: train_audit_02
Issue: Returned empty list. Root cause: audit_non_eu_logins.py depended on guessing exact field names (user/email, geo country, ip) and bailed when no single combination worked. The dataset likely uses different fields, so the script selected no aggregation and emitted []. Also lacked VPN allowlist handling despite instructions.
Fix: Implemented runtime_mappings to coalesce user, country, and ip across common ECS fields and aggregate on those runtime fields. Added optional corporate VPN CIDR allowlist exclusion. Updated SKILL.md to match actual logic. Kept strict time-window coverage rule for exceptions.
Action: Revised skills/audit_user_access/tools/audit_non_eu_logins.py and SKILL.md; extended environment.yaml with exceptions.vpn_cidrs.
