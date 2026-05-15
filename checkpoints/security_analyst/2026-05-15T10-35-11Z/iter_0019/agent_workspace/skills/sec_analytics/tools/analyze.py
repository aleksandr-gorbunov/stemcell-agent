import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
import urllib.request
import urllib.parse
import ipaddress
import re

import yaml


def load_env():
    workspace = Path(os.environ.get("STEMCELL_AGENT_WORKSPACE", "."))
    env = yaml.safe_load(workspace.joinpath("environment.yaml").read_text()) or {}
    base = env.get("opensearch", {}).get("base_url", "http://localhost:9200")
    idx = env.get("indices", {"auth": "auth_logs", "network": "network", "dns": "dns"})
    eu = set(env.get("eu_countries", []))
    allow = env.get("allowlists", {})
    internal = env.get("internal", {})
    return base, idx, eu, allow, internal


def http_post_json(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def search(index, body, base_url):
    url = f"{base_url.rstrip('/')}/{index}/_search"
    return http_post_json(url, body)


def parse_iso(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def daterange_days(start_dt, end_dt):
    cur = start_dt.date()
    end_date = (end_dt - timedelta(microseconds=1)).date()
    while cur <= end_date:
        yield cur
        cur = cur + timedelta(days=1)


def ip_in_range(ip_str, start_end):
    start_s, end_s = start_end.split("-")
    ip_int = int(ipaddress.ip_address(ip_str))
    return int(ipaddress.ip_address(start_s)) <= ip_int <= int(ipaddress.ip_address(end_s))


def ip_in_any_range(ip_str, ranges):
    for r in ranges:
        if "/" in r:
            if ipaddress.ip_address(ip_str) in ipaddress.ip_network(r, strict=False):
                return True
        elif "-" in r:
            if ip_in_range(ip_str, r):
                return True
        else:
            if ip_str == r:
                return True
    return False


def weekday_name(d):
    return ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"][d.weekday()]


# ---------- Subcommands ----------

def cmd_health_partner(args):
    base, idx, eu, allow, internal = load_env()
    start = parse_iso(args['--start'])
    end = parse_iso(args['--end'])

    src_range = allow.get("partner_monitoring_range", "198.51.100.10-198.51.100.20")
    per_day = {}
    for day in daterange_days(start, end):
        day_start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        body = {
            "query": {
                "bool": {
                    "filter": [
                        {"range": {"@timestamp": {"gte": day_start.isoformat().replace("+00:00","Z"), "lt": day_end.isoformat().replace("+00:00","Z")}}},
                        {"term": {"event.action": "login_attempt"}},
                        {"term": {"event.outcome": "failure"}},
                        {"term": {"user.name": "monitor@partneracme.com"}},
                        {"term": {"url.path": "/api/auth/health"}},
                    ]
                }
            },
            "size": 0,
            "aggs": {"by_src": {"terms": {"field": "source.ip", "size": 50}}}
        }
        resp = search(idx["auth"], body, base)
        total = resp.get("hits", {}).get("total", {}).get("value", 0)
        # filter src by partner range
        count = 0
        for b in resp.get("aggregations", {}).get("by_src", {}).get("buckets", []):
            if ip_in_range(b["key"], src_range):
                count += b["doc_count"]
        per_day[day.isoformat()] = count if count else 0

    # Determine status
    zeros = [d for d, c in per_day.items() if c == 0]
    if len(zeros) == len(per_day):
        status = "outage"
    elif zeros:
        status = "degraded"
    else:
        status = "healthy"

    print(json.dumps({"service": "partner_monitoring", "status": status, "daily_events": per_day}))


def cmd_health_cicd(args):
    base, idx, eu, allow, internal = load_env()
    start = parse_iso(args['--start'])
    end = parse_iso(args['--end'])

    cicd_host = internal.get("cicd_host", "10.0.0.40")
    app_range = [internal.get("app_range", "10.0.0.60-10.0.0.62")]
    db_range = [internal.get("db_range", "10.0.0.50-10.0.0.54")]
    ext_cidrs = allow.get("ci_cd_external_cidrs", [])

    per_day = {}
    for day in daterange_days(start, end):
        weekday = weekday_name(datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc))
        expected = weekday in ["Monday","Tuesday","Wednesday","Thursday","Friday"]
        w_start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=3)
        w_end = w_start + timedelta(hours=1)
        body = {
            "query": {
                "bool": {
                    "filter": [
                        {"range": {"@timestamp": {"gte": w_start.isoformat().replace("+00:00","Z"), "lt": w_end.isoformat().replace("+00:00","Z")}}},
                        {"term": {"source.ip": cicd_host}},
                        {"term": {"event.action": "allow"}}
                    ]
                }
            },
            "size": 0,
            "aggs": {"dests": {"terms": {"field": "destination.ip", "size": 200}}}
        }
        resp = search(idx["network"], body, base)
        dests = [b["key"] for b in resp.get("aggregations", {}).get("dests", {}).get("buckets", [])]
        to_app = any(ip_in_any_range(d, app_range) for d in dests)
        to_db = any(ip_in_any_range(d, db_range) for d in dests)
        to_ext = any(ip_in_any_range(d, ext_cidrs) for d in dests)
        ok = to_app and to_db and to_ext if expected else True
        per_day[day.isoformat()] = {"expected": expected, "to_app": to_app, "to_db": to_db, "to_ext": to_ext, "ok": ok}

    if all(v["ok"] for v in per_day.values()):
        status = "healthy"
    elif any(v["ok"] for v in per_day.values()):
        status = "degraded"
    else:
        status = "outage"
    print(json.dumps({"service": "ci_cd_pipeline", "status": status, "daily": per_day}))


