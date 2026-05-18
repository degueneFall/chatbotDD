# -*- coding: utf-8 -*-
"""
Point d'entrée du scraper (pipeline RAG).

Par défaut : télécharge les pages listées dans **scrape_targets.py** (PAGE_URLS)
et écrit **data/scraped.jsonl** (pas d’API WordPress).

Chaîne complète :
  1. `python scraper.py`              → data/scraped.jsonl
  2. `python indexer.py`                → data/embeddings.npy + data/metadata.json
  3. `app_backup.py` / `app.py`        → recherche + DeepSeek

Mode optionnel WordPress (REST) si l’API répond chez toi :
  python scraper.py --wp

Tout régénérer : `python update_from_site.py --no-http-refresh`
"""
from __future__ import annotations

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_ROOT, "data"))


def _run_urls() -> int:
    from scrape_urls import main as urls_main

    return int(urls_main())


def _run_wp() -> int:
    from ingest_wp import main as wp_main

    return int(wp_main())


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Scraper Dakar Dem Dikk → data/scraped.jsonl")
    ap.add_argument(
        "--wp",
        action="store_true",
        help="Utiliser l’API WordPress (ingest_wp.py) au lieu des URLs dans scrape_targets.py",
    )
    args = ap.parse_args()
    code = _run_wp() if args.wp else _run_urls()
    raise SystemExit(code)
