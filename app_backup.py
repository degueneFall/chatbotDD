# -*- coding: utf-8 -*-
"""
Dakar Dem Dikk Chatbot — Module principal (reconstruit d'après documentation).
Gère : recherche vectorielle, lignes/arrêts urbains, réseau interurbain,
       correction de fautes, routes /ask /health /cities /full_page.
Compilé en __pycache__/app_backup.cpython-314.pyc et chargé par app.py.
"""
import os
import re
import json
import time
import unicodedata
import urllib.parse

from flask import Flask, request, jsonify, g

# ── Données interurbain ───────────────────────────────────────────────────────
try:
    from interurbain_data import (
        INTERURBAIN_SECTIONS,
        get_section_by_ville,
        get_prix_for_ville,
        get_contact_for_ville,
    )
except ImportError:
    INTERURBAIN_SECTIONS = []
    def get_section_by_ville(v): return None
    def get_prix_for_ville(v): return None
    def get_contact_for_ville(v): return []

try:
    from interurbain_routes import (
        get_route_info,
        format_itinerary_prose,
        format_duration_prose,
    )
except ImportError:
    def get_route_info(v): return {}
    def format_itinerary_prose(itin, titre): return ""
    def format_duration_prose(durees, departs=None): return ""

# ── Données lignes urbaines ───────────────────────────────────────────────────
try:
    from lines_data import URBAN_LINES as _URBAN_LINES
except ImportError:
    _URBAN_LINES = []

# Bloc de contact (identique à app.py `_CONTACT_BLOCK`) — réponse quand rien n’est trouvé dans l’index
_CONTACT_NOT_FOUND_BLOCK = (
    "Je n'ai pas trouvé cette information.\n"
    "Vous pouvez contacter notre service client directement :\n"
    "– Téléphone : +221 33 824 10 10 / +221 33 865 15 55\n"
    "– Email : info@demdikk.sn / contact@demdikk.sn\n"
    "– Adresse : Km 4,5 Avenue Cheikh Anta Diop, dépôt Ouakam, Dakar\n"
    "– Horaires : Lundi – Vendredi, 08h – 17h\n"
    "– Site web : demdikk.sn"
)

# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder='static', static_url_path='/static')

try:
    from flask_cors import CORS
    CORS(app, resources={r"/*": {"origins": "*"}},
         allow_headers=["Content-Type", "Authorization"],
         methods=["GET", "POST", "OPTIONS"])
except ImportError:
    pass

# ── État global ───────────────────────────────────────────────────────────────
last_index_refresh: str = ""

_BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR  = os.path.join(_BASE_DIR, 'data')
_EMB_FILE  = os.path.join(_DATA_DIR, 'embeddings.npy')
_META_FILE = os.path.join(_DATA_DIR, 'metadata.json')

_model      = None
_embeddings = None
_metadata: list = []


# ── Index / modèle ────────────────────────────────────────────────────────────
def _load_index():
    global _embeddings, _metadata
    try:
        import numpy as np
        _metadata = json.load(open(_META_FILE, encoding='utf-8')) if os.path.exists(_META_FILE) else []
        if os.path.exists(_EMB_FILE) and _metadata:
            _embeddings = np.load(_EMB_FILE)
            return len(_metadata), True
        _embeddings = None
        return len(_metadata), False
    except Exception as exc:
        print(f"[index] {exc}")
        _embeddings = None; _metadata = []
        return 0, False


def _load_model():
    global _model
    if os.environ.get("SKIP_MODEL", "0") == "1" or _model is not None:
        return
    try:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
        print("[model] SentenceTransformer chargé : paraphrase-multilingual-MiniLM-L12-v2")
    except Exception as exc:
        print(f"[model] {exc}"); _model = None


def _reload_index_from_disk():
    return _load_index()


_load_index()
_load_model()


# ── Normalisation ─────────────────────────────────────────────────────────────
def _norm(s: str) -> str:
    s = s.lower().strip()
    s = unicodedata.normalize('NFD', s)
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return s


# ── Correction de fautes ──────────────────────────────────────────────────────
_TYPO_MAP = {
    "reservaton":  "réservation", "reservasion": "réservation",
    "reservez":    "réservation", "reserver":    "réservation",
    "srvice":      "service",     "sevice":      "service",
    "horaier":     "horaire",     "horairre":    "horaire",
    "ligme":       "ligne",       "linge":       "ligne",
    "arret":       "arrêt",       "arrets":      "arrêts",
    "tekdem":      "tek dem",     "tek-dem":     "tek dem",
    "abonement":   "abonnement",  "abonnemnt":   "abonnement",
    "guediawaye":  "guédiawaye",  "thiaroye":    "thiaroye",
    "pikine":      "pikine",      "rufisque":    "rufisque",
}

def normalize_query_typos(q: str) -> str:
    words = q.split()
    corrected = [_TYPO_MAP.get(w.lower(), w) for w in words]
    return ' '.join(corrected)


# ── Hors-sujet (sport, météo, etc.) — aligné sur app.py /ask ─────────────────
# Même logique que le wrapper : évite qu’un mot isolé (« barça ») soit traité comme un arrêt.
_VOWEL_IN_TOKEN_RE = re.compile(r"[aeiouyàâäéèêëïîôùûüÿœæ]")


def _token_is_consonant_gibberish(tok: str) -> bool:
    """Mot sans voyelle (ex. « cdfgfh ») : très improbable comme arrêt / question transport."""
    if len(tok) < 5 or not tok.isalpha():
        return False
    return _VOWEL_IN_TOKEN_RE.search(tok) is None


def _question_looks_gibberish_normed(qn: str) -> bool:
    toks = [t for t in (qn or "").split() if t]
    if not toks or len(toks) > 5:
        return False
    weird = sum(1 for t in toks if _token_is_consonant_gibberish(t))
    if len(toks) == 1:
        return weird == 1
    return weird >= max(1, (len(toks) + 1) // 2)


_OFF_TOPIC_TOKENS = frozenset({
    "meteo", "weather", "temperature", "pluie", "soleil",
    "politique", "president", "gouvernement", "election",
    "macky", "sall", "sonko", "wade",
    "football", "sport", "match",
    "can", "caf", "copa", "mondial", "champions", "ligue", "nba", "rugby", "tennis",
    "barca", "barcelona", "barcelone",
    "messi", "ronaldo", "psg", "om", "ol", "liverpool", "chelsea", "arsenal",
    "cinema", "film", "serie", "musique",
    "restaurant", "hotel", "tourisme",
    "sante", "medecin", "hopital", "pharmacie",
    "bitcoin", "crypto", "bourse", "finance",
    "recette", "cuisine",
})
_OFF_TOPIC_REPLY_TEXT = (
    "En tant qu'assistant de Dakar Dem Dikk, je suis là pour vous accompagner sur tout ce qui concerne nos services😊.\n"
    "Je ne suis malheureusement pas en mesure de répondre à cette question."
)
_TRANSPORT_CONTEXT_MARKERS = (
    "bus", "ligne", "lignes", "arret", "arrets", "transport", "voyage",
    "dem dikk", "demdikk", "ddd",
    "reservation", "billet", "ticket", "abonnement", "tek dem",
    "carte", "colis", "horaire", "horaires", "tarif", "prix", "contact", "agence",
    "interurbain", "touba", "thies", "thiès", "saint-louis", "fatick",
    "bagage", "bagages", "remboursement", "annulation", "report",
    "gare", "terminus", "destination", "navette", "aibd", "aeroport",
    "perturbation", "retard", "greve",
)


def _is_off_topic_question(question: str) -> bool:
    """Délègue à app.py si chargé (règles hors-sujet avec contexte transport)."""
    try:
        import sys as _sys
        _app_mod = _sys.modules.get("app")
        _fn = getattr(_app_mod, "_is_strict_off_topic", None) if _app_mod else None
        if _fn:
            return _fn(question)
    except Exception:
        pass
    qn = _norm(question)
    if not qn:
        return False
    if _is_smalltalk_question(question):
        return False
    if _question_looks_gibberish_normed(qn):
        return True
    if not (set(qn.split()) & _OFF_TOPIC_TOKENS):
        return False
    return not any(k in qn for k in _TRANSPORT_CONTEXT_MARKERS)


def _json_off_topic():
    return {
        "answer": _OFF_TOPIC_REPLY_TEXT,
        "summary": _OFF_TOPIC_REPLY_TEXT[:200],
        "bullets": [],
        "sources": [{"title": "Assistant Dakar Dem Dikk", "url": "https://demdikk.sn/", "score": 1.0}],
        "results": [],
        "query_type": "general",
        "needs_clarification": False,
        "has_structured_data": False,
        "is_city_query": False,
        "is_line_query": False,
        "show_more_info": False,
    }


# ── Catégorisation des lignes ─────────────────────────────────────────────────
def _categorize_lines(lines: list) -> dict:
    urbaines  = [l for l in lines if l.get("category") == "urbaine"]
    banlieue  = [l for l in lines if l.get("category") == "banlieue"]
    autres    = [l for l in lines if l.get("category") not in ("urbaine", "banlieue")]
    return {
        "urbaine":  urbaines,
        "banlieue": banlieue,
        "autres":   autres,
    }


# ── Recherche vectorielle ─────────────────────────────────────────────────────
def _vector_search(query: str, top_k: int = 5) -> list:
    if _model is None or _embeddings is None or not _metadata:
        return []
    try:
        import numpy as np
        q_vec  = _model.encode([query], normalize_embeddings=True)
        scores = (_embeddings @ q_vec.T).flatten()
        top_idx = scores.argsort()[::-1][:top_k]
        return [
            {
                "content": _metadata[i].get("text", ""),
                "title":   _metadata[i].get("title", ""),
                "url":     _metadata[i].get("url", ""),
                "score":   float(scores[i]),
            }
            for i in top_idx
        ]
    except Exception as exc:
        print(f"[vector_search] {exc}"); return []


# ── Formatage réponse ville ───────────────────────────────────────────────────
def _format_city_answer(section: dict, ville: str) -> str:
    """Legacy structuré (▸) — préférer _format_city_response_prose."""
    return _format_city_response_prose(section, ville, aspect="full")


_TRAVEL_INTENT_RE = re.compile(
    r"\b("
    r"je\s+veux\s+aller|j\s*aimerais\s+aller|je\s+souhaite\s+aller|"
    r"comment\s+aller|voyage\s+(?:pour|vers|a|à)|"
    r"partir\s+(?:pour|vers|a|à)|partez\s+(?:a|à|vers)|"
    r"se\s+rendre\s+(?:a|à)|destination\s+"
    r")\b",
    re.IGNORECASE,
)

_INTERURBAIN_RESERVATION = (
    "Pour réserver, rendez-vous en agence, dans nos gares routières ou via "
    "l'application mobile Dakar Dem Dikk ou appeler le +221 33 824 10 10."
)
_INTERURBAIN_RESERVATION_SHORT = (
    "Réservation en agence, en gare routière ou via l'appli Dakar Dem Dikk ou appeler le +221 33 824 10 10."
)
_SERVICE_CLIENT_TEL = "+221 33 824 10 10"


def _city_name_tokens(section: dict) -> set[str]:
    names: set[str] = set()
    for v in section.get("villes") or []:
        vn = _norm(v)
        names.add(vn)
        names.add(vn.replace("-", " "))
    tit = _norm(section.get("titre") or "")
    if tit:
        names.add(tit)
        names.add(tit.replace("-", " "))
    return names


def _is_city_only_query(qn: str, section: dict) -> bool:
    """Ex. « touba » seul — pas une intention de voyage détaillée."""
    city_names = _city_name_tokens(section)
    qnh = qn.replace("-", " ")
    tokens = [w for w in qnh.split() if w not in _ENRICH_STOPWORDS and len(w) >= 2]
    if not tokens:
        return False
    if all(t in city_names for t in tokens):
        return True
    return False


def _city_query_aspect(qn: str, question: str) -> str:
    """Aspect demandé — réponse minimale, sans surcharge."""
    if _TRAVEL_INTENT_RE.search(question or "") or re.search(
        r"\b(voyage|partir|partez|se\s+rendre)\b", qn, re.I
    ):
        return "full"
    if any(w in qn for w in ("reserver", "reservation", "reservez", "billet", "ticket")):
        return "reservation"
    if any(w in qn for w in ("horaire", "heures", "heure")):
        return "horaires"
    if any(w in qn for w in ("prix", "tarif", "cout", "combien", "fcfa", "cher", "coute")):
        return "prix"
    if any(w in qn for w in ("itineraire", "trajet", "route", "passage")):
        return "itineraire"
    if any(w in qn for w in ("arrivee", "contact", "telephone", "tel")):
        return "contact"
    return "clarify"


def _interurban_itinerary_from_site(city_title: str) -> str:
    """Itinéraire officiel depuis chatbot-2303 (ou section locale)."""
    info = get_route_info(city_title)
    if info.get("itineraire"):
        return info["itineraire"]
    try:
        import sys as _sys
        app_mod = _sys.modules.get("app")
        fetch = getattr(app_mod, "_fetch_page_text", None) if app_mod else None
        if not fetch:
            return ""
        text = fetch("https://demdikk.sn/reseau-interurbain/") or ""
        if not text:
            return ""
        ct = (city_title or "").upper().strip()
        idx = text.upper().find(ct)
        if idx < 0:
            return ""
        chunk = text[idx: idx + 2500]
        for line in chunk.splitlines():
            ln = line.strip()
            if re.match(r"^itin[eé]raire\s*:", ln, re.I):
                return re.sub(r"^itin[eé]raire\s*:\s*", "", ln, flags=re.I).strip()
            if re.match(r"^itin[eé]raire\b", ln, re.I) and "–" in ln:
                return re.sub(r"^itin[eé]raire\s*", "", ln, flags=re.I).strip()
    except Exception:
        pass
    return ""


def _city_route_meta(section: dict, ville: str) -> dict:
    """Fusionne itinéraire / durées : chatbot-2303 + section locale."""
    route = get_route_info(ville) or {}
    itin = (section.get("itineraire") or "").strip() or (route.get("itineraire") or "").strip()
    durees = dict(route.get("durees") or {})
    if not durees:
        _, durations = _split_horaires_raw(
            [str(h).strip() for h in (section.get("horaires") or []) if str(h).strip()]
        )
        durees = durations
    return {"itineraire": itin, "durees": durees}


def _horaires_detail_places(horaires: list[str]) -> bool:
    blob = " ".join(horaires).lower()
    return "depart" in blob or "départ" in blob or "arrivee" in blob or "arrivée" in blob


_RE_HORAIRE_TIME = re.compile(r"(\d{1,2})h(\d{2})?")


def _time_to_minutes(token: str) -> int | None:
    m = _RE_HORAIRE_TIME.search((token or "").strip())
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2) or "0")


