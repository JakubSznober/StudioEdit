"""Automatyczne poprawki QC (bez LLM): zwraca nową oś (bez zapisu) i opis zmian po polsku."""
from __future__ import annotations

from ..project.model import AClip, Keyframe, Timeline, VClip


def _v1(tl: Timeline) -> list[VClip]:
    return sorted([c for c in tl.tracks.get("V1", []) if isinstance(c, VClip)], key=lambda c: c.start)


def _media_dur(project, c: VClip) -> float | None:
    try:
        d = float((project.get_media(c.media_id).info or {}).get("duration") or 0)
        return d or None
    except Exception:
        return None


def _find(tl: Timeline, cid: str):
    for k, items in tl.tracks.items():
        for c in items:
            if getattr(c, "id", None) == cid:
                return c
    return None


def _scale_end(c: VClip) -> float:
    if not c.transform:
        return 1.0
    return sorted(c.transform, key=lambda k: k.t)[-1].scale


def _fix_punch_in(tl: Timeline, p: dict, notes: list[str]) -> None:
    b = _find(tl, p.get("clip_id", ""))
    a = _find(tl, p.get("prev_clip_id", "")) if p.get("prev_clip_id") else None
    if not isinstance(b, VClip):
        return
    scale = float(p.get("scale", 1.12))
    if isinstance(a, VClip) and _scale_end(a) >= 1.08:
        # poprzedni klip już powiększony -> naprzemiennie: drugi wraca do 100%
        b.transform = [Keyframe(t=0, scale=1.0, ease="hold")]
        notes.append(f"Jump cut w {b.start:.2f} s: naprzemienne skale (poprzedni {_scale_end(a):.0%}, "
                     f"klip „{b.label or b.id}” 100%)")
        return
    if b.transform and any(abs(k.scale - 1) > 1e-3 for k in b.transform):
        b.transform = [Keyframe(t=k.t, scale=k.scale * scale, x=k.x, y=k.y, rot=k.rot, ease=k.ease)
                       for k in b.transform]
    else:
        b.transform = [Keyframe(t=0, scale=scale, ease="hold")]
    notes.append(f"Jump cut w {b.start:.2f} s: punch-in {scale:.0%} na klipie „{b.label or b.id}”")


def _fix_gap(project, tl: Timeline, p: dict, notes: list[str]) -> None:
    t0, t1 = float(p.get("t0", 0)), float(p.get("t1", 0))
    d = t1 - t0
    if d <= 0:
        return
    v1 = _v1(tl)
    inside = next((c for c in v1 if c.start - 0.02 <= t0 and t1 <= c.end + 0.02), None)
    if inside is None:
        prev = max((c for c in v1 if c.end <= t0 + 0.05), key=lambda c: c.end, default=None)
        nxt = min((c for c in v1 if c.start >= t1 - 0.05), key=lambda c: c.start, default=None)
        if prev is not None and not prev.speed_ramp:
            md = _media_dur(project, prev)
            gap = (nxt.start if nxt else t1) - prev.end
            ext = gap * prev.speed
            if md is None or prev.src_out + ext <= md:
                prev.src_out += ext
                notes.append(f"Luka/czerń {t0:.2f}-{t1:.2f} s: wydłużono klip „{prev.label or prev.id}” o {gap:.2f} s")
                return
        if nxt is not None and not nxt.speed_ramp:
            gap = nxt.start - (prev.end if prev else t0)
            back = min(gap * nxt.speed, nxt.src_in)
            if back > 0:
                nxt.src_in -= back
                nxt.start -= back / nxt.speed
                notes.append(f"Luka/czerń {t0:.2f}-{t1:.2f} s: klip „{nxt.label or nxt.id}” zaczyna się "
                             f"{back / nxt.speed:.2f} s wcześniej")
                return
        notes.append(f"Czerń {t0:.2f}-{t1:.2f} s: brak materiału do wypełnienia - wymaga decyzji reżysera")
        return
    c = inside
    if c.speed_ramp:
        notes.append(f"Czerń w klipie z rampą prędkości „{c.label or c.id}” - pominięto")
        return
    idx = v1.index(c)
    if t0 - c.start < 0.6:
        cut = t1 - c.start
        prev = v1[idx - 1] if idx > 0 else None
        c.src_in += cut * c.speed
        c.start += cut
        if prev is not None and abs(prev.end - (c.start - cut)) < 0.05 and not prev.speed_ramp:
            md = _media_dur(project, prev)
            if md is None or prev.src_out + cut * prev.speed <= md:
                prev.src_out += cut * prev.speed
        notes.append(f"Czarne klatki na początku klipu „{c.label or c.id}”: przycięto {cut:.2f} s")
    elif c.end - t1 < 0.6:
        cut = c.end - t0
        nxt = v1[idx + 1] if idx + 1 < len(v1) else None
        c.src_out -= cut * c.speed
        if nxt is not None and abs(nxt.start - (c.end + cut)) < 0.05 and not nxt.speed_ramp:
            back = min(cut * nxt.speed, nxt.src_in)
            nxt.src_in -= back
            nxt.start -= back / nxt.speed
        notes.append(f"Czarne klatki na końcu klipu „{c.label or c.id}”: przycięto {cut:.2f} s")
    else:
        notes.append(f"Czerń w środku klipu „{c.label or c.id}” ({t0:.2f} s) - wymaga decyzji reżysera")


