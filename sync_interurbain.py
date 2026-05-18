# -*- coding: utf-8 -*-
"""
Synchronise les données interurbaines depuis la page officielle
https://demdikk.sn/reseau-interurbain/

Usage:
  python sync_interurbain.py                # aperçu (dry-run)
  python sync_interurbain.py --write        # régénère interurbain_data.py
  python sync_interurbain.py --json-out data/interurbain_snapshot.json

Nécessite: requests, beautifulsoup4 (déjà utilisés par le projet).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from typing import Any

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError as e:
    print("Installez requests et beautifulsoup4 : pip install requests beautifulsoup4", file=sys.stderr)
    raise SystemExit(1) from e

DEFAULT_URL = "https://demdikk.sn/reseau-interurbain/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Referer": DEFAULT_URL,
}

# Numéros sénégalais courants sur la page (2 digits + 3 + 2 + 2, ou 338241010, ou 9 chiffres)
_PHONE_RE = re.compile(
    r"(?:\+\s*221\s*)?"
    r"(?:33\s*8?\s*241\s*01\s*10|338241010)"
    r"|(?:\d{2}\s*\d{3}\s*\d{2}\s*\d{2})"
    r"|(?:\d{2}\s*\d{2}\s*\d{2}\s*\d{2}\s*\d{2})"
)


def _norm_txt(s: str) -> str:
    return unicodedata.normalize("NFKC", (s or "").strip())


def _slug_ville(name: str) -> str:
    n = _norm_txt(name).lower()
    n = re.sub(r"[''`]", "", n)
    n = unicodedata.normalize("NFKD", n)
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"[^a-z0-9]+", "-", n).strip("-")
    repl = {
        "thies": "thies",
        "saint-louis": "saint-louis",
        "saint_louis": "saint-louis",
        "ziguinchor": "ziguinchor",
        "velingara": "velingara",
        "velingarra": "velingara",
        "sedhiou": "sedhiou",
        "ourossogui": "ourossogui",
        "ndioum": "ndioum",
        "kebemer": "kebemer",
        "kedougou": "kedougou",
    }
    return repl.get(n, n)


def titre_to_villes(titre: str) -> list[str]:
    t = _norm_txt(titre)
    parts = re.split(r"\s+et\s+|\s*/\s*", t, flags=re.IGNORECASE)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        first = p.split()[0]
        out.append(_slug_ville(first))
    return out or [_slug_ville(t)]


def titre_destination_anchors(titre: str) -> list[str]:
    """Mots-clés pour détecter le début du bloc arrivée (hors Dakar)."""
    villes = titre_to_villes(titre)
    anchors: list[str] = []
    for v in villes:
        if v in ("dakar",):
            continue
        anchors.append(v)
        if v == "saint":
            anchors.append("saint-louis")
        if v == "saint-louis":
            anchors.append("saint")
    # libellés tels que sur le site
    raw = _norm_txt(titre).upper()
    parts = re.split(r"\s+ET\s+|\s*/\s*", raw)
    for p in parts:
        w = p.strip().split()
        if w:
            anchors.append(w[0].lower())
    # dédoublonner en gardant l'ordre
    seen: set[str] = set()
    uniq: list[str] = []
    for a in anchors:
        if a and a not in seen:
            seen.add(a)
            uniq.append(a)
    return uniq


def _looks_like_jours_only(line: str) -> bool:
    """Ligne du type « Lundi, Mercredi… » ou « Tous les jours » sans lieu de départ."""
    raw = _norm_txt(line).strip()
    low = raw.lower()
    if not raw:
        return False
    if re.search(r"\b(dakar|terminus|hlm|gare de| grand yoff)\b", low):
        return False
    if re.search(
        r"^(tous les jours|lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\b",
        low,
    ):
        return True
    if re.search(
        r"\b(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\b.*,"
        r"|,\s*(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)",
        low,
    ):
        return True
    return False


def _line_matches_anchor(line: str, anchors: list[str]) -> bool:
    """True si cette ligne ouvre le bloc lieu / arrivée (pas une ligne « … vers … »)."""
    raw = _norm_txt(line).strip()
    low = raw.lower()
    if not raw:
        return False
    if " vers " in low:
        return False
    if re.match(r"^dakar\s+vers\b", low):
        return False
    first_tok = re.split(r"[\s:/]+", low, 1)[0]
    first_slug = _slug_ville(first_tok.replace("é", "e"))
    for a in anchors:
        if not a or a == "dakar":
            continue
        if first_slug == a:
            return True
        if first_slug.startswith(a) and len(first_tok) <= len(a) + 2:
            return True
    return False


def _find_horaires_header_idx(lines: list[str]) -> int:
    for i, ln in enumerate(lines):
        l = ln.lower()
        if "horaires" in l and "lieu" in l and "départ" in l.replace("e", "é"):
            return i
        if "horaires" in l and "lieu" in l:
            return i
    return -1


def _find_depart_start_idx(rest: list[str]) -> int:
    """Première ligne du bloc départ (souvent « Dakar » ou « 7h HLM … »)."""
    for i, ln in enumerate(rest):
        low = ln.lower().strip()
        if low == "dakar":
            return i
        # Bakel, etc. — pas une simple ligne « 07H » seule (souvent horaire avant Dakar)
        if re.match(r"^(?:7h|07h)\s+HLM\b", ln.strip(), re.I):
            return i
        if re.match(r"^\d+h\s+HLM\b", ln.strip(), re.I):
            return i
    return 0


def parse_prix(lines_pre: list[str]) -> str | dict[str, str]:
    # « Prix » + « 5 000 FCFA » sur deux lignes
    if (
        len(lines_pre) >= 2
        and lines_pre[0].strip().lower() == "prix"
        and "FCFA" in lines_pre[1]
    ):
        lines_pre = [lines_pre[0].strip() + " " + lines_pre[1].strip()] + lines_pre[2:]
    # « Prix 13 » / « 000 FCFA » sur deux lignes (ex. KIDIRA)
    if (
        len(lines_pre) >= 2
        and re.match(r"^Prix\s+[\d\s]+\s*$", lines_pre[0], re.I)
        and "FCFA" in lines_pre[1]
    ):
        lines_pre = [lines_pre[0].strip() + " " + lines_pre[1].strip()] + lines_pre[2:]

    merged: list[str] = []
    i = 0
    while i < len(lines_pre):
        ln = lines_pre[i]
        m_pl = re.match(r"^Prix\s+(\w+)\s*$", ln, re.I)
        if m_pl and i + 1 < len(lines_pre) and ":" in lines_pre[i + 1]:
            merged.append(f"{m_pl.group(1)}: " + lines_pre[i + 1].lstrip().lstrip(":").strip())
            i += 2
            continue
        if (
            re.match(r"^[A-Za-zÀ-ÿéèêëàùûüôöîï'\-]+\s*:\s*$", ln)
            and i + 1 < len(lines_pre)
            and "FCFA" in lines_pre[i + 1]
        ):
            merged.append(ln.strip() + " " + lines_pre[i + 1].strip())
            i += 2
            continue
        merged.append(ln)
        i += 1

    blob = " ".join(merged)
    blob = re.sub(r"\s+", " ", blob)

    # Multi-tarifs: « Louga : 4000 FCFA » « Kébémer: 3000 FCFA »
    pairs = re.findall(
        r"([A-Za-zÀ-ÿéèêëàùûüôöîï'\-]+(?:\s+[A-Za-zÀ-ÿéèêëàùûüôöîï'\-]+)?)\s*:\s*([\d\s]+)\s*FCFA",
        blob,
        flags=re.I,
    )
    pairs = [(k, v) for k, v in pairs if not k.strip().lower().startswith("prix")]
    if len(pairs) >= 2:
        out: dict[str, str] = {}
        for k, v in pairs:
            key = k.strip()
            out[key] = re.sub(r"\s+", " ", v.strip()) + " FCFA"
        if out:
            return out

    m = re.search(r"Prix\s*:?\s*([\d\s]+)\s*FCFA", blob, flags=re.I)
    if m:
        num = re.sub(r"\s+", " ", m.group(1).strip())
        return num + " FCFA"
    return ""


def _split_jours_horaires(
    middle: list[str],
) -> tuple[list[str], list[str]]:
    jours: list[str] = []
    horaires: list[str] = []
    jour_pat = re.compile(
        r"tous|lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche|sauf|jour",
        re.I,
    )
    for ln in middle:
        lns = ln.strip()
        if not lns:
            continue
        if jour_pat.search(lns) and not re.search(r"\d+\s*h", lns, re.I):
            jours.append(lns)
        else:
            horaires.append(lns)
    if not jours and middle:
        jours = [middle[0]] if middle else []
    if not horaires and len(middle) > 1:
        horaires = middle[1:]
    return jours, horaires


def _extract_phones(s: str) -> list[str]:
    found = []
    for m in _PHONE_RE.finditer(s):
        t = re.sub(r"\s+", " ", m.group(0).strip())
        if t:
            found.append(t)
    # 338241010 sans espaces
    if "338241010" in s.replace(" ", "") and not any("338" in p for p in found):
        found.append("338241010")
    return found


def parse_section(titre: str, raw_lines: list[str]) -> dict[str, Any]:
    lines = [_norm_txt(x) for x in raw_lines if _norm_txt(x)]
    titre = _norm_txt(titre)
    ih = _find_horaires_header_idx(lines)
    if ih < 0:
        return {
            "titre": titre,
            "villes": titre_to_villes(titre),
            "parse_note": "section_sans_entete_horaires",
            "raw_lines": lines,
        }

    pre = lines[:ih]
    rest = lines[ih + 1 :]

    prix = parse_prix(pre)
    ds = _find_depart_start_idx(rest)
    middle = rest[:ds]
    after = rest[ds:]

    jours, horaires = _split_jours_horaires(middle)
    anchors = titre_destination_anchors(titre)

    # départ : lignes « jours » pures (ex. KAFFRINE) sont réservées aux jours, pas au départ
    depart_parts: list[str] = []
    extra_jours_after_depart: list[str] = []
    i = 0
    while i < len(after):
        ln = after[i]
        if _looks_like_jours_only(ln):
            extra_jours_after_depart.append(ln)
            i += 1
            continue
        if i > 0 and _line_matches_anchor(ln, anchors):
            break
        depart_parts.append(ln)
        i += 1
        if i > 6:
            break
    depart = re.sub(r"\s+", " ", " ".join(depart_parts)).strip()

    # lignes jours/horaires mélangées avant le bloc lieu (ex. 2ᵉ ligne « Mardi… » sur KAFFRINE)
    tl = list(after[i:]) if i < len(after) else []
    extra_sched_tail: list[str] = []
    j = 0
    while j < len(tl):
        if _looks_like_jours_only(tl[j]):
            extra_sched_tail.append(tl.pop(j))
            continue
        j += 1

    lieu_blob = " ".join(tl) if tl else ""
    lieu_blob = re.sub(r"\s+", " ", lieu_blob).strip()
    # construire lieux_contact en découpant sur les téléphones
    lieux_contact: list[dict[str, Any]] = []
    if lieu_blob:
        phones = _extract_phones(lieu_blob)
        if not phones:
            m = re.search(r"(\d{8,})", lieu_blob.replace(" ", ""))
            if m:
                phones = [m.group(1)]

        if phones:
            cursor = 0
            for pi, phone in enumerate(phones):
                idx = lieu_blob.find(phone, cursor)
                if idx < 0:
                    continue
                before = lieu_blob[cursor:idx]
                lieu = before.strip(" /–-—,\n\t|")
                lieu = re.sub(r"\s+", " ", lieu)
                tel = re.sub(r"\s+", " ", phone).strip()
                if lieu or tel:
                    lieux_contact.append({"lieu": lieu, "tel": tel})
                cursor = idx + len(phone)
            tail = lieu_blob[cursor:].strip()
            if tail and not re.search(r"\d{2}\s*\d{3}", tail):
                if lieux_contact:
                    lieux_contact[-1]["lieu"] = (
                        (lieux_contact[-1].get("lieu") or "") + " " + tail
                    ).strip()
        else:
            lieux_contact.append({"lieu": lieu_blob, "tel": None})

    merged_jours = (jours or []) + extra_jours_after_depart + extra_sched_tail

    sec: dict[str, Any] = {
        "titre": titre.upper().replace("É", "E") if titre.isupper() else titre,
        "villes": titre_to_villes(titre),
        "prix": prix or "",
        "horaires": horaires or [],
        "jours": merged_jours or [],
        "depart": depart,
        "lieux_contact": lieux_contact,
    }
    # harmoniser titre comme sur le site (majuscules simples)
    sec["titre"] = titre.strip()
    return sec


def scrape_sections(url: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    r = requests.get(url, timeout=40, headers=_REQUEST_HEADERS)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")

    sections_out: list[dict[str, Any]] = []
    seen_titles: set[str] = set()

    h2_candidates: list[Any] = []
    for sel in (
        ".elementor-widget-heading h2",
        "main h2.elementor-heading-title",
        "main .elementor-widget-heading h2",
        ".entry-content h2",
        "main h2",
    ):
        for h2 in soup.select(sel):
            h2_candidates.append(h2)

    for h2 in h2_candidates:
        titre = _norm_txt(h2.get_text())
        if not titre or len(titre) < 2:
            continue
        if re.match(r"^réseau interurbain$", titre, re.I):
            continue
        key = titre.lower()
        if key in seen_titles:
            continue

        con = None
        for p in h2.parents:
            cls = p.get("class") or []
            if "e-con" in cls and "e-child" in cls:
                con = p
                break
        if con is None:
            con = h2.find_parent("div")

        te = None
        if con is not None:
            te = con.select_one(".elementor-widget-text-editor .elementor-widget-container")
            if te is None:
                te = con.select_one(".elementor-widget-text-editor")
        if te is None:
            wrap = h2.find_parent("div", class_=lambda c: c and "elementor-widget-wrap" in " ".join(c))
            if wrap is not None:
                te = wrap

        if te is None:
            continue

        raw = te.get_text("\n", strip=True)
        lines = [ln for ln in raw.splitlines()]
        parsed = parse_section(titre, lines)
        sections_out.append(parsed)
        seen_titles.add(key)

    meta = {
        "source_url": url,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "sections_count": len(sections_out),
    }
    return sections_out, meta


def _py_repr(obj: Any, indent: int = 4) -> str:
    sp = " " * indent
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        parts = []
        for k, v in obj.items():
            parts.append(f"{sp}{repr(k)}: {_py_repr(v, indent + 4)}")
        return "{\n" + ",\n".join(parts) + "\n" + " " * (indent - 4) + "}"
    if isinstance(obj, list):
        if not obj:
            return "[]"
        parts = [_py_repr(x, indent + 4) for x in obj]
        inner = (",\n").join(parts)
        return "[\n" + inner + "\n" + sp + "]"
    if isinstance(obj, str):
        return repr(obj)
    if obj is None:
        return "None"
    if isinstance(obj, bool):
        return "True" if obj else "False"
    return repr(obj)


HELPERS_BLOCK = '''

def get_section_by_ville(ville: str) -> dict | None:
    """Retourne la section interurbaine pour une ville (ex: 'fatick', 'kebemer', 'ndioum')."""
    ville_lower = ville.lower().strip()
    for section in INTERURBAIN_SECTIONS:
        if ville_lower in [v.lower() for v in section["villes"]]:
            return section
    return None


def get_prix_for_ville(ville: str) -> str | None:
    """Retourne le prix affiché pour une ville (gère Louga/Kébémer)."""
    section = get_section_by_ville(ville)
    if not section:
        return None
    p = section.get("prix")
    if isinstance(p, dict):
        for k, v in p.items():
            if k.lower() == ville.lower() or (ville.lower() == "kebemer" and "ébémer" in k):
                return v
        return next(iter(p.values()), None)
    return p


def get_contact_for_ville(ville: str) -> list[dict]:
    """Retourne les lieux/contacts pour une ville (liste de {lieu, tel})."""
    section = get_section_by_ville(ville)
    if not section:
        return []
    contacts = section.get("lieux_contact", [])
    ville_lower = ville.lower()
    # Pour les sections partagées (Louga/Kébémer, Podor/Ndioum), retourner uniquement l'entrée de la ville demandée
    if len(section.get("villes", [])) > 1:
        for lc in contacts:
            lieu = (lc.get("lieu") or "").lower()
            if ville_lower in lieu or (ville_lower == "kebemer" and "kébémer" in lieu):
                return [lc]
        return contacts[:1] if contacts else []
    return contacts
'''


def build_py_file(sections: list[dict[str, Any]]) -> str:
    header = '''# -*- coding: utf-8 -*-
"""
Données de référence du Réseau Interurbain Dakar Dem Dikk.
Source: demdikk.sn/reseau-interurbain/

