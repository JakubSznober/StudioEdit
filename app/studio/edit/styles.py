"""Katalog stylów montażu: opis dla użytkownika + reguły, które naprawdę zmieniają montaż.

Każdy styl ustawia:
- pace         tempo cięć (spokojne / umiarkowane / dynamiczne / bardzo dynamiczne),
- broll_gap    co ile sekund „gadającej głowy” wstawiać przebitkę (None = bez automatycznych przebitek),
- transitions  'none' (czyste cięcia), 'calm' (przenikanie / czerń przy zmianie sceny), 'flashy' (efektowne),
- punch_in     płynne najazdy na kluczowe słowa,  slow_zoom  powolne zbliżenie na statycznych ujęciach,
- title_card   plansza tytułowa na początku,
- subtitles    tryb i wygląd napisów,
- music_mood   nastrój muzyki (klucz z audio/music.py),
- look         domyślny look kolorystyczny (gdy użytkownik zostawi „Automatycznie”),
- director     wskazówki dla reżysera AI (trafiają do promptu planu narracji).
"""
from __future__ import annotations

from typing import Any

STYLES: dict[str, dict[str, Any]] = {
    "vlog": {
        "label": "Vlog",
        "description": "Luźna opowieść do kamery: żywe tempo, wycięte pauzy, przebitki z miejsc, o których mówisz.",
        "pace": "dynamiczne", "broll_gap": 7.0, "transitions": "calm", "punch_in": True, "slow_zoom": True,
        "title_card": True, "subtitles": ("word_pop", "modern"), "music_mood": "vlog", "look": "warm_film",
        "director": "Prowadź widza jak znajomy: krótki hook zapowiadający, potem chronologicznie. Wycinaj pauzy "
                    "i dygresje, ilustruj przebitkami to, o czym mowa.",
    },
    "rolka": {
        "label": "Rolka (TikTok / Reels / Shorts)",
        "description": "Najmocniejszy moment w 1-2 s, bardzo szybkie cięcia, duże napisy słowo po słowie, zero wstępów.",
        "pace": "bardzo dynamiczne", "broll_gap": 4.0, "transitions": "calm", "punch_in": True, "slow_zoom": False,
        "title_card": False, "subtitles": ("word_pop", "bold_social"), "music_mood": "energiczny",
        "look": "vibrant_social",
        "director": "Hook w pierwszych 1-2 s (najbardziej zaskakujące zdanie lub obraz). Bez powitań i wstępów, "
                    "każde zdanie musi wnosić coś nowego. Zakończ puentą albo pętlą do początku.",
    },
    "reklama": {
        "label": "Reklama / spot",
        "description": "Przekaz lub produkt w centrum: problem, rozwiązanie, korzyść i wyraźne wezwanie do działania.",
        "pace": "dynamiczne", "broll_gap": 5.0, "transitions": "calm", "punch_in": True, "slow_zoom": True,
        "title_card": False, "subtitles": ("word_pop", "modern"), "music_mood": "inspirujacy", "look": "high_key",
        "director": "Układ: problem → rozwiązanie → korzyść → wezwanie do działania. Wybieraj najostrzejsze, "
                    "najładniejsze ujęcia. Kończ hasłem lub konkretnym CTA.",
    },
    "kampania": {
        "label": "Kampania społeczna / konkurs",
        "description": "Krótka historia z przesłaniem: scenka z problemem, reakcja, wyjaśnienie i apel do widza.",
        "pace": "umiarkowane", "broll_gap": 8.0, "transitions": "calm", "punch_in": False, "slow_zoom": True,
        "title_card": False, "subtitles": ("word_pop", "modern"), "music_mood": "napiecie", "look": "clean_doc",
        "director": "Pokaż sytuację problemową w scence, potem reakcję lub zwrot, wyjaśnienie i wyraźny apel na "
                    "końcu. Film ma być zrozumiały dla widza, który nie zna kontekstu.",
    },
    "dokument": {
        "label": "Dokument / reportaż",
        "description": "Rzetelnie i spokojnie: wypowiedzi bez przekłamań, kontekst przed emocją, przebitki ilustrują fakty.",
        "pace": "spokojne", "broll_gap": 12.0, "transitions": "calm", "punch_in": False, "slow_zoom": True,
        "title_card": True, "subtitles": ("static", "modern"), "music_mood": "spokojny", "look": "clean_doc",
        "director": "Nie skracaj wypowiedzi w sposób zmieniający sens. Najpierw kontekst (kto, gdzie, kiedy), "
                    "potem emocje. Przebitki mają ilustrować fakty, o których mowa.",
    },
    "wywiad": {
        "label": "Wywiad / rozmowa",
        "description": "Pytanie → odpowiedź, montaż wokół najlepszych odpowiedzi, spokojne cięcia między rozmówcami.",
        "pace": "umiarkowane", "broll_gap": 10.0, "transitions": "calm", "punch_in": False, "slow_zoom": True,
        "title_card": True, "subtitles": ("static", "modern"), "music_mood": "spokojny", "look": "clean_doc",
        "director": "Zostaw pytania, które nadają sens odpowiedziom. Wybieraj najciekawsze odpowiedzi i nie skle"
                    "jaj fragmentów różnych odpowiedzi w jedno zdanie.",
    },
    "podcast": {
        "label": "Podcast / talk",
        "description": "Długie wypowiedzi i minimum cięć: usuwane są tylko pauzy, wypełniacze i powtórzenia.",
        "pace": "spokojne", "broll_gap": None, "transitions": "none", "punch_in": False, "slow_zoom": False,
        "title_card": True, "subtitles": ("static", "minimal"), "music_mood": "spokojny", "look": "clean_doc",
        "director": "Zachowaj ciągłość rozmowy i kolejność wypowiedzi. Usuń tylko pauzy, powtórzenia i dygresje.",
    },
    "tutorial": {
        "label": "Tutorial / poradnik",
        "description": "Krok po kroku: najpierw cel, potem kolejne kroki bez pomyłek, na końcu efekt.",
        "pace": "umiarkowane", "broll_gap": 9.0, "transitions": "calm", "punch_in": True, "slow_zoom": False,
        "title_card": True, "subtitles": ("static", "modern"), "music_mood": "vlog", "look": "clean_doc",
        "director": "Zachowaj kolejność kroków. Najpierw pokaż cel (co osiągniemy), potem kroki, na koniec efekt. "
                    "Wytnij pomyłki i powtórzone próby.",
    },
    "kinowy": {
        "label": "Kinowy / fabularny",
        "description": "Narracja obrazem: ujęcia mogą wybrzmieć, płynne przejścia między scenami, filmowy kolor.",
        "pace": "umiarkowane", "broll_gap": 10.0, "transitions": "calm", "punch_in": False, "slow_zoom": True,
        "title_card": True, "subtitles": ("static", "cinema"), "music_mood": "epicki", "look": "teal_orange",
        "director": "Buduj nastrój i napięcie, pozwól ujęciom wybrzmieć. Sceny zaczynaj od planu ogólnego, potem "
                    "bliżej. Finał ma być emocjonalny.",
    },
    "zwiastun": {
        "label": "Zwiastun / trailer",
        "description": "Same najmocniejsze momenty, rosnące tempo, czarne przejścia i uderzenia, bez zdradzania końca.",
        "pace": "bardzo dynamiczne", "broll_gap": 4.0, "transitions": "flashy", "punch_in": True, "slow_zoom": True,
        "title_card": True, "subtitles": ("word_pop", "cinema"), "music_mood": "epicki", "look": "moody",
        "director": "Pokaż obietnicę historii, nie zdradzaj zakończenia. Tempo ma rosnąć, używaj krótkich wyimków "
                    "zdań. Kulminacja tuż przed planszą końcową.",
    },
    "teledysk": {
        "label": "Teledysk",
        "description": "Montaż pod muzykę: cięcia na bit, efektowne przejścia, energia ważniejsza niż słowa.",
        "pace": "bardzo dynamiczne", "broll_gap": 3.0, "transitions": "flashy", "punch_in": True, "slow_zoom": True,
        "title_card": False, "subtitles": ("none", "bold_social"), "music_mood": "energiczny", "look": "neon_night",
        "director": "Rytm i energia są ważniejsze niż mowa. Zmieniaj plany co kilka taktów, kulminację obrazu "
                    "dopasuj do kulminacji muzyki.",
    },
    "podroz": {
        "label": "Film podróżniczy",
        "description": "Krajobrazy i poczucie miejsca: spektakularny początek, chronologia trasy, oddech między scenami.",
        "pace": "umiarkowane", "broll_gap": 6.0, "transitions": "calm", "punch_in": False, "slow_zoom": True,
        "title_card": True, "subtitles": ("word_pop", "modern"), "music_mood": "inspirujacy", "look": "golden_hour",
        "director": "Zacznij od najbardziej spektakularnego widoku, potem chronologicznie po trasie. Mieszaj plany "
                    "ogólne z detalami, zakończ refleksją lub zachodem słońca.",
    },
    "event": {
        "label": "Relacja z wydarzenia / ślub",
        "description": "Kluczowe momenty po kolei, emocje i reakcje ludzi, przemówienia skrócone do sedna.",
        "pace": "umiarkowane", "broll_gap": 6.0, "transitions": "calm", "punch_in": False, "slow_zoom": True,
        "title_card": True, "subtitles": ("static", "classic"), "music_mood": "romantyczny", "look": "portrait_soft",
        "director": "Kluczowe momenty wydarzenia w kolejności. Pokazuj reakcje i emocje ludzi. Przemówienia skracaj "
                    "do najważniejszych zdań.",
    },
    "sport": {
        "label": "Sport / highlights",
        "description": "Najlepsze akcje rosnąco do najlepszej, zwolnienia na kulminacjach, reakcje jako przebitki.",
        "pace": "bardzo dynamiczne", "broll_gap": 4.0, "transitions": "flashy", "punch_in": True, "slow_zoom": False,
        "title_card": False, "subtitles": ("word_pop", "bold_social"), "music_mood": "energiczny",
        "look": "bleach_bypass",
        "director": "Najlepsze akcje ułożone rosnąco do najlepszej. Zwolnienia na kulminacjach, reakcje zawodników "
                    "i kibiców jako przebitki.",
    },
    "gaming": {
        "label": "Gaming",
        "description": "Najlepsze akcje i reakcje, wycięte nudne fragmenty rozgrywki, efektowne przejścia.",
        "pace": "bardzo dynamiczne", "broll_gap": 5.0, "transitions": "flashy", "punch_in": True, "slow_zoom": False,
        "title_card": False, "subtitles": ("word_pop", "bold_social"), "music_mood": "energiczny",
        "look": "neon_night",
        "director": "Najlepsze akcje i reakcje. Wytnij nudne fragmenty rozgrywki, przy kulminacjach pokaż reakcję.",
    },
    "skecz": {
        "label": "Skecz / komedia",
        "description": "Timing żartu: przygotowanie, pauza przed puentą, reakcja po puencie, bez nieudanych dubli.",
        "pace": "dynamiczne", "broll_gap": None, "transitions": "none", "punch_in": True, "slow_zoom": False,
        "title_card": False, "subtitles": ("word_pop", "bold_social"), "music_mood": "radosny",
        "look": "vibrant_social",
        "director": "Zachowaj timing: przygotowanie → pauza → puenta. Nie tnij reakcji po puencie zbyt szybko. "
                    "Wytnij nieudane duble.",
    },
    "estetyczny": {
        "label": "Estetyczny / moodboard",
        "description": "Spokojne, ładne kadry dobrane kolorem i ruchem, miękkie przejścia, minimum słów.",
        "pace": "spokojne", "broll_gap": 5.0, "transitions": "calm", "punch_in": False, "slow_zoom": True,
        "title_card": False, "subtitles": ("static", "minimal"), "music_mood": "spokojny", "look": "matte_fade",
        "director": "Układaj ujęcia według kolorów i ruchu, pozwól im trwać. Mało tekstu.",
    },
}

