import argparse
import json
import os
from pathlib import Path
import requests
import yaml
from datetime import datetime


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
    r = requests.post(url, headers={"Content-Type": "application/json"}, json=body, timeout=25)
    r.raise_for_status()
    return r.json()


def in_exception(user, country, ts, exceptions):
    for ex in exceptions:
        if ex.get('user') == user:
            start = ex.get('start')
            end = ex.get('end')
            countries = ex.get('countries', [])
            ok_country = (not countries) or (country in countries)
            if ok_country:
                if (not start or ts >= start) and (not end or ts < end):
                    return True
    return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--index', default='auth_logs')
    p.add_argument('--start', required=True)
    p.add_argument('--end', required=True)
    args = p.parse_args()

    env = load_env()
    base_url = env.get('es', {}).get('base_url', 'http://localhost:9200')
    eu = set((env.get('policy', {}).get('eu_country_codes')) or [])
    exceptions = env.get('exceptions', {}).get('travel', [])

    must = [
        {"range": {"@timestamp": {"gte": args.start, "lt": args.end}}},
        {"terms": {"event.outcome": ["success", "succeeded", "login_success"]}}
    ]
    body = {
        "size": 0,
        "query": {"bool": {"must": must}},
        "aggs": {
            "by_user": {
                "terms": {"field": "user.email.keyword", "size": 10000, "missing": "unknown"},
                "aggs": {
                    "countries": {"terms": {"field": "event.geo.country_iso_code.keyword", "size": 200, "missing": "??"}},
                    "ips": {"terms": {"field": "source.ip", "size": 200}},
                    "first": {"min": {"field": "@timestamp"}},
                    "last": {"max": {"field": "@timestamp"}}
                }
            }
        }
    }

    resp = es_search(base_url, args.index, body)
    users = []

    for b in resp.get('aggregations', {}).get('by_user', {}).get('buckets', []):
        user = b.get('key')
        countries = [c['key'] for c in b.get('countries', {}).get('buckets', []) if c.get('key') and c.get('key') != '??']
        non_eu = [c for c in countries if c not in eu]
        if not non_eu:
            continue
        first_ts = datetime.utcfromtimestamp(b['first']['value'] / 1000).isoformat() + 'Z' if b['first'].get('value') else None
        last_ts = datetime.utcfromtimestamp(b['last']['value'] / 1000).isoformat() + 'Z' if b['last'].get('value') else None

        # If all non-EU countries are covered by exceptions covering the entire window, skip
        covered = True
        for c in non_eu:
            # check mid-window timestamp for coverage; if either boundary is outside, consider not fully covered
            if not (in_exception(user, c, args.start, exceptions) and in_exception(user, c, args.end, exceptions)):
                covered = False
                break
        if covered:
            continue

        ips = [ip['key'] for ip in b.get('ips', {}).get('buckets', [])]
        users.append({
            "user": user,
            "first_seen": first_ts,
            "last_seen": last_ts,
            "countries": countries,
            "non_eu_countries": non_eu,
            "ips": ips,
            "events": b.get('doc_count', 0),
            "rationale": f"Successful non-EU login(s): {','.join(non_eu)} not fully covered by exceptions"
        })

    print(json.dumps(users))


if __name__ == '__main__':
    main()