def cmd_audit_users(args):
    base, idx, eu, allow, internal = load_env()
    body = {
        "query": {
            "bool": {
                "filter": [
                    {"range": {"@timestamp": {"gte": args['--start'], "lt": args['--end']}}},
                    {"term": {"event.action": "login_attempt"}},
                    {"term": {"event.outcome": "success"}}
                ]
            }
        },
        "size": 0,
        "aggs": {
            "by_user": {
                "terms": {"field": "user.name", "size": 1000},
                "aggs": {
                    "by_country": {"terms": {"field": "source.geo.country", "size": 50}},
                    "by_src": {"terms": {"field": "source.ip", "size": 50}},
                    "first": {"min": {"field": "@timestamp"}},
                    "last": {"max": {"field": "@timestamp"}}
                }
            }
        }
    }
    resp = search(idx["auth"], body, base)
    results = []

    allowed_sales = {
        "sales_apac@example.com": set(["SG","JP","KR","AU","HK","IN","TW","TH","PH","ID","VN","MY"]),
        "sales_americas@example.com": set(["US","CA","BR","MX","AR","CL","CO","PE","UY"]),
        "sales_emea@example.com": set(list(eu)),
    }

    for b in resp.get("aggregations", {}).get("by_user", {}).get("buckets", []):
        user = b["key"]
        countries = [c["key"] for c in b.get("by_country", {}).get("buckets", [])]
        outside_eu = [c for c in countries if c not in eu]
        if not outside_eu:
            continue
        if user == "cfo@example.com":
            body_hits = {
                "query": {"bool": {"filter": [
                    {"range": {"@timestamp": {"gte": args['--start'], "lt": args['--end']}}},
                    {"term": {"event.action": "login_attempt"}},
                    {"term": {"event.outcome": "success"}},
                    {"term": {"user.name": user}}
                ]}}, "_source": ["@timestamp","source.geo.country"], "size": 200}
            hits = search(idx["auth"], body_hits, base).get("hits", {}).get("hits", [])
            weekdays = set(weekday_name(parse_iso(h["_source"]["@timestamp"])) for h in hits)
            if all((cn != "SG" or ("Wednesday" in weekdays or "Thursday" in weekdays)) for cn in countries):
                pass
            else:
                results.append({"user": user, "countries": countries, "reason": "CFO outside allowed Wed/Thu SG pattern"})
            continue
        if user in allowed_sales:
            disallowed = [c for c in countries if c not in allowed_sales[user]]
            if disallowed:
                results.append({"user": user, "countries": countries, "reason": "sales region outside allowlist"})
            continue
        results.append({"user": user, "countries": countries, "reason": "outside EU successful login"})

    print(json.dumps({"users_to_investigate": results}))


