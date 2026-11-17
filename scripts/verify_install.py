"""Sprawdzenie instalacji StudioEdit: agenci, moduły, modele AI, GPU.

Użycie (z katalogu C:\\StudioEdit):
  .venv\\Scripts\\python.exe scripts\\verify_install.py
Kod wyjścia 0 = wszystko gotowe, 1 = czegoś brakuje (opis w wypisanej liście).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

from studio import paths  # noqa: E402

paths.setup_env()
paths.ensure_dirs()

from studio import health, models_catalog  # noqa: E402
from studio.agents.registry import AGENTS  # noqa: E402

MARK = {"ok": "[OK]  ", "busy": "[OK]  ", "warn": "[UWAGA]", "off": "[--]  ", "error": "[BRAK]"}


def main() -> int:
    problems = 0
    try:
        import torch

        gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
        print(f"GPU: {gpu or 'BRAK CUDA - modele AI będą działać na CPU (bardzo wolno)'}  (torch {torch.__version__})")
        if not gpu:
            problems += 1
    except Exception as e:
        print(f"[BRAK] PyTorch: {e}")
        problems += 1

    print("\nAgenci:")
    for a in AGENTS:
        print(f"  - {a['name']}: {a['role']}")

    print("\nModuły:")
    for m in health.modules():
        print(f"  {MARK.get(m['state'], '[?]   ')} {m['name']} - {m['detail']}")
        if m["state"] == "error":
            problems += 1

    missing = [e for e in models_catalog.catalog() if not e["installed"]]
    print(f"\nModele AI: {len(models_catalog.catalog()) - len(missing)} zainstalowanych, {len(missing)} brakuje")
    for e in missing:
        print(f"  [BRAK] {e['key']} - {e['description']}")
    problems += len(missing)

    print("\nWszystko gotowe." if not problems else f"\nDo uzupełnienia: {problems} (uruchom INSTALUJ.cmd ponownie)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
