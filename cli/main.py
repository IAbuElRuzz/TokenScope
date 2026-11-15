import argparse
import csv
import json
import sys
import time
import urllib.request
from backend.core.config import api_token, data_directory
from backend.core.transport import local_url, local_open


def main():
    parser = argparse.ArgumentParser(prog="tokenscope")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("start")
    for name in ("status", "live", "sessions", "providers", "doctor", "token"):
        sub.add_parser(name)
    costs = sub.add_parser("costs")
    costs.add_argument("--today", action="store_true")
    export = sub.add_parser("export")
    export.add_argument("--format", choices=["json", "csv"], default="json")
    delete = sub.add_parser("delete")
    delete.add_argument("session_id")
    args = parser.parse_args()
    root = data_directory()
    if args.command == "token":
        print(api_token(root))
        return
    if args.command == "start":
        import uvicorn
        from backend.api.app import create_app

        print("TokenScope: http://127.0.0.1:8000 — retrieve dashboard token with: tokenscope token")
        uvicorn.run(create_app(root), host="127.0.0.1", port=8000, access_log=False)
        return
    if args.command in ("doctor", "providers"):
        from backend.adapters import LocalAdapters

        print(json.dumps(LocalAdapters().doctor(), indent=2))
        return

    def request(endpoint, method="GET"):
        url = local_url(args.url)
        req = urllib.request.Request(
            url + endpoint, method=method, headers={"Authorization": "Bearer " + api_token(root)}
        )
        with local_open(req, timeout=10) as response:
            return json.load(response)

    try:
        if args.command == "delete":
            from urllib.parse import quote

            print(request("/api/sessions/" + quote(args.session_id, safe=""), "DELETE"))
            return
        if args.command == "export":
            rows = request("/api/export")["events"]
            if args.format == "json":
                print(json.dumps(rows, indent=2))
            elif rows:
                writer = csv.DictWriter(sys.stdout, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            return
        while True:
            snap = request("/api/snapshot")
            if args.command == "live":
                print("\033[2J\033[H", end="")
            print(
                json.dumps(
                    snap["sessions"] if args.command in ("sessions", "live") else snap["totals"], indent=2
                )
            )
            if args.command != "live":
                break
            time.sleep(1)
    except KeyboardInterrupt:
        return
    except Exception as error:
        print(
            f"TokenScope request failed ({type(error).__name__}); start the server and check tokenscope doctor.",
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
