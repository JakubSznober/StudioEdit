"""Selekcja dubli (Take Selection) i naturalny oddech (Pacing)."""
from __future__ import annotations

from rapidfuzz import fuzz

from ..project.model import Sentence
from .material import SentenceUnit


def group_takes(units: list[SentenceUnit], *, window: int = 8, threshold: float = 78.0) -> dict[str, list[SentenceUnit]]:
    """Grupuje powtórzenia tej samej kwestii (bliskie w czasie, podobny tekst) i wybiera HERO_TAKE."""
    groups: dict[str, list[SentenceUnit]] = {}
    assigned: dict[str, str] = {}
    for i, u in enumerate(units):
        if u.id in assigned or len(u.text) < 12:
            continue
        gid = f"take_{u.id}"
        members = [u]
        for v in units[i + 1:i + 1 + window]:
            if v.id in assigned or v.spk != u.spk and u.spk and v.spk:
                continue
            a, b = u.text.lower(), v.text.lower()
            if min(len(a), len(b)) / max(len(a), len(b), 1) < 0.5:
                # częsty przypadek: urwany dubel - porównaj z prefiksem
                sim = fuzz.partial_ratio(a, b)
            else:
                sim = fuzz.token_set_ratio(a, b)
            if sim >= threshold:
                members.append(v)
        if len(members) > 1:
            for mbr in members:
                assigned[mbr.id] = gid
                mbr.take_group = gid
            groups[gid] = members
    for gid, members in groups.items():
        longest = max(len(m.text) for m in members)
        for mbr in members:
            completeness = len(mbr.text) / max(1, longest)
            # późniejszy dubel zwykle lepszy (mówca się „rozgrzał”) - lekki bonus
            mbr.score = round(0.55 * mbr.fluency + 0.25 * (mbr.shot.score if mbr.shot else 0.5)
                              + 0.15 * completeness + 0.05 * (members.index(mbr) / max(1, len(members) - 1)), 3)
        best = max(members, key=lambda m: m.score)
        for mbr in members:
            mbr.hero = mbr is best
    return groups


def keep_ranges(sentence: Sentence, *, breath: float = 0.2, max_gap: float = 0.45, pre: float = 0.06,
                min_piece: float = 0.25) -> list[tuple[float, float]]:
    """Zakresy źródła do zachowania: bez wypełniaczy i martwych pauz, z kontrolowanym oddechem."""
    words = sentence.words
    if not words:
        return [(sentence.s, sentence.e)]
    pieces: list[list[float]] = []
    for i, w in enumerate(words):
        if w.filler:
            continue
        ws, we = w.s, w.e
        if not pieces:
            pieces.append([max(0.0, ws - pre), we])
            continue
        gap = ws - pieces[-1][1]
        prev_removed = i > 0 and words[i - 1].filler
        if gap > max_gap or (prev_removed and gap > 0.12):
            # zamknij poprzedni kawałek z oddechem i zacznij nowy
            pieces[-1][1] = min(pieces[-1][1] + breath, ws - 0.04) if gap > breath + 0.04 else pieces[-1][1]
            pieces.append([max(pieces[-1][1], ws - pre), we])
        else:
            pieces[-1][1] = we
    if pieces:
        pieces[-1][1] = pieces[-1][1] + min(breath, 0.25)
    out: list[tuple[float, float]] = []
    for a, b in pieces:
        if b - a < min_piece and out:
            out[-1] = (out[-1][0], b)
        elif b > a:
            out.append((round(a, 3), round(b, 3)))
    return out
