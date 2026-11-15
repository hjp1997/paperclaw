"""Run: python start.py [--port 8765] [--no-browser]."""
import argparse
import threading
import webbrowser
from pathlib import Path

from paperclaw.server import Application, make_server
from paperclaw.instance import InstanceLock


def main():
    parser = argparse.ArgumentParser(description="PaperClaw conference metadata collector")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent / "data")
    args = parser.parse_args()
    instance = InstanceLock(args.data_dir)
    try:
        instance.__enter__()
        app = Application(args.data_dir)
        server = make_server(app, args.port)
    except OSError as error:
        print(f"Startup failed: {error}\nTry: python start.py --port 8766")
        return 1
    except (ValueError, KeyError, TypeError):
        print("Saved data is invalid. Keep a backup of data/results.json and inspect it before restarting.")
        return 1
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"PaperClaw ready: {url}\nKeep this window open. Press Ctrl+C to stop.", flush=True)
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        app.cancel.set()
    finally:
        server.server_close()
        instance.__exit__()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
