import argparse
import csv
import json
import os
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
    server = sub.add_parser("server")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8000)
    users = sub.add_parser("users")
    users.add_argument("action", choices=["create", "list", "disable", "enable", "password"])
    users.add_argument("username", nargs="?")
    users.add_argument("--admin", action="store_true")
    collect = sub.add_parser("collect")
    collect.add_argument("--server", required=True)
    collect.add_argument("--device-token-file", required=True)
    collect.add_argument("--allow-insecure-loopback", action="store_true")
    collect.add_argument("--once", action="store_true")
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
    if args.command == "users":
        from backend.server.auth import AuthDB
        import getpass

        auth = AuthDB(root / "accounts.db")
        try:
            if args.action == "list":
                print(json.dumps(auth.users(), indent=2))
                return
            if not args.username:
                parser.error("Username required")
            if args.action in ("create", "password"):
                password = getpass.getpass("Password (minimum 12 characters): ")
                if password != getpass.getpass("Confirm password: "):
                    parser.error("Passwords do not match")
                if args.action == "create":
                    print(
                        json.dumps(
                            auth.create_user(args.username, password, "admin" if args.admin else "user")
                        )
                    )
                else:
                    auth.set_password(args.username, password)
                    print("Password changed; browser sessions and device credentials revoked.")
            else:
                auth.set_enabled(args.username, args.action == "enable")
                print("Account updated.")
        except ValueError as error:
            parser.error(str(error))
        finally:
            auth.close()
        return
    if args.command == "server":
        import uvicorn
        from backend.server.app import create_server_app

        uvicorn.run(create_server_app(root), host=args.host, port=args.port, access_log=False)
        return
    if args.command == "collect":
        from backend.adapters import LocalAdapters
        from backend.server.collector import (
            Collector,
            Spool,
            read_device_token,
            spool_path,
            collector_origin,
            CollectorAuthorizationError,
        )

        token = read_device_token(args.device_token_file)
        origin = collector_origin(args.server, args.allow_insecure_loopback)
        spool = Spool(spool_path(root, origin, token))
        try:
            success = Collector(origin, token, spool, LocalAdapters(), args.allow_insecure_loopback).run(
                args.once
            )
            if success is False:
                raise SystemExit(1)
        except CollectorAuthorizationError as error:
            print(str(error), file=sys.stderr)
            raise SystemExit(1) from None
        except KeyboardInterrupt:
            pass
        finally:
            spool.close()
        return
    if args.command == "token":
        if os.getenv("TOKENSCOPE_MODE") == "server":
            parser.error("Server mode uses account login and enrolled device credentials")
        print(api_token(root))
        return
    if args.command == "start":
        import uvicorn
        from backend.api.app import create_app

        print("TokenScope: http://127.0.0.1:8000 — retrieve dashboard token with: tokenscope token")
        uvicorn.run(create_app(root), host="127.0.0.1", port=8000, access_log=False)
        return
    if args.command in ("doctor", "providers"):
        if os.getenv("TOKENSCOPE_MODE") == "server":
            print("Server mode: host session discovery disabled; enroll outbound collectors in your account.")
            return
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
