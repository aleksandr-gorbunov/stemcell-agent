# OpenSearch query DSL primer

This is a short reference for the parts of the query DSL the analyst is most
likely to need. The full docs are at opensearch.org. The endpoint is
`http://localhost:9200`.

## Search request shape

```
POST /<index>/_search
{
  "size": 100,
  "query": { ... },
  "sort":  [ { "@timestamp": "asc" } ],
  "aggs":  { ... }
}
```

`size` controls how many documents come back in `hits.hits`. Set it to `0`
when you only want aggregation results and not the documents themselves.
The response shape is:

```
{
  "hits": {
    "total": {"value": <N>},
    "hits": [ {"_source": {...}}, ... ]
  },
  "aggregations": { ... }
}
```

## Bool query

`bool` combines clauses. Each clause type has different semantics:

- `must`: every clause must match (AND). Contributes to score.
- `filter`: same as `must` but does not contribute to score. Use this for
  range filters and term filters where score does not matter.
- `should`: at least one should match (OR). Boosts score.
- `must_not`: documents matching this are excluded.

```
{
  "query": {
    "bool": {
      "filter": [
        {"range": {"@timestamp": {"gte": "2026-04-13", "lt": "2026-04-14"}}},
        {"term":  {"event.outcome": "failure"}}
      ],
      "must_not": [
        {"term": {"user.name": "monitor@partneracme.com"}}
      ]
    }
  }
}
```

## Common clauses

- `term`: exact match on a keyword field.
  `{"term": {"source.ip": "10.0.4.22"}}`
- `terms`: exact match on any of a list.
  `{"terms": {"event.action": ["login_attempt", "mfa_challenge"]}}`
- `range`: numeric or date range. Use `gte`, `lt`, `gt`, `lte`.
  `{"range": {"@timestamp": {"gte": "2026-04-13T00:00:00Z", "lt": "2026-04-14T00:00:00Z"}}}`
- `match`: full-text match. Useful for keyword-ish text but not for IPs;
  prefer `term` for exact identifiers.
- `exists`: documents where the field is present.
  `{"exists": {"field": "url.path"}}`
- `prefix`, `wildcard`: pattern match on keyword fields.
  `{"prefix": {"source.ip": "198.51.100."}}`

## Aggregations

`aggs` runs in addition to the query and returns grouped counts/sums.

- `terms`: group by the distinct values of a field.

  ```
  "aggs": {
    "by_src_ip": {
      "terms": {"field": "source.ip", "size": 20}
    }
  }
  ```

  Returns the top-N values with their doc counts.

- `cardinality`: count distinct values of a field.

  ```
  "aggs": {
    "unique_users": {
      "cardinality": {"field": "user.name"}
    }
  }
  ```

- `date_histogram`: bucket documents by time intervals.

  ```
  "aggs": {
    "per_hour": {
      "date_histogram": {"field": "@timestamp", "fixed_interval": "1h"}
    }
  }
  ```

- `sum`, `avg`, `max`, `min`: numeric aggregates on a field.

  ```
  "aggs": {
    "total_out": {"sum": {"field": "network.bytes_out"}}
  }
  ```

Aggregations nest. Putting a `cardinality` aggregation inside a `terms`
aggregation gives you, for example, "for each source IP, how many distinct
users were attempted".

## Practical patterns

Find IPs that produced many failed logins in a window:

```
{
  "size": 0,
  "query": {
    "bool": {"filter": [
      {"range": {"@timestamp": {"gte": "...", "lt": "..."}}},
      {"term":  {"event.outcome": "failure"}},
      {"term":  {"event.action": "login_attempt"}}
    ]}
  },
  "aggs": {
    "noisy_ips": {
      "terms": {"field": "source.ip", "size": 10},
      "aggs": {"unique_users": {"cardinality": {"field": "user.name"}}}
    }
  }
}
```

Find internal hosts with high outbound volume to external destinations:

```
{
  "size": 0,
  "query": {
    "bool": {"filter": [
      {"range": {"@timestamp": {"gte": "...", "lt": "..."}}},
      {"prefix": {"source.ip": "10."}}
    ]}
  },
  "aggs": {
    "by_source": {
      "terms": {"field": "source.ip", "size": 20},
      "aggs": {"out": {"sum": {"field": "network.bytes_out"}}}
    }
  }
}
```

Find internal hosts whose set of internal destinations is unusually broad:

```
{
  "size": 0,
  "query": {
    "bool": {"filter": [
      {"range": {"@timestamp": {"gte": "...", "lt": "..."}}},
      {"prefix": {"source.ip": "10."}},
      {"prefix": {"destination.ip": "10."}}
    ]}
  },
  "aggs": {
    "by_source": {
      "terms": {"field": "source.ip", "size": 20},
      "aggs": {"unique_dests": {"cardinality": {"field": "destination.ip"}}}
    }
  }
}
```

Find DNS query volume per source IP, optionally restricted to a single domain:

```
{
  "size": 0,
  "query": {"bool": {"filter": [
    {"range": {"@timestamp": {"gte": "...", "lt": "..."}}},
    {"wildcard": {"query.name": "*.tunnel.example.org"}}
  ]}},
  "aggs": {
    "by_source": {
      "terms": {"field": "source.ip", "size": 20},
      "aggs": {"unique_names": {"cardinality": {"field": "query.name"}}}
    }
  }
}
```

A high ratio of unique query names to total queries from one source, paired
with high absolute volume, is a strong tunneling signal. Document length and
character entropy of subdomain labels are also useful when computed on the
client side from the returned hits.

## Calling from a script

A minimal request with the http_request base tool:

```
http_request(
  method="POST",
  url="http://localhost:9200/auth_logs/_search",
  headers={"Content-Type": "application/json"},
  body={ ... query object ... }
)
```

Inside a Python script the agent authors, the equivalent with the standard
library is `urllib.request.urlopen` plus `json.dumps`/`json.loads`. No
third-party clients required.
