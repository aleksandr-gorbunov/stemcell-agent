import argparse
import ipaddress
import json
import math
import os
from datetime import datetime
from pathlib import Path
import requests
import yaml

SLD_SET = {"co", "com", "net", "org", "gov", "ac"}


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
    r = requests.post(url, headers={"Content-Type": "application/json"}, json=body, timeout=60)
    r.raise_for_status()
    return r.json()


def base_domain(name: str) -> str:
    parts = name.lower().strip('.').split('.')
    if len(parts) < 2:
        return name.lower()
    tld = parts[-1]
    sld = parts[-2] if len(parts) >= 2 else ""
    if len(tld) == 2 and len(parts) >= 3 and sld in SLD_SET:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def sub_label(name: str, base: str):
    if name.endswith(base):
        rest = name[:-(len(base))].rstrip('.')
        return rest if rest else None
    return None


def detect_lateral(env, start, end, min_unique=20, pctl=95):
    base_url = env.get('es', {}).get('base_url', 'http://localhost:9200')
    index = env.get('es', {}).get('indices', {}).get('net', 'network')
    body = {
        "size": 0,
        "query": {"bool": {"must": [
            {"range": {"@timestamp": {"gte": start, "lt": end}}},
            {"regexp": {"destination.ip": "(10\\..*|192\\.168\\..*|172\\.(1[6-9]|2[0-9]|3[0-1])\\..*)"}}
        ]}},
        "aggs": {
            "by_src": {
                "terms": {"field": "source.ip", "size": 10000},
                "aggs": {
                    "uniq_dst": {"cardinality": {"field": "destination.ip"}},
                    "flows": {"value_count": {"field": "destination.ip"}},
                    "per_day": {
                        "date_histogram": {"field": "@timestamp", "fixed_interval": "1d"},
                        "aggs": {"d": {"cardinality": {"field": "destination.ip"}}}
                    },
                    "first": {"min": {"field": "@timestamp"}},
                    "last": {"max": {"field": "@timestamp"}}
                }
            }
        }
    }
    resp = es_search(base_url, index, body)
    rows = []
    uniq_values = []
    for b in resp.get('aggregations', {}).get('by_src', {}).get('buckets', []):
        src = b.get('key')
        uniq = b.get('uniq_dst', {}).get('value', 0)
        flows = b.get('flows', {}).get('value', 0)
        by_day = [{"date": bd.get('key_as_string', '')[:10], "count": int(bd.get('d', {}).get('value', 0))} for bd in b.get('per_day', {}).get('buckets', [])]
        first_v = b.get('first', {}).get('value')
        last_v = b.get('last', {}).get('value')
        first_ts = (datetime.utcfromtimestamp(first_v/1000).isoformat()+'Z') if first_v is not None else None
        last_ts = (datetime.utcfromtimestamp(last_v/1000).isoformat()+'Z') if last_v is not None else None
        rows.append((src, uniq, flows, by_day, first_ts, last_ts))
        uniq_values.append(uniq)
    uniq_values_sorted = sorted(uniq_values)
    if uniq_values_sorted:
        k = max(0, min(len(uniq_values_sorted)-1, int(math.ceil(pctl/100.0 * len(uniq_values_sorted)))-1))
        thresh = uniq_values_sorted[k]
    else:
        thresh = min_unique
    findings = []
    for src, uniq, flows, by_day, first_ts, last_ts in rows:
        if uniq >= max(min_unique, thresh):
            findings.append({
                "type": "lateral_movement",
                "entity": src,
                "window": {"start": start, "end": end},
                "evidence": {
                    "unique_dsts": int(uniq),
                    "total_flows": int(flows),
                    "pctl_threshold": int(thresh),
                    "by_day": by_day,
                    "first_seen": first_ts,
                    "last_seen": last_ts
                }
            })
    return findings


