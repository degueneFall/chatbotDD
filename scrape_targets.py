# -*- coding: utf-8 -*-
"""
Liste des pages à télécharger pour le RAG (scraper.py → scraped.jsonl → indexer.py).

Modifie PAGE_URLS : une URL par ligne, pages publiques du site Dakar Dem Dikk
(ou d’autres domaines si besoin). Pas besoin de l’API WordPress.
"""
from __future__ import annotations

PAGE_URLS: list[str] = [
    "https://demdikk.sn/",
    "https://demdikk.sn/chatbot-2303/",
    "https://demdikk.sn/services/",
    "https://demdikk.sn/reseau-urbain-dakar/",
    "https://demdikk.sn/reseau-interurbain/",
    "https://demdikk.sn/presentation/",
    "https://demdikk.sn/actualites/",
]
