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


USER_FIELDS = [
    "user.email.keyword",
    "user.name.keyword",
    "user.keyword"
]
COUNTRY_FIELDS = [
    "event.geo.country_iso_code.keyword",
    "source.geo.country_iso_code.keyword",
    "client.geo.country_iso_code.keyword",
    "geo.country_iso_code.keyword",
    "geoip.country_iso_code.keyword"
]
IP_FIELDS = [
    "source.ip",
    "client.ip",
    "related.ip"
]


def try_agg(base_url, index, start, end, user_field, country_field, ip_field):
    must = [
        {"range": {"@timestamp": {"gte": start, "lt": end}}}
    ]
    # Keep success filtering broad but optional: if dataset has markers, they will narrow; if not, we still get results
    should = [
        {"terms": {"event.outcome": ["success", "succeeded", "login_success"]}},
        {"term": {"event.action": "login"}},
        {"term": {"authentication.type": "success"}},
        {"term": {"auth.result": "success"}}
    ]
    body = {
        "size": 0,
        "query": {"bool": {"must": must, "should": should, "minimum_should_match": 0}},
        "aggs": {
            "by_user": {
                "terms": {"field": user_field, "size": 10000, "missing": "unknown"},
                "aggs": {
                    "countries": {"terms": {"field": country_field, "size": 200, "missing": "??"}},
                    "ips": {"terms": {"field": ip_field, "size": 200}},
                    "first": {"min": {"field": "@timestamp"}},
                    "last": {"max": {"field": "@timestamp"}}
                }
            }
        }
    }
    try:
        resp = es_search(base_url, index, body)
        buckets = resp.get('aggregations', {}).get('by_user', {}).get('buckets', [])
        valid = any(b.get('key') not in (None, '', 'unknown') for b in buckets)
        return valid, buckets
    except requests.HTTPError:
        return False, []


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

    chosen = None
    buckets = []
    for uf in USER_FIELDS:
        for cf in COUNTRY_FIELDS:
            for ipf in IP_FIELDS:
                ok, b = try_agg(base_url, args.index, args.start, args.end, uf, cf, ipf)
                if ok:
                    chosen = (uf, cf, ipf)
                    buckets = b
                    break
            if chosen:
                break
        if chosen:
            break

    if not chosen:
        print(json.dumps([]))
        return

    users = []

    for b in buckets:
        user = b.get('key')
        countries = [c['key'] for c in b.get('countries', {}).get('buckets', []) if c.get('key') and c.get('key') != '??']
        non_eu = [c for c in countries if c not in eu]
        if not non_eu:
            continue
        first_val = b.get('first', {}).get('value')
        last_val = b.get('last', {}).get('value')
        first_ts = datetime.utcfromtimestamp(first_val / 1000).isoformat() + 'Z' if first_val is not None else None
        last_ts = datetime.utcfromtimestamp(last_val / 1000).isoformat() + 'Z' if last_val is not None else None

        covered = True
        for c in non_eu:
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
            "rationale": f"Successful (or likely successful) non-EU login(s): {','.join(non_eu)} not fully covered by exceptions"
        })

    print(json.dumps(users))


if __name__ == '__main__':
    main()