def detect_dns_tunnel(env, start, end, min_total=200, min_fraction=0.5, top_n=5):
    base_url = env.get('es', {}).get('base_url', 'http://localhost:9200')
    index = env.get('es', {}).get('indices', {}).get('dns', 'dns')
    body = {
        "size": 0,
        "query": {"bool": {"must": [
            {"range": {"@timestamp": {"gte": start, "lt": end}}}
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
    resp = es_search(base_url, index, body)
    strict = []
    cands = []
    for b in resp.get('aggregations', {}).get('by_src', {}).get('buckets', []):
        src = b.get('key')
        name_buckets = b.get('names', {}).get('buckets', [])
        by_base = {}
        for nb in name_buckets:
            fq = nb.get('key')
            cnt = int(nb.get('doc_count', 0))
            if not fq or cnt <= 0:
                continue
            bd = base_domain(fq)
            by_base.setdefault(bd, []).append((fq, cnt))
        for bd, fq_items in by_base.items():
            total = 0
            subs = []
            for fq, cnt in fq_items:
                total += cnt
                lab = sub_label(fq, bd)
                if lab:
                    subs.append(lab)
            if not subs:
                continue
            unique_left = set(s.split('.')[-1] for s in subs)
            high = 0
            samples = []
            for left in unique_left:
                # entropy on left-most token of left-most label
                H = 0.0
                freq = {}
                for ch in left:
                    freq[ch] = freq.get(ch, 0) + 1
                L = len(left)
                for c in freq.values():
                    p = c / L if L else 0
                    if p > 0:
                        H -= p * math.log2(p)
                if len(left) >= 12 and H >= 3.5:
                    high += 1
                    if len(samples) < 5:
                        samples.append(left)
            frac = high / max(1, len(unique_left))
            score = frac * math.log1p(total)
            rec = {
                "src_ip": src,
                "base_domain": bd,
                "total": total,
                "unique_labels": len(unique_left),
                "frac_high_entropy": round(frac, 3),
                "score": round(score, 3),
                "example_labels": samples,
            }
            cands.append(rec)
            if total >= min_total and frac >= min_fraction:
                strict.append(rec)
    out = strict
    if not out:
        filtered = [c for c in cands if c["total"] >= 100 and c["unique_labels"] >= 20]
        filtered.sort(key=lambda x: x["score"], reverse=True)
        out = filtered[: max(0, top_n)]
    else:
        out.sort(key=lambda x: x["score"], reverse=True)
    findings = []
    for r in out:
        findings.append({
            "type": "dns_tunneling",
            "entity": r["src_ip"],
            "window": {"start": start, "end": end},
            "evidence": r
        })
    return findings


def detect_non_eu_logins(env, start, end):
    base_url = env.get('es', {}).get('base_url', 'http://localhost:9200')
    index = env.get('es', {}).get('indices', {}).get('auth', 'auth_logs')
    eu = set((env.get('policy', {}).get('eu_country_codes')) or [])
    exceptions = env.get('exceptions', {}).get('travel', [])
    USER_FIELDS = ["user.email.keyword", "user.name.keyword", "user.keyword"]
    COUNTRY_FIELDS = [
        "event.geo.country_iso_code.keyword",
        "source.geo.country_iso_code.keyword",
        "client.geo.country_iso_code.keyword",
        "geo.country_iso_code.keyword",
        "geoip.country_iso_code.keyword"
    ]
    IP_FIELDS = ["source.ip", "client.ip", "related.ip"]

    def in_exception(user, country):
        for ex in exceptions:
            if ex.get('user') == user:
                start_ex = ex.get('start')
                end_ex = ex.get('end')
                countries = ex.get('countries', [])
                ok_country = (not countries) or (country in countries)
                if ok_country:
                    if (not start_ex or start >= start_ex) and (not end_ex or end <= end_ex):
                        return True
        return False

    chosen = None
    buckets = []
    for uf in USER_FIELDS:
        for cf in COUNTRY_FIELDS:
            for ipf in IP_FIELDS:
                must = [{"range": {"@timestamp": {"gte": start, "lt": end}}}]
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
                            "terms": {"field": uf, "size": 10000, "missing": "unknown"},
                            "aggs": {
                                "countries": {"terms": {"field": cf, "size": 200, "missing": "??"}},
                                "ips": {"terms": {"field": ipf, "size": 200}},
                                "first": {"min": {"field": "@timestamp"}},
                                "last": {"max": {"field": "@timestamp"}}
                            }
                        }
                    }
                }
                try:
                    resp = es_search(base_url, index, body)
                    bks = resp.get('aggregations', {}).get('by_user', {}).get('buckets', [])
                    valid = any(b.get('key') not in (None, '', 'unknown') for b in bks)
                    if valid:
                        chosen = (uf, cf, ipf)
                        buckets = bks
                        break
                except requests.HTTPError:
                    pass
            if chosen:
                break
        if chosen:
            break
    if not chosen:
        return []

    out = []
    for b in buckets:
        user = b.get('key')
        if not user or user == 'unknown':
            continue
        countries = [c['key'] for c in b.get('countries', {}).get('buckets', []) if c.get('key') and c.get('key') != '??']
        non_eu = [c for c in countries if c not in eu]
        if not non_eu:
            continue
        # if fully covered by exception window, skip
        covered = all(in_exception(user, c) for c in non_eu)
        if covered:
            continue
        ips = [ip['key'] for ip in b.get('ips', {}).get('buckets', []) if ip.get('key')]
        first_v = b.get('first', {}).get('value')
        last_v = b.get('last', {}).get('value')
        first_ts = (datetime.utcfromtimestamp(first_v/1000).isoformat()+'Z') if first_v is not None else None
        last_ts = (datetime.utcfromtimestamp(last_v/1000).isoformat()+'Z') if last_v is not None else None
        out.append({
            "type": "non_eu_login",
            "entity": user,
            "window": {"start": start, "end": end},
            "evidence": {
                "countries": countries,
                "non_eu_countries": non_eu,
                "ips": ips,
                "first_seen": first_ts,
                "last_seen": last_ts,
                "events": b.get('doc_count', 0)
            }
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', required=True)
    ap.add_argument('--end', required=True)
    ap.add_argument('--min-unique', type=int, default=20)
    ap.add_argument('--pctl', type=int, default=95)
    ap.add_argument('--min-total-dns', type=int, default=200)
    ap.add_argument('--min-frac-dns', type=float, default=0.5)
    ap.add_argument('--topn-dns', type=int, default=5)
    args = ap.parse_args()

    env = load_env()

    incidents = []
    try:
        incidents.extend(detect_non_eu_logins(env, args.start, args.end))
    except Exception as e:
        pass
    try:
        incidents.extend(detect_lateral(env, args.start, args.end, args.min_unique, args.pctl))
    except Exception as e:
        pass
    try:
        incidents.extend(detect_dns_tunnel(env, args.start, args.end, args.min_total_dns, args.min_frac_dns, args.topn_dns))
    except Exception as e:
        pass

    print(json.dumps(incidents))


if __name__ == '__main__':
    main()