def _format_trip_duration(minutes: int) -> str:
    if minutes <= 0:
        return ""
    hours, mins = divmod(minutes, 60)
    if mins == 0:
        return f"{hours} h" if hours != 1 else "1 h"
    if hours == 0:
        return f"{mins} min"
    return f"{hours} h {mins:02d}"


def _parse_arrival_duration_pairs(line: str) -> dict[str, str]:
    """Ex. « 11h00 max (bus 07h) » → {'07h00': '4 h'}."""
    out: dict[str, str] = {}
    for m in re.finditer(
        r"(\d{1,2}h\d{0,2})\s*max\s*\(\s*bus\s+(\d{1,2}h\d{0,2})\s*\)",
        line,
        re.I,
    ):
        arr_m = _time_to_minutes(m.group(1))
        dep_tok = m.group(2)
        dep_m = _time_to_minutes(dep_tok)
        if arr_m is None or dep_m is None or arr_m < dep_m:
            continue
        dep_norm = dep_tok if re.search(r"h\d{2}", dep_tok) else f"{dep_tok}00"
        if not re.search(r"h\d{2}", dep_norm):
            dep_norm = dep_norm.replace("h", "h") + "00"
        dur = _format_trip_duration(arr_m - dep_m)
        if dur:
            out[dep_norm] = dur
            # clé aussi sans minutes (07h ↔ 07h00)
            short = re.match(r"(\d{1,2}h)\d{0,2}", dep_norm)
            if short:
                out[short.group(1)] = dur
    return out


def _split_horaires_raw(horaires: list[str]) -> tuple[list[str], dict[str, str]]:
    """Sépare lignes de départ et durées calculables depuis « arrivée estimée »."""
    depart_lines: list[str] = []
    durations: dict[str, str] = {}
    for line in horaires:
        if re.search(r"arriv[eé]e\s+estim", line, re.I):
            durations.update(_parse_arrival_duration_pairs(line))
        else:
            depart_lines.append(line)
    return depart_lines, durations


def _normalize_time_token(token: str) -> str:
    t = (token or "").strip().lower()
    m = re.match(r"(\d{1,2})h(\d{2})?", t)
    if not m:
        return t
    return f"{int(m.group(1)):02d}h{m.group(2) or '00'}"


def _parse_depart_horaire_line(line: str) -> tuple[str, list[str]] | None:
    m = re.match(r"^[Dd][ée]part\s+(.+?)\s*:\s*(.+)$", (line or "").strip())
    if not m:
        return None
    place = m.group(1).strip()
    tail = m.group(2).strip()
    times = _extract_times_from_text(tail)
    # Legacy : « Départ Dakar : Dakar Terminus Liberté 5 à 7h »
    if re.fullmatch(r"Dakar", place, re.I) and re.search(r"dakar|terminus|libert", tail, re.I):
        place = _normalize_dakar_place(tail) or tail
    return place, times


def _ville_key_from_query(q_norm: str, section: dict) -> str:
    qnh = q_norm.replace("-", " ")
    for v in section.get("villes") or []:
        vn = _norm(v).replace("-", " ")
        if re.search(r"\b" + re.escape(vn) + r"\b", qnh):
            return v
    return section["villes"][0]


def _place_label_for_prose(place: str, titre_disp: str) -> str:
    p = place.strip()
    pm = re.match(r"^Dakar\s*\(([^)]+)\)$", p, re.I)
    if pm:
        return f"{pm.group(1).strip()} (Dakar)"
    pm = re.match(rf"^{re.escape(titre_disp)}\s*\(([^)]+)\)$", p, re.I)
    if pm:
        return f"{pm.group(1).strip()} ({titre_disp})"
    if re.search(r"\bdakar\b", p, re.I):
        return p
    return f"{p} ({titre_disp})"


def _format_duration_phrase(times: list[str], durations: dict[str, str]) -> str:
    if not times or not durations:
        return ""
    pairs: list[tuple[str, str]] = []
    for t in times:
        norm = _normalize_time_token(t)
        short = re.match(r"(\d{1,2}h)", norm)
        dur = durations.get(norm) or (short and durations.get(short.group(1)))
        if dur:
            pairs.append((norm, dur))
    if not pairs:
        return ""
    if len(pairs) == 1:
        return f", comptez environ {pairs[0][1]} de route"
    chunks = [f"{dur} pour le {tm}" for tm, dur in pairs]
    return ", comptez environ " + " et ".join(chunks)


def _format_times_list(times: list[str]) -> str:
    norms = [_normalize_time_token(t) for t in times]
    if len(norms) == 1:
        return norms[0]
    if len(norms) == 2:
        return f"{norms[0]} et {norms[1]}"
    return ", ".join(norms[:-1]) + f" et {norms[-1]}"


def _horaires_to_prose(
    horaires: list[str],
    jours: list[str],
    titre_disp: str,
    *,
    include_circulation: bool = True,
) -> list[str]:
    """Horaires interurbains en phrases, durée calculée si disponible."""
    depart_lines, durations = _split_horaires_raw(horaires)
    sentences: list[str] = []

    if include_circulation and jours:
        jtxt = jours[0] if len(jours) == 1 else ", ".join(jours)
        jl = jtxt[0].lower() + jtxt[1:] if len(jtxt) > 1 else jtxt.lower()
        if jl.startswith("tous"):
            sentences.append(f"Les bus circulent {jl}")
        else:
            sentences.append(f"Circulation : {jtxt}")

    dakar_parts: list[str] = []
    retour_parts: list[str] = []
    for line in depart_lines:
        parsed = _parse_depart_horaire_line(line)
        if not parsed:
            continue
        place, times = parsed
        if not times:
            continue
        label = _place_label_for_prose(place, titre_disp)
        times_txt = _format_times_list(times)
        is_dakar = bool(re.search(r"\bdakar\b", place, re.I)) and titre_disp.lower() not in place.lower()
        dur_hint = _format_duration_phrase(times, durations) if is_dakar else ""
        chunk = f"depuis {label} à {times_txt}{dur_hint}"
        if is_dakar:
            dakar_parts.append(chunk)
        else:
            retour_parts.append(chunk)

    if dakar_parts:
        sentences.append(
            "Départs " + dakar_parts[0]
            if len(dakar_parts) == 1
            else "Départs " + " ; ".join(dakar_parts)
        )
    if retour_parts:
        sentences.append(
            "Au retour, " + retour_parts[0]
            if len(retour_parts) == 1
            else "Au retour, " + " ; ".join(retour_parts)
        )

    return sentences


