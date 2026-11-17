"""Detekcja twarzy w analizie ujęć: wiele wątków z klatkami o różnych proporcjach naraz.

Regresja z projektu „Europa”: wspólny FaceDetectorYN dawał asercję OpenCV
„buf.shape() == m.shape()”, gdy dwa wątki analizowały pliki pionowe i poziome jednocześnie.
"""
from __future__ import annotations

import threading

import numpy as np
import pytest

from studio import paths

paths.setup_env()

from studio.analysis import shots  # noqa: E402

needs_yunet = pytest.mark.skipif(
    not list((paths.WEIGHTS / "face").glob("face_detection_yunet*.onnx")) if (paths.WEIGHTS / "face").exists() else True,
    reason="brak wag YuNet")


@needs_yunet
def test_faces_parallel_mixed_sizes():
    sizes = [(180, 320), (569, 320), (240, 320), (320, 320)]  # poziomy 16:9, pionowy 9:16, 4:3, 1:1
    errors: list[BaseException] = []
    start = threading.Barrier(8)

    def worker(k: int) -> None:
        rng = np.random.default_rng(k)
        h, w = sizes[k % len(sizes)]
        frame = rng.integers(0, 255, (h, w, 3), dtype=np.uint8)
        gray = frame.mean(axis=2).astype(np.uint8)
        start.wait()
        try:
            for _ in range(60):
                shots._faces(gray, frame)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"{len(errors)} wątków zgłosiło błąd: {errors[0]}"
