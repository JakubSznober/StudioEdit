"""Operacje edycyjne na osi (używane przez czat reżyserski i Region Editor): ripple delete, prędkość,
punch-in, slow zoom, przebitki, przejścia, tytuły, warianty."""
from __future__ import annotations

from ..project.model import AClip, Keyframe, SpeedPoint, TitleClip, Timeline, Transition, VClip
from .material import ShotUnit, collect, keywords

TRACKS = ("V1", "V2", "V3", "A1", "A2", "A3", "A4", "A5")


def _items(tl: Timeline, k: str) -> list:
    return list(tl.tracks.get(k, []))


def shift_after(tl: Timeline, t: float, delta: float, *, exclude: set[str] | None = None) -> None:
    """Przesuwa wszystkie klipy zaczynające się w/po czasie t o delta (ripple)."""
    exclude = exclude or set()
    for k in TRACKS:
        for c in tl.tracks.get(k, []):
            if c.id in exclude:
                continue
            if c.start >= t - 1e-6:
                c.start = round(max(0.0, c.start + delta), 4)
    for tr in tl.transitions:
        if tr.at >= t - 1e-6:
            tr.at = round(tr.at + delta, 4)
    for mk in tl.markers:
        if mk.t >= t - 1e-6:
            mk.t = round(max(0.0, mk.t + delta), 4)


def _split_clip(c, t: float):
    """Dzieli klip w czasie osi t → (lewy, prawy) (kopie)."""
    if isinstance(c, TitleClip):
        left = c.model_copy(deep=True)
        right = c.model_copy(deep=True)
        left.duration = t - c.start
        right.start, right.duration = t, c.end - t
        right.id = right.id + "b"
        return left, right
    speed = getattr(c, "speed", 1.0) or 1.0
    src_t = c.src_in + (t - c.start) * speed
    left = c.model_copy(deep=True)
    right = c.model_copy(deep=True)
    left.src_out = round(src_t, 4)
    right.src_in = round(src_t, 4)
    right.start = round(t, 4)
    right.id = right.id + "b"
    if isinstance(c, VClip):
        if c.transform:
            right.transform = [Keyframe(**{**k.model_dump(), "t": round(k.t - (t - c.start), 4)})
                               for k in c.transform if k.t >= (t - c.start)] or []
            left.transform = [k for k in c.transform if k.t <= (t - c.start)]
        right.reframe = {v: [{**p, "t": round(p["t"] - (t - c.start), 3)} for p in pts if p["t"] >= (t - c.start) - 0.5]
                         for v, pts in c.reframe.items()}
        right.speed_ramp = []
        left.speed_ramp = []
    return left, right


def ripple_delete(tl: Timeline, t0: float, t1: float) -> float:
    """Wycina zakres [t0, t1] ze wszystkich ścieżek i zamyka lukę. Zwraca usuniętą długość."""
    t0, t1 = max(0.0, min(t0, t1)), max(t0, t1)
    d = t1 - t0
    if d <= 0:
        return 0.0
    for k in TRACKS:
        new = []
        for c in tl.tracks.get(k, []):
            if c.end <= t0 or c.start >= t1:
                new.append(c)
                continue
            if c.start < t0:
                left, rest = _split_clip(c, t0)
                new.append(left)
                c = rest
            if c.end > t1:
                _cut, right = _split_clip(c, t1)
                new.append(right)
        tl.tracks[k] = new
    tl.transitions = [tr for tr in tl.transitions if not (t0 - 0.01 <= tr.at <= t1 + 0.01)]
    tl.markers = [m for m in tl.markers if not (t0 < m.t < t1)]
    shift_after(tl, t1, -d)
    tl.normalize()
    return d


def set_speed(tl: Timeline, media: dict, t0: float, t1: float, factor: float, *, ramp: bool = False) -> list[str]:
    """Slow motion / przyspieszenie. B-roll (V2) i V1 bez dialogu; V1 z dialogiem → informacja zwrotna."""
    msgs: list[str] = []
    for k in ("V2", "V1"):
        for c in list(tl.tracks.get(k, [])):
            if not isinstance(c, VClip) or c.end <= t0 or c.start >= t1:
                continue
            linked = any(a.link == c.id for a in tl.tracks.get("A1", []))
            if k == "V1" and linked:
                msgs.append(f"Klip {c.label} zawiera dialog — nie spowalniam mowy; dodaj przebitkę lub zaznacz B-roll.")
                continue
            m = media.get(c.media_id)
            hfr = m is not None and "HFR" in m.flags
            old_dur = c.duration
            if ramp:
                c.speed_ramp = [SpeedPoint(t=0.0, speed=1.0), SpeedPoint(t=0.3, speed=factor),
                                SpeedPoint(t=0.7, speed=factor), SpeedPoint(t=1.0, speed=1.0)]
                c.speed = 1.0
            else:
                c.speed = factor
                c.speed_ramp = []
            if factor < 1 and not hfr:
                c.interpolate = "optical" if factor >= 0.33 else "blend"
            if k == "V2":
                # zachowaj długość na osi - zużyj mniej/więcej materiału źródłowego
                avg = (sum(p.speed for p in c.speed_ramp) / len(c.speed_ramp)) if c.speed_ramp else c.speed
                need = old_dur * avg
                src_max = float(m.info.get("duration", c.src_out)) if m else c.src_out
                c.src_out = round(min(src_max, c.src_in + need), 4)
            else:
                delta = c.duration - old_dur
                shift_after(tl, c.start + old_dur, delta, exclude={c.id})
            msgs.append(f"{'Speed ramp' if ramp else 'Prędkość'} {factor:g}x na {c.label}" +
                        (" (natywne HFR)" if hfr and factor < 1 else " (interpolacja ruchu)" if factor < 1 else ""))
    tl.normalize()
    return msgs