def _join_prose_sentences(parts: list[str]) -> str:
    out: list[str] = []
    for p in parts:
        s = (p or "").strip()
        if not s:
            continue
        if not s.endswith((".", "!", "?")):
            s += "."
        out.append(s)
    return " ".join(out)


def _legacy_horaires_from_section(section: dict) -> list[str]:
    """Reconstitue des lignes horaires propres quand seuls depart / lieux_contact existent."""
    out: list[str] = []
    depart = (section.get("depart") or "").strip()
    titre = (section.get("titre") or "").strip()
    if depart:
        m = re.search(
            r"^(?:dakar\s+)?(\d+h(?:\s*et\s*\d+h)?)\s+(.+)$", depart, re.I
        )
        if m:
            times = m.group(1).replace(" ", " ").replace("het", "h et ")
            place = m.group(2).strip()
            out.append(f"Départ Dakar ({place}) : {times}")
        else:
            out.append(f"Départ Dakar : {depart}")
    for c in section.get("lieux_contact") or []:
        lieu = (c.get("lieu") or "").strip()
        if not lieu:
            continue
        m = re.match(
            r"^[A-Za-zÀ-ÿ\s\-']+?\s+(\d+h(?:\s*et\s*\d+h)?)\s+(.+)$", lieu, re.I
        )
        if m:
            out.append(f"Départ {titre.title()} ({m.group(2).strip()}) : {m.group(1)}")
        elif not out:
            out.append(f"Point {titre.title()} : {lieu}")
    return out


def _city_display_name(ville: str) -> str:
    v = (ville or "").strip().lower()
    names = {
        "saint-louis": "Saint-Louis",
        "kebemer": "Kébémer",
        "kedougou": "Kédougou",
        "sedhiou": "Sédhiou",
        "ourossogui": "Ourossogui",
        "ziguinchor": "Ziguinchor",
        "velingara": "Vélingara",
        "tivaouane": "Tivaouane",
        "tambacounda": "Tambacounda",
        "ndioum": "Ndioum",
    }
    if v in names:
        return names[v]
    return (ville or "").replace("-", " ").strip().title()


def _format_prix_display(prix: str | None) -> str:
    if not prix:
        return ""
    m = re.match(r"(\d[\d\s]*)\s*(FCFA)?", str(prix).strip(), re.I)
    if not m:
        return str(prix).strip()
    num = re.sub(r"\s", "", m.group(1))
    try:
        return f"{int(num):,}".replace(",", " ") + " FCFA"
    except ValueError:
        return str(prix).strip()


def _extract_times_from_text(text: str) -> list[str]:
    if not text:
        return []
    seen: set[int] = set()
    out: list[str] = []
    for m in re.finditer(r"(\d{1,2})h(\d{2})?", text, re.I):
        h = int(m.group(1))
        if h in seen:
            continue
        seen.add(h)
        out.append(f"{h}h" if not m.group(2) else f"{h}h{m.group(2)}")
    return out


def _normalize_dakar_place(raw: str) -> str:
    p = re.sub(r"\d{1,2}h\d{0,2}", " ", raw or "", flags=re.I)
    p = re.sub(r"\bet\b", " ", p, flags=re.I)
    p = re.sub(r"\s+", " ", p).strip()
    p = re.sub(r"^Dakar\s+", "", p, flags=re.I)
    p = re.sub(r"\s*à\s+\d{1,2}h\s*$", "", p, flags=re.I).strip()
    p = re.sub(r",?\s*Dakar\s*$", "", p, flags=re.I).strip()
    p = re.sub(r"^Terminus\s+", "", p, flags=re.I).strip()
    p = re.sub(r"^Dakar\s+Terminus\s+", "", p, flags=re.I).strip()
    if re.search(r"libert", p, re.I):
        return "Liberté 5"
    if re.search(r"colobane", p, re.I):
        return "gare de Colobane"
    if re.search(r"grand yoff", p, re.I):
        return "HLM Grand Yoff"
    if re.search(r"hlm", p, re.I) and "yoff" in p.lower():
        return "HLM Grand Yoff"
    return p.strip()


def _parse_legacy_depart_field(depart: str) -> tuple[str, list[str]]:
    if not depart:
        return "", []
    times = _extract_times_from_text(depart)
    place = _normalize_dakar_place(depart)
    return place, times


def _shorten_city_place(place: str) -> str:
    p = (place or "").strip()
    if "," in p and re.search(r"quartier", p, re.I):
        p = p.split(",")[0].strip()
    for sep in (" route ", " Route ", " en face ", " derrière ", " près "):
        if sep.lower() in p.lower():
            idx = p.lower().find(sep.lower())
            p = p[:idx].strip()
            break
    m = re.search(r"(quartier\s+(?:\S+\s+){0,2}\S+)", p, re.I)
    if m:
        return m.group(1).strip()
    if len(p) <= 55:
        return p
    chunk = p.split(",")[0].strip()
    return chunk if len(chunk) <= 55 else chunk[:52].rsplit(" ", 1)[0] + "…"


def _parse_lieu_contact(lieu: str, ville: str, titre_disp: str) -> tuple[str, list[str]]:
    if not lieu:
        return "", []
    l = lieu.strip()
    ville_pat = re.escape(ville)
    titre_pat = re.escape(titre_disp)
    times = _extract_times_from_text(l)
    place = l
    for pat in (ville_pat, titre_pat, r"Thi[eè]s", r"Thies"):
        place = re.sub(rf"^{pat}\s+", "", place, flags=re.I)
    place = re.sub(r"^\d{1,2}h\d{0,2}\s+", "", place, flags=re.I)
    place = re.sub(r"^\d{1,2}\s*h\s+", "", place, flags=re.I)
    place = re.sub(r"\s+à\s+\d{1,2}h\d{0,2}\s*$", "", place, flags=re.I)
    return _shorten_city_place(place), times


def _format_departs_simple(times: list[str], horaires_blob: str = "") -> str:
    if horaires_blob:
        m = re.search(r"(\d{1,2})h\s*à\s*(\d{1,2})h", horaires_blob, re.I)
        if m:
            return f"{m.group(1)}h à {m.group(2)}h"
    hours: list[str] = []
    seen: set[int] = set()
    for t in times:
        m = re.match(r"(\d{1,2})h", (t or "").strip(), re.I)
        if not m:
            continue
        h = int(m.group(1))
        if h in seen:
            continue
        seen.add(h)
        hours.append(f"{h}h")
    hours.sort(key=lambda x: int(re.match(r"(\d+)", x).group(1)))
    if not hours:
        return ""
    if len(hours) == 1:
        return hours[0]
    if len(hours) == 2:
        return f"{hours[0]} et à {hours[1]}"
    return ", ".join(hours[:-1]) + f" et à {hours[-1]}"


def _format_jours_phrase(jours: list[str]) -> str:
    if not jours:
        return ""
    if len(jours) >= 2 and all(
        re.search(r"lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche", j, re.I)
        for j in jours
    ):
        a = re.sub(r"\s*,\s*", ", ", jours[0].strip())
        b = re.sub(r"\s*,\s*", ", ", jours[1].strip())
        jl = f"{a.lower()}, ou {b.lower()}"
        return jl if jl.startswith("le ") else jl

    text = " ".join(j.strip() for j in jours if j and j.strip())
    if re.search(r"\d+h", text, re.I) and re.search(r"tous les jours", text, re.I):
        m = re.search(r"(tous les jours(?:\s+sauf\s+[^,]+)?)", text, re.I)
        text = m.group(1).strip() if m else "tous les jours"
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",\s*,+", ",", text)
    if text.lower().startswith("tous"):
        text = text[0].lower() + text[1:]
    elif re.match(r"^(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)", text, re.I):
        text = "le " + text[0].lower() + text[1:]
    text = re.sub(
        r"\bsauf\s+([A-Za-zÀ-ÿ\-]+)",
        lambda m: f"sauf le {m.group(1).lower()}",
        text,
        flags=re.I,
    )
    return text.strip()


def _dakar_depart_label(place: str) -> str:
    p = re.sub(r"^terminus\s+", "", (place or "").strip(), flags=re.I)
    if re.search(r"libert", p, re.I):
        return f"du terminus {p} à Dakar"
    if re.search(r"gare de colobane", p, re.I):
        return "de la gare de Colobane à Dakar"
    if re.search(r"grand yoff|hlm", p, re.I):
        return "du HLM Grand Yoff à Dakar"
    if re.search(r"terminus", place or "", re.I):
        return f"du {place.strip()} à Dakar"
    return f"du {p} à Dakar" if p else "de Dakar"


def _city_depart_label(place: str, titre_disp: str) -> str:
    p = (place or "").strip()
    if not p:
        return f"de {titre_disp}"
    return f"du {p} à {titre_disp}"