def entropy(s):
    import math
    if not s:
        return 0.0
    p = {}
    for ch in s:
        p[ch] = p.get(ch, 0) + 1
    l = len(s)
    return -sum((c/l) * math.log2(c/l) for c in p.values())


def cmd_dns_tunnel(args):
    base, idx, eu, allow, internal = load_env()
    body = {
        "query": {"range": {"@timestamp": {"gte": args['--start'], "lt": args['--end']}}},
        "size": 0,
        "aggs": {
            "by_src": {
                "terms": {"field": "source.ip", "size": 200},
                "aggs": {
                    "total": {"value_count": {"field": "query.name"}},
                    "by_q": {"terms": {"field": "query.name", "size": 10000}},
                    "by_type": {"terms": {"field": "query.type", "size": 20}},
                    "by_rcode": {"terms": {"field": "response.code", "size": 20}}
                }
            }
        }
    }
    resp = search(idx["dns"], body, base)
    suspects = []
    for b in resp.get("aggregations", {}).get("by_src", {}).get("buckets", []):
        src = b["key"]
        total = b.get("doc_count", 0)
        counts = {}
        subdomains = {}
        for qb in b.get("by_q", {}).get("buckets", []):
            q = qb["key"]
            parts = q.split('.')
            if len(parts) < 2:
                base_dom = q
                left = ""
            else:
                base_dom = '.'.join(parts[-2:])
                left = '.'.join(parts[:-2])
            counts[base_dom] = counts.get(base_dom, 0) + qb["doc_count"]
            if left:
                subdomains.setdefault(base_dom, set()).add(left)
        if not counts:
            continue
        top_dom = max(counts, key=counts.get)
        top_count = counts[top_dom]
        uniq_sub = len(subdomains.get(top_dom, []))
        rcode_buckets = {x["key"]: x["doc_count"] for x in b.get("by_rcode", {}).get("buckets", [])}
        nx = rcode_buckets.get("NXDOMAIN", 0) + rcode_buckets.get("3", 0)
        types = {x["key"]: x["doc_count"] for x in b.get("by_type", {}).get("buckets", [])}
        txt_like = types.get("TXT", 0) + types.get("NULL", 0)
        score = 0
        if top_count >= 500: score += 2
        if uniq_sub >= 50: score += 2
        if nx / max(total, 1) >= 0.3: score += 1
        if txt_like / max(total, 1) >= 0.1: score += 1
        if score >= 3:
            suspects.append({"source": src, "domain": top_dom, "total": int(total), "to_domain": int(top_count), "unique_subs": int(uniq_sub), "nx_ratio": round(nx/max(total,1),3), "txt_ratio": round(txt_like/max(total,1),3)})
    print(json.dumps({"dns_tunneling": suspects}))


def cmd_lateral(args):
    base, idx, eu, allow, internal = load_env()
    body = {
        "query": {
            "bool": {"filter": [
                {"range": {"@timestamp": {"gte": args['--start'], "lt": args['--end']}}},
                {"prefix": {"source.ip": "10."}},
                {"prefix": {"destination.ip": "10."}},
                {"term": {"event.action": "allow"}}
            ]}
        },
        "size": 0,
        "aggs": {
            "by_src": {"terms": {"field": "source.ip", "size": 1000},
                       "aggs": {
                           "uniq_dests": {"cardinality": {"field": "destination.ip"}},
                           "bytes_out": {"sum": {"field": "network.bytes_out"}}
                       }}
        }
    }
    resp = search(idx["network"], body, base)
    suspects = []
    for b in resp.get("aggregations", {}).get("by_src", {}).get("buckets", []):
        src = b["key"]
        uniq = int(b.get("uniq_dests", {}).get("value", 0))
        if src == internal.get("backup_host"):
            continue
        if src == internal.get("cicd_host"):
            continue
        if uniq >= 15:
            suspects.append({"source": src, "unique_internal_dests": uniq, "bytes_out": int(b.get("bytes_out", {}).get("value", 0))})
    print(json.dumps({"lateral_movement": suspects}))