def _fix_freeze(project, tl: Timeline, p: dict, notes: list[str]) -> None:
    t0, t1 = float(p.get("t0", 0)), float(p.get("t1", 0))
    c = next((c for c in _v1(tl) if c.start - 0.05 <= t0 < c.end), None)
    if c is None:
        return
    if (c.speed < 0.999 or c.speed_ramp) and c.interpolate == "none":
        c.interpolate = "blend"
        notes.append(f"Zamrożenie w spowolnionym klipie „{c.label or c.id}”: włączono interpolację klatek")
        return
    md = _media_dur(project, c)
    if md is not None and c.src_out > md - 0.02:
        over = c.src_out - md + 1 / 30
        c.src_out -= over
        notes.append(f"Klip „{c.label or c.id}” wychodził poza koniec pliku - skrócono o {over:.2f} s")
        return
    if c.end - t1 < 0.3:
        cut = min(t1 - t0, c.duration * 0.5)
        c.src_out -= cut * c.speed
        notes.append(f"Zamrożony koniec klipu „{c.label or c.id}”: przycięto {cut:.2f} s")
        return
    notes.append(f"Zamrożenie {t0:.2f}-{t1:.2f} s w „{c.label or c.id}” - prawdopodobnie statyczne ujęcie, bez zmian")


def apply_fixes(project, timeline: Timeline, issues: list[dict]) -> tuple[Timeline, list[str]]:
    tl = timeline.model_copy(deep=True)
    tl.normalize()
    notes: list[str] = []
    done_loud = False
    for it in issues:
        fix = it.get("fix") or {}
        act = fix.get("action")
        p = fix.get("params") or {}
        try:
            if act == "punch_in":
                _fix_punch_in(tl, p, notes)
            elif act == "fill_gap":
                _fix_gap(project, tl, p, notes)
            elif act == "fix_freeze":
                _fix_freeze(project, tl, p, notes)
            elif act == "loudness" and not done_loud:
                delta = float(p.get("delta", 0))
                old = tl.mix.target_lufs
                new = max(-24.0, min(-9.0, old + 0.8 * delta))
                if abs(new - old) > 0.05:
                    tl.mix.target_lufs = round(new, 2)
                    notes.append(f"Głośność: korekta celu normalizacji {old:.1f} → {new:.1f} LUFS "
                                 f"(kompensacja odchyłki {delta:+.1f} LU)")
                done_loud = True
            elif act == "true_peak":
                old = tl.mix.true_peak
                new = max(-3.0, min(old, float(p.get("true_peak", old - 0.5))))
                if new < old:
                    tl.mix.true_peak = round(new, 2)
                    notes.append(f"Przester: limit true peak {old:.1f} → {new:.1f} dBFS")
                else:
                    for c in tl.tracks.get("A3", []):
                        if isinstance(c, AClip):
                            c.gain_db -= 2.0
                    notes.append("Przester: muzyka (A3) ściszona o 2 dB")
            elif act == "dialog_gain":
                g = float(p.get("gain_db", 3.0))
                a1 = [c for c in tl.tracks.get("A1", []) if isinstance(c, AClip) and c.role == "dialog"]
                if a1:
                    for c in a1:
                        c.gain_db = min(12.0, c.gain_db + g)
                    notes.append(f"Cichy dialog: +{g:.1f} dB na {len(a1)} klipach A1")
                else:
                    tl.mix.music_gain_db -= min(6.0, g)
                    notes.append(f"Cichy dialog (audio z V1): muzyka ściszona o {min(6.0, g):.1f} dB")
            elif act == "subtitles":
                if "max_chars" in p and int(p["max_chars"]) < tl.subtitles.max_chars:
                    old = tl.subtitles.max_chars
                    tl.subtitles.max_chars = int(p["max_chars"])
                    notes.append(f"Ucięte napisy: limit znaków w linii {old} → {tl.subtitles.max_chars}")
                if p.get("position") and p["position"] != tl.subtitles.position:
                    tl.subtitles.position = p["position"]
                    notes.append(f"Napisy przeniesione wyżej (pozycja: {p['position']}) - bezpieczna strefa 9:16")
                if p.get("size"):
                    tl.subtitles.size = int(p["size"])
            elif act == "resync":
                c = _find(tl, p.get("clip_id", ""))
                if isinstance(c, AClip):
                    old = c.start
                    c.start = float(p["start"])
                    notes.append(f"Synchronizacja: dialog „{c.label or c.id}” przesunięty o "
                                 f"{(c.start - old) * 1000:+.0f} ms")
            elif act == "room_tone" and not tl.mix.room_tone:
                tl.mix.room_tone = True
                notes.append("Martwe cisze: włączono room tone pod całym filmem")
        except Exception as e:  # pojedyncza poprawka nie może zatrzymać pozostałych
            notes.append(f"Poprawka {act} nieudana: {e}")
    tl.normalize()
    return tl, notes


FIXABLE = {"punch_in", "fill_gap", "fix_freeze", "loudness", "true_peak", "dialog_gain", "subtitles", "resync",
           "room_tone"}


def fixable(issues: list[dict]) -> list[dict]:
    return [i for i in issues if (i.get("fix") or {}).get("action") in FIXABLE and i.get("severity") != "info"]