def _extract_interurban_depart_info(
    section: dict,
    ville: str,
    horaires: list[str],
    depart: str,
    arrivee: str,
    contacts: list[dict],
) -> tuple[str, str, list[str]]:
    """Extrait lieux et horaires — formats structurés (Touba) et legacy (Kaolack…)."""
    titre_disp = _city_display_name(ville)
    dakar_place = ""
    city_place = ""
    times: list[str] = []
    horaires_blob = " ".join(horaires or [])

    # 1. Champ depart (source la plus fiable pour le terminus Dakar)
    if depart:
        dp, dt = _parse_legacy_depart_field(depart)
        if dp:
            dakar_place = dp
        if dt:
            times = dt

    # 2. Horaires structurés « Départ Ville (lieu) : … »
    depart_lines, _ = _split_horaires_raw(horaires)
    for line in depart_lines:
        parsed = _parse_depart_horaire_line(line)
        if not parsed:
            continue
        place, tms = parsed
        if re.search(r"\bdakar\b", place, re.I) and titre_disp.lower() not in place.lower():
            if not dakar_place or dakar_place.lower() == "dakar":
                raw = place.strip()
                pm = re.match(r"^Dakar\s*\(([^)]+)\)$", raw, re.I)
                dakar_place = _normalize_dakar_place(pm.group(1) if pm else raw) or raw
            if tms:
                times = tms
        else:
            pm = re.match(
                rf"^(?:{re.escape(titre_disp)}|{re.escape(ville)})\s*\(([^)]+)\)$",
                place.strip(),
                re.I,
            )
            if pm or not re.search(r"\bdakar\b", place, re.I):
                city_place = (
                    pm.group(1).strip()
                    if pm
                    else _shorten_city_place(_normalize_dakar_place(place) if place.lower() == "dakar" else place)
                )
            if tms and not times:
                times = tms

    # 3. Horaires legacy (7h et 15h, Dakar 8h…) — sans lignes « arrivée estimée »
    clean_blob = re.sub(r"arriv[eé]e\s+estim[^.]*", "", horaires_blob, flags=re.I)
    if not times:
        for line in horaires:
            if re.match(r"^[Dd][ée]part\s+Dakar\s*:", line):
                continue
            if re.search(r"arriv[eé]e\s+estim", line, re.I):
                continue
            if line.strip().lower() in {ville.lower(), titre_disp.lower()}:
                continue
            tms = _extract_times_from_text(line)
            if tms:
                times = tms
                break
        if not times:
            times = _extract_times_from_text(clean_blob)

    # 4. Contact destination (filtré par ville pour Podor/Ndioum, Louga/Kébémer…)
    ville_contacts = get_contact_for_ville(ville) or contacts
    if ville_contacts and not city_place:
        for c in ville_contacts:
            lieu = (c.get("lieu") or "").strip()
            if not lieu:
                continue
            cp, ct = _parse_lieu_contact(lieu, ville, titre_disp)
            if cp:
                city_place = cp
                if ct and not times:
                    times = ct
                break

    if arrivee and not city_place:
        city_place = _shorten_city_place(arrivee.split(",")[0])

    if not dakar_place or dakar_place.lower() == "dakar":
        dakar_place = "Liberté 5"

    # Fusionner heures de départ (sans reprendre les heures d'arrivée)
    all_times: list[str] = list(times)
    for src in (depart, clean_blob):
        for t in _extract_times_from_text(src or ""):
            if t not in all_times:
                all_times.append(t)
    times = all_times

    return dakar_place, city_place, times


def _format_city_bus_sentence(
    section: dict,
    ville: str,
    jours: list[str],
    horaires: list[str],
    depart: str,
    arrivee: str,
    contacts: list[dict],
    *,
    include_duration: bool = True,
) -> str:
    titre_disp = _city_display_name(ville)
    horaires_blob = " ".join(horaires or [])
    dakar_place, city_place, times = _extract_interurban_depart_info(
        section, ville, horaires, depart, arrivee, contacts
    )
    parts = [_dakar_depart_label(dakar_place)]
    if city_place:
        parts.append(_city_depart_label(city_place, titre_disp))
    sentence = "Les bus partent " + " et ".join(parts)
    jours_phrase = _format_jours_phrase(jours)
    if jours_phrase:
        sentence += f", {jours_phrase}"
    departs = _format_departs_simple(times, horaires_blob)
    if departs:
        sentence += f", avec des départs à {departs}"
    if include_duration:
        meta = _city_route_meta(section, ville)
        dur_txt = format_duration_prose(meta.get("durees") or {}, times)
        if dur_txt:
            sentence += f", {dur_txt.rstrip('.')}"
    return sentence + "."


def _format_city_full_prose(
    section: dict,
    ville: str,
    *,
    prix_str: str,
    jours: list[str],
    horaires: list[str],
    depart: str,
    arrivee: str,
    tels: list[str],
    contacts: list[dict],
) -> str:
    """Voyage complet — réponse courte type agent Dem Dikk."""
    titre_disp = _city_display_name(ville)
    prix_disp = _format_prix_display(get_prix_for_ville(ville) or prix_str)

    intro = f"Pour aller à {titre_disp}"
    if prix_disp:
        intro += f", le trajet coûte {prix_disp}"
    intro += "."

    bus_sentence = _format_city_bus_sentence(
        section, ville, jours, horaires, depart, arrivee, contacts
    )

    ville_contacts = get_contact_for_ville(ville) or contacts
    tels_local = [str(c.get("tel")).strip() for c in ville_contacts if c.get("tel")]

    footer = _INTERURBAIN_RESERVATION_SHORT
    if tels_local:
        footer += f" Contact sur place : {tels_local[0]}."

    chunks = [intro, bus_sentence, footer]
    return " ".join(p.strip() for p in chunks if p.strip())


def _format_city_clarify_prose(section: dict, ville: str) -> str:
    """Ville seule — ton agent humain."""
    titre_disp = _city_display_name(ville)
    prix_disp = _format_prix_display(get_prix_for_ville(ville) or section.get("prix"))
    intro = f"Oui, nos bus Dakar Dem Dikk vont bien à {titre_disp} sur le réseau interurbain."
    if prix_disp:
        detail = f" Depuis Dakar, le trajet est à {prix_disp}."
        follow = (
            " Dites-moi si vous cherchez les horaires, comment réserver "
            "ou une autre info."
        )
    else:
        detail = ""
        follow = (
            " Dites-moi si vous cherchez les horaires, le tarif, "
            "comment réserver ou autre chose."
        )
    return intro + detail + follow


def _section_contacts_for_ville(section: dict, ville: str) -> list[dict]:
    return get_contact_for_ville(ville) or section.get("lieux_contact") or []


def _format_city_response_prose(section: dict, ville: str, aspect: str = "full") -> str:
    """
    Réponse interurbaine concise : uniquement l'aspect demandé, sans redondance.
    Données interurbain_data / site — pas de reformulation LLM.
    """
    titre_disp = _city_display_name(ville)
    prix_disp = _format_prix_display(get_prix_for_ville(ville) or section.get("prix"))
    if isinstance(section.get("prix"), dict) and not get_prix_for_ville(ville):
        prix_disp = _format_prix_display(
            " / ".join(f"{k} : {v}" for k, v in section["prix"].items())
        )
    jours = [str(j).strip() for j in (section.get("jours") or []) if str(j).strip()]
    horaires_raw = [str(h).strip() for h in (section.get("horaires") or []) if str(h).strip()]
    horaires = horaires_raw if horaires_raw else []
    contacts = _section_contacts_for_ville(section, ville)
    itineraire = (section.get("itineraire") or "").strip()
    if not itineraire:
        itineraire = _city_route_meta(section, ville).get("itineraire") or ""
    depart = (section.get("depart") or "").strip()
    arrivee = (section.get("arrivee") or "").strip()
    tels = [str(c.get("tel")).strip() for c in contacts if c.get("tel")]
    places_in_horaires = _horaires_detail_places(horaires_raw)

    if aspect == "clarify":
        return _format_city_clarify_prose(section, ville)

    if aspect == "reservation":
        return _INTERURBAIN_RESERVATION

    if aspect == "prix":
        if prix_disp:
            return f"Le trajet vers {titre_disp} coûte {prix_disp}."
        return (
            "Tarif non disponible ici. Consultez demdikk.sn/reseau-interurbain/ "
            "ou le service client au +221 33 824 10 10."
        )

    if aspect == "itineraire":
        if itineraire:
            return f"Itinéraire Dakar–{titre_disp} : {itineraire}."
        return (
            f"Itinéraire non disponible ici. Consultez demdikk.sn/reseau-interurbain/ "
            f"ou appelez le +221 33 824 10 10."
        )

    if aspect == "contact":
        parts: list[str] = []
        if arrivee and not places_in_horaires:
            parts.append(f"Arrivée à {titre_disp} : {arrivee}.")
        elif contacts and not places_in_horaires:
            lieu = (contacts[0].get("lieu") or "").strip()
            if lieu:
                parts.append(f"Point {titre_disp} : {lieu}.")
        if tels:
            parts.append(f"Contact : {tels[0]}.")
        return " ".join(parts) if parts else _INTERURBAIN_RESERVATION

    if aspect == "horaires":
        return _format_city_bus_sentence(
            section, ville, jours, horaires_raw, depart, arrivee, contacts
        )

    # aspect == "full" — voyage complet, réponse courte
    return _format_city_full_prose(
        section,
        ville,
        prix_str=prix_disp,
        jours=jours,
        horaires=horaires_raw,
        depart=depart,
        arrivee=arrivee,
        tels=tels,
        contacts=contacts,
    )


def _resolve_city_aspect(qn: str, question: str, section: dict) -> str:
    if _is_city_only_query(qn, section):
        return "clarify"
    return _city_query_aspect(qn, question)


def _json_interurban_city(
    question: str, q_norm: str, city_hint: str = ""
) -> dict | None:
    """Payload JSON ville interurbaine — None si pas de ville ou requête ligne urbaine."""
    city_section = (get_section_by_ville(city_hint) if city_hint else None) or _detect_city(q_norm)
    if not city_section:
        return None
    qtype = detect_query_type(question)
    if qtype in ("all_lines_summary", "line_X"):
        return None
    ville_key = city_hint or _ville_key_from_query(q_norm, city_section)
    aspect = _resolve_city_aspect(q_norm, question, city_section)
    answer = _format_city_response_prose(city_section, ville_key, aspect=aspect)
    titre = _city_display_name(ville_key)
    return {
        "answer": answer,
        "summary": f"Réseau interurbain : {titre}",
        "sources": [
            {
                "title": "Réseau Interurbain DDD",
                "url": "https://demdikk.sn/reseau-interurbain/",
                "score": 1.0,
            }
        ],
        "results": [{"content": answer, "target_city": titre}],
        "query_type": "city_info",
        "has_structured_data": False,
        "is_city_query": False,
        "is_line_query": False,
        "needs_clarification": False,
        "show_more_info": aspect != "clarify",
    }


# ── Détection ville ───────────────────────────────────────────────────────────
def _detect_city(q_norm: str) -> dict | None:
    """Détecte une ville interurbaine dans la requête normalisée (mot entier uniquement)."""
    # Variante sans tiret pour matcher "saint louis" == "saint-louis"
    q_no_hyphen = q_norm.replace("-", " ")
    for section in INTERURBAIN_SECTIONS:
        for ville in section.get("villes", []):
            ville_n = _norm(ville)
            ville_no_hyphen = ville_n.replace("-", " ")
            # Tester les deux variantes (avec et sans tiret)
            for (pattern, text) in [
                (re.escape(ville_n), q_norm),
                (re.escape(ville_no_hyphen), q_no_hyphen),
            ]:
                if re.search(r'\b' + pattern + r'\b', text):
                    return section
    return None


