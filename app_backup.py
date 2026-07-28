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


_QUERY_ACRONYMS = {
    "sdd": "senegal dem dikk",
    "ddd": "dakar dem dikk",
    "add": "afrique dem dikk",
}


def _expand_query_acronyms(question: str) -> str:
    """Développe les acronymes connus (insensible à la casse : sdd, SDD…)."""
    q = (question or "").strip()
    if not q:
        return q
    for acr, expansion in _QUERY_ACRONYMS.items():
        q = re.sub(rf"(?<!\w){re.escape(acr)}(?!\w)", expansion, q, flags=re.I)
    return q


_ACRONYM_DEFINITION_MARKERS = (
    "que signifie",
    "qu est ce que",
    "quest ce que",
    "c est quoi",
    "c est qui",
    "signifie",
    "signification",
    "veut dire",
    "que veut dire",
)

_ACRONYM_DEFINITIONS = (
    (
        ("add", "afrique dem dikk"),
        "ADD",
        "Afrique Dem Dikk",
        "le réseau international de Dakar Dem Dikk (liaisons transfrontalières, ex. Dakar–Banjul).",
    ),
    (
        ("sdd", "senegal dem dikk", "sengal dem dikk"),
        "SDD",
        "Sénégal Dem Dikk",
        "le réseau interurbain de Dakar Dem Dikk, reliant Dakar aux principales villes du Sénégal.",
    ),
    (
        ("ddd", "dakar dem dikk", "demdikk"),
        "DDD",
        "Dakar Dem Dikk",
        "l'opérateur public de transport en commun au Sénégal (urbain, interurbain, AIBD, international).",
    ),
    (
        ("dem dikk",),
        "DDD",
        "Dakar Dem Dikk",
        "l'opérateur public de transport en commun au Sénégal (urbain, interurbain, AIBD, international).",
    ),
    (
        ("cco", "centre de controle", "centre de contrôle", "tour de controle", "tour de contrôle"),
        "CCO",
        "Centre de Contrôle des Opérations",
        "la « tour de contrôle » de l'entreprise (suivi de flotte, incidents, assistance conducteurs).",
    ),
    (
        ("tek dem", "tekdem", "tek-dem"),
        "Tek Dem",
        "carte pass rechargeable",
        "le titre de transport DDD (carte/pass) pour payer et valider les trajets à bord.",
    ),
    (
        ("aibd", "blaise diagne", "aeroport blaise diagne"),
        "AIBD",
        "Aéroport International Blaise Diagne",
        "l'aéroport de référence desservi par la navette express Dakar Dem Dikk (Dakar–AIBD).",
    ),
)


def _match_acronym_definition_entry(q_norm: str) -> tuple[str, str, str] | None:
    qn = (q_norm or "").strip()
    if not qn:
        return None
    for keys, sigle, name, role in _ACRONYM_DEFINITIONS:
        for k in keys:
            if len(k) <= 5:
                if re.search(rf"(?<!\w){re.escape(k)}(?!\w)", qn):
                    return sigle, name, role
            elif k in qn:
                return sigle, name, role
    return None


def _is_acronym_definition_query(q_norm: str) -> bool:
    qn = (q_norm or "").strip()
    if not qn or not any(m in qn for m in _ACRONYM_DEFINITION_MARKERS):
        return False
    return _match_acronym_definition_entry(qn) is not None


def _json_acronym_definition(question: str, q_norm: str) -> dict | None:
    """« Que signifie SDD ? » → définition courte du sigle."""
    if not _is_acronym_definition_query(q_norm):
        return None
    matched = _match_acronym_definition_entry(q_norm)
    if not matched:
        return None
    sigle, name, role = matched
    answer = f"{sigle} signifie {name} : {role}"
    more_url = "https://demdikk.sn/"
    if sigle == "SDD":
        more_url = "https://demdikk.sn/reseau-interurbain/"
    elif sigle == "ADD":
        more_url = "https://demdikk.sn/reseau-international/"
    elif sigle == "CCO":
        more_url = "https://demdikk.sn/chatbot-2303/"
    elif sigle in ("Tek Dem", "AIBD"):
        more_url = "https://demdikk.sn/chatbot-2303/"
    return {
        "answer": answer,
        "summary": f"{sigle} — {name}",
        "sources": [{"title": name, "url": more_url, "score": 1.0}],
        "results": [],
        "query_type": "general",
        "has_structured_data": False,
        "is_city_query": False,
        "is_line_query": False,
        "needs_clarification": False,
        "show_more_info": True,
    }


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
    "interurbain", "senegal dem dikk", "sdd",
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

_INTERURBAIN_OVERVIEW_TRIGGERS = (
    "interurbain",
    "interurbains",
    "reseau-interurbain",
    "réseau-interurbain",
    "senegal dem dikk",
    "sénégal dem dikk",
    "sdd",
    "gare routiere de dieuppeul",
    "gare routière de dieuppeul",
)