def punch_in(tl: Timeline, t0: float, t1: float, scale: float = 1.15) -> list[str]:
    msgs = []
    for c in tl.tracks.get("V1", []):
        if c.end <= t0 or c.start >= t1:
            continue
        a, b = max(0.0, t0 - c.start), min(c.duration, t1 - c.start)
        base = c.transform[0].scale if c.transform else 1.0
        kf = [k for k in c.transform if k.t < a - 0.01] or [Keyframe(t=0.0, scale=base, ease="linear")]
        kf.append(Keyframe(t=round(max(0.0, a - 0.001), 4), scale=base, ease="hold"))
        kf.append(Keyframe(t=round(a, 4), scale=scale, ease="hold"))
        if b < c.duration - 0.05:
            kf.append(Keyframe(t=round(b - 0.001, 4), scale=scale, ease="hold"))
            kf.append(Keyframe(t=round(b, 4), scale=base, ease="hold"))
        c.transform = kf
        msgs.append(f"Punch-in {int(round(scale * 100))}% na {c.label}")
    return msgs


def slow_zoom(tl: Timeline, t0: float, t1: float, scale: float = 1.08, track: str = "V1") -> list[str]:
    msgs = []
    for c in tl.tracks.get(track, []):
        if c.end <= t0 or c.start >= t1:
            continue
        c.transform = [Keyframe(t=0.0, scale=1.0, ease="linear"), Keyframe(t=round(c.duration, 4), scale=scale,
                                                                          ease="linear")]
        msgs.append(f"Slow zoom 100→{int(round(scale * 100))}% na {c.label}")
    return msgs


def remove_broll(tl: Timeline, t0: float, t1: float) -> int:
    before = len(tl.tracks.get("V2", []))
    tl.tracks["V2"] = [c for c in tl.tracks.get("V2", []) if c.end <= t0 or c.start >= t1]
    return before - len(tl.tracks["V2"])


def add_broll(project, tl: Timeline, t0: float, t1: float, query: str, *, shot_size: str | None = None,
              slowmo: bool = False) -> str:
    _sent, shots = collect(project)
    media = {m.id: m for m in project.media()}
    used = {(c.media_id, round(c.src_in, 1)) for c in tl.tracks.get("V2", [])}
    q = keywords(query)
    best: ShotUnit | None = None
    best_sc = -1.0
    anchor = next((c for c in tl.tracks.get("V1", []) if c.start <= t0 < c.end), None)
    for s in shots:
        if (s.media_id, round(s.s, 1)) in used:
            continue
        if anchor and s.media_id == anchor.media_id and abs(s.s - anchor.src_in) < 5:
            continue
        sc = 0.55 * (len(q & s.keywords) / max(1, len(q)) if q else 0) + 0.35 * s.shot.score
        if shot_size and shot_size in (s.shot.shot_size or ""):
            sc += 0.3
        if slowmo and s.hfr:
            sc += 0.2
        if sc > best_sc:
            best, best_sc = s, sc
    if best is None:
        return ""
    dur = max(0.8, t1 - t0)
    speed = 0.5 if (slowmo and best.hfr) else 1.0
    need = min(best.dur, dur * speed)
    dur = need / speed
    m = media[best.media_id]
    vc = VClip(media_id=best.media_id, src_in=round(best.s, 3), src_out=round(best.s + need, 3), start=round(t0, 4),
               speed=speed, role="b_roll", label=m.name, reason=f"B-roll z czatu: „{query}” · {best.shot.description[:60]}",
               interpolate="none" if best.hfr or speed == 1 else "optical")
    tl.tracks.setdefault("V2", []).append(vc)
    tl.normalize()
    return f"{best.shot.shot_size or 'ujęcie'}: {best.shot.description[:80]}"


def add_transition(tl: Timeline, t0: float, t1: float, kind: str = "dissolve", duration: float = 0.6) -> str:
    cuts = [c.end for c in tl.tracks.get("V1", [])[:-1]]
    if not cuts:
        return ""
    mid = (t0 + t1) / 2
    at = min(cuts, key=lambda x: abs(x - mid))
    tl.transitions = [t for t in tl.transitions if abs(t.at - at) > 0.02]
    tl.transitions.append(Transition(at=round(at, 4), kind=kind, duration=duration, reason="z czatu"))
    tl.normalize()
    return f"{kind} w {at:.2f} s"


def add_title(tl: Timeline, text: str, start: float, duration: float = 3.0, style: str = "lower_third") -> None:
    tl.tracks.setdefault("V3", []).append(TitleClip(text=text, start=start, duration=duration, style=style))
    tl.normalize()


def trim_pauses(tl: Timeline, t0: float, t1: float, amount: float = 0.08) -> int:
    """Zacieśnia tempo: skraca końcówki klipów V1 z dialogiem (oddech) w zakresie."""
    n = 0
    for vc in list(tl.tracks.get("V1", [])):
        if vc.end <= t0 or vc.start >= t1 or vc.duration < 1.0:
            continue
        a = next((x for x in tl.tracks.get("A1", []) if x.link == vc.id), None)
        cut = min(amount, vc.duration * 0.1)
        vc.src_out = round(vc.src_out - cut * vc.speed, 4)
        if isinstance(a, AClip):
            a.src_out = round(a.src_out - cut, 4)
        shift_after(tl, vc.start + vc.duration + cut - 1e-4, -cut, exclude={vc.id})
        n += 1
    tl.normalize()
    return n
