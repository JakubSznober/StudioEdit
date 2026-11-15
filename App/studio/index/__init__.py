"""Indeks materiałów: baza wiedzy o każdym nagraniu i ujęciu.

Etapy (każdy osobno wznawialny):
  probe    – ffprobe: metadane, profil koloru
  proxy    – proxy 720p + audio 16 kHz
  shots    – granice ujęć (PySceneDetect) + jednostki analizy
  quality  – ostrość, ekspozycja, ruch, klatki kluczowe
  speech   – transkrypcja Whisper ze znacznikami słów
  embed    – embeddingi SigLIP (obraz ↔ tekst, wielojęzyczne)
  vision   – opis ujęć przez lokalny VLM (Ollama)
"""
from .db import Database  # noqa: F401
from .pipeline import STAGES, index_paths  # noqa: F401
from .query import search  # noqa: F401
