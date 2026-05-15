import json
import os
import sys
from pathlib import Path
import urllib.request


def main():
    if len(sys.argv) < 3:
        print(json.dumps({"ok": False, "error": "usage: query.py <index_key|raw_index> <json_body>"}))
        sys.exit(1)

    index_key = sys.argv[1]
    body_str = sys.argv[2]
    try:
        body = json.loads(body_str)
    except json.JSONDecodeError:
        print(json.dumps({"ok": False, "error": "body must be valid JSON"}))
        sys.exit(1)

    workspace = Path(os.environ.get("STEMCELL_AGENT_WORKSPACE", "."))
    env_path = workspace.joinpath("environment.yaml")
    import yaml
    env = yaml.safe_load(env_path.read_text()) or {}

    base_url = env.get("opensearch", {}).get("base_url", "http://localhost:9200")
    indices = env.get("indices", {})
    index = indices.get(index_key, index_key)

    url = f"{base_url.rstrip('/')}/{index}/_search"
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read().decode("utf-8")
            print(data)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e), "url": url}))
        sys.exit(2)


if __name__ == "__main__":
    main()
