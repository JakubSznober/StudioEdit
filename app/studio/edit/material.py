"""Materiał dla reżysera: jednostki A-roll (zdania) i B-roll (ujęcia) z cechami do decyzji montażowych."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..project.model import MediaItem, Sentence, Shot


@dataclass
class SentenceUnit:
    id: str
    media_id: str
    text: str
    s: float
    e: float
    spk: str | None
    fluency: float
    sentence: Sentence
    shot: Shot | None = None
    take_group: str | None = None
    hero: bool = True
    score: float = 0.5
    order: int = 0  # kolejność chronologiczna w materiale

    @property
    def dur(self) -> float:
        return max(0.0, self.e - self.s)


@dataclass
class ShotUnit:
    id: str
    media_id: str
    s: float
    e: float
    shot: Shot
    hfr: bool = False
    fps: float = 30.0
    is_aroll_gap: bool = False  # fragment A-rollu bez mowy (też może być przebitką)
    used: bool = False
    keywords: set[str] = field(default_factory=set)

    @property
    def dur(self) -> float:
        return max(0.0, self.e - self.s)


def shot_at(m: MediaItem, t: float) -> Shot | None:
    for sh in m.analysis.shots:
        if sh.s <= t < sh.e:
            return sh
    return m.analysis.shots[-1] if m.analysis.shots else None


def _kw(text: str) -> set[str]:
    import re

    words = re.findall(r"[\wąćęłńóśźż]{4,}", (text or "").lower())
    stop = {"jest", "się", "oraz", "który", "która", "które", "przez", "tylko", "tutaj", "teraz", "jakby", "właśnie",
            "bardzo", "będzie", "jestem", "jesteśmy", "można", "trzeba", "wszystko", "takie", "taki", "żeby", "tego",
            "temu", "tych", "tym", "widać", "kadr", "ujęcie", "osoba", "osoby"}
    return {w[:6] for w in words if w not in stop}


def collect(project) -> tuple[list[SentenceUnit], list[ShotUnit]]:
    sentences: list[SentenceUnit] = []
    shots: list[ShotUnit] = []
    order = 0
    for m in sorted(project.media(), key=lambda x: (x.info.get("tags", {}).get("creation_time", ""), x.added_at)):
        if m.bin == "Odrzucone_Duble" or m.kind not in ("video", "image"):
            continue
        fps = float(m.info.get("fps") or 30)
        hfr = "HFR" in m.flags
        spoken: list[tuple[float, float]] = []
        if m.transcript and m.bin != "B-Roll":
            for s in m.transcript.sentences:
                words = [w for w in s.words if not w.filler] or s.words
                if not words:
                    continue
                sh = shot_at(m, (s.s + s.e) / 2)
                vis = sh.score if sh else 0.5
                look = 0.15 if sh and "patrzy_w_obiektyw" in sh.tags else 0.0
                smile = 0.05 if sh and "uśmiech" in sh.tags else 0.0
                score = 0.55 * s.fluency + 0.25 * vis + look + smile
                sentences.append(SentenceUnit(id=s.id, media_id=m.id, text=s.text.strip(), s=s.s, e=s.e, spk=s.spk,
                                              fluency=s.fluency, sentence=s, shot=sh, score=round(score, 3),
                                              order=order))
                order += 1
                spoken.append((s.s, s.e))
        for sh in m.analysis.shots:
            # przebitką może być całe ujęcie B-roll albo fragment A-rollu bez mowy (>= 1,5 s)
            if m.bin == "B-Roll" or m.kind == "image" or not spoken:
                kw = _kw(sh.description + " " + " ".join(sh.tags) + " " + sh.action)
                dur = sh.e - sh.s
                if dur > 6.0 and m.kind != "image":
                    # długie ujęcie → okna ~3 s (różne fragmenty akcji do wyboru przez reżysera)
                    n = int(dur // 3.0)
                    step = dur / n
                    for wi in range(n):
                        a = sh.s + wi * step
                        shots.append(ShotUnit(id=f"{sh.id}_w{wi}", media_id=m.id, s=round(a, 3),
                                              e=round(a + step, 3), shot=sh, hfr=hfr, fps=fps, keywords=kw))
                else:
                    shots.append(ShotUnit(id=sh.id, media_id=m.id, s=sh.s, e=sh.e, shot=sh, hfr=hfr, fps=fps,
                                          keywords=kw))
            else:
                gaps = _free_ranges(sh.s, sh.e, spoken)
                for gi, (a, b) in enumerate(gaps):
                    if b - a >= 1.5:
                        shots.append(ShotUnit(id=f"{sh.id}_g{gi}", media_id=m.id, s=a, e=b, shot=sh, hfr=hfr, fps=fps,
                                              is_aroll_gap=True,
                                              keywords=_kw(sh.description + " " + " ".join(sh.tags))))
    return sentences, shots


def _free_ranges(a: float, b: float, busy: list[tuple[float, float]], pad: float = 0.3) -> list[tuple[float, float]]:
    out = []
    cur = a
    for s, e in sorted(busy):
        if e < a or s > b:
            continue
        if s - pad > cur:
            out.append((cur, s - pad))
        cur = max(cur, e + pad)
    if cur < b:
        out.append((cur, b))
    return out


def keywords(text: str) -> set[str]:
    return _kw(text)
