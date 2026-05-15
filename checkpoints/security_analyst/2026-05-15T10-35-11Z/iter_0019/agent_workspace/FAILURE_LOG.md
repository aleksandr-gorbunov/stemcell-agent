Task train_compromise_01 failed on the first attempt.
Root cause: The investigation subcommand returned a non-conforming schema (severity/reasons instead of compromise_likelihood/evidence) and applied shallow heuristics that missed key account-compromise indicators (impossible travel, UA changes, tight correlation to post-login network/DNS anomalies). Evidence text was vague and not timestamped.
Fix in this iteration:
- Upgrade skills/sec_analytics/tools/analyze.py investigate routine to compute: outside-EU successes, impossible-travel between consecutive successes, user-agent changes, and post-login network/DNS anomalies. Map findings to a clear compromise_likelihood (high/medium/low) and detailed, timestamped evidence strings.
- Document JSON output contract in SKILL.md and add examples.
Next steps after testing: If correlation to post-login host needs strengthening, add optional --minutes-after to window network/DNS checks and attempt to tie internal host via same-day activity.