Ce fichier peut être régénéré avec :
  python sync_interurbain.py --write
"""

# Liste des sections interurbaines avec prix, horaires, jours, départ, arrivée/contact
'''
    body = "INTERURBAIN_SECTIONS = " + _py_repr(sections, 4) + "\n\n"
    fallback_loader = """
# Si le scrape n'a rien produit, données de secours (villes courantes)
if not INTERURBAIN_SECTIONS:
    try:
        from interurbain_fallback_sections import INTERURBAIN_FALLBACK_SECTIONS as _INT_FB
        INTERURBAIN_SECTIONS.extend(_INT_FB)
    except ImportError:
        pass
"""
    return header + body + fallback_loader + HELPERS_BLOCK


def main() -> None:
    ap = argparse.ArgumentParser(description="Sync données interurbaines depuis demdikk.sn")
    ap.add_argument("--url", default=DEFAULT_URL, help="URL de la page réseau interurbain")
    ap.add_argument("--write", action="store_true", help="Écrase interurbain_data.py")
    ap.add_argument(
        "--json-out",
        metavar="PATH",
        help="Écrit aussi un snapshot JSON (sections + meta)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Alias : n'écrit pas le .py (défaut sans --write)")
    args = ap.parse_args()

    sections, meta = scrape_sections(args.url)
    payload = {"meta": meta, "sections": sections}

    if args.json_out:
        out_path = args.json_out
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"Écrit {out_path}")

    # Résumé console
    print(f"Source : {meta['source_url']}")
    print(f"Sections : {meta['sections_count']} — scrapé le {meta['scraped_at']}")
    for s in sections[:5]:
        t = s.get("titre", "?")
        px = s.get("prix", "")
        print(f"  • {t[:48]:<48} prix={str(px)[:40]}")
    if len(sections) > 5:
        print(f"  … ({len(sections) - 5} autres)")

    if args.write:
        py_path = __file__.replace("sync_interurbain.py", "interurbain_data.py")
        # si script pas à la racine
        import os

        root = os.path.dirname(os.path.abspath(__file__))
        py_path = os.path.join(root, "interurbain_data.py")
        text = build_py_file(sections)
        with open(py_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Mis à jour : {py_path}")
    else:
        print("(dry-run) Ajoutez --write pour régénérer interurbain_data.py")


if __name__ == "__main__":
    main()