def cmd_investigate(args):
    user = args["--user"]
    base, idx, eu, allow, internal = load_env()
    # 1) Auth timeline for user
    body_auth = {
        "query": {"bool": {"filter": [
            {"range": {"@timestamp": {"gte": args['--start'], "lt": args['--end']}}},
            {"term": {"user.name": user}},
            {"term": {"event.action": "login_attempt"}}
        ]}},
        "size": 500,
        "sort": [{"@timestamp": "asc"}],
        "_source": ["@timestamp","event.outcome","source.ip","source.geo.country","user_agent.name"]
    }
    auth_hits = search(idx["auth"], body_auth, base).get("hits", {}).get("hits", [])
    successes = [h for h in auth_hits if h["_source"].get("event.outcome") == "success"]
    countries = [h["_source"].get("source.geo.country") for h in successes if h["_source"].get("source.geo.country")]
    uniq_countries = sorted(set(countries))
    outside_eu = [c for c in uniq_countries if c not in eu]
    # impossible travel: <6h between consecutive successes with different country
    impossible = []
    for i in range(len(successes)-1):
        c1 = successes[i]["_source"].get("source.geo.country")
        c2 = successes[i+1]["_source"].get("source.geo.country")
        if not c1 or not c2 or c1 == c2:
            continue
        t1 = parse_iso(successes[i]["_source"]["@timestamp"]) 
        t2 = parse_iso(successes[i+1]["_source"]["@timestamp"]) 
        if (t2 - t1) < timedelta(hours=6):
            impossible.append({"from": c1, "to": c2, "t1": successes[i]["_source"]["@timestamp"], "t2": successes[i+1]["_source"]["@timestamp"]})
    # UA change
    ua_names = [h["_source"].get("user_agent.name") for h in successes if h["_source"].get("user_agent.name")]
    ua_changed = len(set(ua_names)) > 1
    # failures burst before success
    burst_then_success = False
    for i in range(len(auth_hits)-1):
        if auth_hits[i]["_source"].get("event.outcome") == "failure" and auth_hits[i+1]["_source"].get("event.outcome") == "success":
            burst_then_success = True; break
    # 2) Post-login network anomalies in window (coarse)
    body_exf = {
        "query": {"bool": {"filter": [
            {"range": {"@timestamp": {"gte": args['--start'], "lt": args['--end']}}},
            {"term": {"event.action": "allow"}},
            {"bool": {"must_not": {"prefix": {"destination.ip": "10."}}}}
        ]}},
        "size": 0,
        "aggs": {"by_src": {"terms": {"field": "source.ip", "size": 200}, "aggs": {"bytes_out": {"sum": {"field": "network.bytes_out"}}, "first": {"min": {"field": "@timestamp"}}, "last": {"max": {"field": "@timestamp"}}}}}
    }
    exf = search(idx["network"], body_exf, base).get("aggregations", {}).get("by_src", {}).get("buckets", [])
    heavy = [b for b in exf if b.get("bytes_out", {}).get("value", 0) >= 50_000_000 and b["key"].startswith("10.")]
    # 3) DNS tunneling suspects in window
    body_dns = {
        "query": {"range": {"@timestamp": {"gte": args['--start'], "lt": args['--end']}}},
        "size": 0,
        "aggs": {"by_src": {"terms": {"field": "source.ip", "size": 200}, "aggs": {"by_q": {"terms": {"field": "query.name", "size": 10000}}, "total": {"value_count": {"field": "query.name"}}}}}
    }
    dns = search(idx["dns"], body_dns, base).get("aggregations", {}).get("by_src", {}).get("buckets", [])
    dns_sus = []
    for b in dns:
        counts = {}
        subs = {}
        total = b.get("total", {}).get("value", b.get("doc_count", 0))
        for qb in b.get("by_q", {}).get("buckets", []):
            q = qb["key"]
            parts = q.split('.')
            if len(parts) >= 2:
                base_dom = '.'.join(parts[-2:])
                left = '.'.join(parts[:-2])
            else:
                base_dom = q; left = ""
            counts[base_dom] = counts.get(base_dom, 0) + qb["doc_count"]
            if left:
                subs.setdefault(base_dom, set()).add(left)
        if not counts:
            continue
        top_dom = max(counts, key=counts.get)
        if counts[top_dom] >= 500 and len(subs.get(top_dom, [])) >= 50:
            dns_sus.append({"source": b["key"], "domain": top_dom, "count": int(counts[top_dom])})

    evidence = []
    if outside_eu:
        evidence.append(f"Successful login from outside EU: {outside_eu}")
    for imp in impossible:
        evidence.append(f"Impossible travel {imp['from']}->{imp['to']} between {imp['t1']} and {imp['t2']}")
    if ua_changed:
        evidence.append("User-agent changed across successes")
    if burst_then_success:
        evidence.append("Burst of failures immediately followed by success")
    for b in heavy:
        evidence.append(f"Large external egress from {b['key']} totaling {int(b['bytes_out']['value'])} bytes during window")
    for d in dns_sus:
        evidence.append(f"DNS tunneling pattern from {d['source']} to {d['domain']} (~{d['count']} queries)")

    score = 0
    if outside_eu: score += 2
    if impossible: score += 2
    if ua_changed: score += 1
    if burst_then_success: score += 1
    if heavy: score += 1
    if dns_sus: score += 1

    if score >= 4: likelihood = "high"
    elif score >= 2: likelihood = "medium"
    else: likelihood = "low"

    print(json.dumps({"user": user, "compromise_likelihood": likelihood, "evidence": evidence}))


