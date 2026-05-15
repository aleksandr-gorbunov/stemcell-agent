import argparse
import json
import os
from pathlib import Path
import sys
import math
import requests
import yaml


def load_env():
    workspace = Path(os.environ.get("STEMCELL_AGENT_WORKSPACE", "."))
    env_path = workspace / "environment.yaml"
    if env_path.exists():
        try:
            return yaml.safe_load(env_path.read_text()) or {}
        except Exception:
            return {}
    return {}


def es_request(base_url, index, body):
    url = f"{base_url.rstrip('/')}/{index}/_search"
    headers = {"Content-Type": "application/json"}
    r = requests.post(url, headers=headers, data=json.dumps(body), timeout=20)
    r.raise_for_status()
    return r.json()


def hourly_histogram(base_url, index, start, end, term_filters):
    must = [
        {"range": {"@timestamp": {"gte": start, "lt": end}}}
    ]
    for k, v in term_filters.items():
        must.append({"term": {k: v}})
    body = {
        "size": 0,
        "query": {"bool": {"must": must}},
        "aggs": {
            "h": {
                "date_histogram": {
                    "field": "@timestamp",
                    "fixed_interval": "1h",
                    "min_doc_count": 0,
                    "extended_bounds": {"min": start, "max": end}
                }
            },
            "failures": {
                "filter": {"terms": {"event.outcome": ["failure", "error"]}}
            }
        }
    }
    return es_request(base_url, index, body)


def summarize_series(buckets):
    counts = [b.get('doc_count', 0) for b in buckets]
    hours_total = len(counts)
    hours_with_events = sum(1 for c in counts if c > 0)
    hours_zero = hours_total - hours_with_events
    pct_zero_hours = (hours_zero / hours_total * 100.0) if hours_total else 0.0
    # longest zero streak
    longest = 0
    current = 0
    for c in counts:
        if c == 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    mean = (sum(counts) / hours_total) if hours_total else 0.0
    var = sum((c - mean) ** 2 for c in counts) / hours_total if hours_total else 0.0
    std = math.sqrt(var)
    # dips: consecutive hours below 20% of mean (if mean>0)
    dips = []
    if mean > 0:
        thresh = 0.2 * mean
        streak = []
        for i, c in enumerate(counts):
            if c < thresh:
                streak.append(i)
            else:
                if len(streak) >= 2:
                    dips.append(streak.copy())
                streak = []
        if len(streak) >= 2:
            dips.append(streak.copy())
    return {
        "hours_total": hours_total,
        "hours_with_events": hours_with_events,
        "hours_zero": hours_zero,
        "pct_zero_hours": pct_zero_hours,
        "longest_zero_streak_hours": longest,
        "mean_per_hour": mean,
        "std_per_hour": std,
        "low_count_runs": dips,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--index', action='append', required=True, help='Index name; repeatable')
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--filter-kv', action='append', help='key=value filter; repeatable', default=[])
    args = parser.parse_args()

    env = load_env()
    base_url = env.get('es', {}).get('base_url', 'http://localhost:9200')

    term_filters = {}
    for kv in args.filter_kv:
        if '=' in kv:
            k, v = kv.split('=', 1)
            term_filters[k] = v

    combined = {
        "hours_total": 0,
        "hours_with_events": 0,
        "hours_zero": 0,
        "pct_zero_hours": None,
        "longest_zero_streak_hours": 0,
        "mean_per_hour": 0.0,
        "std_per_hour": 0.0,
        "low_count_runs": [],
        "per_index": {}
    }

    all_counts = []
    per_index_series = {}

    for idx in args.index:
        resp = hourly_histogram(base_url, idx, args.start, args.end, term_filters)
        buckets = resp.get('aggregations', {}).get('h', {}).get('buckets', [])
        s = summarize_series(buckets)
        combined['per_index'][idx] = s
        combined['hours_total'] = max(combined['hours_total'], s['hours_total'])
        combined['longest_zero_streak_hours'] = max(combined['longest_zero_streak_hours'], s['longest_zero_streak_hours'])
        # accumulate for mean/std across indices by summing series per hour
        per_index_series[idx] = [b.get('doc_count', 0) for b in buckets]

    # Merge series by hour position if equal length; otherwise approximate via totals
    if per_index_series:
        lengths = {len(v) for v in per_index_series.values()}
        if len(lengths) == 1:
            L = next(iter(lengths))
            for i in range(L):
                all_counts.append(sum(per_index_series[idx][i] for idx in per_index_series))
            # recompute summary on merged series
            tmp = summarize_series([{"doc_count": c} for c in all_counts])
            combined.update({k: tmp[k] for k in tmp})
        else:
            # fallback: approximate totals
            totals = [sum(v) for v in per_index_series.values()]
            combined['mean_per_hour'] = sum(totals) / max(1, combined['hours_total'])
            combined['std_per_hour'] = None
            combined['pct_zero_hours'] = None

    # Summary string
    summary = []
    summary.append(f"Hours: {combined['hours_total']}, zero_hours: {combined['hours_zero']} (longest_gap={combined['longest_zero_streak_hours']}h)")
    if combined['mean_per_hour']:
        summary.append(f"mean/hr≈{combined['mean_per_hour']:.1f}")
    if combined['low_count_runs']:
        runs = [f"{len(r)}h@{r[0]}" for r in combined['low_count_runs']]
        summary.append("low_runs=" + ",".join(runs))

    combined['summary'] = "; ".join(summary)

    print(json.dumps(combined))


if __name__ == '__main__':
    main()
