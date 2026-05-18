# -*- coding: utf-8 -*-
"""
Automatise la mise à jour des données locales depuis demdikk.sn :

  1. sync_interurbain.py --write  → interurbain_data.py (+ snapshot JSON)
  2. scraper.py                     → données brutes pour l’index RAG (PAGE_URLS dans scrape_targets.py, ou --wp pour l’API WP)
  3. indexer.py                     → embeddings / metadata
  4. (optionnel) POST /reload_embeddings → recharge l’index en mémoire sans refaire scraper/indexer

Usage :
  python update_from_site.py
  python update_from_site.py --no-http-refresh
  python update_from_site.py --reload-url http://127.0.0.1:5000/reload_embeddings

Variables d’environnement utiles :
  REFRESH_TOKEN            — même secret Bearer que pour /reload_embeddings et /refresh_index
  RELOAD_EMBEDDINGS_URL    — URL POST (défaut http://127.0.0.1:5000/reload_embeddings)
  POST_UPDATE_CMD          — commande exécutée en fin de succès (ex. redémarrage service)

Alternative « tout-en-un » sur le serveur où Flask tourne : une seule requête
  POST /refresh_index  (Authorization: Bearer REFRESH_TOKEN)
  → sync_interurbain + scraper + indexer + rechargement mémoire (voir app.py).

Planification :
  Windows : Planificateur de tâches → « python » avec argument
            « C:\\chemin\\vers\\update_from_site.py »
  Linux     : cron : 0 3 * * * cd /path/to/project && /path/to/python update_from_site.py

Note : interurbain_data.py est chargé au démarrage du worker Flask ; après mise à jour,
       redémarrez le processus (gunicorn, Windows service, etc.) pour les réponses
       « ville » si HTTP refresh ne suffit pas à recharger les modules.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time


def _root() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _run_step(name: str, argv: list[str], cwd: str, timeout: int | None) -> None:
    print(f"\n── {name} ──")
    print(" ", " ".join(argv))
    r = subprocess.run(argv, cwd=cwd, capture_output=False, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"{name} a échoué (code {r.returncode})")


def _http_refresh(url: str, token: str) -> None:
    try:
        import urllib.request
        import json as _json
    except ImportError:
        raise RuntimeError("urllib indisponible")

    body = _json.dumps({}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    print(f"\n── HTTP refresh ──\n  POST {url}")
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read().decode("utf-8", errors="replace")
        print(data[:2000])


def main() -> int:
    ap = argparse.ArgumentParser(description="Met à jour scraper, index et données interurbaines")
    ap.add_argument(
        "--no-http-refresh",
        action="store_true",
        help="Ne pas appeler POST /refresh_index (ex. pas de serveur local)",
    )
    ap.add_argument(
        "--reload-url",
        default=os.environ.get("RELOAD_EMBEDDINGS_URL", "http://127.0.0.1:5000/reload_embeddings"),
        help="URL POST pour recharger l’index en mémoire (évite de refaire scraper/indexer)",
    )
    ap.add_argument(
        "--skip-interurbain",
        action="store_true",
        help="Ne pas exécuter sync_interurbain.py",
    )
    ap.add_argument(
        "--skip-scraper",
        action="store_true",
        help="Ne pas exécuter scraper.py",
    )
    ap.add_argument(
        "--skip-indexer",
        action="store_true",
        help="Ne pas exécuter indexer.py",
    )
    args = ap.parse_args()

    root = _root()
    py = sys.executable or "python"
    os.chdir(root)
    started = time.time()

    try:
        if not args.skip_interurbain:
            snap = os.path.join(root, "data", "interurbain_snapshot.json")
            os.makedirs(os.path.join(root, "data"), exist_ok=True)
            _run_step(
                "sync_interurbain",
                [py, "sync_interurbain.py", "--write", "--json-out", snap],
                root,
                timeout=120,
            )

        if not args.skip_scraper:
            _run_step("scraper", [py, "scraper.py"], root, timeout=180)

        if not args.skip_indexer:
            _run_step("indexer", [py, "indexer.py"], root, timeout=600)

        if not args.no_http_refresh:
            token = (os.environ.get("REFRESH_TOKEN") or "").strip()
            if not token:
                print(
                    "\n⚠ REFRESH_TOKEN non défini : impossible d’appeler /refresh_index."
                    " Définissez-le ou utilisez --no-http-refresh.\n"
                    "   Les fichiers sur disque sont à jour ; rechargez l’index à la main ou redémarrez l’app."
                )
            else:
                _http_refresh(args.reload_url.strip(), token)

        post = (os.environ.get("POST_UPDATE_CMD") or "").strip()
        if post:
            print(f"\n── POST_UPDATE_CMD ──\n  {post}")
            subprocess.run(post, shell=True, cwd=root)

        elapsed = int(time.time() - started)
        print(f"\n✓ Terminé en {elapsed}s")
        print(
            "\nRappel : si les réponses « ville » interurbaines ne changent pas sans redémarrage,"
            "\n  redémarrez le processus Flask / gunicorn après mise à jour d’interurbain_data.py."
        )
        return 0

    except subprocess.TimeoutExpired as e:
        print(f"\n✗ Timeout : {e}", file=sys.stderr)
        return 124
    except Exception as e:
        print(f"\n✗ Erreur : {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