def cmd_incidents(args):
    base, idx, eu, allow, internal = load_env()
    results = []
    body_burst = {
        "query": {"bool": {"filter": [
            {"range": {"@timestamp": {"gte": args['--start'], "lt": args['--end']}}},
            {"term": {"event.action": "login_attempt"}},
            {"term": {"event.outcome": "failure"}}
        ]}},
        "size": 0,
        "aggs": {"by_src": {"terms": {"field": "source.ip", "size": 200}, "aggs": {"uniq_users": {"cardinality": {"field": "user.name"}}, "count": {"value_count": {"field": "user.name"}}}}}
    }
    burst = search(idx["auth"], body_burst, base).get("aggregations", {}).get("by_src", {}).get("buckets", [])
    pen_ip = allow.get("pen_test_ip", "198.51.100.50")
    for b in burst:
        if b.get("uniq_users", {}).get("value", 0) >= 20 and b.get("count", {}).get("value", 0) >= 200 and b["key"] != pen_ip:
            results.append({"type": "credential_stuffing", "indicator": b["key"], "evidence": f"{int(b['count']['value'])} failed logins across {int(b['uniq_users']['value'])} users"})
    body_exf = {
        "query": {"bool": {"filter": [
            {"range": {"@timestamp": {"gte": args['--start'], "lt": args['--end']}}},
            {"term": {"event.action": "allow"}},
            {"bool": {"must_not": {"prefix": {"destination.ip": "10."}}}}
        ]}},
        "size": 0,
        "aggs": {"by_src": {"terms": {"field": "source.ip", "size": 200}, "aggs": {"bytes_out": {"sum": {"field": "network.bytes_out"}}}}}
    }
    exf = search(idx["network"], body_exf, base).get("aggregations", {}).get("by_src", {}).get("buckets", [])
    for b in exf:
        src = b["key"]
        if src == internal.get("cicd_host"):
            continue
        bo = b.get("bytes_out", {}).get("value", 0)
        if bo >= 50_000_000:
            results.append({"type": "data_exfiltration", "indicator": src, "evidence": f"{int(bo)} bytes to external destinations"})
    body_lat = {
        "query": {"bool": {"filter": [
            {"range": {"@timestamp": {"gte": args['--start'], "lt": args['--end']}}},
            {"prefix": {"source.ip": "10."}}, {"prefix": {"destination.ip": "10."}}, {"term": {"event.action": "allow"}}
        ]}}, "size": 0, "aggs": {"by_src": {"terms": {"field": "source.ip", "size": 1000}, "aggs": {"uniq_dests": {"cardinality": {"field": "destination.ip"}}}}}
    }
    lat = search(idx["network"], body_lat, base).get("aggregations", {}).get("by_src", {}).get("buckets", [])
    for b in lat:
        src = b["key"]
        if src in [internal.get("backup_host"), internal.get("cicd_host")]:
            continue
        if int(b.get("uniq_dests", {}).get("value", 0)) >= 15:
            results.append({"type": "lateral_movement", "indicator": src, "evidence": f"connected to {int(b['uniq_dests']['value'])} internal hosts"})
    body_dns = {
        "query": {"range": {"@timestamp": {"gte": args['--start'], "lt": args['--end']}}},
        "size": 0,
        "aggs": {"by_src": {"terms": {"field": "source.ip", "size": 200}, "aggs": {"by_q": {"terms": {"field": "query.name", "size": 10000}}, "total": {"value_count": {"field": "query.name"}}}}}
    }
    dns = search(idx["dns"], body_dns, base).get("aggregations", {}).get("by_src", {}).get("buckets", [])
    for b in dns:
        counts = {}
        subs = {}
        total = b.get("total", {}).get("value", b.get("doc_count", 0))
        for qb in b.get("by_q", {}).get("buckets", []):
            q = qb["key"]
            parts = q.split('.')
            if len(parts) >= 2:
                base_dom = '.'.join(parts[-2:])
                left = '.'.join(parts[:-2])
            else:
                base_dom = q; left = ""
            counts[base_dom] = counts.get(base_dom, 0) + qb["doc_count"]
            if left:
                subs.setdefault(base_dom, set()).add(left)
        if not counts:
            continue
        top_dom = max(counts, key=counts.get)
        if counts[top_dom] >= 500 and len(subs.get(top_dom, [])) >= 50:
            results.append({"type": "dns_tunneling", "indicator": b["key"], "evidence": f"{counts[top_dom]} queries to {top_dom} with {len(subs.get(top_dom, []))} unique subdomains"})

    print(json.dumps({"incidents": results}))


