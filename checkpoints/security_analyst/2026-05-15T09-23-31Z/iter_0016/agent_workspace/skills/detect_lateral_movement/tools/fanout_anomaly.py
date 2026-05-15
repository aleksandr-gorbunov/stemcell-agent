import argparse
import ipaddress
import json
import math
import os
from pathlib import Path
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


def es_search(base_url, index, body):
    url = f"{base_url.rstrip('/')}/{index}/_search"
    r = requests.post(url, headers={"Content-Type": "application/json"}, json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--index', default='network')
    ap.add_argument('--start', required=True)
    ap.add_argument('--end', required=True)
    ap.add_argument('--min-unique', type=int, default=20)
    ap.add_argument('--pctl', type=int, default=95)
    args = ap.parse_args()

    env = load_env()
    base_url = env.get('es', {}).get('base_url', 'http://localhost:9200')

    # Pull per-source unique internal destinations and total counts using composite aggs
    body = {
        "size": 0,
        "query": {"bool": {"must": [
            {"range": {"@timestamp": {"gte": args.start, "lt": args.end}}},
            {"regexp": {"destination.ip": "(10\\..*|192\\.168\\..*|172\\.(1[6-9]|2[0-9]|3[0-1])\\..*)"}}
        ]}},
        "aggs": {
            "by_src": {
                "terms": {"field": "source.ip", "size": 10000},
                "aggs": {
                    "uniq_dst": {"cardinality": {"field": "destination.ip"}},
                    "flows": {"value_count": {"field": "destination.ip"}}
                }
            }
        }
    }

    resp = es_search(base_url, args.index, body)

    rows = []
    uniq_values = []
    for b in resp.get('aggregations', {}).get('by_src', {}).get('buckets', []):
        src = b.get('key')
        uniq = b.get('uniq_dst', {}).get('value', 0)
        flows = b.get('flows', {}).get('value', 0)
        rows.append((src, uniq, flows))
        uniq_values.append(uniq)

    uniq_values_sorted = sorted(uniq_values)
    if uniq_values_sorted:
        k = max(0, min(len(uniq_values_sorted)-1, int(math.ceil(args.pctl/100.0 * len(uniq_values_sorted)))-1))
        thresh = uniq_values_sorted[k]
    else:
        thresh = args.min_unique

    findings = []
    for src, uniq, flows in rows:
        if uniq >= max(args.min_unique, thresh):
            findings.append({
                "src_ip": src,
                "unique_dsts": int(uniq),
                "total_flows": int(flows),
                "pctl_threshold": int(thresh),
                "rationale": f"Unique internal destinations {uniq} >= max(min={args.min_unique}, p{args.pctl}={thresh})"
            })

    print(json.dumps(findings))


if __name__ == '__main__':
    main()
