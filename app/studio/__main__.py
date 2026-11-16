"""Start serwera: python -m studio [--no-browser] [--port 8765]"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
import webbrowser


def _port_state(host: str, port: int) -> str:
    """'free' | 'studioedit' (nasza instancja już działa) | 'busy' (inny program)."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        if s.connect_ex((host, port)) != 0:
            return "free"
    try:
        import httpx

        r = httpx.get(f"http://{host}:{port}/api/health", timeout=2)
        if r.status_code == 200 and r.json().get("ok"):
            return "studioedit"
    except Exception:
        pass
    return "busy"


def main() -> None:
    from . import paths

    paths.setup_env()
    paths.ensure_dirs()
    from .config import get_config

    cfg = get_config()
    ap = argparse.ArgumentParser(prog="studio")
    ap.add_argument("--host", default=cfg.get("server.host", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(cfg.get("server.port", 8765)))
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    log_file = paths.LOGS / "server.log"
    handlers: list[logging.Handler] = [logging.FileHandler(log_file, encoding="utf-8")]
    if sys.stdout is not None:
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s %(message)s",
                        handlers=handlers)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    url = f"http://{args.host}:{args.port}"
    state = _port_state(args.host, args.port)
    if state == "studioedit":
        print(f"\n  StudioEdit już działa  ->  {url}\n  Otwieram przeglądarkę. (Zatrzymanie: StudioEdit-stop.cmd)\n",
              flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        return
    if state == "busy":
        print(f"\n  [!] Port {args.port} jest zajęty przez inny program.\n"
              f"      Zamknij go albo zmień port w config\\config.json (server.port) lub uruchom: "
              f"StudioEdit.cmd --port 8766\n", flush=True)
        sys.exit(1)
    if cfg.get("server.open_browser", True) and not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(2.0), webbrowser.open(url)), daemon=True).start()
    print(f"\n  StudioEdit  ->  {url}\n  (Ctrl+C zatrzymuje serwer)\n", flush=True)
    import uvicorn

    uvicorn.run("studio.server.app:app", host=args.host, port=args.port, log_level="warning", ws_ping_interval=20,
                log_config=None)


if __name__ == "__main__":
    main()