# ---------- CLI parsing ----------

def parse_args(argv):
    if len(argv) < 2:
        return {"cmd": "help"}
    cmd = argv[1]
    args = {"cmd": cmd}
    i = 2
    while i < len(argv):
        if argv[i].startswith("--"):
            key = argv[i]
            val = None
            if i+1 < len(argv) and not argv[i+1].startswith("--"):
                val = argv[i+1]; i += 1
            args[key] = val if val is not None else True
        i += 1
    return args


def main():
    args = parse_args(sys.argv)
    cmd = args.get("cmd")
    if cmd in ("incidents","health-partner","health-cicd","audit-users","lateral","dns-tunnel","investigate"):
        if "--start" not in args or "--end" not in args:
            print(json.dumps({"ok": False, "error": "--start and --end are required"})); return
        if cmd == "investigate" and "--user" not in args:
            print(json.dumps({"ok": False, "error": "--user is required for investigate"})); return
        if cmd == "incidents":
            cmd_incidents(args)
        elif cmd == "health-partner":
            cmd_health_partner(args)
        elif cmd == "health-cicd":
            cmd_health_cicd(args)
        elif cmd == "audit-users":
            cmd_audit_users(args)
        elif cmd == "lateral":
            cmd_lateral(args)
        elif cmd == "dns-tunnel":
            cmd_dns_tunnel(args)
        elif cmd == "investigate":
            cmd_investigate(args)
    else:
        print(json.dumps({"ok": False, "error": "unknown or missing command"}))


if __name__ == "__main__":
    main()
