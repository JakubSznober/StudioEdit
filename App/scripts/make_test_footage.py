"""Syntetyczny materiał testowy z prawdziwą polską mową (Windows TTS) i wyraźnymi cięciami.

Powstaje w C:\\StudioEdit\\Cache\\TestFootage\\. Służy do testów mechaniki indeksu,
NIE zastępuje testu na prawdziwym nagraniu.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio import CACHE_DIR, FFMPEG  # noqa: E402

OUT = CACHE_DIR / "TestFootage"

SPEECH = (
    "Dzień dobry, to jest test studia montażowego. Nagrywam krótki komentarz do kamery. "
    "Za chwilę zobaczysz scenę w górach, a potem miasto nocą. Na końcu pokażę zbliżenie na detale."
)


def tts(text: str, wav: Path) -> None:
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.SelectVoice('Microsoft Paulina Desktop'); $s.Rate = 0; "
        f"$s.SetOutputToWaveFile('{wav}'); $s.Speak('{text}'); $s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True)


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-800:])


def main() -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    speech = OUT / "speech_pl.wav"
    if not speech.exists():
        tts(SPEECH, speech)

    # 5 "ujęć" po kilka sekund: mówiąca plansza, ostry ruch, rozmyte, ciemne, prześwietlone
    seg = OUT / "seg"
    seg.mkdir(exist_ok=True)
    f = str(FFMPEG)
    common = ["-y", "-hide_banner", "-loglevel", "error"]
    parts = [
        ("s1_talk", ["-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=25:duration=8",
                     "-vf", "drawtext=fontfile='C\\:/Windows/Fonts/arial.ttf':text='KOMENTARZ DO KAMERY':fontsize=72:fontcolor=white:x=(w-tw)/2:y=h-120:box=1:boxcolor=black@0.5"]),
        ("s2_motion", ["-f", "lavfi", "-i", "testsrc2=size=2560x1440:rate=25:duration=6",
                       "-vf", "crop=1920:1080:x='min(iw-ow\\,t*90)':y='min(ih-oh\\,t*50)',hue=h=t*40"]),
        ("s3_blur", ["-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=25:duration=5", "-vf", "boxblur=20:3"]),
        ("s4_dark", ["-f", "lavfi", "-i", "smptebars=size=1920x1080:rate=25:duration=5", "-vf", "eq=brightness=-0.45"]),
        ("s5_bright", ["-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=25:duration=5", "-vf", "eq=brightness=0.5:saturation=0.3"]),
    ]
    files = []
    for name, args in parts:
        p = seg / f"{name}.mp4"
        run([f, *common, *args, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-an", str(p)])
        files.append(p)

    lst = seg / "list.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in files), encoding="utf-8")
    video = OUT / "video_only.mp4"
    run([f, *common, "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(video)])

    final = OUT / "test_clip_pl.mp4"
    # mowa od 0.5 s, potem cisza do końca; audio 48 kHz stereo jak z kamery
    run([f, *common, "-i", str(video), "-i", str(speech),
         "-filter_complex", "[1:a]adelay=500|500,apad[a]", "-map", "0:v", "-map", "[a]", "-shortest",
         "-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-metadata", "title=StudioEdit synthetic test", str(final)])
    print(final)
    return final


if __name__ == "__main__":
    main()
