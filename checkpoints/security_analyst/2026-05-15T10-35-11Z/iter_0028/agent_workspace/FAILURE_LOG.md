Task train_compromise_02 failed on first attempt.
Root cause: The investigate subcommand treated environment-wide network/DNS anomalies as evidence for the target user (cto@example.com) without proving association via auth signals or host linkage. This inflated the score to "medium" even though the user’s own auth activity likely showed no compromise indicators.
Fix in this iteration:
- Gate post-login network/DNS anomalies behind at least one authentication-side indicator (outside-EU success, impossible travel, UA change, or failure-burst-then-success). If no auth-side red flags exist, do not attribute unrelated network/DNS activity to the user and default to "low" unless future host linkage logic ties them.
- Update SKILL.md to document the gating rule.
Next steps:
- Implement optional host linkage: map login source NAT range to office and infer workstation subnet; then only consider network/DNS from those internal ranges within N hours after success. Add a --post-window parameter.
