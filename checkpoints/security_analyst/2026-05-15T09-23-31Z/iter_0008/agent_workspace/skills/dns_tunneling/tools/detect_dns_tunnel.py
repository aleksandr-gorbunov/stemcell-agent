import argparse
import json
import os
from pathlib import Path
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


def es_search(base_url, index, body):
    url = f"{base_url.rstrip('/')}/{index}/_search"
    r = requests.post(url, headers={"Content-Type": "application/json"}, json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def base_domain(name: str) -> str:
    parts = name.lower().strip('.').split('.')
    if len(parts) < 2:
        return name.lower()
    # very small heuristic for SLDs like co.uk
    if len(parts) >= 3 and parts[-2] in {"co", "com"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def sub_label(name: str, base: str):
    if name.endswith(base):
        rest = name[:-(len(base))].rstrip('.')
        return rest if rest else None
    return None


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    freq = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    H = 0.0
    L = len(s)
    for c in freq.values():
        p = c / L
        H -= p * math.log2(p)
    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--index', default='dns')
    ap.add_argument('--start', required=True)
    ap.add_argument('--end', required=True)
    ap.add_argument('--min-total', type=int, default=500, help='Minimum total query count (doc_count), not unique names')
    ap.add_argument('--min-fraction', type=float, default=0.6, help='Minimum fraction of high-entropy labels (by unique label)')
    args = ap.parse_args()

    env = load_env()
    base_url = env.get('es', {}).get('base_url', 'http://localhost:9200')

    # Aggregate all DNS names per source.ip in the time window, capturing doc_count per FQDN
    body = {
        "size": 0,
        "query": {"bool": {"must": [
            {"range": {"@timestamp": {"gte": args.start, "lt": args.end}}}
        ]}},
        "aggs": {
            "by_src": {
                "terms": {"field": "source.ip", "size": 10000},
                "aggs": {
                    "names": {"terms": {"field": "dns.question.name.keyword", "size": 200000}}
                }
            }
        }
    }

    resp = es_search(base_url, args.index, body)

    findings = []

    for b in resp.get('aggregations', {}).get('by_src', {}).get('buckets', []):
        src = b.get('key')
        name_buckets = b.get('names', {}).get('buckets', [])
        if not name_buckets:
            continue
        # Group by base domain and keep both fqdn and doc_count
        by_base = {}
        for nb in name_buckets:
            fq = nb.get('key')
            cnt = int(nb.get('doc_count', 0))
            if not fq or cnt <= 0:
                continue
            bd = base_domain(fq)
            by_base.setdefault(bd, []).append((fq, cnt))

        for bd, fq_items in by_base.items():
            # Build list of sub-labels (unique) and compute total query volume
            subs = []
            total = 0
            for fq, cnt in fq_items:
                total += cnt
                lab = sub_label(fq, bd)
                if lab:
                    subs.append(lab)
            if not subs:
                continue
            # Compute fraction of high-entropy among UNIQUE left-most labels
            unique_left = set(s.split('.')[-1] for s in subs)
            high = 0
            samples = []
            for left in unique_left:
                H = shannon_entropy(left)
                if len(left) >= 12 and H >= 3.5:
                    high += 1
                    if len(samples) < 5:
                        samples.append(left)
            frac = high / max(1, len(unique_left))
            if total >= args.min_total and frac >= args.min_fraction:
                findings.append({
                    "src_ip": src,
                    "base_domain": bd,
                    "total": total,
                    "unique_labels": len(unique_left),
                    "frac_high_entropy": round(frac, 3),
                    "example_labels": samples
                })

    print(json.dumps(findings))


if __name__ == '__main__':
    main()
