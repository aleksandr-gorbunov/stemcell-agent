import argparse
import json
import os
from pathlib import Path
import requests
import yaml
from datetime import datetime
import ipaddress


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


def all_ips_in_vpn(ips, vpn_cidrs):
    if not vpn_cidrs:
        return False
    networks = [ipaddress.ip_network(c) for c in vpn_cidrs]
    ok = True
    saw_any = False
    for ip in ips:
        try:
            ip_obj = ipaddress.ip_address(ip)
        except ValueError:
            continue
        saw_any = True
        if not any(ip_obj in n for n in networks):
            ok = False
            break
    return ok and saw_any


RUNTIME_MAPPINGS = {
    "user_r": {
        "type": "keyword",
        "script": {
            "source": ";".join([
                "def c(v){if(v!=null){emit(v instanceof List ? v[0] : v);return true;}return false;}",
                "if(c(doc['user.email.keyword'].value)){return;}",
                "if(c(doc['user.name.keyword'].value)){return;}",
                "if(c(doc['user.keyword'].value)){return;}"
            ])
        }
    },
    "country_r": {
        "type": "keyword",
        "script": {
            "source": ";".join([
                "def c(v){if(v!=null){emit(v instanceof List ? v[0] : v);return true;}return false;}",
                "if(c(doc['event.geo.country_iso_code.keyword'].value)){return;}",
                "if(c(doc['source.geo.country_iso_code.keyword'].value)){return;}",
                "if(c(doc['client.geo.country_iso_code.keyword'].value)){return;}",
                "if(c(doc['geo.country_iso_code.keyword'].value)){return;}",
                "if(c(doc['geoip.country_iso_code.keyword'].value)){return;}"
            ])
        }
    },
    "ip_r": {
        "type": "ip",
        "script": {
            "source": ";".join([
                "def e(v){if(v!=null){emit(v instanceof List ? v[0] : v);return true;}return false;}",
                "if(e(doc['source.ip'].value)){return;}",
                "if(e(doc['client.ip'].value)){return;}",
                "if(e(doc['related.ip'].value)){return;}"
            ])
        }
    }
}


SUCCESS_SHOULD = [
    {"terms": {"event.outcome": ["success", "succeeded", "login_success"]}},
    {"term": {"event.action": {"value": "login"}}},
    {"term": {"authentication.type": {"value": "success"}}},
    {"term": {"auth.result": {"value": "success"}}}
]


def run_query(base_url, index, start, end):
    body = {
        "size": 0,
        "runtime_mappings": RUNTIME_MAPPINGS,
        "query": {
            "bool": {
                "must": [{"range": {"@timestamp": {"gte": start, "lt": end}}}],
                "should": SUCCESS_SHOULD,
                "minimum_should_match": 0
            }
        },
        "aggs": {
            "by_user": {
                "terms": {"field": "user_r", "size": 10000, "missing": "unknown"},
                "aggs": {
                    "countries": {"terms": {"field": "country_r", "size": 200, "missing": "??"}},
                    "ips": {"terms": {"field": "ip_r", "size": 200}},
                    "first": {"min": {"field": "@timestamp"}},
                    "last": {"max": {"field": "@timestamp"}}
                }
            }
        }
    }
    return es_search(base_url, index, body)


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
    vpn_cidrs = env.get('exceptions', {}).get('vpn_cidrs', [])

    try:
        resp = run_query(base_url, args.index, args.start, args.end)
    except requests.HTTPError:
        print(json.dumps([]))
        return

    buckets = resp.get('aggregations', {}).get('by_user', {}).get('buckets', [])

    users = []
    for b in buckets:
        user = b.get('key')
        if not user or user == 'unknown':
            continue
        countries = [c['key'] for c in b.get('countries', {}).get('buckets', []) if c.get('key') and c.get('key') != '??']
        non_eu = [c for c in countries if c not in eu]
        if not non_eu:
            continue
        first_val = b.get('first', {}).get('value')
        last_val = b.get('last', {}).get('value')
        first_ts = datetime.utcfromtimestamp(first_val / 1000).isoformat() + 'Z' if first_val is not None else None
        last_ts = datetime.utcfromtimestamp(last_val / 1000).isoformat() + 'Z' if last_val is not None else None

        # Check travel exceptions: require entire window coverage for each non-EU country
        covered_by_travel = True
        for c in non_eu:
            if not (in_exception(user, c, args.start, exceptions) and in_exception(user, c, args.end, exceptions)):
                covered_by_travel = False
                break
        if covered_by_travel:
            continue

        ips = [ip['key'] for ip in b.get('ips', {}).get('buckets', [])]
        if all_ips_in_vpn(ips, vpn_cidrs):
            # Fully explained by VPN egress
            continue

        users.append({
            "user": user,
            "first_seen": first_ts,
            "last_seen": last_ts,
            "countries": countries,
            "non_eu_countries": non_eu,
            "ips": ips,
            "events": b.get('doc_count', 0),
            "rationale": f"Non-EU successful (or likely) logins not fully covered by travel/VPN exceptions"
        })

    print(json.dumps(users))


if __name__ == '__main__':
    main()
