Skill: dns_tunneling

Purpose
- Detect DNS tunneling behavior: high query volume to a single domain with many random-looking subdomains from an internal host during a time window.

Signals considered per (src_ip, base_domain)
- total: total query volume (sum of doc_count over all FQDNs under the base)
- unique_labels: count of unique left-most labels
- frac_high_entropy: fraction of unique left-most labels whose Shannon entropy ≥ 3.5 bits/char and length ≥ 12
- score: frac_high_entropy * log1p(total)

Default decision rule
- Flag if total ≥ 200 and frac_high_entropy ≥ 0.5.
- If no pairs meet the thresholds, emit the top N (default 5) by score with guards total ≥ 100 and unique_labels ≥ 20. This prevents empty results while keeping noise controlled.

Tool
- tools/detect_dns_tunnel.py

Usage
# Recommended for the training week
python skills/dns_tunneling/tools/detect_dns_tunnel.py \
  --start 2026-04-13T00:00:00Z --end 2026-04-20T00:00:00Z \
  --index dns --min-total 200 --min-fraction 0.5 --top-n 5

Assumptions
- dns index fields: @timestamp, source.ip, dns.question.name.keyword
- environment.yaml provides es.base_url and es.indices.dns (defaults to http://localhost:9200 and index "dns" if unset)

Output
- JSON array of findings, sorted by score desc:
  {src_ip, base_domain, total, unique_labels, frac_high_entropy, score, example_labels:[...]}

Notes
- Base-domain heuristic: use the last two labels normally (example.com). If the TLD is a 2-letter country code and the second-level is in {co, com, net, org, gov, ac}, use the last three (example.co.uk). This prevents mis-grouping under generic TLDs.
