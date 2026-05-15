Skill: dns_tunneling

Purpose
- Detect DNS tunneling behavior: high query volume to a single domain with many random-looking subdomains from an internal host during a window.

Signals
- For each (src_ip, base_domain):
  - total queries (sum of doc_count across all FQDNs under that base)
  - unique subdomain-label count
  - character entropy of left-most labels
  - fraction of left-most labels with high entropy (>3.5 bits/char) and length >= 12.
- Flag if total queries >= 500 (default) AND fraction_high_entropy >= 0.6.

Tool
- tools/detect_dns_tunnel.py

Usage
python skills/dns_tunneling/tools/detect_dns_tunnel.py \
  --index dns --start 2026-04-13T00:00:00Z --end 2026-04-20T00:00:00Z \
  --min-total 500 --min-fraction 0.6

Assumptions
- dns index fields: @timestamp, source.ip, dns.question.name (keyword).

Output
- JSON array of findings: {src_ip, base_domain, total, unique_labels, frac_high_entropy, example_labels:[...]}

Environment
- Reads es.base_url from environment.yaml.

Notes
- This version correctly uses total query volume (doc_count), not just unique FQDN count, to meet the threshold.