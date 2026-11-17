"""Instalator modeli AI StudioEdit.

Użycie (z katalogu C:\\StudioEdit):
  .venv\\Scripts\\python.exe scripts\\install_models.py            # zestaw podstawowy
  .venv\\Scripts\\python.exe scripts\\install_models.py --all      # wszystko (w tym Wan 2.2, MusicGen, Qwen 3.8)
  .venv\\Scripts\\python.exe scripts\\install_models.py --list     # lista i stan
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]  # konsola cp1250 nie zna „→”
except Exception:
    pass
try:
    import truststore  # Norton i inne antywirusy z inspekcją TLS: certyfikaty z magazynu Windows

    truststore.inject_into_ssl()
except Exception:
    pass

from studio import paths  # noqa: E402

paths.setup_env()
paths.ensure_dirs()

from studio import models_catalog  # noqa: E402

# modele "ciężkie" instalowane tylko z --all
HEAVY_HINTS = ("wan", "musicgen", "director_max", "qwen3.8", "chatterbox", "whisper-large-v3-turbo-hf")


def is_heavy(e: dict) -> bool:
    k = (e["key"] + " " + e.get("role", "")).lower()
    return any(h in k for h in HEAVY_HINTS)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--only", nargs="*", default=None, help="klucze modeli do instalacji")
    args = ap.parse_args()
    cat = models_catalog.catalog()
    if args.list:
        for e in cat:
            print(f"{'[x]' if e['installed'] else '[ ]'} {e['key']:<45} {str(e.get('size_gb') or '?'):>6} GB  "
                  f"{e['description']}")
        return 0
    todo = [e for e in cat if not e["installed"]]
    if args.only:
        todo = [e for e in todo if e["key"] in args.only]
    elif not args.all:
        todo = [e for e in todo if not is_heavy(e)]
    if not todo:
        print("Wszystkie wybrane modele są już zainstalowane.")
        return 0
    failed = []
    for e in todo:
        print(f"\n==> {e['key']}  ({e['description']})", flush=True)
        t0 = time.time()
        last = [0.0]

        def prog(p, _l=last):
            if p - _l[0] >= 0.05 or p >= 1:
                _l[0] = p
                print(f"    {p * 100:5.1f}%", flush=True)
        try:
            models_catalog.install(e["key"], progress=prog)
            print(f"    OK ({time.time() - t0:.0f} s)")
        except Exception as ex:
            print(f"    BŁĄD: {ex}")
            failed.append(e["key"])
    print("\nGotowe." if not failed else f"\nNieudane: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
