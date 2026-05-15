import argparse
import json
import os
from pathlib import Path
from datetime import datetime
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


def summarize_auth(base_url, index, user, start, end):
    body = {
        "size": 0,
        "query": {"bool": {"must": [
            {"range": {"@timestamp": {"gte": start, "lt": end}}},
            {"term": {"user.email.keyword": user}}
        ]}},
        "aggs": {
            "outcome": {"terms": {"field": "event.outcome", "size": 10}},
            "ips": {"terms": {"field": "source.ip", "size": 1000}},
            "countries": {"terms": {"field": "event.geo.country_iso_code.keyword", "size": 200}},
            "hours": {"date_histogram": {"field": "@timestamp", "fixed_interval": "1h", "min_doc_count": 0}}
        }
    }
    resp = es_search(base_url, index, body)
    buckets = resp.get('aggregations', {}).get('hours', {}).get('buckets', [])
    off_hours = sum(1 for b in buckets if b.get('key_as_string') and int(b['key_as_string'][11:13]) < 6 and b.get('doc_count',0)>0)
    return {
        "events": sum(b.get('doc_count',0) for b in buckets),
        "successes": next((x['doc_count'] for x in resp['aggregations']['outcome']['buckets'] if x['key'] in ['success','succeeded','login_success']), 0),
        "failures": next((x['doc_count'] for x in resp['aggregations']['outcome']['buckets'] if x['key'] in ['failure','failed','login_failure','error']), 0),
        "ips": [x['key'] for x in resp['aggregations']['ips']['buckets']],
        "countries": [x['key'] for x in resp['aggregations']['countries']['buckets']],
        "off_hours_buckets": off_hours
    }


def fanout_for_ips(base_url, index, ips, start, end):
    if not ips:
        return {}
    body = {
        "size": 0,
        "query": {"bool": {"must": [
            {"range": {"@timestamp": {"gte": start, "lt": end}}},
            {"terms": {"source.ip": ips}},
            {"regexp": {"destination.ip": "(10\\..*|192\\.168\\..*|172\\.(1[6-9]|2[0-9]|3[0-1])\\..*)"}}
        ]}},
        "aggs": {
            "by_src": {"terms": {"field": "source.ip", "size": 1000},
                        "aggs": {"uniq_dst": {"cardinality": {"field": "destination.ip"}},
                                  "flows": {"value_count": {"field": "destination.ip"}}}}
        }
    }
    resp = es_search(base_url, index, body)
    rows = []
    for b in resp.get('aggregations', {}).get('by_src', {}).get('buckets', []):
        rows.append({
            "src_ip": b['key'],
            "unique_dsts": int(b['uniq_dst']['value']),
            "total_flows": int(b['flows']['value'])
        })
    return rows


def dns_for_ips(base_url, index, ips, start, end):
    if not ips:
        return []
    body = {
        "size": 0,
        "query": {"bool": {"must": [
            {"range": {"@timestamp": {"gte": start, "lt": end}}},
            {"terms": {"source.ip": ips}}
        ]}},
        "aggs": {
            "by_src": {"terms": {"field": "source.ip", "size": 1000},
                        "aggs": {"names": {"terms": {"field": "dns.question.name.keyword", "size": 100000}}}}
        }
    }
    resp = es_search(base_url, index, body)
    out = []
    for b in resp.get('aggregations', {}).get('by_src', {}).get('buckets', []):
        names = [n['key'] for n in b.get('names', {}).get('buckets', []) if n.get('key')]
        out.append({"src_ip": b['key'], "names": names})
    return out


def verdict_from(auth, eu_set, fanout_rows, dns_rows):
    reasons = []
    score = 0
    non_eu = [c for c in auth['countries'] if c not in eu_set]
    if non_eu:
        reasons.append(f"Non-EU geo: {','.join(non_eu)}")
        score += 1
    if auth['failures'] >= 3 and auth['successes'] >= 1:
        reasons.append("Multiple failures preceding success")
        score += 1
    if auth['off_hours_buckets'] >= 3:
        reasons.append("Off-hours activity spikes")
        score += 1
    for r in fanout_rows:
        if r['unique_dsts'] >= 40:
            reasons.append(f"High internal fan-out from {r['src_ip']} ({r['unique_dsts']} dests)")
            score += 1
    # Simple DNS tunneling hint: many long random labels
    for d in dns_rows:
        long_labels = [n for n in d['names'] if any(len(part) >= 25 for part in n.split('.'))]
        if len(long_labels) >= 100:
            reasons.append(f"Possible DNS tunneling from {d['src_ip']}")
            score += 1
    verdict = 'benign'
    if score >= 3:
        verdict = 'compromised'
    elif score == 2:
        verdict = 'suspicious'
    return verdict, reasons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--user', required=True)
    ap.add_argument('--start', required=True)
    ap.add_argument('--end', required=True)
    ap.add_argument('--auth-index', default='auth_logs')
    ap.add_argument('--net-index', default='network')
    ap.add_argument('--dns-index', default='dns')
    args = ap.parse_args()

    env = load_env()
    base_url = env.get('es', {}).get('base_url', 'http://localhost:9200')
    eu = set((env.get('policy', {}).get('eu_country_codes')) or [])

    auth = summarize_auth(base_url, args.auth_index, args.user, args.start, args.end)
    fanout_rows = fanout_for_ips(base_url, args.net_index, auth['ips'], args.start, args.end)
    dns_rows = dns_for_ips(base_url, args.dns_index, auth['ips'], args.start, args.end)

    verdict, reasons = verdict_from(auth, eu, fanout_rows, dns_rows)

    result = {
        "user": args.user,
        "auth_summary": auth,
        "network_anomalies": fanout_rows,
        "dns_summary": [{"src_ip": d['src_ip'], "names_count": len(d['names'])} for d in dns_rows],
        "verdict": verdict,
        "reasons": reasons
    }
    print(json.dumps(result))


if __name__ == '__main__':
    main()
