# -*- coding: utf-8 -*-
"""
Téléchargement HTML des pages listées dans scrape_targets.py (racine du projet).
Produit le même format que l’ancien ingest_wp : dicts {url, title, text} pour scraped.jsonl.
"""
from __future__ import annotations

import html as html_module
import re
import sys
from typing import Any

import requests
from bs4 import BeautifulSoup

_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}


def _strip_noise_soup(soup: BeautifulSoup) -> None:
    for sel in (
        "header",
        "nav",
        "footer",
        "aside",
        "script",
        "style",
        "noscript",
        ".site-header",
        ".site-footer",
        ".elementor-location-header",
        ".elementor-location-footer",
        ".widget",
        ".sidebar",
        ".menu",
        ".navbar",
        ".breadcrumb",
        ".breadcrumbs",
    ):
        for el in soup.select(sel):
            el.decompose()


def _page_title(soup: BeautifulSoup) -> str:
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return html_module.unescape(og["content"].strip())
    t = soup.find("title")
    if t and t.string:
        return html_module.unescape(t.string.strip())
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(" ", strip=True)
    return ""


def scrape_one(url: str, timeout: int = 45) -> dict[str, Any] | None:
    try:
        r = requests.get(url, headers=_REQUEST_HEADERS, timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"Échec GET {url}: {e}", file=sys.stderr)
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    _strip_noise_soup(soup)
    title = _page_title(soup)
    container = (
        soup.select_one("main")
        or soup.select_one("article")
        or soup.select_one(".entry-content")
        or soup.select_one(".elementor-location-single")
        or soup.body
        or soup
    )
    raw = container.get_text("\n", strip=True) if container else ""
    # Normaliser espaces / lignes vides
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) < 40:
        print(f"Contenu trop court pour {url} ({len(text)} car.) — page vide ou blocage ?", file=sys.stderr)
        return None
    return {"url": url.rstrip("/"), "title": title, "text": text}


def scrape_urls(urls: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for u in urls:
        u = (u or "").strip()
        if not u or u.startswith("#"):
            continue
        print(f"... {u}")
        doc = scrape_one(u)
        if doc:
            out.append(doc)
    return out


def main(urls: list[str] | None = None) -> int:
    import json
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if urls is None:
        sys.path.insert(0, root)
        try:
            from scrape_targets import PAGE_URLS
        except ImportError:
            print("Fichier scrape_targets.py introuvable à la racine du projet.", file=sys.stderr)
            return 1
        urls = list(PAGE_URLS)

    if not urls:
        print("PAGE_URLS est vide dans scrape_targets.py", file=sys.stderr)
        return 1

    scraped = scrape_urls(urls)
    if not scraped:
        print("\nERREUR: Aucune page recuperee -- scraped.jsonl non ecrit.", file=sys.stderr)
        return 1

    os.makedirs(os.path.join(root, "data"), exist_ok=True)
    out_path = os.path.join(root, "data", "scraped.jsonl")
    with open(out_path, "w", encoding="utf-8") as fout:
        for obj in scraped:
            fout.write(json.dumps(obj, ensure_ascii=False) + "\n")

    print(f"\nOK: {len(scraped)} page(s) -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