# ── Détection lignes ──────────────────────────────────────────────────────────
_RE_LINE_NUM = re.compile(
    r'\bligne\s+([0-9]+[a-zA-Z]?|taf\s*taf|to1|rufisque.yenne)\b'
    r'|^([0-9]+[a-zA-Z]?)$'
    r'|\b(taf\s*taf|taf-taf)\b'
    r'|\b(to1)\b'
    r'|\b(terminus\s+rufisque)\b',
    re.IGNORECASE,
)

def _detect_line_number(question: str) -> str | None:
    q = question.strip()
    # TAF TAF
    if re.search(r'\btaf\s*taf\b', q, re.IGNORECASE):
        return "TAF TAF"
    # TO1
    if re.search(r'\bto\s*1\b', q, re.IGNORECASE):
        return "TO1"
    # RUFISQUE–YENNE
    if re.search(r'\brufisque.yenne\b', q, re.IGNORECASE):
        return "RUFISQUE–YENNE"
    # numéro standard
    m = re.search(r'\bligne\s+([0-9]+[a-zA-Z]?)\b', q, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def _get_line_by_number(num: str) -> dict | None:
    num_c = num.strip().upper().replace(' ', '')
    for line in _URBAN_LINES:
        if line["number"].upper().replace(' ', '') == num_c:
            return line
    return None


# ── Lignes desservant un arrêt ────────────────────────────────────────────────
_LINES_TO_STOP_KW = re.compile(
    r'\b(quelle\s+ligne|quelles\s+lignes|aller\s+[aà]|comment\s+aller|'
    r'bus\s+pour|prendre\s+pour|pour\s+aller|je\s+veux\s+aller|'
    r'passer\s+par|dessert|desservent|desserte|'
    r'passe(?:nt)?\s+par|ligne[s]?\s+(?:pour|qui\s+pass|vers)|'
    r'quel\s+bus|quelles?\s+bus|recherche\s+d[\u0027]?\s*arrets?|cherche\s+d[\u0027]?\s*arrets?|'
    r'trouver\s+(?:une\s+)?ligne)\b',
    re.IGNORECASE,
)
# Contexte « arrêt / station » → recherche de lignes
_STOP_PLACE_KW = re.compile(
    r'\b(?:arret|arrêt|arrêts?|arrets?|station|terminus|'
    r'point\s+d[\u0027]?\s*arrets?)\b',
    re.IGNORECASE,
)

def _stop_name_matches_query(sn: str, stop_n: str) -> bool:
    """
    True si la requête correspond à un arrêt sans faux positifs du type
    « barca » ⊂ « embarcadere » (mot entier sur chaîne normalisée).
    """
    if not sn or not stop_n:
        return False
    if stop_n == sn:
        return True
    parts = [p for p in stop_n.split() if len(p) >= 2]
    if not parts:
        return False

    def _whole_word(needle: str, hay: str) -> bool:
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", hay))

    if len(parts) == 1:
        return _whole_word(parts[0], sn)
    return all(_whole_word(p, sn) for p in parts)


def find_lines_for_stop(stop_name: str) -> list:
    """Retourne les lignes dont un arrêt correspond à stop_name (mots entiers)."""
    if not _stop_candidate_is_plausible(stop_name):
        return []
    stop_n = _norm(stop_name)
    if len(stop_n) < 3:
        return []
    results = []
    for line in _URBAN_LINES:
        for s in line.get("stops", []):
            sn = _norm(s)
            if _stop_name_matches_query(sn, stop_n):
                results.append({
                    "number":   line["number"],
                    "start":    line["start"],
                    "end":      line["end"],
                    "category": line["category"],
                    "matched_stop": s,
                })
                break
    return results


def _implicit_stop_card_ok(stop_for_lines: str, matching_lines: list) -> bool:
    """
    Pour une question sans mot-clé « arrêt / quelle ligne » (inférence seule),
    n'affiche la carte « lignes à l'arrêt » que si le libellé d'arrêt correspond
    clairement au texte saisi (évite un prénom type « assane » matché au milieu
    d'un nom d'arrêt très long).
    """
    qn = _norm((stop_for_lines or "").strip())
    if not qn or not matching_lines:
        return False
    q_words = [w for w in qn.split() if w]
    for row in matching_lines:
        ms = _norm((row.get("matched_stop") or "").strip())
        if not ms:
            return False
        toks = [t for t in ms.split() if t]
        if len(q_words) == 1:
            qw = q_words[0]
            if ms == qn:
                continue
            if len(toks) == 1 and toks[0] == qw:
                continue
            # Dernier segment du libellé (ex. « … (Sandaga) » : token « (sandaga) » après normalisation)
            last_raw = toks[-1] if toks else ""
            last_core = re.sub(r"^\W+|\W+$", "", last_raw)
            if 2 <= len(toks) <= 8 and (last_raw == qw or last_core == qw):
                continue
            return False
        if not _stop_name_matches_query(ms, qn):
            return False
    return True


# Extraits d'un seul mot trop génériques (ex. « point d'arrêt » + regex « arrêt … » → « service »).
_BAD_STOP_EXTRACT = frozenset(
    _norm(x) for x in (
        "service", "client", "commercial", "facturation",
        "information", "informations", "accueil", "assistance",
        "aide", "contact", "réclamation", "reclamation", "urgence",
        "de", "la", "le", "les", "du", "des", "au", "aux", "un", "une",
    )
)

_FRENCH_FUNCTION_WORDS = frozenset(
    _norm(w) for w in (
        "de", "la", "le", "les", "du", "des", "au", "aux", "un", "une", "en",
        "a", "je", "tu", "veux", "faire", "fait", "pour", "par", "sur", "dans",
        "et", "ou", "si", "que", "qui", "quoi", "pas", "ne", "ce", "se",
    )
)

_BAD_STOP_PHRASES = frozenset(
    _norm(p) for p in (
        "de la", "de le", "de les", "du", "des", "au", "aux", "a la", "a le",
        "en la", "en le", "pour la", "pour le", "faire de", "veux faire", "je veux",
        "de la pub", "de la publicite", "la", "le", "les", "un", "une",
    )
)

_SKIP_IMPLICIT_STOP_KEYWORDS = (
    "publicite", "partenariat", "annonce", "publicitaire", "regie publicitaire",
    "pub", "emploi", "recrutement", "candidature", "location", "reservation",
    "abonnement", "messagerie", "colis",
)


def _stop_candidate_is_plausible(chunk: str) -> bool:
    """Rejette « de la », mots-outils seuls, etc. — pas un nom d'arrêt."""
    t = (chunk or "").strip()
    if len(t) < 2:
        return False
    nw = _norm(t)
    if len(nw) < 4 or nw in _BAD_STOP_PHRASES:
        return False
    toks = [x for x in nw.split() if x]
    if not toks or all(x in _FRENCH_FUNCTION_WORDS for x in toks):
        return False
    return any(len(x) >= 4 and x not in _FRENCH_FUNCTION_WORDS for x in toks)


def _should_skip_implicit_stop_inference(q_norm: str) -> bool:
    """Pas d'inférence arrêt pour sujets service / commercial / RH."""
    return any(k in q_norm for k in _SKIP_IMPLICIT_STOP_KEYWORDS)


def _san_stop_tail(t: str) -> str | None:
    """None si l'extrait ne peut raisonnablement pas être un nom d'arrêt seul."""
    t = (t or "").strip()
    if len(t) < 2:
        return None
    parts = t.split()
    if len(parts) == 1 and _norm(parts[0]) in _BAD_STOP_EXTRACT:
        return None
    if not _stop_candidate_is_plausible(t):
        return None
    return t[:200]


def _extract_stop_from_query(question: str) -> str:
    """Extrait le nom d'arrêt (quelle ligne pour X, arrêt X, passe par X, etc.)."""
    q = question.strip()
    # « point d'arrêt X » — avant « arrêt … » : sinon « arrêt » matche dans « d'arrêt »
    # et ne laisse que le mot suivant (ex. « service »).
    m_pt = re.search(
        r'\bpoint\s+d[\u0027\u2019]?\s*(?:arrêt|arret|arrêts?|arrets?)\s+(.+?)(?:\?|$)',
        q, re.IGNORECASE,
    )
    if m_pt:
        got = _san_stop_tail(m_pt.group(1))
        if got:
            return got
    # « l'arrêt X »
    m_la = re.search(
        r"\bl[\u0027\u2019]arrêt\s+(.+?)(?:\?|$)", q, re.IGNORECASE,
    )
    if m_la:
        got = _san_stop_tail(m_la.group(1))
        if got:
            return got
    # « arrêt Sandaga », « station Leclerc », « terminus Keur Massar »
    # Pas après une apostrophe (évite « d'arrêt » / « l'arrêt » mal découpés).
    m = re.search(
        r'(?<![\u0027\u2019])\b(?:arret|arrêt|arrêts?|arrets?|station|terminus)\s+(.+?)(?:\?|$)',
        q, re.IGNORECASE,
    )
    if m:
        got = _san_stop_tail(m.group(1))
        if got:
            return got
    # « passe par Sandaga », « passent par le Plateau »
    m2 = re.search(r'\bpasse(?:nt)?\s+par\s+(.+?)(?:\?|$)', q, re.IGNORECASE)
    if m2:
        got = _san_stop_tail(m2.group(1))
        if got:
            return got
    # Nettoyage générique
    for pat in [
        r'\b(quelle\s+ligne|quelles\s+lignes|comment\s+aller|aller\s+[aà]|'
        r'bus\s+pour|prendre\s+pour|pour\s+aller|je\s+veux\s+aller|passer\s+par|'
        r'dessert|desservent|desserte|quelle\s+est\s+la\s+ligne|quel\s+bus|'
        r'ligne[s]?\s+(?:pour|vers|qui)|recherche|cherche|trouver)\b',
        r'\b(dakar\s+dem\s+dikk|ddd|dem\s+dikk)\b',
        r'\b(s\'?il\s+vous\s+pla[iî]t|svp|merci)\b',
        r'[?!.,;]',
    ]:
        q = re.sub(pat, ' ', q, flags=re.IGNORECASE)
    q = re.sub(r'^\s*(pour|vers|[aà]|au|aux|de|du|en)\s+', '', q, flags=re.IGNORECASE)
    return _san_stop_tail(q.strip()[:200]) or ""


# Mots à ignorer pour l'inférence « nom d'arrêt seul »
_STOP_QUERY_STOPWORDS = frozenset(
    _norm(w) for w in (
        "qui", "que", "quoi", "comment", "pourquoi", "combien", "quand", "ou",
        "dans", "avec", "sans", "sur", "chez", "plus", "moins", "tres", "très",
        "ligne", "lignes", "bus", "ddd", "dakar", "dem", "dikk", "transport",
        "reseau", "réseau", "urbain", "urbaine", "les", "des", "une", "pour",
        "vers", "depuis", "jusqua", "jusqu", "donne", "dis", "moi", "svp",
        "service", "client", "application", "appli",
        "assane", "thierno", "directeur", "directeurs",
    )
)


def _infer_stop_name_implicit(question: str) -> str | None:
    """
    Si la question est courte et ressemble à un nom d'arrêt (sans mot-clé explicite),
    retourne le meilleur candidat qui matche au moins une ligne.
    """
    if not _URBAN_LINES or len(question.split()) > 8:
        return None
    qn = _norm(question)
    if len(qn) < 4:
        return None
    words = [w for w in re.split(r'\s+', question.strip()) if len(w) >= 2]
    if len(words) == 1 and _norm(words[0]) in _STOP_QUERY_STOPWORDS:
        return None
    candidates = []
    # Phrase entière : seulement si ce n'est pas un mot-ban (sinon « service » seul repassait ici).
    full_q = question.strip()
    if _norm(full_q) not in _STOP_QUERY_STOPWORDS and _stop_candidate_is_plausible(full_q):
        candidates.append(full_q)
    # Phrases de 1 à 4 mots consécutifs
    for n in range(1, min(5, len(words) + 1)):
        for i in range(0, len(words) - n + 1):
            chunk = ' '.join(words[i : i + n])
            nw = _norm(chunk)
            if len(nw) < 4 or nw in _STOP_QUERY_STOPWORDS:
                continue
            if not _stop_candidate_is_plausible(chunk):
                continue
            candidates.append(chunk)

    best = None
    best_n = 0
    seen = set()
    for cand in candidates:
        key = _norm(cand)
        if key in seen:
            continue
        if key in _STOP_QUERY_STOPWORDS:
            continue
        seen.add(key)
        n = len(find_lines_for_stop(cand))
        if n > best_n:
            best_n, best = n, cand
    return best if best_n > 0 else None


# ── Détection du type de requête ──────────────────────────────────────────────
def detect_query_type(question: str) -> str:
    q = _norm(question)
    # Toutes les lignes
    if q in ("ligne", "lignes") or re.search(
        r'\b(toutes\s+les\s+lignes|liste\s+des\s+lignes|reseau\s+urbain|'
        r'lignes\s+ddd|lignes\s+dem\s+dikk|voir\s+les\s+lignes|'
        r'combien\s+de\s+lignes?|nombre\s+de\s+lignes?|'
        r'il\s+y\s+a\s+combien\s+de\s+lignes?|ya\s+combien\s+de\s+lignes?)\b', q
    ):
        return "all_lines_summary"

    # Lignes passant par un arrêt (formulations explicites)
    if _LINES_TO_STOP_KW.search(question) and not re.search(r'\bville\b|\bvilles\b', q):
        return "lines_to_stop"
    if _STOP_PLACE_KW.search(question) and not re.search(r'\bville\b|\bvilles\b', q):
        return "lines_to_stop"

    # Ligne spécifique
    if _detect_line_number(question):
        return "line_X"

    return "other"


# ── Recherche vectorielle TF-IDF de secours ───────────────────────────────────
def _keyword_search(query: str, top_k: int = 5) -> list:
    """Recherche par mots-clés quand le modèle n'est pas disponible."""
    q_words = set(_norm(query).split())
    scored = []
    for meta in _metadata:
        text_n = _norm(meta.get("text", ""))
        score  = sum(1 for w in q_words if w in text_n and len(w) > 2)
        if score > 0:
            scored.append((score, meta))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {"content": m.get("text", ""), "title": m.get("title", ""),
         "url": m.get("url", ""), "score": s / max(len(q_words), 1)}
        for s, m in scored[:top_k]
    ]


