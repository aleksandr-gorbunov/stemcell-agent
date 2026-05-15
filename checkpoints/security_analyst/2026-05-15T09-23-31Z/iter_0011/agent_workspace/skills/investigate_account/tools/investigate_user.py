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
    runtime = {
        "user_coalesced": {
            "type": "keyword",
            "script": {
                "source": "def v = params._source; \n def cands = new ArrayList(); \n if (v.containsKey('user')) { def u=v['user']; if (u instanceof Map && u.containsKey('email')) cands.add(u['email']); if (u instanceof Map && u.containsKey('name')) cands.add(u['name']); if (!(u instanceof Map)) cands.add(u); } \n if (v.containsKey('user.email')) cands.add(v['user.email']); \n if (cands.size()>0) emit(cands.get(0)); "
            }
        },
        "ip_coalesced": {
            "type": "ip",
            "script": {
                "source": "def v=params._source; if (v.containsKey('source') && v['source'] instanceof Map && v['source'].containsKey('ip')) emit(v['source']['ip']); else if (v.containsKey('client') && v['client'] instanceof Map && v['client'].containsKey('ip')) emit(v['client']['ip']); else if (v.containsKey('related') && v['related'] instanceof Map && v['related'].containsKey('ip')) emit(v['related']['ip']);"
            }
        },
        "country_coalesced": {
            "type": "keyword",
            "script": {
                "source": "def v=params._source; def g=null; if (v.containsKey('event') && v['event'] instanceof Map && v['event'].containsKey('geo')) g=v['event']['geo']; if (g==null && v.containsKey('source') && v['source'] instanceof Map && v['source'].containsKey('geo')) g=v['source']['geo']; if (g==null && v.containsKey('client') && v['client'] instanceof Map && v['client'].containsKey('geo')) g=v['client']['geo']; if (g==null && v.containsKey('geo')) g=v['geo']; if (g!=null && g instanceof Map && g.containsKey('country_iso_code')) emit(g['country_iso_code']); else if (v.containsKey('geoip') && v['geoip'] instanceof Map && v['geoip'].containsKey('country_iso_code')) emit(v['geoip']['country_iso_code']);"
            }
        }
    }

    body = {
        "size": 0,
        "runtime_mappings": runtime,
        "query": {"bool": {"must": [
            {"range": {"@timestamp": {"gte": start, "lt": end}}},
            {"term": {"user_coalesced": user}}
        ]}},
        "aggs": {
            "outcome": {"terms": {"field": "event.outcome", "size": 10}},
            "ips": {"terms": {"field": "ip_coalesced", "size": 1000}},
            "countries": {"terms": {"field": "country_coalesced", "size": 200}},
            "hours": {"date_histogram": {"field": "@timestamp", "fixed_interval": "1h", "min_doc_count": 0}},
            "examples": {"top_hits": {"size": 20, "sort": [{"@timestamp": {"order": "desc"}}],
                          "_source": {"includes": ["@timestamp","event.action","event.outcome","ip_coalesced","country_coalesced"]}}}
        }
    }
    resp = es_search(base_url, index, body)
    buckets = resp.get('aggregations', {}).get('hours', {}).get('buckets', [])
    off_hours = sum(1 for b in buckets if b.get('key_as_string') and int(b['key_as_string'][11:13]) < 6 and b.get('doc_count',0)>0)
    successes = next((x['doc_count'] for x in resp['aggregations'].get('outcome',{}).get('buckets',[]) if x['key'] in ['success','succeeded','login_success']), 0)
    failures = next((x['doc_count'] for x in resp['aggregations'].get('outcome',{}).get('buckets',[]) if x['key'] in ['failure','failed','login_failure','error']), 0)
    ip_b = resp['aggregations'].get('ips',{}).get('buckets',[])
    c_b = resp['aggregations'].get('countries',{}).get('buckets',[])
    hits = resp['aggregations'].get('examples',{}).get('hits',{}).get('hits',[])

    examples = []
    for h in hits:
        src = h.get('_source', {})
        examples.append({
            "@timestamp": src.get('@timestamp'),
            "action": src.get('event',{}).get('action'),
            "outcome": src.get('event',{}).get('outcome'),
            "ip": src.get('ip_coalesced'),
            "country": src.get('country_coalesced')
        })

    return {
        "events": sum(b.get('doc_count',0) for b in buckets),
        "successes": successes,
        "failures": failures,
        "ips": [x['key'] for x in ip_b if x.get('key')],
        "countries": [x['key'] for x in c_b if x.get('key')],
        "off_hours_buckets": off_hours,
        "examples": examples
    }


def fanout_for_ips(base_url, index, ips, start, end):
    if not ips:
        return []
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
    non_eu = [c for c in auth['countries'] if c and c not in eu_set]
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
