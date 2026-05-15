"""Wipe and reload the OpenSearch indices from the fixed NDJSON files under data/<mode>/."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

DEFAULT_HOST = "http://localhost:9200"
INDICES = ("auth_logs", "network", "dns")
DATA_ROOT = Path(__file__).resolve().parent / "data"

INDEX_MAPPINGS = {
    "auth_logs": {
        "mappings": {
            "properties": {
                "@timestamp": {"type": "date"},
                "user": {"properties": {"name": {"type": "keyword"}}},
                "source": {
                    "properties": {
                        "ip": {"type": "keyword"},
                        "geo": {"properties": {"country": {"type": "keyword"}}},
                    }
                },
                "event": {
                    "properties": {
                        "action": {"type": "keyword"},
                        "outcome": {"type": "keyword"},
                        "dataset": {"type": "keyword"},
                    }
                },
                "user_agent": {"properties": {"name": {"type": "keyword"}}},
                "url": {"properties": {"path": {"type": "keyword"}}},
            }
        }
    },
    "network": {
        "mappings": {
            "properties": {
                "@timestamp": {"type": "date"},
                "source": {"properties": {"ip": {"type": "keyword"}}},
                "destination": {
                    "properties": {
                        "ip": {"type": "keyword"},
                        "port": {"type": "integer"},
                    }
                },
                "network": {
                    "properties": {
                        "protocol": {"type": "keyword"},
                        "bytes_out": {"type": "long"},
                        "bytes_in": {"type": "long"},
                    }
                },
                "event": {
                    "properties": {
                        "action": {"type": "keyword"},
                        "dataset": {"type": "keyword"},
                    }
                },
            }
        }
    },
    "dns": {
        "mappings": {
            "properties": {
                "@timestamp": {"type": "date"},
                "source": {"properties": {"ip": {"type": "keyword"}}},
                "query": {
                    "properties": {
                        "name": {"type": "keyword"},
                        "type": {"type": "keyword"},
                    }
                },
                "response": {"properties": {"code": {"type": "keyword"}}},
            }
        }
    },
}


def http(method: str, url: str, body: bytes | None = None,
         content_type: str = "application/json") -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def wipe_and_create(host: str, index: str) -> None:
    status, body = http("DELETE", f"{host}/{index}")
    if status not in (200, 404):
        raise RuntimeError(f"DELETE {index} failed [{status}]: {body.decode()}")
    mapping = json.dumps(INDEX_MAPPINGS[index]).encode()
    status, body = http("PUT", f"{host}/{index}", body=mapping)
    if status not in (200, 201):
        raise RuntimeError(f"CREATE {index} failed [{status}]: {body.decode()}")


def bulk_load(host: str, index: str, ndjson_path: Path) -> int:
    chunk_lines = 1000
    total = 0
    pending_buf: list[bytes] = []
    pending_docs = 0

    def flush():
        nonlocal pending_buf, pending_docs, total
        if not pending_buf:
            return
        body = b"".join(pending_buf)
        status, resp = http("POST", f"{host}/{index}/_bulk",
                            body=body, content_type="application/x-ndjson")
        if status != 200:
            raise RuntimeError(f"_bulk failed [{status}]: {resp.decode()[:400]}")
        parsed = json.loads(resp)
        if parsed.get("errors"):
            for item in parsed["items"]:
                op = next(iter(item.values()))
                if op.get("status", 0) >= 400:
                    raise RuntimeError(f"_bulk item error: {op}")
        total += pending_docs
        pending_buf = []
        pending_docs = 0

    with ndjson_path.open("rb") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            pending_buf.append(b'{"index":{}}\n')
            pending_buf.append(line + b"\n")
            pending_docs += 1
            if pending_docs >= chunk_lines:
                flush()
    flush()
    return total


def refresh(host: str, index: str) -> None:
    status, body = http("POST", f"{host}/{index}/_refresh")
    if status != 200:
        raise RuntimeError(f"refresh {index} failed [{status}]: {body.decode()}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=("train", "eval"), required=True)
    p.add_argument("--host", default=DEFAULT_HOST)
    args = p.parse_args()

    data_dir = DATA_ROOT / args.mode
    if not data_dir.exists():
        print(f"missing data directory: {data_dir}", file=sys.stderr)
        sys.exit(1)

    try:
        status, _ = http("GET", f"{args.host}/_cluster/health")
        if status != 200:
            raise RuntimeError(f"health endpoint returned {status}")
    except Exception as exc:
        print(f"OpenSearch at {args.host} not reachable: {exc}", file=sys.stderr)
        print("Start it with: cd test_setup/security_analyst && docker compose up -d",
              file=sys.stderr)
        sys.exit(1)

    for idx in INDICES:
        print(f"  wiping + recreating {idx}")
        wipe_and_create(args.host, idx)

    for idx in INDICES:
        ndjson = data_dir / f"{idx}.ndjson"
        if not ndjson.exists():
            raise FileNotFoundError(ndjson)
        loaded = bulk_load(args.host, idx, ndjson)
        refresh(args.host, idx)
        print(f"  {idx}: loaded {loaded} docs from {ndjson.relative_to(data_dir.parent.parent)}")

    print(f"OK. Mode={args.mode} loaded against {args.host}.")


if __name__ == "__main__":
    main()
