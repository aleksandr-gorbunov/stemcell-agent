# Skill: opensearch

Purpose: Send ad-hoc OpenSearch queries to indices defined in environment.yaml and print raw JSON.

When to use: debugging queries, spot-checking counts, building aggregations quickly.

Tooling:
- tools/query.py: POST /<index>/_search with a JSON request body.

Usage:
1) Read environment.yaml to ensure `opensearch.base_url` and `indices` are set.
2) Build your OpenSearch DSL JSON and pass it as a single-argument string.
3) Example:
   python skills/opensearch/tools/query.py auth '{"query":{"range":{"@timestamp":{"gte":"2026-04-13T00:00:00Z","lt":"2026-04-14T00:00:00Z"}}},"size":0,"aggs":{"by_country":{"terms":{"field":"source.geo.country","size":100}}}}'

Output: prints the raw JSON response from OpenSearch to stdout.

Notes:
- This script intentionally does not interpret the response.
- Keep `size` small (prefer size:0 with aggregations) to avoid large payloads.