def _search(query: str, top_k: int = 5) -> list:
    results = _vector_search(query, top_k)
    if not results:
        results = _keyword_search(query, top_k)
    return results


def _deepseek_is_followup(context_summary: str, new_question: str) -> bool | None:
    """
    Demande à DeepSeek si `new_question` est une suite de `context_summary`.
    Retourne True/False, ou None si DeepSeek est indisponible (fallback keywords).
    Timeout court (4 s) pour ne pas ralentir le chatbot.
    """
    try:
        import urllib.request as _ur, json as _json
        api_key  = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
        base_url = (os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
        model    = (os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat").strip()
        if not api_key:
            return None

        prompt = (
            f"Contexte de la conversation précédente : « {context_summary} »\n"
            f"Nouvelle question de l'utilisateur : « {new_question} »\n\n"
            "Est-ce que la nouvelle question est une suite ou une clarification "
            "directe du contexte précédent (même sujet, même destination, même ligne) ?\n"
            "Réponds UNIQUEMENT par OUI ou NON, sans aucune explication."
        )
        payload = _json.dumps({
            "model": model,
            "max_tokens": 5,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = _ur.Request(
            f"{base_url}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with _ur.urlopen(req, timeout=4) as resp:
            body = _json.loads(resp.read())
        answer = (body["choices"][0]["message"]["content"] or "").strip().upper()
        return answer.startswith("OUI")
    except Exception:
        return None  # fallback sur les mots-clés


def _question_significant_word_count(question: str) -> int:
    """Nombre de tokens « utiles » (évite que « ? » compte comme un 4ᵉ mot)."""
    parts = (question or "").strip().split()
    n = 0
    for w in parts:
        w2 = re.sub(
            r"^[^\wàâäéèêëïîôùûüÿœæ0-9'-]+|[^\wàâäéèêëïîôùûüÿœæ0-9'-]+$",
            "",
            w,
            flags=re.IGNORECASE,
        )
        if len(w2) >= 2:
            n += 1
    return n


def _is_smalltalk_question(question: str) -> bool:
    """Politesse / banalités — ne pas les coller au contexte « ligne » de l'historique."""
    qn = _norm((question or "").strip())
    if not qn:
        return False
    if re.search(
        r"^("
        # Formules de remerciement et réponses positives
        r"(ok\s+)?merci(\s+(beaucoup|bien|infiniment|mille\s+fois))?|"
        r"(merci\s+)?super(\s+merci)?|(merci\s+)?parfait(\s+merci)?|"
        r"(ok\s+)?(c[\u2019']est\s+bon|cest\s+bon|ca\s+va|c\s+est\s+bon)(\s+merci)?|"
        r"ok(\s+merci)?(\s+bien)?|d[\u2019']accord(\s+merci)?|genial|"
        # Salutations
        r"tu\s+vas(\s+bien)?|comment\s+(tu\s+)?vas|tout\s+va(\s+bien)?|"
        r"comment\s+allez[-\s]?vous|vous\s+allez\s+bien|"
        r"salut|bonjour|bonsoir|coucou|hello|hi|bye|au\s+revoir|a\s+bientot"
        r")[\s?!.,;:]*$",
        qn,
    ):
        return True
    # Présentations personnelles et questions hors-sujet DDD
    if re.search(
        r"(je\s+m[\u2019']appelle|mon\s+nom\s+est|je\s+suis\s+\w+|"
        r"comment\s+tu\s+t[\u2019']appelles?|quel\s+(est\s+)?ton\s+nom|"
        r"tu\s+es\s+qui|c[\u2019']est\s+quoi\s+ton\s+nom|"
        r"quel\s+age|j[\u2019']ai\s+\d+\s+ans|j[\u2019']habite|"
        r"je\s+viens\s+de|d[\u2019']ou\s+viens[-\s]tu)",
        qn,
    ):
        return True
    return False


def _parse_history_entries(history_raw) -> list:
    """Historique client [{role, content}, …] ; liste vide si absent ou invalide."""
    if not history_raw or not isinstance(history_raw, list):
        return []
    out = []
    for item in history_raw[-40:]:
        if not isinstance(item, dict):
            continue
        role = (item.get("role") or "").strip().lower()
        if role not in ("user", "assistant"):
            continue
        c = (item.get("content") or "").strip()
        if c:
            out.append({"role": role, "content": c})
    return out


def _history_last_city_section(entries: list) -> dict | None:
    """Dernière section interurbaine mentionnée dans l'historique (du plus récent au plus ancien)."""
    for item in reversed(entries):
        sec = _detect_city(_norm(item.get("content") or ""))
        if sec:
            return sec
    return None


def _history_last_line_number(entries: list) -> str | None:
    """Dernier numéro / libellé de ligne urbain détecté dans l'historique."""
    for item in reversed(entries):
        ln = _detect_line_number(item.get("content") or "")
        if ln:
            return ln
    return None


def _city_token_for_enrichment(section: dict) -> str:
    """Mot-clé ville pour préfixer la question (aligné sur les entrées interurbaines)."""
    villes = section.get("villes") or []
    if villes:
        return str(villes[0]).strip()
    tit = (section.get("titre") or "").strip()
    return tit.split()[0] if tit else "ville"


_ENRICH_STOPWORDS = frozenset({
    "et","pour","le","la","les","de","du","des","un","une","au","aux",
    "en","sur","par","ce","qui","que","quoi","quel","quelle","quels",
    "quelles","il","elle","ils","elles","je","tu","nous","vous","on",
    "ne","pas","plus","est","sont","a","y","en","or","ni","mais","donc",
    "comment","combien","quand","pourquoi","ou",
    # Verbes auxiliaires/courants sans sens porteur dans le contexte de suivi
    "se","me","te","va","vont","fait","font","faire","aller","avoir","etre",
    "part","partent","arrive","arrivent","prend","prennent","met","mettent",
})

# Noms de la société → jamais enrichis comme suite de conversation
_COMPANY_NAME_TOKENS = frozenset({"dem dikk", "demdikk", "ddd", "dakar dem dikk"})


def _enrich_short_question_from_history(question: str, history_raw) -> str:
    """
    Résolution de contexte : question courte (mots significatifs < 3) +
    historique avec ville ou ligne récente → enrichir avant city_info / line_X / RAG.
    """
    q = (question or "").strip()
    if not q:
        return q
    # Nom de la société seul → jamais enrichi comme suite de conversation
    qn_check = _norm(q)
    if qn_check in _COMPANY_NAME_TOKENS or all(
        t in _COMPANY_NAME_TOKENS or t in _ENRICH_STOPWORDS
        for t in qn_check.split()
    ):
        return q
    # Compter uniquement les mots porteurs de sens (hors stopwords)
    qn_words = qn_check.split()
    meaningful = [w for w in qn_words if len(w) >= 2 and w not in _ENRICH_STOPWORDS]
    if len(meaningful) >= 4:
        return q
    if _is_smalltalk_question(q):
        return q
    entries = _parse_history_entries(history_raw)
    if not entries:
        return q
    # Déjà explicite : ne pas dupliquer
    if _detect_city(_norm(q)) or _detect_line_number(q):
        return q

    city_sec = _history_last_city_section(entries)
    line_num = _history_last_line_number(entries)
    if not city_sec and not line_num:
        # Fallback : si question très courte, enrichir avec la dernière question utilisateur
        if len(meaningful) <= 3:
            _Q_STARTERS = re.compile(
                r"^(existe[- ]t[- ]il|est[- ]ce\s+qu[e']|y\s+a[- ]t[- ]il|"
                r"qu[e']?est[- ]ce\s+qu[e']|pouvez[- ]vous|puis[- ]je|"
                r"comment|quand|pourquoi|combien|quel(?:le)?s?\s+sont|"
                r"que[l]?s?\s+sont|qu[e']|a[- ]t[- ]on)\s*",
                re.IGNORECASE,
            )
            last_user_q = next(
                (e.get("content", "") for e in reversed(entries)
                 if (e.get("role") == "user" and _norm(e.get("content", "")) != _norm(q))),
                None,
            )
            if last_user_q:
                # Nettoyer la dernière question (enlever les formules d'introduction)
                clean_ctx = _Q_STARTERS.sub("", last_user_q).strip(" ?!.,;:")
                if clean_ctx and _norm(clean_ctx) != _norm(q):
                    return f"{q} {clean_ctx}".strip()
        return q

    qn = _norm(q)

    # ── Suivi pronominal direct : « elle part d'où » → rattacher immédiatement
    if line_num and re.match(r"^\s*(elle|il|celle|celui|ça|ce)\b", q, re.IGNORECASE):
        return f"ligne {line_num} {q}".strip()

    # ── Détection rapide de mots de référence contextuelle ───────────────────
    # Ces formulations indiquent clairement une question de suivi sur le même sujet
    # sans ambiguïté : on enrichit sans appeler DeepSeek.
    _FAST_REF = re.compile(
        r"d[\u2019\u0027\u02bc]o[u\u00f9]"           # d'où / d'ou
        r"|depuis\s+o[u\u00f9]"                        # depuis où
        r"|\ble\s+prix\b|\ble\s+tarif\b"               # le prix / le tarif
        r"|\bl[ae]?\s+dur[e\u00e9]e\b"                 # la durée
        r"|\bcombi[e\u00e8]n\s+de\s+temps\b"           # combien de temps
        r"|\bcombi[e\u00e8]n\s+(?:ca|\u00e7a)\s+co[u\u00fb]te?\b"  # combien ça coûte
        r"|\b[a\u00e0]\s+quelle\s+heure\b"             # à quelle heure
        r"|\bheures?\b"                                 # heure / heures (suivi contexte ville)
        r"|\bcomment\s+(?:r[e\u00e9]server|partir|r[e\u00e9]server)\b",  # comment réserver
        re.IGNORECASE,
    )
    if re.search(_FAST_REF, q):
        # Référence contextuelle évidente → enrichir immédiatement
        # Exception : questions de prix/tarif → ne jamais enrichir avec un numéro de ligne
        # (sinon "c'est combien le ticket" → "ligne 1 c'est combien le ticket" → fiche ligne)
        _PRICE_QN_RE = re.compile(
            r'\b(combien|tarif|prix|ticket|billet|co[uû]te?|fcfa|payer?)\b',
            re.IGNORECASE,
        )
        is_price_question = bool(re.search(_PRICE_QN_RE, q))
        if city_sec and line_num:
            if is_price_question:
                return f"{_city_token_for_enrichment(city_sec)} {q}".strip()
            return f"{_city_token_for_enrichment(city_sec)} ligne {line_num} {q}".strip()
        if city_sec:
            return f"{_city_token_for_enrichment(city_sec)} {q}".strip()
        if line_num:
            if is_price_question:
                return q  # pas d'enrichissement ligne pour une question prix
            return f"ligne {line_num} {q}".strip()

    # ── Résolution intelligente via DeepSeek ─────────────────────────────────
    # On construit un résumé du contexte pour DeepSeek
    ctx_parts = []
    if city_sec:
        ctx_parts.append(f"destination {_city_token_for_enrichment(city_sec)}")
    if line_num:
        ctx_parts.append(f"ligne {line_num}")
    context_summary = ", ".join(ctx_parts)

    is_followup = _deepseek_is_followup(context_summary, q)

    if is_followup is True:
        # DeepSeek confirme que c'est une suite → enrichir
        if city_sec and line_num:
            return f"{_city_token_for_enrichment(city_sec)} ligne {line_num} {q}".strip()
        if city_sec:
            return f"{_city_token_for_enrichment(city_sec)} {q}".strip()
        if line_num:
            return f"ligne {line_num} {q}".strip()

    elif is_followup is False:
        # DeepSeek confirme que c'est une question indépendante → pas d'enrichissement
        return q

    else:
        # DeepSeek indisponible → fallback sur les mots-clés
        priceish = any(w in qn for w in (
            "prix", "tarif", "cout", "fcfa", "coute", "combien", "cher", "paye",
            "horaire", "heure", "heures", "depart", "billet", "ticket", "reservation",
        ))
        lineish = any(w in qn for w in (
            "ligne", "arret", "station", "terminus", "bus", "dessert", "desservent",
        ))
        if city_sec and line_num:
            if priceish and not lineish:
                # Question de prix → enrichir avec la ville seulement, pas la ligne
                return f"{_city_token_for_enrichment(city_sec)} {q}".strip()
            if lineish and not priceish:
                return f"ligne {line_num} {q}".strip()
            return f"{_city_token_for_enrichment(city_sec)} ligne {line_num} {q}".strip()
        if city_sec:
            return f"{_city_token_for_enrichment(city_sec)} {q}".strip()
        if line_num and (priceish or lineish):
            return f"ligne {line_num} {q}".strip()

    return q


# ── Route /ask ────────────────────────────────────────────────────────────────
@app.route('/ask', methods=['POST'])
def ask():
    try:
        body = request.get_json(silent=True) or {}
    except Exception:
        body = {}

    if 'history' in body:
        history_raw = body.get('history')
    else:
        history_raw = body.get('conversationHistory')

    question_raw = (
        getattr(g, "resolved_question", None)
        or body.get("question")
        or body.get("q")
        or ""
    ).strip()
    question_resolved = _enrich_short_question_from_history(question_raw, history_raw)
    question  = normalize_query_typos(question_resolved.strip())
    city_hint = (body.get('city') or '').strip()

    if not question:
        return jsonify({
            "answer": "Veuillez poser une question.",
            "summary": "", "sources": [], "results": [],
            "query_type": "general", "has_structured_data": False,
            "is_city_query": False, "is_line_query": False,
            "needs_clarification": True,
        })

    q_norm = _norm(question)

    # Présentation de la société (ex. « Dakar dem dikk » seul)
    _company_only = q_norm in _COMPANY_NAME_TOKENS or (
        q_norm.replace(" ", "") in {"demdikk", "ddd"}
        and all(t in {"dakar", "dem", "dikk", "demdikk", "ddd"} for t in q_norm.split())
    )
    if _company_only or any(k in q_norm for k in (
        "presentation", "présentation", "c est quoi ddd", "qu est ce que ddd",
        "histoire de dem dikk", "parle moi de dem dikk",
    )):
        try:
            import sys as _sys
            _app_mod = _sys.modules.get("app")
            _fb = getattr(_app_mod, "_fallback_presentation_page", None) if _app_mod else None
            if _fb:
                pres = _fb(question)
                if pres and pres.get("answer"):
                    return jsonify({
                        "answer": pres["answer"],
                        "summary": pres.get("summary", "Présentation de Dakar Dem Dikk"),
                        "sources": pres.get("sources", []),
                        "results": pres.get("results", []),
                        "query_type": "general",
                        "has_structured_data": False,
                        "is_city_query": False,
                        "is_line_query": False,
                        "needs_clarification": False,
                        "show_more_info": True,
                    })
        except Exception:
            pass

    # Ville interurbaine — avant réservation / publicité générique (« comment réserver touba »)
    _city_payload = _json_interurban_city(question, q_norm, city_hint)
    if _city_payload:
        return jsonify(_city_payload)

    # Services DDD (publicité, partenariat…) — avant inférence arrêt / hors-sujet
    if any(k in q_norm for k in _SKIP_IMPLICIT_STOP_KEYWORDS):
        try:
            import sys as _sys
            _app_mod = _sys.modules.get("app")
            _fb = getattr(_app_mod, "_fallback_publicite_partenariat", None) if _app_mod else None
            if _fb and _app_mod and getattr(_app_mod, "_is_publicite_query", lambda q: False)(q_norm):
                com = _fb(question)
                if com and com.get("answer"):
                    return jsonify({
                        "answer": com["answer"],
                        "summary": com.get("summary", "Partenariat et publicité DDD")[:200],
                        "sources": com.get("sources", []),
                        "results": com.get("results", []),
                        "query_type": "general",
                        "has_structured_data": False,
                        "is_city_query": False,
                        "is_line_query": False,
                        "needs_clarification": False,
                        "show_more_info": True,
                    })
            _fb = getattr(_app_mod, "_fallback_from_site", None) if _app_mod else None
            if _fb:
                com = _fb(question)
                ans = (com.get("answer") or "").lower() if com else ""
                junk = (
                    "agent-ia", "guide complet des services",
                    "je n'ai pas trouv",
                )
                if com and com.get("answer") and not any(j in ans for j in junk):
                    return jsonify({
                        "answer": com["answer"],
                        "summary": com.get("summary", "Partenariat et publicité DDD")[:200],
                        "sources": com.get("sources", []),
                        "results": com.get("results", []),
                        "query_type": "general",
                        "has_structured_data": False,
                        "is_city_query": False,
                        "is_line_query": False,
                        "needs_clarification": False,
                        "show_more_info": True,
                    })
        except Exception:
            pass

    if _is_off_topic_question(question):
        return jsonify(_json_off_topic())

    # ── 0. Lookup base de connaissances résolue (questions déjà traitées) ────
    try:
        import importlib as _imp
        import sys as _sys
        _app_mod = _imp.import_module("app") if "app" in _sys.modules else None
        if _app_mod is None:
            _app_mod = _sys.modules.get("app")
        _lookup_fn = getattr(_app_mod, "lookup_resolved_query", None) if _app_mod else None
        if _lookup_fn:
            _resolved = _lookup_fn(question)
            if _resolved:
                if _resolved.get("status") == "repondu" and _resolved.get("reponse_text"):
                    return jsonify({
                        "answer":      _resolved["reponse_text"],
                        "summary":     _resolved["reponse_text"][:200],
                        "sources":     [{"title": "Base de connaissances DDD",
                                         "url": _resolved.get("page_cible_url") or "https://demdikk.sn",
                                         "score": 1.0}],
                        "results":     [],
                        "query_type":  "general",
                        "has_structured_data": False,
                        "is_city_query": False,
                        "is_line_query": False,
                        "needs_clarification": False,
                        "show_more_info": bool(_resolved.get("page_cible_url")),
                    })
                elif _resolved.get("status") == "redirige" and _resolved.get("page_cible_url"):
                    _url  = _resolved["page_cible_url"]
                    _ans  = (f"Pour cette question, consultez la page dédiée sur notre site :\n"
                             f"→ {_url}")
                    return jsonify({
                        "answer":      _ans,
                        "summary":     _ans,
                        "sources":     [{"title": "Dakar Dem Dikk", "url": _url, "score": 1.0}],
                        "results":     [],
                        "query_type":  "general",
                        "has_structured_data": False,
                        "is_city_query": False,
                        "is_line_query": False,
                        "needs_clarification": False,
                        "show_more_info": True,
                    })
    except Exception:
        pass  # Ne jamais bloquer sur le lookup

    qtype = detect_query_type(question)
    lines_stop_explicit = qtype == "lines_to_stop"

    # ── 1. Ville interurbaine (secours si non traitée plus haut) ───────────────
    _city_payload = _json_interurban_city(question, q_norm, city_hint)
    if _city_payload:
        return jsonify(_city_payload)

    # ── 2. Toutes les lignes ──────────────────────────────────────────────────
    if qtype == "all_lines_summary":
        cat = _categorize_lines(_URBAN_LINES)
        return jsonify({
            "answer":           f"Le réseau urbain DDD compte {len(_URBAN_LINES)} lignes.",
            "summary":          "Réseau urbain Dakar Dem Dikk",
            "sources":          [{"title": "Réseau Urbain DDD",
                                  "url": "https://demdikk.sn/reseau-urbain-dakar/", "score": 1.0}],
            "results":          [],
            "lines":            _URBAN_LINES,
            "lines_summary":    _URBAN_LINES,
            "categorized_lines": cat,
            "total_lines":      len(_URBAN_LINES),
            "query_type":       "all_lines_summary",
            "has_structured_data": True,
            "is_city_query":    False, "is_line_query": True,
            "needs_clarification": False,
        })

    # ── 3. Lignes desservant un arrêt ─────────────────────────────────────────
    stop_for_lines = None
    matching_lines = []

    if qtype == "lines_to_stop":
        stop_for_lines = _extract_stop_from_query(question)
        matching_lines = find_lines_for_stop(stop_for_lines) if stop_for_lines else []
        if not matching_lines:
            infer = _infer_stop_name_implicit(question) if not _is_smalltalk_question(question) else None
            if infer:
                stop_for_lines = infer
                matching_lines = find_lines_for_stop(infer)
    # Nom d'arrêt seul (ex. « Sandaga ») : pas de mot-clé « arrêt / quelle ligne »,
    # mais hors-sujet (sport, etc.) déjà filtré plus haut — on tente une correspondance arrêt.
    elif qtype not in ("all_lines_summary", "line_X") and not _is_smalltalk_question(question):
        # Ne pas inférer un arrêt si la question porte sur le prix/tarif/ticket
        # ou sur l'AIBD/aéroport (navette, pas arrêt de bus urbain)
        _STOP_SKIP_RE = re.compile(
            r'\b(ticket|tarif|prix|combien|co[uû]te?|billet|payer?|fcfa|'
            r'aibd|a[eé]roport|navette|blaise\s+diagne|'
            r'publicite|publicité|partenariat|annonce|publicitaire|regie|pub|'
            r'emploi|recrutement|location|reservation|abonnement)\b',
            re.IGNORECASE,
        )
        if not re.search(_STOP_SKIP_RE, question) and not _should_skip_implicit_stop_inference(q_norm):
            infer = _infer_stop_name_implicit(question)
            if infer:
                ml = find_lines_for_stop(infer)
                if ml:
                    stop_for_lines = infer
                    matching_lines = ml

    if stop_for_lines and matching_lines:
        if not lines_stop_explicit and not _implicit_stop_card_ok(stop_for_lines, matching_lines):
            stop_for_lines, matching_lines = None, []
    if stop_for_lines and matching_lines:
        # Liste courte uniquement : « lieu » : Ligne 1, Ligne 4, …
        compact = ", ".join(f"Ligne {l['number']}" for l in matching_lines)
        answer = f"« {stop_for_lines} » : {compact}"
        return jsonify({
            "answer":         answer,
            "summary":        f"Lignes à l'arrêt {stop_for_lines}",
            "sources":        [{"title": "Réseau Urbain DDD",
                                "url": "https://demdikk.sn/reseau-urbain-dakar/", "score": 1.0}],
            "results":        matching_lines,
            "lines_summary":  matching_lines,
            "stop_requested": stop_for_lines,
            "total_lines":    len(matching_lines),
            "query_type":     "lines_to_stop",
            "has_structured_data": True,
            "is_city_query":  False, "is_line_query": True,
            "needs_clarification": False,
        })

    # ── 4. Ligne spécifique ───────────────────────────────────────────────────
    if qtype == "line_X":
        line_num  = _detect_line_number(question)
        line_data = _get_line_by_number(line_num) if line_num else None
        if line_data:
            ld = dict(line_data)
            ld["stop_count"] = len(ld.get("stops", []))
            return jsonify({
                "answer":      f"Ligne {ld['number']} : {ld['start']} ↔ {ld['end']}",
                "summary":     f"LIGNE {ld['number']}",
                "sources":     [{"title": "Réseau Urbain DDD",
                                 "url": "https://demdikk.sn/reseau-urbain-dakar/", "score": 1.0}],
                "results":     [],
                "line_details": ld,
                "line_summary": ld,
                "query_type":  "line_details",
                "has_structured_data": True,
                "is_city_query": False, "is_line_query": True,
                "needs_clarification": False,
            })
        elif line_num:
            return jsonify({
                "answer":  f"La ligne {line_num} n'est pas répertoriée dans notre base de données.",
                "summary": f"LIGNE {line_num.upper()}",
                "sources": [{"title": "Réseau Urbain DDD",
                             "url": "https://demdikk.sn/reseau-urbain-dakar/", "score": 0.5}],
                "results": [],
                "query_type":  "line_details",
                "has_structured_data": False,
                "is_city_query": False, "is_line_query": True,
                "needs_clarification": False,
            })

    q_lower = question.lower()
    word_count = len(q_lower.split())

    # ── 5. Recherche générale (vectorielle / mots-clés) ───────────────────────
    results = _search(question, top_k=5)
    if results and results[0]["score"] >= 0.30:
        top = results[0]
        return jsonify({
            "answer":   top["content"][:700],
            "summary":  top["title"][:120],
            "sources":  [{"title": top["title"] or "Dakar Dem Dikk",
                          "url":   top["url"]   or "https://demdikk.sn",
                          "score": top["score"]}],
            "results":  [{"content": r["content"], "title": r["title"], "url": r["url"]}
                         for r in results],
            "query_type": "general",
            "has_structured_data": False,
            "is_city_query": False, "is_line_query": False,
            "needs_clarification": False,
        })

    # ── 6. Fallback (le wrapper app.py peut encore enrichir / reformuler) ─────
    return jsonify({
        "answer":   _CONTACT_NOT_FOUND_BLOCK,
        "summary":  "Je n'ai pas trouvé cette information.",
        "sources":  [{"title": "Dakar Dem Dikk — Contact", "url": "https://demdikk.sn/", "score": 0}],
        "results":  [],
        "query_type": "other",
        "has_structured_data": False,
        "is_city_query": False, "is_line_query": False,
        "needs_clarification": False,
    })


# ── Route /health ─────────────────────────────────────────────────────────────
@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status":          "ok",
        "documents":       len(_metadata),
        "embeddings":      _embeddings is not None,
        "model_loaded":    _model is not None,
        "lines_count":     len(_URBAN_LINES),
        "cities_count":    sum(len(s.get("villes", [])) for s in INTERURBAIN_SECTIONS),
        "last_refresh":    last_index_refresh,
    })


# ── Route /cities ─────────────────────────────────────────────────────────────
@app.route('/cities', methods=['GET'])
def cities():
    city_list = [v.lower()
                 for s in INTERURBAIN_SECTIONS
                 for v in s.get("villes", [])]
    return jsonify({"cities": city_list})


# ── Route /full_page/<url> ────────────────────────────────────────────────────
@app.route('/full_page/<path:url_encoded>', methods=['GET'])
def full_page(url_encoded: str):
    """Retourne le contenu complet d'une URL depuis les métadonnées indexées."""
    try:
        url = urllib.parse.unquote(url_encoded)
    except Exception:
        url = url_encoded
    chunks = [m for m in _metadata if m.get("url", "").rstrip('/') == url.rstrip('/')]
    if not chunks:
        return jsonify({"error": "Page non trouvée", "url": url}), 404
    full_text = "\n\n".join(c.get("text", "") for c in chunks)
    return jsonify({
        "url":     url,
        "title":   chunks[0].get("title", ""),
        "content": full_text,
        "chunks":  len(chunks),
    })


# ── Route / ───────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    from flask import send_from_directory
    return send_from_directory(_BASE_DIR, 'ui.html')


# ── Point d'entrée ────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)),
            debug=os.environ.get('FLASK_DEBUG', '1') == '1')
