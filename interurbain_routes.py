# -*- coding: utf-8 -*-
"""
Itinéraires et durées interurbaines — source officielle chatbot-2303.
La page réseau-interurbain ne publie pas ces détails.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

_CHATBOT_URL = "https://demdikk.sn/chatbot-2303/"
_CACHE: dict[str, dict[str, Any]] | None = None

_ROUTE_ALIASES: dict[str, str] = {
    "ourossogui": "matam",
    "tambacounda": "kedougou",
}

# Données officielles chatbot-2303 (secours si fetch indisponible)
ROUTES_FALLBACK: dict[str, dict[str, Any]] = {
    "mbour": {
        "itineraire": "",
        "durees": {"08h": "1 h 35", "16h": "1 h 30"},
    },
    "thies": {
        "itineraire": "Dakar – Autoroute – Thiès",
        "durees": {"default": "1 h 15"},
    },
    "fatick": {
        "itineraire": "Dakar – Autoroute – Keur Balla – Sessene – Tiadiaye – Diouroup – Fatick",
        "durees": {"07h": "3 h", "15h": "3 h"},
    },
    "kaolack": {
        "itineraire": "Dakar – Autoroute – Keur Balla – Sessene – Tiadiaye – Diouroup – Fatick – Gandiaye – Sibosor – Kaolack",
        "durees": {"07h": "4 h", "15h": "4 h"},
    },
    "kaffrine": {
        "itineraire": "Dakar – Autoroute – Keur Balla – Sessene – Tiadiaye – Diouroup – Fatick – Gandiaye – Sibosor – Kaolack – Kaffrine",
        "durees": {"default": "5 h", "08h": "5 h"},
    },
    "saint-louis": {
        "itineraire": "Dakar – Autoroute – Thiès – Tivaouane – Kébémer – Ngoumba Guewoul – Louga – Sakaal – Mpal – Raw – Ngandiol – Saint-Louis",
        "durees": {"07h": "5 h", "14h": "5 h 45"},
    },
    "kebemer": {
        "itineraire": "",
        "durees": {"07h": "3 h 35", "15h": "3 h 35"},
    },
    "louga": {
        "itineraire": "Dakar – Autoroute – Thiès – Tivaouane – Kébémer – Ngoumba Guewoul – Louga",
        "durees": {"07h": "4 h", "15h": "4 h"},
    },
    "diourbel": {
        "itineraire": "Dakar – Autoroute – Diourbel",
        "durees": {"07h": "3 h", "15h": "2 h"},
    },
    "touba": {
        "itineraire": "Dakar – Péage – Ngabou – Ndam – Touba",
        "durees": {"07h": "4 h", "15h": "3 h"},
    },
    "tivaouane": {
        "itineraire": "Dakar – Autoroute – Thiès – Tivaouane",
        "durees": {"07h": "3 h", "10h": "2 h", "17h": "2 h 45"},
    },
    "kidira": {
        "itineraire": "Dakar – Dyabougou – Mossi – Goudiry – Bala – Boynguel – Kothiary – Kidira",
        "durees": {"default": "10 h", "07h": "10 h"},
    },
    "kedougou": {
        "itineraire": "Dakar – Autoroute – Keur Balla – Fatick – Kaolack – Kaffrine – Koungheul – Tamba – Kédougou",
        "durees": {"default": "12 h", "07h": "12 h"},
    },
    "matam": {
        "itineraire": "Dakar – Dahra – Ranerou – Barkedji – Linguère – Ourossogui – Matam",
        "durees": {"default": "7 h", "08h": "7 h"},
    },
    "bakel": {
        "itineraire": "Dakar – Dahra – Ranerou – Linguère – Ourossogui – Bakel",
        "durees": {"default": "10 h", "07h": "10 h"},
    },
    "kolda": {
        "itineraire": "Dakar – Autoroute – Keur Balla – Sessene – Tiadiaye – Diouroup – Fatick – Gandiaye – Sibosor – Kaolack – Nioro – Keur Ayib – Seneba – Carrefour Diaroumbe – Carrefour Ndiaye – Kolda",
        "durees": {"default": "10 h", "08h": "10 h"},
    },
    "sedhiou": {
        "itineraire": "Dakar – Autoroute – Keur Balla – Sessene – Tiadiaye – Diouroup – Fatick – Gandiaye – Sibosor – Kaolack – Nioro – Keur Ayib – Seneba – Carrefour Diaroumbe – Carrefour Ndiaye – Sédhiou",
        "durees": {"default": "10 h", "07h": "10 h"},
    },
    "ziguinchor": {
        "itineraire": "Dakar – Autoroute – Keur Balla – Sessene – Tiadiaye – Diouroup – Fatick – Gandiaye – Sibosor – Kaolack – Nioro – Keur Ayib – Seneba – Carrefour Diaroumbe – Bignona – Ziguinchor",
        "durees": {"default": "9 h 30", "08h": "9 h 30"},
    },
    "bignona": {
        "itineraire": "Dakar – Autoroute – Keur Balla – Sessene – Tiadiaye – Diouroup – Fatick – Gandiaye – Sibosor – Kaolack – Nioro – Keur Ayib – Seneba – Carrefour Diaroumbe – Bignona",
        "durees": {"default": "9 h", "08h": "9 h"},
    },
}


def _strip_accents(s: str) -> str:
    n = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in n if not unicodedata.combining(c))


def _slug_city(name: str) -> str:
    raw = (name or "").strip()
    raw = re.split(r"\s*/\s*", raw)[0].strip()
    s = _strip_accents(raw).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    repl = {
        "saint-louis": "saint-louis",
        "saint": "saint-louis",
        "thies": "thies",
        "sédhiou": "sedhiou",
        "sedhiou": "sedhiou",
        "kédougou": "kedougou",
        "kedougou": "kedougou",
    }
    return repl.get(s, s)


def _time_to_minutes(token: str) -> int | None:
    m = re.search(r"(\d{1,2})h(\d{2})?", (token or "").strip(), re.I)
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2) or "0")


def _format_duration(minutes: int) -> str:
    if minutes <= 0:
        return ""
    h, mn = divmod(minutes, 60)
    if mn == 0:
        return f"{h} h" if h != 1 else "1 h"
    if h == 0:
        return f"{mn} min"
    return f"{h} h {mn:02d}"


def _register_duree(out: dict[str, str], dep_token: str, dur: str) -> None:
    """Enregistre une durée sous 07h, 7h, 07h00… pour faciliter la recherche."""
    dep_key = dep_token.lower()
    out[dep_key] = dur
    m = re.match(r"(\d{1,2})h(\d{2})?", dep_key, re.I)
    if not m:
        return
    h = int(m.group(1))
    mn = m.group(2) or ""
    out[f"{h}h"] = dur
    out[f"{h:02d}h"] = dur
    if mn:
        out[f"{h}h{mn}"] = dur
        out[f"{h:02d}h{mn}"] = dur


def _lookup_duree(durees: dict[str, str], depart: str) -> str:
    dm = re.match(r"(\d{1,2})h(\d{2})?", (depart or "").strip(), re.I)
    if not dm:
        return durees.get("default", "")
    h = int(dm.group(1))
    mn = dm.group(2) or ""
    keys = [f"{h}h{mn}", f"{h:02d}h{mn}", f"{h}h", f"{h:02d}h"]
    for key in keys:
        if durees.get(key):
            return durees[key]
    return durees.get("default", "")


def _parse_arrivals_from_body(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in re.finditer(
        r"D[ée]part\s+(?:Dakar\s+)?(\d{1,2}h(?:\d{2})?)\s*[→\-–—>]+?\s*arriv[eé]e au plus tard\s+(\d{1,2}h(?:\d{2})?)",
        body,
        re.I,
    ):
        dep_m = _time_to_minutes(m.group(1))
        arr_m = _time_to_minutes(m.group(2))
        if dep_m is None or arr_m is None or arr_m < dep_m:
            continue
        dur = _format_duration(arr_m - dep_m)
        if not dur:
            continue
        _register_duree(out, m.group(1), dur)
    if not out:
        m_single = re.search(
            r"Heure d['']arriv[eé]e\s*:\s*Au plus tard\s+(\d{1,2}h(?:\d{2})?)",
            body,
            re.I,
        )
        m_dep = re.search(r"D[ée]part de Dakar:.*?(?:à\s+)?(\d{1,2}h)", body, re.I)
        if m_single and m_dep:
            arr_m = _time_to_minutes(m_single.group(1))
            dep_m = _time_to_minutes(m_dep.group(1))
            if arr_m is not None and dep_m is not None and arr_m >= dep_m:
                dur = _format_duration(arr_m - dep_m)
                if dur:
                    out["default"] = dur
                    _register_duree(out, m_dep.group(1), dur)
    return out


def _extract_itineraire_from_body(body: str) -> str:
    for pat in (
        r"Itin[eé]raire\s*\n\s*:\s*(.+)",
        r"Itin[eé]raire\s*:\s*(.+)",
    ):
        mi = re.search(pat, body, re.I)
        if mi:
            return mi.group(1).strip()
    return ""


def _truncate_itineraire(itineraire: str, ville_slug: str, ville_label: str) -> str:
    if not itineraire:
        return ""
    parts = re.split(r"\s*[–-]\s*", itineraire)
    out: list[str] = []
    target = _strip_accents(ville_label or ville_slug).lower()
    for p in parts:
        pn = _strip_accents(p).lower()
        out.append(p.strip())
        if target in pn or pn in target:
            break
    if out and out[0].lower() == "dakar":
        out = out[1:]
    return " – ".join(out)


def parse_routes_from_chatbot(page_text: str) -> dict[str, dict[str, Any]]:
    routes: dict[str, dict[str, Any]] = {}
    if not page_text:
        return routes
    pattern = re.compile(
        r"^DAKAR\s*[–\-]\s*([^\n]+)\n(.*?)(?=^DAKAR\s*[–\-]\s*[^\n]+\n|\Z)",
        re.I | re.S | re.M,
    )
    for m in pattern.finditer(page_text):
        city_label = m.group(1).strip()
        if (
            not city_label
            or city_label.lower().startswith("dakar")
            or ":" in city_label
            or len(city_label) > 35
            or re.search(r"d[ée]part|arriv", city_label, re.I)
        ):
            continue
        body = m.group(2)
        slug = _slug_city(city_label)
        if slug in routes:
            continue
        routes[slug] = {
            "itineraire": _extract_itineraire_from_body(body),
            "durees": _parse_arrivals_from_body(body),
            "ville_label": city_label,
        }
    return routes


def _fetch_chatbot_page() -> str:
    try:
        import sys

        app_mod = sys.modules.get("app")
        fetch = getattr(app_mod, "_fetch_page_text", None) if app_mod else None
        if fetch:
            text = fetch(_CHATBOT_URL) or ""
            if text:
                return text
    except Exception:
        pass
    try:
        import requests
        from bs4 import BeautifulSoup

        r = requests.get(
            _CHATBOT_URL,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (compatible; DakarDemDikkBot/1.0)"},
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        main = soup.select_one("main") or soup.body or soup
        return main.get_text("\n", strip=True)
    except Exception:
        return ""


def _load_routes() -> dict[str, dict[str, Any]]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    merged = {k: dict(v) for k, v in ROUTES_FALLBACK.items()}
    live = parse_routes_from_chatbot(_fetch_chatbot_page())
    for slug, info in live.items():
        base = merged.get(slug, {})
        itin = (info.get("itineraire") or "").strip() or (base.get("itineraire") or "").strip()
        durees = dict(info.get("durees") or {}) or dict(base.get("durees") or {})
        if itin or durees:
            merged[slug] = {
                "itineraire": itin,
                "durees": durees,
                "ville_label": info.get("ville_label") or base.get("ville_label") or slug,
            }
    _CACHE = merged
    return _CACHE


def get_route_info(ville: str) -> dict[str, Any]:
    slug = _slug_city(ville)
    routes = _load_routes()
    info = routes.get(slug)
    if not info and slug in _ROUTE_ALIASES:
        base = routes.get(_ROUTE_ALIASES[slug], {})
        if base:
            itin = _truncate_itineraire(base.get("itineraire") or "", slug, ville)
            info = {
                "itineraire": itin,
                "durees": dict(base.get("durees") or {}),
                "ville_label": ville,
            }
    return dict(info) if info else {}


def format_itinerary_prose(itineraire: str, titre_disp: str) -> str:
    if not itineraire:
        return ""
    route = re.sub(r"^Dakar\s*[–-]\s*", "", itineraire, flags=re.I)
    route = route.replace(" – ", ", ").replace(" - ", ", ")
    segments = [s.strip() for s in route.split(",") if s.strip()]
    if segments and titre_disp.lower() in segments[-1].lower():
        segments = segments[:-1]
    if not segments:
        return ""
    if len(segments) > 6:
        segments = segments[:6] + ["…"]
    return f"L'itinéraire passe par {', '.join(segments)}."


def _duration_to_minutes(label: str) -> int:
    s = (label or "").strip().lower()
    m = re.match(r"(\d+)\s*h(?:\s*(\d+))?", s)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2) or 0)
    m = re.match(r"(\d+)\s*min", s)
    if m:
        return int(m.group(1))
    return 0


def _pick_representative_duration(labels: list[str]) -> str:
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    return min(labels, key=lambda x: (_duration_to_minutes(x), x))


def format_duration_prose(durees: dict[str, str], departs: list[str] | None = None) -> str:
    if not durees:
        return ""
    matched: list[str] = []
    seen_hours: set[int] = set()
    if departs:
        for d in departs:
            dm = re.match(r"(\d{1,2})h", d, re.I)
            if not dm:
                continue
            hour = int(dm.group(1))
            if hour in seen_hours:
                continue
            dur = _lookup_duree(durees, d)
            if dur:
                seen_hours.add(hour)
                matched.append(dur)
    if not matched and durees.get("default"):
        return f"environ {durees['default']} de route"
    if not matched:
        matched = [v for k, v in durees.items() if k != "default" and v]
    if not matched:
        return ""
    dur = _pick_representative_duration(matched)
    return f"environ {dur} de route"