DEFAULT = "vlog"
ALIASES = {"tiktok": "rolka", "reels": "rolka", "shorts": "rolka", "short": "rolka", "youtube": "vlog",
           "reportaz": "dokument", "reportaż": "dokument", "interview": "wywiad", "film": "kinowy",
           "fabularny": "kinowy", "trailer": "zwiastun", "music_video": "teledysk", "travel": "podroz",
           "podróż": "podroz", "podroznicze": "podroz", "slub": "event", "ślub": "event", "wesel": "event",
           "highlights": "sport", "komedia": "skecz", "spoleczny": "kampania", "społeczny": "kampania",
           "konkurs": "kampania", "aesthetic": "estetyczny", "poradnik": "tutorial"}


def key(style: str | None) -> str:
    s = (style or "").strip().lower()
    return s if s in STYLES else ALIASES.get(s, DEFAULT)


def get(style: str | None) -> dict[str, Any]:
    return STYLES[key(style)]


def public() -> list[dict[str, Any]]:
    """Katalog dla UI: opis + czytelne podsumowanie reguł."""
    trans = {"none": "czyste cięcia", "calm": "przenikania przy zmianie sceny", "flashy": "efektowne przejścia"}
    out = []
    for k, s in STYLES.items():
        rules = [f"tempo: {s['pace']}",
                 "bez automatycznych przebitek" if s["broll_gap"] is None else f"przebitka co ~{s['broll_gap']:.0f} s",
                 trans[s["transitions"]]]
        if s["punch_in"]:
            rules.append("płynne najazdy na kluczowe słowa")
        if s["slow_zoom"]:
            rules.append("powolne zbliżenia")
        rules.append({"none": "bez napisów", "static": "napisy zdaniami", "word_pop": "napisy słowo po słowie",
                      "karaoke": "napisy karaoke"}[s["subtitles"][0]])
        out.append({"key": k, "label": s["label"], "description": s["description"], "rules": rules,
                    "look": s["look"], "music_mood": s["music_mood"], "pace": s["pace"]})
    return out