def _interurban_destination_names() -> list[str]:
    seen: set[str] = set()
    names: list[str] = []
    for section in INTERURBAIN_SECTIONS:
        for ville in section.get("villes") or []:
            key = (ville or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            names.append(_city_display_name(ville))
    return names


def _format_destinations_list(names: list[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} et {names[1]}"
    return ", ".join(names[:-1]) + f" et {names[-1]}"


def _format_interurban_overview() -> str:
    intro = (
        "Dakar Dem Dikk propose un service interurbain appelé Sénégal Dem Dikk, "
        "lancé en février 2017, pour faciliter les déplacements entre les villes du pays."
    )
    dests = _interurban_destination_names()
    if not dests:
        return intro
    return f"{intro} Destinations : {_format_destinations_list(dests)}."


def _is_interurban_overview_query(qn: str, question: str = "") -> bool:
    """Question générale sur le réseau interurbain (sans ville précise)."""
    qn = (qn or "").strip()
    if not qn or not any(t in qn for t in _INTERURBAIN_OVERVIEW_TRIGGERS):
        return False
    if _detect_city(qn) or _detect_line_number(question or ""):
        return False
    if _TRAVEL_INTENT_RE.search(question or ""):
        return False
    return True


_INTERURBAIN_OVERVIEW_INTENT_MARKERS = (
    "comment",
    "pourquoi",
    "fonctionne",
    "fonctionnement",
    "avantage",
    "avantages",
    "historique",
    "difference",
    "c est quoi",
    "qu est ce que",
    "explique",
)
_INTERURBAIN_OVERVIEW_RAG_MIN_SCORE = 0.30
_INTERURBAIN_OVERVIEW_FAQ_MIN_SCORE = 0.5
_INTERURBAIN_RAG_LABEL_ONLY = frozenset({
    "destinations senegal dem dikk",
    "senegal dem dikk",
})


def _debug_interurban_overview(message: str) -> None:
    if os.environ.get("FLASK_DEBUG", "1") == "1":
        print(f"[interurban_overview] {message}")


def _interurban_overview_has_specific_intent(qn: str) -> bool:
    """Intention autre qu'une simple liste de destinations."""
    qn = (qn or "").strip()
    if not qn:
        return False
    return any(marker in qn for marker in _INTERURBAIN_OVERVIEW_INTENT_MARKERS)


def _interurban_overview_faq_helpers() -> tuple:
    try:
        import sys as _sys
        app_mod = _sys.modules.get("app")
        if not app_mod:
            return None, None, None
        return (
            getattr(app_mod, "_search_chatbot_page_blocks", None),
            getattr(app_mod, "_chatbot_faq_score", None),
            getattr(app_mod, "_faq_answer_usable", None),
        )
    except Exception:
        return None, None, None


# Infra FAQ/RAG partagée par les triggers à contenu fixe (hors interurban_overview).
_TRIGGER_FAQ_MIN_SCORE = 0.5
_TRIGGER_RAG_MIN_SCORE = 0.30


def _debug_fixed_trigger(name: str, message: str) -> None:
    if os.environ.get("FLASK_DEBUG", "1") == "1":
        print(f"[{name}] {message}")


def _trigger_rag_content_usable(content: str) -> bool:
    c = (content or "").strip()
    if len(c) < 60:
        return False
    cl = c.lower()
    if any(j in cl for j in ("agent-ia", "guide complet des services", "je n'ai pas trouv")):
        return False
    return True


def _try_faq_then_rag(question: str, q_norm: str) -> tuple[dict | None, str]:
    """FAQ chatbot-2303 puis RAG — seuils identiques à interurban_overview."""
    search_faq, faq_score_fn, faq_usable_fn = _interurban_overview_faq_helpers()
    if callable(search_faq):
        try:
            fb = search_faq(question)
            faq_score = faq_score_fn(fb) if callable(faq_score_fn) else 0.0
            faq_ok = callable(faq_usable_fn) and faq_usable_fn(fb, question, q_norm)
            if fb and faq_score >= _TRIGGER_FAQ_MIN_SCORE and faq_ok:
                out = dict(fb)
                out.setdefault("query_type", "general")
                out.setdefault("show_more_info", True)
                return out, f"faq score={faq_score:.2f}"
        except Exception as exc:
            return None, f"faq_error={exc!r}"
    try:
        for hit in (_search(question, top_k=8) or []):
            content = (hit.get("content") or "").strip()
            if not _trigger_rag_content_usable(content):
                continue
            score = float(hit.get("score") or 0)
            if score >= _TRIGGER_RAG_MIN_SCORE:
                out = _payload_from_rag_hit(hit)
                out.setdefault("query_type", "general")
                out.setdefault("show_more_info", True)
                return out, f"rag score={score:.2f} title={(hit.get('title') or '')[:50]!r}"
    except Exception as exc:
        return None, f"rag_error={exc!r}"
    return None, "no_hit"


# ── Événements / actualités — FAQ avant city_info (Magal, Tabaski, Gamou…) ──
_EVENT_INTENT_MARKERS = (
    "magal", "tabaski", "korite", "aid", "gamou", "fete", "edition",
    "evenement", "dispositif special",
)
_RE_EVENT_YEAR_PAIR = re.compile(
    r"\b(?:magal|tabaski|korite|aid|gamou|fete|edition|evenement|dispositif\s+special)\b"
    r".{0,40}\b(20\d{2})\b"
    r"|\b(20\d{2})\b.{0,40}\b(?:magal|tabaski|korite|aid|gamou|fete|edition|evenement|dispositif\s+special)\b",
    re.I,
)


def _detect_event_intent(qn: str) -> str | None:
    qn = (qn or "").strip()
    if not qn:
        return None
    for marker in _EVENT_INTENT_MARKERS:
        if marker in qn:
            return marker
    if _RE_EVENT_YEAR_PAIR.search(qn):
        return "year+event"
    return None


def _debug_event_city_route(
    event_term: str | None,
    city_name: str | None,
    source: str,
    detail: str = "",
) -> None:
    if os.environ.get("FLASK_DEBUG", "1") == "1":
        msg = (
            f"[event_city] event={event_term or 'none'} "
            f"city={city_name or 'none'} source={source}"
        )
        if detail:
            msg += f" ({detail})"
        print(msg)


def _try_event_faq_only(question: str, q_norm: str) -> tuple[dict | None, str]:
    """FAQ chatbot-2303 seule — même seuil que les autres triggers fixe."""
    search_faq, faq_score_fn, faq_usable_fn = _interurban_overview_faq_helpers()
    if not callable(search_faq):
        return None, "no_faq_fn"
    try:
        fb = search_faq(question)
        faq_score = faq_score_fn(fb) if callable(faq_score_fn) else 0.0
        faq_ok = callable(faq_usable_fn) and faq_usable_fn(fb, question, q_norm)
        if fb and faq_score >= _TRIGGER_FAQ_MIN_SCORE and faq_ok:
            out = dict(fb)
            out.setdefault("query_type", "general")
            out.setdefault("show_more_info", True)
            return out, f"score={faq_score:.2f}"
        return None, f"score={faq_score:.2f} usable={faq_ok}"
    except Exception as exc:
        return None, f"error={exc!r}"


def _try_event_faq_before_city_info(
    question_raw: str,
    q_norm_enriched: str,
    city_hint: str = "",
) -> dict | None:
    """Question originale avec terme d'événement → FAQ prioritaire sur city_info."""
    qn_orig = _norm(question_raw)
    event_term = _detect_event_intent(qn_orig)
    if not event_term:
        return None
    city_section = (
        (get_section_by_ville(city_hint) if city_hint else None)
        or _detect_city(q_norm_enriched)
        or _detect_city(qn_orig)
    )
    city_name = None
    if city_section:
        city_name = city_hint or _ville_key_from_query(q_norm_enriched, city_section)
        if not city_name:
            city_name = _ville_key_from_query(qn_orig, city_section)
    faq_payload, reason = _try_event_faq_only(question_raw.strip(), qn_orig)
    if faq_payload:
        _debug_event_city_route(event_term, city_name, "FAQ", reason)
        return faq_payload
    _debug_event_city_route(event_term, city_name, "city_info", f"fallback {reason}")
    return None


# ── TRIGGER 1 : AIBD / navette ──
# Bloc wrapper : app.py ~2739-2744 (_aibd_triggers → _fallback_from_site forcé)
_AIBD_TRIGGERS = ("aibd", "aeroport", "navette", "blaise diagne", "blaise-diagne")
_AIBD_INTENT_MARKERS = (
    "horaire", "heure", "depart", "frequence", "tarif", "prix", "billet",
    "reservation", "reserver", "duree", "combien", "comment", "ou ",
    "quand", "terminal", "arret", "retard", "contact", "telephone",
)


def _aibd_has_specific_intent(qn: str) -> bool:
    return any(m in (qn or "") for m in _AIBD_INTENT_MARKERS)


def _aibd_price_intent(qn: str) -> bool:
    return any(m in (qn or "") for m in ("combien", "tarif", "prix", "coute", "cout", "coûte", "coût"))


def _aibd_horaire_intent(qn: str) -> bool:
    return any(m in (qn or "") for m in ("horaire", "heure", "depart", "frequence", "quand"))


def _aibd_answer_satisfies_intent(q_norm: str, answer: str) -> bool:
    an = _norm(answer)
    if not an:
        return False
    if _aibd_price_intent(q_norm):
        has_price = any(w in an for w in ("fcfa", "000", "cfa", "franc"))
        if not has_price:
            return False
        if "6000" in an or "6 000" in (answer or ""):
            return True
        if "navette" in an and has_price:
            return True
        return "aibd" in an and has_price
    if _aibd_horaire_intent(q_norm):
        return (
            any(w in an for w in ("horaire", "heure", "depart", "premier", "dernier"))
            or bool(re.search(r"\d{1,2}h\d{0,2}", an))
        )
    if any(m in q_norm for m in ("contact", "telephone")):
        return "+221" in answer or "221" in an
    return len(an) >= 40


def _aibd_rag_hit_usable(q_norm: str, hit: dict) -> bool:
    content = (hit.get("content") or "").strip()
    if not _trigger_rag_content_usable(content):
        return False
    cn = _norm(content)
    if "afrique dem dikk" in cn and not any(w in cn for w in ("navette", "aibd", "aeroport", "6000")):
        return False
    return _aibd_answer_satisfies_intent(q_norm, content)


def _aibd_specific_search_queries(question: str, q_norm: str) -> list[str]:
    queries = [question]
    if _aibd_price_intent(q_norm):
        queries.extend([
            "tarif navette AIBD Blaise Diagne 6000 FCFA",
            "Navette Aéroportuaire Express tarif aller simple",
        ])
    elif _aibd_horaire_intent(q_norm):
        queries.extend([
            "horaires navette AIBD départ Dakar aéroport",
            "Premier départ navette Express AIBD",
        ])
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        key = _norm(q)
        if key and key not in seen:
            seen.add(key)
            out.append(q)
    return out


_AIBD_TARIF_CURATED = (
    "Navette Aéroportuaire Express (AIBD Dem Dikk) : le tarif unique pour la liaison entre Dakar "
    "et l'Aéroport International Blaise Diagne (AIBD) est fixé à 6 000 FCFA l'aller simple. "
    "Une franchise bagages est incluse (supplément par kilo excédentaire). "
    "Réservation et infos : application Dem Dikk, booking.demdikk.sn ou +221 33 824 10 10."
)


def _try_aibd_specific_answer(question: str, q_norm: str) -> tuple[dict | None, str]:
    search_faq, faq_score_fn, faq_usable_fn = _interurban_overview_faq_helpers()
    if callable(search_faq):
        for q in _aibd_specific_search_queries(question, q_norm):
            try:
                fb = search_faq(q)
                faq_score = faq_score_fn(fb) if callable(faq_score_fn) else 0.0
                if not fb or faq_score < _TRIGGER_FAQ_MIN_SCORE:
                    continue
                ans = fb.get("answer") or ""
                if not _aibd_answer_satisfies_intent(q_norm, ans):
                    continue
                out = dict(fb)
                out.setdefault("query_type", "general")
                out.setdefault("show_more_info", True)
                return out, f"faq score={faq_score:.2f}"
            except Exception:
                continue
        if _aibd_price_intent(q_norm):
            try:
                import sys as _sys
                app_mod = _sys.modules.get("app")
                fetch = getattr(app_mod, "_fetch_page_text", None) if app_mod else None
                extract = getattr(app_mod, "_extract_section_priority", None) if app_mod else None
                make = getattr(app_mod, "_make_chatbot_result", None) if app_mod else None
                if callable(fetch) and callable(extract) and callable(make):
                    page = fetch("https://demdikk.sn/chatbot-2303/")
                    section = extract(
                        page or "",
                        (
                            "Navette Aéroportuaire Express",
                            "Navette Aeroportuaire Express",
                            "AIBD Dem Dikk",
                            "6000 FCFA",
                        ),
                        max_chars=900,
                    )
                    if section and _aibd_answer_satisfies_intent(q_norm, section):
                        if _aibd_price_intent(q_norm):
                            flat = re.sub(r"\s+", " ", section)
                            m = re.search(
                                r"tarif unique pour la liaison[^.]*(?:6000|6\s*000)\s*FCFA[^.]*",
                                flat,
                                re.I,
                            )
                            if m:
                                section = m.group(0).strip().rstrip(".") + "."
                            else:
                                section = _AIBD_TARIF_CURATED
                        out = make(section)
                        out.setdefault("query_type", "general")
                        out.setdefault("show_more_info", True)
                        return out, "faq_extract_tarif"
            except Exception:
                pass
    try:
        best: dict | None = None
        best_score = -1.0
        seen: set[str] = set()
        for q in _aibd_specific_search_queries(question, q_norm):
            for hit in (_search(q, top_k=8) or []):
                content = (hit.get("content") or "").strip()
                key = _norm(content)[:240]
                if not key or key in seen:
                    continue
                seen.add(key)
                if not _aibd_rag_hit_usable(q_norm, hit):
                    continue
                score = float(hit.get("score") or 0)
                if score > best_score:
                    best_score = score
                    best = hit
        if best and best_score >= _TRIGGER_RAG_MIN_SCORE:
            out = _payload_from_rag_hit(best)
            out.setdefault("query_type", "general")
            out.setdefault("show_more_info", True)
            return out, f"rag score={best_score:.2f} title={(best.get('title') or '')[:50]!r}"
    except Exception as exc:
        return None, f"rag_error={exc!r}"
    if _aibd_price_intent(q_norm):
        out = _payload_from_curated_interurban(_AIBD_TARIF_CURATED)
        out["sources"] = [{
            "title": "Navette Express AIBD",
            "url": "https://demdikk.sn/chatbot-2303/",
            "score": 1.0,
        }]
        return out, "curated_tarif"
    return None, "no_hit"


# ── TRIGGER 2 : Colis / messagerie ──
# Blocs : app_backup ~2353-2395 (_try_ddd_service_fallback, _json_service_payload)
#         app_backup ~3025-3040 (early return services)
#         app.py ~2802-2806 (wrapper colis)
_COLIS_TRIGGERS = ("colis", "messagerie", "courrier", "expedier", "expedition")
_COLIS_INTENT_MARKERS = (
    "tarif", "prix", "combien", "delai", "duree", "poids", "dimension",
    "maximum", "suivi", "suivre", "tracking", "envoyer", "expedier", "comment",
    "ou ", "agence", "adresse", "horaire", "assurance", "interdit",
    "autorise", "fragile", "international",
)


def _is_colis_service_query(qn: str) -> bool:
    return any(t in (qn or "") for t in _COLIS_TRIGGERS)


def _colis_has_specific_intent(qn: str) -> bool:
    return any(m in (qn or "") for m in _COLIS_INTENT_MARKERS)


def _colis_clip_answer_for_intent(q_norm: str, answer: str) -> str:
    """Évite de coller la section tarification à une question suivi/délai."""
    text = (answer or "").strip()
    if not text:
        return text
    if any(m in q_norm for m in ("suivi", "suivre", "tracking")):
        for marker in ("\n\nTarification", "\nTarification", "\n\nLes tarifs", "\n\nNos tarifs"):
            idx = text.find(marker)
            if idx > 0:
                return text[:idx].strip()
    if any(m in q_norm for m in ("poids", "dimension", "maximum")):
        for marker in ("\n\nPour suivre", "\nPour suivre", "\n\nSuivi"):
            idx = text.find(marker)
            if idx > 0:
                return text[:idx].strip()
    return text


def _try_colis_specific_answer(question: str, q_norm: str) -> tuple[dict | None, str]:
    specific, reason = _try_faq_then_rag(question, q_norm)
    if not specific:
        return specific, reason
    clipped = _colis_clip_answer_for_intent(q_norm, specific.get("answer") or "")
    if clipped != (specific.get("answer") or ""):
        specific = dict(specific)
        specific["answer"] = clipped
        specific["summary"] = clipped[:200]
        reason = f"{reason}+clipped"
    return specific, reason


# ── TRIGGER 3 : Tek Dem ──
# Blocs : app.py ~2900-2903 (_site_triggers tek dem / carte / pass / rechargement)
#         app.py ~2917+ (fallback _fallback_from_site générique)
_TEK_DEM_CORE_TRIGGERS = ("tek dem", "tekdem")
_TEK_DEM_RECHARGE_TRIGGERS = ("rechargement", "recharger", "recharge", "rechargez")
_TEK_DEM_INTENT_MARKERS = (
    "recharg", "recharge", "solde", "comment", "ou ", "agence", "point de vente",
    "prix", "tarif", "combien", "obtenir", "acheter", "souscrire", "enfant",
    "duplicata", "opposition", "bloquer", "perdu", "perdue", "volee", "vole",
    "bug", "erreur", "fonctionne", "marche pas", "compte", "validite",
    "abonnement", "etudiant", "adulte", "fonctionnaire", "jeune actif",
)


def _matches_tek_dem_trigger(qn: str) -> str | None:
    qn = (qn or "").strip()
    if not qn:
        return None
    for t in _TEK_DEM_CORE_TRIGGERS:
        if t in qn:
            return t
    if any(t in qn for t in _TEK_DEM_RECHARGE_TRIGGERS):
        if any(w in qn for w in ("tek", "carte", "pass", "dem dikk", "ddd")):
            return "rechargement tek"
    if "carte" in qn and any(
        w in qn for w in ("tek", "perdu", "perdue", "volee", "vole", "duplicata", "opposition", "recharg")
    ):
        return "carte tek"
    if qn in ("carte", "pass", "ma carte", "mon pass"):
        return "carte/pass"
    return None


def _tek_dem_has_specific_intent(qn: str) -> bool:
    return any(m in (qn or "") for m in _TEK_DEM_INTENT_MARKERS)


def _tek_dem_specific_search_queries(question: str, q_norm: str) -> list[str]:
    queries = [question]
    if any(m in q_norm for m in ("recharg", "recharge", "solde")):
        queries.extend([
            "recharger carte Tek Dem solde agence",
            "rechargement pass Tek Dem",
        ])
    if any(m in q_norm for m in ("prix", "tarif", "combien", "abonnement")):
        queries.extend([
            "abonnement Tek Dem tarif étudiant adulte FCFA",
            "frais carte Tek Dem 1000 FCFA",
        ])
    if any(m in q_norm for m in ("perdu", "perdue", "volee", "vole", "duplicata", "opposition")):
        queries.extend([
            "carte Tek Dem perdue opposition duplicata",
            "carte volée Tek Dem que faire",
        ])
    if any(m in q_norm for m in ("obtenir", "acheter", "souscrire", "comment", "ou ")):
        queries.append("obtenir carte Tek Dem points de souscription agence")
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        key = _norm(q)
        if key and key not in seen:
            seen.add(key)
            out.append(q)
    return out


def _tek_dem_answer_satisfies_intent(q_norm: str, answer: str) -> bool:
    an = _norm(answer)
    if not an:
        return False
    if any(m in q_norm for m in ("perdu", "perdue", "volee", "vole", "duplicata", "opposition")):
        return any(w in an for w in ("perdu", "vole", "duplicata", "opposition", "bloquer", "service client"))
    if not any(w in an for w in ("tek dem", "tekdem", "carte", "pass", "recharg", "abonnement")):
        return False
    if any(m in q_norm for m in ("recharg", "recharge", "solde")):
        return any(w in an for w in ("recharg", "solde", "agence", "point de vente"))
    if any(m in q_norm for m in ("prix", "tarif", "combien", "abonnement")):
        return any(w in an for w in ("fcfa", "tarif", "prix", "000", "abonnement"))
    return len(an) >= 40


def _tek_dem_rag_hit_usable(q_norm: str, hit: dict) -> bool:
    content = (hit.get("content") or "").strip()
    if not _trigger_rag_content_usable(content):
        return False
    return _tek_dem_answer_satisfies_intent(q_norm, content)


_TEK_DEM_PRESENTATION = (
    "Tek Dem est la carte pass rechargeable de Dakar Dem Dikk pour payer et valider "
    "vos trajets à bord (déploiement progressif de la validation). "
    "Abonnements mensuels : étudiant 10 000 FCFA, adulte 15 000 FCFA, etc. "
    "Frais de carte : 1 000 FCFA pour un nouveau pass. Recharge et souscription en agence DDD."
)


def _tek_dem_fixe_payload(fb: dict | None) -> dict | None:
    """Contenu fixe Tek Dem — synthèse si l'extrait FAQ est trop court ou mal découpé."""
    if not fb:
        return None
    ans = (fb.get("answer") or "").strip()
    if len(ans) < 80 or ans.startswith(":"):
        return {
            **fb,
            "answer": _TEK_DEM_PRESENTATION,
            "summary": "Tek Dem — carte pass DDD",
        }
    return fb


def _try_tek_dem_specific_answer(question: str, q_norm: str) -> tuple[dict | None, str]:
    search_faq, faq_score_fn, faq_usable_fn = _interurban_overview_faq_helpers()
    try:
        import sys as _sys
        app_mod = _sys.modules.get("app")
        fetch = getattr(app_mod, "_fetch_page_text", None) if app_mod else None
        extract = getattr(app_mod, "_extract_section_priority", None) if app_mod else None
        make = getattr(app_mod, "_make_chatbot_result", None) if app_mod else None
        if callable(fetch) and callable(extract) and callable(make):
            page = fetch("https://demdikk.sn/chatbot-2303/")
            if any(m in q_norm for m in ("perdu", "perdue", "volee", "vole", "duplicata", "opposition")):
                section = extract(
                    page or "",
                    ("Carte perdue/volée", "Carte perdue/volee", "opposition et duplicata"),
                    max_chars=600,
                )
                if section and _tek_dem_answer_satisfies_intent(q_norm, section):
                    flat = re.sub(r"\s+", " ", section)
                    m = re.search(r"Carte perdue[^.]*opposition et duplicata\.?", flat, re.I)
                    if m:
                        section = m.group(0).strip()
                    out = make(section)
                    out.setdefault("query_type", "general")
                    out.setdefault("show_more_info", True)
                    return out, "faq_extract_perdu"
            if any(m in q_norm for m in ("prix", "tarif", "combien", "abonnement", "etudiant", "adulte")):
                section = extract(
                    page or "",
                    ("Tek Dem : rechargeable", "Abonnements mensuels", "Carte Tek Dem"),
                    max_chars=900,
                )
                if section and _tek_dem_answer_satisfies_intent(q_norm, section):
                    out = make(section)
                    out.setdefault("query_type", "general")
                    out.setdefault("show_more_info", True)
                    return out, "faq_extract_tarif"
    except Exception:
        pass
    if callable(search_faq):
        for q in _tek_dem_specific_search_queries(question, q_norm):
            try:
                fb = search_faq(q)
                faq_score = faq_score_fn(fb) if callable(faq_score_fn) else 0.0
                faq_ok = callable(faq_usable_fn) and faq_usable_fn(fb, question, q_norm)
                ans = (fb or {}).get("answer") or ""
                if (
                    fb
                    and faq_score >= _TRIGGER_FAQ_MIN_SCORE
                    and faq_ok
                    and _tek_dem_answer_satisfies_intent(q_norm, ans)
                ):
                    out = dict(fb)
                    out.setdefault("query_type", "general")
                    out.setdefault("show_more_info", True)
                    return out, f"faq score={faq_score:.2f}"
            except Exception:
                continue
    try:
        best: dict | None = None
        best_score = -1.0
        seen: set[str] = set()
        for q in _tek_dem_specific_search_queries(question, q_norm):
            for hit in (_search(q, top_k=8) or []):
                content = (hit.get("content") or "").strip()
                key = _norm(content)[:240]
                if not key or key in seen:
                    continue
                seen.add(key)
                if not _tek_dem_rag_hit_usable(q_norm, hit):
                    continue
                score = float(hit.get("score") or 0)
                if score > best_score:
                    best_score = score
                    best = hit
        if best and best_score >= _TRIGGER_RAG_MIN_SCORE:
            out = _payload_from_rag_hit(best)
            out.setdefault("query_type", "general")
            out.setdefault("show_more_info", True)
            return out, f"rag score={best_score:.2f} title={(best.get('title') or '')[:50]!r}"
    except Exception as exc:
        return None, f"rag_error={exc!r}"
    return None, "no_hit"


def _interurban_rag_content_usable(content: str) -> bool:
    """Ignore les libellés courts type « Destinations Sénégal Dem Dikk »."""
    c = (content or "").strip()
    if len(c) < 80:
        return False
    cn = _norm(c)
    if cn in _INTERURBAIN_RAG_LABEL_ONLY:
        return False
    lines = [ln.strip() for ln in c.split("\n") if ln.strip()]
    if len(lines) == 1 and len(lines[0]) < 55:
        return False
    return True


def _interurban_specific_search_queries(question: str, q_norm: str) -> list[str]:
    queries = [question]
    if any(
        m in q_norm
        for m in (
            "comment", "fonctionne", "fonctionnement",
            "c est quoi", "qu est ce que", "explique", "historique",
        )
    ):
        queries.append("Sénégal Dem Dikk définition rôle mission offre interurbaine")
        queries.append("Senegal Dem Dikk lancé février 2017 cars interurbains")
    if any(m in q_norm for m in ("avantage", "avantages", "pourquoi")):
        queries.append("avantages Sénégal Dem Dikk confort bus fidélité")
        queries.append("programme fidélité voyageurs Sénégal Dem Dikk")
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        key = _norm(q)
        if key and key not in seen:
            seen.add(key)
            out.append(q)
    return out


def _is_bare_senegal_dem_dikk_query(q_norm: str) -> bool:
    """Requête = marque seule (SDD / Sénégal Dem Dikk), sans autre mot."""
    qn = (q_norm or "").strip()
    return qn in ("senegal dem dikk", "sengal dem dikk", "sdd")


def _interurban_presentation_text() -> str:
    return (
        "Sénégal Dem Dikk (SDD) est l'offre interurbaine de Dakar Dem Dikk, lancée le 1er février 2017. "
        "Elle relie Dakar aux principales villes du pays (Saint-Louis, Thiès, Kaolack, Ziguinchor, "
        "Kédougou, Tambacounda, etc.) avec des cars de grand tourisme (sièges inclinables, espace bagages). "
        "Le réseau compte environ 80 lignes interurbaines. "
        "Réservation : application mobile Dem Dikk, agences ou gares routières — assistance au +221 33 824 10 10."
    )


def _interurban_fonctionnement_text() -> str:
    return (
        "Sénégal Dem Dikk (SDD) est le réseau interurbain de Dakar Dem Dikk : des cars relient Dakar "
        "aux grandes villes du Sénégal (Saint-Louis, Thiès, Kaolack, Ziguinchor, Tambacounda, etc.).\n\n"
        "Comment ça fonctionne :\n"
        "• Réservation : application mobile Dem Dikk, agences ou gares routières "
        "(notamment le Terminus Liberté 5 à Dakar).\n"
        "• Départs : horaires selon la destination (souvent le matin vers 7h–8h, parfois l'après-midi).\n"
        "• À bord : cars grand tourisme avec sièges inclinables et espace bagages.\n"
        "• Tarifs : forfait selon la destination.\n\n"
        "Assistance : +221 33 824 10 10."
    )


def _interurban_avantages_text() -> str:
    return (
        "Les avantages de Sénégal Dem Dikk :\n"
        "• Couverture nationale : environ 80 lignes et une vingtaine de destinations.\n"
        "• Confort : cars de grand tourisme (sièges inclinables, espace bagages accru).\n"
        "• Bus connectés pour voyager sereinement entre les régions.\n"
        "• Programme de fidélité pour les voyageurs réguliers (points échangeables contre des titres de transport).\n"
        "• Réservation flexible via l'application, en agence ou en gare.\n\n"
        "Assistance : +221 33 824 10 10."
    )


def _interurban_curated_answer(q_norm: str) -> str | None:
    if _is_bare_senegal_dem_dikk_query(q_norm):
        return _interurban_presentation_text()
    if any(
        m in q_norm
        for m in (
            "comment", "fonctionne", "fonctionnement",
            "c est quoi", "qu est ce que", "explique", "historique",
        )
    ):
        return _interurban_fonctionnement_text()
    if any(m in q_norm for m in ("avantage", "avantages", "pourquoi")):
        return _interurban_avantages_text()
    return None


def _try_interurban_presentation_answer(question: str, q_norm: str) -> tuple[dict, str]:
    """Présentation du réseau SDD (marque seule) — RAG puis texte structuré."""
    pres_queries = [
        question,
        "Sénégal Dem Dikk présentation offre interurbaine",
        "Senegal Dem Dikk lancé février 2017 cars interurbains",
    ]
    seen_q: set[str] = set()
    seen_content: set[str] = set()
    best: dict | None = None
    best_score = -1.0
    for q in pres_queries:
        key = _norm(q)
        if not key or key in seen_q:
            continue
        seen_q.add(key)
        for search_fn in (_search, _keyword_search):
            try:
                hits = search_fn(q, top_k=8)
            except Exception:
                continue
            for hit in hits:
                content = (hit.get("content") or "").strip()
                ck = _norm(content)[:240]
                if not ck or ck in seen_content:
                    continue
                seen_content.add(ck)
                if not _interurban_rag_content_usable(content):
                    continue
                score = float(hit.get("score") or 0)
                cn = _norm(content)
                if "senegal dem dikk" in cn:
                    score += 0.8
                if any(w in cn for w in ("lance", "2017", "interurbain", "ligne", "car", "mission", "confort")):
                    score += 1.5
                if score > best_score:
                    best_score = score
                    best = hit
    if best and len((best.get("content") or "")) >= 100:
        return _payload_from_rag_hit(best), f"rag score={float(best.get('score', 0)):.2f}"
    return _payload_from_curated_interurban(_interurban_presentation_text()), "curated"


def _interurban_rag_intent_bonus(q_norm: str, content: str) -> float:
    """Favorise les extraits qui répondent vraiment à l'intention (ex. fidélité pour « avantages »)."""
    cn = _norm(content)
    bonus = 0.0
    if any(m in q_norm for m in ("avantage", "avantages", "pourquoi")):
        if any(w in cn for w in ("avantage", "fidel", "programme", "recompense", "points")):
            bonus += 3.0
        elif "confort" in cn:
            bonus += 0.4
    if any(
        m in q_norm
        for m in ("comment", "fonctionne", "fonctionnement", "explique", "c est quoi", "historique")
    ):
        if any(w in cn for w in ("lance", "2017", "interurbain", "senegal dem dikk", "ligne", "car", "mission")):
            bonus += 2.0
    return bonus


def _interurban_rag_satisfies_intent(q_norm: str, content: str) -> bool:
    cn = _norm(content)
    if any(m in q_norm for m in ("avantage", "avantages", "pourquoi")):
        return any(w in cn for w in ("avantage", "fidel", "programme", "recompense", "points", "confort"))
    if any(m in q_norm for m in ("comment", "fonctionne", "fonctionnement", "explique")):
        return any(
            w in cn
            for w in ("reserv", "billet", "depart", "gare", "appli", "horaire", "terminus", "tarif", "car")
        )
    return len(cn) >= 100


def _gather_interurban_context_for_intent(question: str, q_norm: str, limit: int = 4) -> str:
    """Plusieurs extraits index pour alimenter la reformulation LLM."""
    extra_queries: list[str] = []
    if any(m in q_norm for m in ("comment", "fonctionne", "fonctionnement", "explique")):
        extra_queries.extend([
            "Réservation Sénégal Dem Dikk application agence gare",
            "Sénégal Dem Dikk départ Terminus Liberté 5 horaires",
            "Sénégal Dem Dikk définition rôle cars interurbains lancé 2017",
        ])
    elif any(m in q_norm for m in ("avantage", "avantages", "pourquoi")):
        extra_queries.extend([
            "Sénégal Dem Dikk confort cars grand tourisme bagages",
            "programme fidélité voyageurs Sénégal Dem Dikk avantages",
            "Sénégal Dem Dikk lignes destinations couverture nationale",
        ])
    queries = _interurban_specific_search_queries(question, q_norm) + extra_queries
    seen: set[str] = set()
    parts: list[str] = []
    for q in queries:
        if len(parts) >= limit:
            break
        for search_fn in (_search, _keyword_search):
            try:
                hits = search_fn(q, top_k=6)
            except Exception:
                continue
            for hit in hits:
                content = (hit.get("content") or "").strip()
                key = _norm(content)[:240]
                if not key or key in seen or not _interurban_rag_content_usable(content):
                    continue
                seen.add(key)
                parts.append(content)
                if len(parts) >= limit:
                    break
            if len(parts) >= limit:
                break
    return "\n\n".join(parts)


def _enrich_interurban_payload_with_context(payload: dict, question: str, q_norm: str) -> dict:
    extra = _gather_interurban_context_for_intent(question, q_norm)
    if not extra:
        return payload
    out = dict(payload)
    base = (out.get("answer") or "").strip()
    combined = f"{base}\n\n{extra}".strip() if base else extra
    out["answer"] = combined
    results = list(out.get("results") or [])
    if results:
        results[0] = dict(results[0])
        results[0]["content"] = combined
    else:
        results = [{"content": combined, "title": out.get("summary") or "Sénégal Dem Dikk", "url": "https://demdikk.sn/reseau-interurbain/"}]
    out["results"] = results
    return out


def _best_usable_interurban_rag_hit(question: str, q_norm: str) -> dict | None:
    """Vectoriel + mots-clés : premier extrait substantiel (pas un simple libellé)."""
    seen: set[str] = set()
    best: dict | None = None
    best_score = -1.0
    search_fns = (_search, _keyword_search)
    for q in _interurban_specific_search_queries(question, q_norm):
        for search_fn in search_fns:
            try:
                hits = search_fn(q, top_k=10)
            except Exception:
                continue
            for hit in hits:
                content = (hit.get("content") or "").strip()
                key = _norm(content)[:240]
                if not key or key in seen:
                    continue
                seen.add(key)
                if not _interurban_rag_content_usable(content):
                    continue
                score = float(hit.get("score") or 0)
                cn = _norm(content)
                if "senegal dem dikk" in cn:
                    score += 0.6
                score += _interurban_rag_intent_bonus(q_norm, content)
                if score > best_score:
                    best_score = score
                    best = hit
    return best


def _payload_from_rag_hit(hit: dict) -> dict:
    raw = (hit.get("content") or "").strip()
    content = raw
    if len(content) > 700:
        content = content[:700].rsplit(" ", 1)[0] + "…"
    title = hit.get("title") or "Dakar Dem Dikk"
    url = hit.get("url") or "https://demdikk.sn/reseau-interurbain/"
    if "agent-ia" in (title or "").lower() or title.strip().lower() == "dakar dem dikk":
        first_line = next((ln.strip() for ln in raw.split("\n") if ln.strip()), "")
        summary = (first_line[:120] if len(first_line) > 12 else "Réseau Sénégal Dem Dikk")
    else:
        summary = title[:120]
    return {
        "answer": content,
        "summary": summary,
        "sources": [{"title": summary, "url": url, "score": hit.get("score", 0)}],
        "results": [
            {"content": raw, "title": summary, "url": url}
        ],
        "query_type": "general",
        "has_structured_data": False,
        "is_city_query": False,
        "is_line_query": False,
        "needs_clarification": False,
        "show_more_info": True,
    }


def _payload_from_curated_interurban(text: str) -> dict:
    return {
        "answer": text,
        "summary": text[:120],
        "sources": [{
            "title": "Réseau Sénégal Dem Dikk",
            "url": "https://demdikk.sn/reseau-interurbain/",
            "score": 1.0,
        }],
        "results": [{"content": text, "title": "Réseau Sénégal Dem Dikk", "url": "https://demdikk.sn/reseau-interurbain/"}],
        "query_type": "general",
        "has_structured_data": False,
        "is_city_query": False,
        "is_line_query": False,
        "needs_clarification": False,
        "show_more_info": True,
    }


def _try_interurban_specific_answer(question: str, q_norm: str) -> tuple[dict | None, str]:
    """FAQ chatbot-2303 puis RAG si la question interurbaine est précise."""
    search_faq, faq_score_fn, faq_usable_fn = _interurban_overview_faq_helpers()
    if callable(search_faq):
        try:
            fb = search_faq(question)
            faq_score = faq_score_fn(fb) if callable(faq_score_fn) else 0.0
            faq_ok = callable(faq_usable_fn) and faq_usable_fn(fb, question, q_norm)
            if fb and faq_score >= _INTERURBAIN_OVERVIEW_FAQ_MIN_SCORE and faq_ok:
                out = dict(fb)
                out.setdefault("query_type", "general")
                out.setdefault("show_more_info", True)
                return out, f"faq score={faq_score:.2f}"
        except Exception as exc:
            _debug_interurban_overview(f"faq_error={exc!r}")

    # « Avantages » : ancrage structuré (confort, couverture, fidélité…) + contexte index pour le LLM
    if any(m in q_norm for m in ("avantage", "avantages", "pourquoi")):
        payload = _payload_from_curated_interurban(_interurban_avantages_text())
        payload = _enrich_interurban_payload_with_context(payload, question, q_norm)
        return payload, "curated_avantages"

    try:
        hit = _best_usable_interurban_rag_hit(question, q_norm)
        if hit:
            payload = _payload_from_rag_hit(hit)
            ans = payload.get("answer") or ""
            if _interurban_rag_satisfies_intent(q_norm, ans):
                payload = _enrich_interurban_payload_with_context(payload, question, q_norm)
                return (
                    payload,
                    f"rag score={float(hit.get('score', 0)):.2f} title={hit.get('title', '')[:60]!r}",
                )
            _debug_interurban_overview("rag_hit_weak_intent")
    except Exception as exc:
        _debug_interurban_overview(f"rag_error={exc!r}")

    curated = _interurban_curated_answer(q_norm)
    if curated:
        payload = _payload_from_curated_interurban(curated)
        payload = _enrich_interurban_payload_with_context(payload, question, q_norm)
        return payload, "curated"

    return None, "no_hit"


def _json_interurban_overview_payload(answer: str) -> dict:
    return {
        "answer": answer,
        "summary": answer[:200],
        "sources": [
            {
                "title": "Réseau Interurbain DDD",
                "url": "https://demdikk.sn/reseau-interurbain/",
                "score": 1.0,
            }
        ],
        "results": [{"content": answer, "target_city": ""}],
        "query_type": "interurban_overview",
        "has_structured_data": True,
        "is_city_query": False,
        "is_line_query": False,
        "needs_clarification": False,
        "show_more_info": True,
    }


def _json_interurban_overview(question: str, q_norm: str) -> dict | None:
    if not _is_interurban_overview_query(q_norm, question):
        return None

    if _is_bare_senegal_dem_dikk_query(q_norm):
        pres, reason = _try_interurban_presentation_answer(question, q_norm)
        _debug_interurban_overview(f"route=presentation ({reason}) q={question!r}")
        return pres

    if _interurban_overview_has_specific_intent(q_norm):
        specific, reason = _try_interurban_specific_answer(question, q_norm)
        if specific:
            _debug_interurban_overview(
                f"route=specific ({reason}) q={question!r}"
            )
            return specific
        _debug_interurban_overview(
            f"route=overview_fallback (intent=yes, {reason}) q={question!r}"
        )
    else:
        _debug_interurban_overview(f"route=overview (intent=no) q={question!r}")

    return _json_interurban_overview_payload(_format_interurban_overview())

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


# ── Comparaisons (« X vs Y », « différence entre X et Y »…) ───────────────────
_RE_COMP_DIFF = re.compile(
    r"(?:quelle\s+est\s+la\s+)?diff[eé]rence\s+(?:de\s+\w+\s+)?entre\s+(.+?)\s+et\s+(.+?)\s*$"
    r"|(?:quelle\s+est\s+la\s+)?diff[eé]rence\s+(?:entre|de)\s+(.+?)\s+et\s+(.+?)\s*$",
    re.I,
)
_RE_COMP_LEQUEL = re.compile(
    r"lequel(?:le)?(?:s)?\s+(?:entre|de)\s+(.+?)\s+et\s+(.+?)(?:\s+(?:est|sont)\s+.+)?\s*$",
    re.I,
)
_RE_COMP_MOINS_CHER = re.compile(
    r"(?:entre|de)\s+(.+?)\s+et\s+(.+?)\s+(?:est\s+)?(?:le\s+)?(?:moins|plus)\s+cher(?:e)?\s*$"
    r"|^(.+?)\s+et\s+(.+?)\s*,?\s*(?:le\s+)?(?:moins|plus)\s+cher(?:e)?\s*$",
    re.I,
)
_RE_COMP_VS = re.compile(r"^(.+?)\s+(?:vs\.?|versus)\s+(.+?)\s*$", re.I)
_RE_COMP_OU = re.compile(
    r"^(?:c['\u2019]est\s+)?(.+?)\s+ou\s+(.+?)\s*\??\s*$",
    re.I,
)
_COMP_OU_SKIP = re.compile(
    r"\b(comment|pourquoi|quand|combien|est ce que|peut on|puis je)\b",
    re.I,
)


def _clean_comparison_part(part: str) -> str:
    p = (part or "").strip().strip("?.!,;:")
    p = re.sub(r"^(?:le|la|les|l['\u2019]|un|une|des|du|de)\s+", "", p, flags=re.I)
    p = re.sub(r"\s+(?:est|sont)\b(?:\s+.*)?$", "", p, flags=re.I)
    return p.strip()


def _comparison_focus_from_question(question: str) -> str | None:
    """Aspect cible extrait de la question comparative (prix, durée…)."""
    qn = _norm(question)
    if any(w in qn for w in ("moins cher", "plus cher", "prix", "tarif", "cout", "coute", "fcfa", "cher")):
        return "prix"
    if any(w in qn for w in ("duree", "durees", "temps", "longtemps")) or "combien de temps" in qn:
        return "duree"
    if any(w in qn for w in ("horaire", "horaires", "depart", "departs", "heure", "heures")):
        return "horaires"
    if any(w in qn for w in ("itineraire", "trajet", "route", "passage")):
        return "itineraire_detail"
    return None


def _detect_comparison_query(question: str) -> tuple[str, str] | None:
    """
    Repère une question comparative et renvoie deux sous-requêtes brutes (X, Y).
    Ne présume pas que X/Y sont des villes ou des lignes.
    """
    q = (question or "").strip()
    if not q or len(q) < 3:
        return None
    # Format raccourci « Touba/Thiès » ou « Touba / Thiès »
    if q.count("/") == 1 and "://" not in q:
        raw_left, raw_right = q.split("/", 1)
        left, right = _clean_comparison_part(raw_left), _clean_comparison_part(raw_right)
        if len(left) >= 2 and len(right) >= 2 and left != right:
            return left, right
    if len(q) < 7:
        return None
    for pat in (_RE_COMP_DIFF, _RE_COMP_LEQUEL, _RE_COMP_VS):
        m = pat.search(q)
        if m:
            g = [m.group(i) for i in range(1, pat.groups + 1) if m.group(i)]
            if len(g) >= 2:
                left, right = _clean_comparison_part(g[0]), _clean_comparison_part(g[1])
                if left and right and left != right:
                    return left, right
    m_mc = _RE_COMP_MOINS_CHER.search(q)
    if m_mc:
        g = [m_mc.group(i) for i in range(1, 3) if m_mc.group(i)]
        if len(g) >= 2:
            left, right = _clean_comparison_part(g[0]), _clean_comparison_part(g[1])
            if left and right and left != right:
                return left, right
    if not _COMP_OU_SKIP.search(q):
        m = _RE_COMP_OU.match(q)
        if m:
            left, right = _clean_comparison_part(m.group(1)), _clean_comparison_part(m.group(2))
            if len(left) >= 2 and len(right) >= 2 and left != right:
                return left, right
    return None


def _subquery_result_usable(ctx: dict | None) -> bool:
    if not ctx or not (ctx.get("answer") or "").strip():
        return False
    ans = (ctx["answer"] or "").lower()
    if "je n'ai pas trouv" in ans or "pas répertoriée" in ans or "pas repertoriee" in ans:
        return False
    if ctx.get("query_type") == "other":
        return False
    return len(ctx["answer"].strip()) >= 15


def _resolve_subquery_context(
    sub_question: str, *, focus: str | None = None, comparison: bool = False
) -> dict | None:
    """
    Cascade isolée pour une moitié de comparaison — même ordre de priorité
    que ask(), sans comparaison ni historique.
    """
    sub = (sub_question or "").strip()
    if not sub:
        return None
    sub_norm = _norm(sub)
    label = sub

    city_section = (get_section_by_ville(sub) if sub else None) or _detect_city(sub_norm)
    if city_section:
        ville_key = _ville_key_from_query(sub_norm, city_section)
        if focus:
            aspect = focus
        elif comparison:
            aspect = "presence"
        else:
            aspect = _resolve_city_aspect(sub_norm, sub, city_section)
        answer = _format_city_response_prose(city_section, ville_key, aspect=aspect)
        if answer:
            return {
                "label": label,
                "answer": answer,
                "source_type": "city_info",
                "sources": [{
                    "title": "Réseau Interurbain DDD",
                    "url": "https://demdikk.sn/reseau-interurbain/",
                    "score": 1.0,
                }],
                "query_type": "city_info",
            }

    city_payload = _json_interurban_city(sub, sub_norm)
    if city_payload and city_payload.get("answer"):
        return {
            "label": label,
            "answer": city_payload["answer"],
            "source_type": "city_info",
            "sources": city_payload.get("sources", []),
            "query_type": city_payload.get("query_type"),
        }

    qtype = detect_query_type(sub)
    if qtype == "line_X":
        line_num = _detect_line_number(sub)
        line_data = _get_line_by_number(line_num) if line_num else None
        if line_data:
            ld = dict(line_data)
            stops = ld.get("stops") or []
            ans = (
                f"Ligne {ld['number']} : {ld.get('start', '')} ↔ {ld.get('end', '')}. "
                f"{len(stops)} arrêts."
            )
            return {
                "label": label,
                "answer": ans,
                "source_type": "line_details",
                "sources": [{"title": "Réseau Urbain DDD",
                             "url": "https://demdikk.sn/reseau-urbain-dakar/", "score": 1.0}],
                "query_type": "line_details",
            }

    svc = _json_service_payload(sub, sub_norm)
    if svc and svc.get("answer"):
        return {
            "label": label,
            "answer": svc["answer"],
            "source_type": "service",
            "sources": svc.get("sources", []),
            "query_type": "general",
        }

    try:
        import sys as _sys
        _app_mod = _sys.modules.get("app")
        _faq_fn = getattr(_app_mod, "_search_chatbot_page_blocks", None) if _app_mod else None
        if _faq_fn:
            fb = _faq_fn(sub)
            if fb and fb.get("answer"):
                return {
                    "label": label,
                    "answer": fb["answer"],
                    "source_type": "faq",
                    "sources": fb.get("sources", []),
                    "query_type": "general",
                }
    except Exception:
        pass

    results = _search(sub, top_k=3)
    if results and results[0].get("score", 0) >= 0.30:
        top = results[0]
        return {
            "label": label,
            "answer": top["content"][:700],
            "source_type": "rag",
            "sources": [{"title": top.get("title") or "Dakar Dem Dikk",
                         "url": top.get("url") or "https://demdikk.sn",
                         "score": top.get("score", 0)}],
            "query_type": "general",
        }
    return None


def _json_comparison_payload(question: str, left: str, right: str) -> dict | None:
    """Construit le payload comparison — None si aucune moitié exploitable."""
    focus = _comparison_focus_from_question(question)
    left_ctx = _resolve_subquery_context(left, focus=focus, comparison=True)
    right_ctx = _resolve_subquery_context(right, focus=focus, comparison=True)
    left_ok = _subquery_result_usable(left_ctx)
    right_ok = _subquery_result_usable(right_ctx)

    if not left_ok and not right_ok:
        return None

    sources: list[dict] = []
    for ctx in (left_ctx, right_ctx):
        if ctx and ctx.get("sources"):
            sources.extend(ctx["sources"])

    if left_ok and right_ok:
        return {
            "answer": "",
            "summary": f"Comparaison : {left} / {right}",
            "sources": sources[:4],
            "results": [],
            "query_type": "comparison",
            "comparison_mode": "both",
            "comparison_left": left_ctx,
            "comparison_right": right_ctx,
            "comparison_question": question,
            "has_structured_data": False,
            "is_city_query": False,
            "is_line_query": False,
            "needs_clarification": False,
            "show_more_info": True,
        }

    found = left_ctx if left_ok else right_ctx
    missing = right if left_ok else left
    return {
        "answer": (
            f"{found['answer']}\n\n"
            f"Par contre, je n'ai pas d'info sur « {missing} »."
        ),
        "summary": found["answer"][:200],
        "sources": found.get("sources", []),
        "results": [],
        "query_type": "comparison",
        "comparison_mode": "partial",
        "comparison_left": left_ctx,
        "comparison_right": right_ctx,
        "comparison_question": question,
        "has_structured_data": False,
        "is_city_query": False,
        "is_line_query": False,
        "needs_clarification": False,
        "show_more_info": True,
        "llm_enhanced": False,
    }


def _city_query_aspect(qn: str, question: str) -> str:
    """Aspect demandé — réponse minimale, sans surcharge."""
    if _TRAVEL_INTENT_RE.search(question or "") or re.search(
        r"\b(voyage|partir|partez|se\s+rendre)\b", qn, re.I
    ):
        return "full"
    if any(w in qn for w in ("reserver", "reservation", "reservez", "billet", "ticket")):
        return "reservation"
    wants_itin = any(w in qn for w in ("itineraire", "trajet", "route", "passage"))
    wants_horaires = any(
        w in qn
        for w in ("horaire", "horaires", "heures", "heure", "depart", "departs")
    )
    wants_duree = any(
        w in qn
        for w in ("duree", "durees", "temps", "combien de temps", "longtemps")
    ) or re.search(r"\bcombi[eè]n\s+(?:de\s+)?temps\b", qn, re.I)
    if wants_horaires and not wants_itin:
        return "horaires"
    if wants_duree and not wants_itin:
        return "duree"
    if any(w in qn for w in ("prix", "tarif", "cout", "combien", "fcfa", "cher", "coute")):
        return "prix"
    if wants_itin:
        return "itineraire_detail"
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


def _format_city_itinerary_detail_prose(
    section: dict,
    ville: str,
    *,
    itineraire: str,
    jours: list[str],
    horaires: list[str],
    depart: str,
    arrivee: str,
    contacts: list[dict],
) -> str:
    """Itinéraire + horaires de départ + durée — sans prix ni réservation."""
    titre_disp = _city_display_name(ville)
    chunks: list[str] = []
    if itineraire:
        chunks.append(f"Itinéraire Dakar–{titre_disp} : {itineraire}.")
    bus_sentence = _format_city_bus_sentence(
        section, ville, jours, horaires, depart, arrivee, contacts,
        include_duration=True,
    )
    if bus_sentence:
        chunks.append(bus_sentence)
    if chunks:
        return " ".join(chunks)
    return (
        f"Détails non disponibles pour {titre_disp}. "
        "Consultez demdikk.sn/reseau-interurbain/ ou le service client au +221 33 824 10 10."
    )


def _format_city_duration_prose(
    section: dict,
    ville: str,
    *,
    horaires: list[str],
    depart: str,
    arrivee: str,
    contacts: list[dict],
) -> str:
    """Durée du trajet uniquement — sans itinéraire ni horaires."""
    titre_disp = _city_display_name(ville)
    horaires_blob = " ".join(horaires or [])
    _, _, times = _extract_interurban_depart_info(
        section, ville, horaires, depart, arrivee, contacts
    )
    meta = _city_route_meta(section, ville)
    dur_txt = format_duration_prose(meta.get("durees") or {}, times)
    if not dur_txt and horaires_blob:
        _, durations = _split_horaires_raw(horaires)
        dur_txt = format_duration_prose(durations, times)
    if dur_txt:
        return f"Le trajet vers {titre_disp} : {dur_txt.rstrip('.')}."
    return (
        f"Durée non disponible pour {titre_disp}. "
        "Consultez demdikk.sn/reseau-interurbain/ ou le service client au +221 33 824 10 10."
    )


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

    if aspect == "presence":
        return (
            f"Oui, nos bus Dakar Dem Dikk vont bien à {titre_disp} "
            f"sur le réseau interurbain."
        )

    if aspect == "reservation":
        return _INTERURBAIN_RESERVATION

    if aspect == "prix":
        if prix_disp:
            return f"Le trajet vers {titre_disp} coûte {prix_disp}."
        return (
            "Tarif non disponible ici. Consultez demdikk.sn/reseau-interurbain/ "
            "ou le service client au +221 33 824 10 10."
        )

    if aspect == "duree":
        return _format_city_duration_prose(
            section,
            ville,
            horaires=horaires_raw,
            depart=depart,
            arrivee=arrivee,
            contacts=contacts,
        )

    if aspect == "itineraire":
        if itineraire:
            return f"Itinéraire Dakar–{titre_disp} : {itineraire}."
        return (
            f"Itinéraire non disponible ici. Consultez demdikk.sn/reseau-interurbain/ "
            f"ou appelez le +221 33 824 10 10."
        )

    if aspect == "itineraire_detail":
        return _format_city_itinerary_detail_prose(
            section,
            ville,
            itineraire=itineraire,
            jours=jours,
            horaires=horaires_raw,
            depart=depart,
            arrivee=arrivee,
            contacts=contacts,
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
    "pub", "emploi", "recrutement", "candidature", "location", "louer", "loue",
    "reservation", "abonnement", "messagerie", "colis",
)

_STANDALONE_SERVICE_RE = re.compile(
    r"\b("
    r"louer|loue|location|louer un bus|louer des bus|"
    r"faire de la pub|publicit[eé]|partenariat|regie publicitaire|"
    r"recruter|recrutement|emploi|candidature|"
    r"messagerie|expedier|exp[eé]dier|courrier|colis|"
    r"abonnement|objet perdu|carte perdue|carte volee|"
    r"aibd|aeroport|navette|directeur|presentation|historique"
    r")\b",
    re.I,
)


def _is_standalone_service_question(qn: str, question: str = "") -> bool:
    """Nouveau sujet (service DDD) — ne pas coller à une ville/ligne de l'historique."""
    qn = (qn or _norm(question)).strip()
    if not qn:
        return False
    if any(k in qn for k in _SKIP_IMPLICIT_STOP_KEYWORDS):
        return True
    return bool(_STANDALONE_SERVICE_RE.search(qn))


def _try_ddd_service_fallback(question: str, q_norm: str) -> dict | None:
    """Publicité, location, messagerie… via app.py (chatbot-2303)."""
    if not _is_standalone_service_question(q_norm, question):
        return None
    try:
        import sys as _sys
        _app_mod = _sys.modules.get("app")
        if not _app_mod:
            return None
        _fb = getattr(_app_mod, "_fallback_publicite_partenariat", None)
        if _fb and getattr(_app_mod, "_is_publicite_query", lambda q: False)(q_norm):
            com = _fb(question)
            if com and com.get("answer"):
                return com
        _fb = getattr(_app_mod, "_fallback_from_site", None)
        if _fb:
            com = _fb(question)
            ans = (com.get("answer") or "").lower() if com else ""
            junk = ("agent-ia", "guide complet des services", "je n'ai pas trouv")
            if com and com.get("answer") and not any(j in ans for j in junk):
                return com
    except Exception:
        pass
    return None


def _json_service_payload(question: str, q_norm: str) -> dict | None:
    colis_matched = None
    if _is_colis_service_query(q_norm):
        colis_matched = next(t for t in _COLIS_TRIGGERS if t in q_norm)
        if _colis_has_specific_intent(q_norm):
            specific, reason = _try_colis_specific_answer(question, q_norm)
            if specific:
                _debug_fixed_trigger(
                    "colis", f"keyword={colis_matched!r} specific=yes source={reason}"
                )
                return {
                    "answer": specific["answer"],
                    "summary": specific.get("summary", "Service messagerie")[:200],
                    "sources": specific.get("sources", []),
                    "results": specific.get("results", []),
                    "query_type": "general",
                    "has_structured_data": False,
                    "is_city_query": False,
                    "is_line_query": False,
                    "needs_clarification": False,
                    "show_more_info": True,
                }
    com = _try_ddd_service_fallback(question, q_norm)
    if not com or not com.get("answer"):
        return None
    if colis_matched:
        _debug_fixed_trigger("colis", f"keyword={colis_matched!r} specific=no source=fixe")
    summary = com.get("summary", "Service Dakar Dem Dikk")[:200]
    return {
        "answer": com["answer"],
        "summary": summary,
        "sources": com.get("sources", []),
        "results": com.get("results", []),
        "query_type": "general",
        "has_structured_data": False,
        "is_city_query": False,
        "is_line_query": False,
        "needs_clarification": False,
        "show_more_info": True,
    }


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


def _is_company_presentation_query(qn: str, question: str = "") -> bool:
    """Présentation, mission, histoire de Dakar Dem Dikk."""
    try:
        import sys as _sys
        _app_mod = _sys.modules.get("app")
        _fn = getattr(_app_mod, "_is_presentation_query", None) if _app_mod else None
        if _fn and _fn(question, qn):
            return True
    except Exception:
        pass
    qn = (qn or "").strip()
    if qn in _COMPANY_NAME_TOKENS:
        return True
    markers = (
        "presentation", "présentation", "mission", "vision", "valeurs",
        "objectif", "histoire", "creation", "création",
        "c est quoi ddd", "qu est ce que ddd", "parle moi de dem dikk",
    )
    return any(m in qn for m in markers)


def _enrich_short_question_from_history(question: str, history_raw) -> str:
    """
    Résolution de contexte : question courte (mots significatifs < 3) +
    historique avec ville ou ligne récente → enrichir avant city_info / line_X / RAG.
    """
    q = (question or "").strip()
    if not q:
        return q
    qn_check = _norm(q)
    # Nouveau sujet (location, pub, colis…) → ne jamais enrichir avec l'historique
    if _is_standalone_service_question(qn_check, q):
        return q
    # Événement / actualité (Magal, Tabaski…) → sujet FAQ autonome, pas la ville précédente
    if _detect_event_intent(qn_check):
        return q
    # Nom de la société seul → jamais enrichi comme suite de conversation
    if qn_check in _COMPANY_NAME_TOKENS or all(
        t in _COMPANY_NAME_TOKENS or t in _ENRICH_STOPWORDS
        for t in qn_check.split()
    ):
        return q
    # Compter uniquement les mots porteurs de sens (hors stopwords)
    qn_words = qn_check.split()
    meaningful = [w for w in qn_words if len(w) >= 2 and w not in _ENRICH_STOPWORDS]
    if _is_smalltalk_question(q):
        return q
    entries = _parse_history_entries(history_raw)
    if not entries:
        if len(meaningful) >= 4:
            return q
        return q
    # Déjà explicite : ne pas dupliquer
    if _detect_city(_norm(q)) or _detect_line_number(q):
        return q

    city_sec = _history_last_city_section(entries)
    line_num = _history_last_line_number(entries)

    _CITY_DETAIL_FOLLOWUP_RE = re.compile(
        r"\b(itineraire|trajet|horaire|heures?|depart|departs|duree|durees|"
        r"tarif|prix|reserv|billet|contact|arrivee|passage|route)\b",
        re.I,
    )
    if len(meaningful) >= 4:
        if not (city_sec and _CITY_DETAIL_FOLLOWUP_RE.search(qn_check)):
            return q
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
        r"|\b(itineraire|trajet|duree|durees|passage)\b"  # détail trajet
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
        if _is_standalone_service_question(qn, q):
            return q
        priceish = any(w in qn for w in (
            "prix", "tarif", "cout", "fcfa", "coute", "combien", "cher", "paye",
            "horaire", "heure", "heures", "depart", "billet", "ticket", "reservation",
        ))
        lineish = any(w in qn for w in (
            "ligne", "arret", "station", "terminus", "dessert", "desservent",
        )) or (re.search(r"\bbus\b", qn) and not re.search(r"\blouer\b|\bloue\b|\blocation\b", qn))
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
    question = normalize_query_typos(_expand_query_acronyms(question_resolved.strip()))
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

    # Comparaisons X vs Y — avant routes structurées
    _cmp_parts = _detect_comparison_query(question)
    if _cmp_parts:
        _cmp_payload = _json_comparison_payload(question, _cmp_parts[0], _cmp_parts[1])
        if _cmp_payload:
            return jsonify(_cmp_payload)

    if _is_company_presentation_query(q_norm, question):
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

    # « Que signifie SDD / DDD / ADD ? » — définition du sigle (pas liste destinations)
    _acr_payload = _json_acronym_definition(question, q_norm)
    if _acr_payload:
        return jsonify(_acr_payload)

    # Réseau interurbain (vue d'ensemble) — réponse courte : intro + destinations
    _overview_payload = _json_interurban_overview(question, q_norm)
    if _overview_payload:
        return jsonify(_overview_payload)

    # Services DDD (location, pub, colis…) — avant ville si nouveau sujet explicite
    _service_payload = _json_service_payload(question, q_norm)
    if _service_payload:
        return jsonify(_service_payload)

    # Événement / actualité sur question originale — FAQ avant city_info
    _event_faq_payload = _try_event_faq_before_city_info(question_raw, q_norm, city_hint)
    if _event_faq_payload:
        return jsonify(_event_faq_payload)

    # Ville interurbaine — sauf si intention voyage explicite ou ville seule
    _city_payload = _json_interurban_city(question, q_norm, city_hint)
    if _city_payload:
        # Ne pas répondre « ville » si la question est un autre service DDD
        if not _is_standalone_service_question(q_norm, question_raw):
            return jsonify(_city_payload)

    # Services DDD (réservation liée à une ville déjà dans la question enrichie)
    if any(k in q_norm for k in _SKIP_IMPLICIT_STOP_KEYWORDS):
        _service_payload = _json_service_payload(question, q_norm)
        if _service_payload:
            return jsonify(_service_payload)

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
            r'emploi|recrutement|location|reservation|abonnement|'
            r'mission|presentation|présentation|vision|valeurs|objectif|histoire|creation)\b',
            re.IGNORECASE,
        )
        if (
            not re.search(_STOP_SKIP_RE, question)
            and not _should_skip_implicit_stop_inference(q_norm)
            and not _is_company_presentation_query(q_norm, question)
        ):
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
