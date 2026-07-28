"""
Dakar Dem Dikk Chatbot — Application Flask principale.
Charge app_backup (ou .pyc) et enrichit les réponses (DeepSeek, fallbacks, etc.).

Démarrage WSGI : Gunicorn doit cibler ce fichier, ex. :
  gunicorn --chdir /var/www/dakar_dem_dikk_chatbot app:app
et non « app_backup:app » (sinon ce module n'est jamais exécuté).
"""
import importlib.util
import sys
import os
import re
import json
import glob
import time
import functools
import subprocess as _subprocess
from flask import request, jsonify

# Charger les variables d'environnement depuis .env (si présent)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Chargement du module applicatif ─────────────────────────────────────────
# Toujours charger `app_backup.py` quand il est présent : un `app_backup.cpython-*.pyc`
# peut rester « à jour » en date tout en étant obsolète après édition du .py ailleurs,
# ou masquer des correctifs (ex. arrêt « Sandaga » seul).
_root_dir = os.path.dirname(os.path.abspath(__file__))
_pyc_candidates = sorted(
    glob.glob(os.path.join(_root_dir, "__pycache__", "app_backup.cpython-*.pyc")),
    key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0.0,
    reverse=True,
)
_pyc_path = _pyc_candidates[0] if _pyc_candidates else ""
_py_path = os.path.join(_root_dir, "app_backup.py")


def _pick_app_impl_path() -> str:
    if os.path.isfile(_py_path):
        return _py_path
    if _pyc_path and os.path.isfile(_pyc_path):
        return _pyc_path
    return ""


_module_path = _pick_app_impl_path()
if not _module_path:
    raise FileNotFoundError(
        "Module applicatif introuvable.\n"
        f"- Cherché : {_pyc_path}\n"
        f"- Ou : {_py_path}\n"
        "Assurez-vous que app_backup.py est présent (recommandé en production)."
    )

# Enregistrer le backup sous un nom dédié — ne JAMAIS faire sys.modules['app'] = _mod :
# cela remplacerait le module « app.py » dans le cache d'import et ferait pointer
# `import app` vers app_backup (sans CORS, sans enveloppe /ask, sans _strip_nav_content, etc.).
_IMPL_MODULE_NAME = "app_flask_impl"
_spec = importlib.util.spec_from_file_location(_IMPL_MODULE_NAME, _module_path)
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_IMPL_MODULE_NAME] = _mod
_spec.loader.exec_module(_mod)

# Objet Flask principal (exposé pour gunicorn : gunicorn app:app)
app = _mod.app

# ── CORS — autorise les requêtes depuis toute origine (XAMPP, fichier local, etc.) ──
try:
    from flask_cors import CORS
    CORS(app, resources={r"/*": {"origins": "*"}},
         allow_headers=["Content-Type", "Authorization"],
         methods=["GET", "POST", "OPTIONS"])
except ImportError:
    pass

# ── Reformulation via DeepSeek (OpenAI-compatible) ───────────────────────────
_deepseek_cfg = None

# Bloc de contact complet affiché quand l'information n'est pas trouvée
_CONTACT_BLOCK = (
    "Je n'ai pas trouvé cette information.\n"
    "Vous pouvez contacter notre service client directement :\n"
    "– Téléphone : +221 33 824 10 10 / +221 33 865 15 55\n"
    "– Email : info@demdikk.sn / contact@demdikk.sn\n"
    "– Adresse : Km 4,5 Avenue Cheikh Anta Diop, dépôt Ouakam, Dakar\n"
    "– Horaires : Lundi – Vendredi, 08h – 17h\n"
)

_ADDRESS_BLOCK = (
    "Le siège de Dakar Dem Dikk est situé au :\n"
    "– Km 4,5 Avenue Cheikh Anta Diop, dépôt Ouakam, Dakar\n\n"
    "Contacts :\n"
    "– Téléphone : +221 33 824 10 10 / +221 33 865 15 55\n"
    "– Email : info@demdikk.sn\n"
    "– Horaires agence : Lundi – Vendredi, 08h – 17h"
)

# Mots-clés indiquant une question hors du périmètre DDD
# SOFT : hors-sujet seulement s'il n'y a aucun contexte transport DDD
_OFF_TOPIC_SOFT = frozenset([
    "meteo", "weather", "temperature", "pluie", "soleil", "intemperie",
    "politique", "president", "gouvernement", "election",
    "football", "sport", "match",
    "can", "caf", "copa", "mondial", "champions", "ligue", "nba", "rugby", "tennis",
    "cinema", "film", "serie", "musique",
    "restaurant", "hotel", "tourisme",
    "sante", "medecin", "hopital", "pharmacie",
    "recette", "cuisine",
])
# HARD : hors-sujet même avec un mot transport faible (noms propres, crypto…)
_OFF_TOPIC_HARD = frozenset([
    "macky", "sall", "sonko", "wade",
    "barca", "barcelona", "barcelone",
    "messi", "ronaldo", "psg", "om", "ol", "liverpool", "chelsea", "arsenal",
    "bitcoin", "crypto", "bourse", "finance",
])
# Compat : union pour les grep / usages legacy
_OFF_TOPIC_WORDS = _OFF_TOPIC_SOFT | _OFF_TOPIC_HARD

_TRANSPORT_CONTEXT_MARKERS = (
    "bus", "ligne", "lignes", "arret", "arrets", "transport", "voyage",
    "dem dikk", "demdikk", "ddd", "dakar dem dikk",
    "reservation", "reserver", "billet", "ticket", "abonnement", "tek dem", "tekdem",
    "carte", "colis", "horaire", "horaires", "tarif", "tarifs", "prix",
    "contact", "agence", "agences", "assistance",
    "interurbain", "touba", "thies", "thiès", "saint-louis", "fatick", "kaolack",
    "bagage", "bagages", "remboursement", "annulation", "report",
    "gare", "terminus", "destination", "navette", "aibd", "aeroport",
    "depart", "departs", "trajet", "itineraire", "horaire",
    "publicite", "partenariat", "messagerie", "location", "dem dikk",
    "parcelles", "petersen", "ouakam", "colobane", "liberte", "ddd",
    "valideur", "conducteur", "receveur", "passager", "voyageur",
    "perturbation", "retard", "greve", "incident",
)


def _has_transport_context(qn: str) -> bool:
    """True si la question évoque clairement DDD / transport (évite faux hors-sujet)."""
    if not qn:
        return False
    if any(k in qn for k in _TRANSPORT_CONTEXT_MARKERS):
        return True
    # « arrêt X », « ligne 10 », « bus 12 »
    if re.search(r"\b(ligne|bus|arret|arrets|gare|terminus)\s+\w", qn):
        return True
    if re.search(r"\b(ligne|bus)\s*\d", qn):
        return True
    return False


def _is_strict_off_topic(question: str, qn: str | None = None) -> bool:
    """
    Hors-sujet métier (sport pur, crypto…) — pas les salutations (gérées avant).
    Les mots ambigus (restaurant, match, météo…) ne bloquent que sans contexte transport.
    """
    qn = qn if qn is not None else _norm(question)
    if not qn:
        return False
    if _conversational_kind(question, qn):
        return False
    if _question_looks_gibberish_normed(qn):
        return True
    words = set(qn.split())
    if words & _OFF_TOPIC_HARD:
        return True
    if (words & _OFF_TOPIC_SOFT) and not _has_transport_context(qn):
        return True
    return False


def _typo_vocab() -> tuple[str, ...]:
    """Mots fréquents DDD pour détecter les fautes de frappe (cache module)."""
    words: set[str] = set()
    for marker in _TRANSPORT_CONTEXT_MARKERS:
        words.update(w for w in marker.split() if len(w) >= 3)
    words.update({
        "bonjour", "bonsoir", "salut", "coucou", "hello", "merci", "comment",
        "vas", "bien", "allez", "vous", "ca", "revoir", "bientot", "genial", "aller",
        "remboursement", "annulation", "abonnement", "bagage", "bagages", "colis",
        "messagerie", "publicite", "partenariat", "recrutement", "horaire", "horaires",
        "tarif", "tarifs", "billet", "billets", "reservation", "rechargement", "carte",
        "tek", "dem", "dikk", "touba", "thies", "saint", "louis", "fatick", "kaolack",
        "petersen", "colobane", "ouakam", "liberte", "parcelles", "aibd", "aeroport",
        "enfant", "location", "perturbation", "greve", "retard", "objet", "perdu",
        "application", "appli", "navette", "gare", "terminus", "arret", "arrets",
        "ligne", "lignes", "bus", "interurbain", "contact", "assistance", "prix",
        "modifier", "report", "annuler", "annule", "tekdem", "demdikk", "presenter",
        "identite", "qui", "es", "tu", "peux", "faire", "aider", "comment",
    })
    return tuple(sorted(words))


def _find_typo_corrections(qn: str) -> list[tuple[str, str]]:
    """Retourne [(mot_fautif, mot_corrigé), …] via proximité orthographique."""
    import difflib

    vocab = _typo_vocab()
    vocab_set = set(vocab)
    corrections: list[tuple[str, str]] = []
    for w in (qn or "").split():
        if len(w) < 4 or w in vocab_set:
            continue
        cutoff = 0.80 if len(w) <= 5 else 0.86
        matches = difflib.get_close_matches(w, vocab, n=1, cutoff=cutoff)
        if matches and matches[0] != w:
            corrections.append((w, matches[0]))
    return corrections


def _apply_typo_corrections_qn(qn: str, corrections: list[tuple[str, str]]) -> str:
    out = qn
    for wrong, right in corrections:
        out = re.sub(r"\b" + re.escape(wrong) + r"\b", right, out)
    return out


def _rebuild_question_with_corrections(question: str, corrections: list[tuple[str, str]]) -> str:
    out = question or ""
    for wrong, right in corrections:
        out = re.sub(r"\b" + re.escape(wrong) + r"\b", right, out, flags=re.IGNORECASE)
    return out.strip()


def _format_typo_notice(corrections: list[tuple[str, str]]) -> str:
    if not corrections:
        return ""
    if len(corrections) == 1:
        wrong, right = corrections[0]
        return f"Je pense que vous vouliez écrire « {right} » plutôt que « {wrong} ». "
    parts = ", ".join(f"« {r} »" for _, r in corrections)
    return f"Je corrige une petite faute de frappe ({parts}). "


def _should_apply_typo_fix(
    question: str,
    qn: str,
    fixed_q: str,
    fixed_qn: str,
    corrections: list[tuple[str, str]],
) -> bool:
    if not corrections or fixed_qn == qn:
        return False
    vocab_set = set(_typo_vocab())
    if all(right in vocab_set for _, right in corrections):
        return True
    if _conversational_kind(fixed_q, fixed_qn) and not _conversational_kind(question, qn):
        return True
    if _is_strict_off_topic(question, qn) and not _is_strict_off_topic(fixed_q, fixed_qn):
        return True
    service_words = {
        "remboursement", "annulation", "abonnement", "bagage", "horaire", "horaires", "tarif",
        "billet", "reservation", "rechargement", "colis", "messagerie", "publicite",
        "recrutement", "location", "contact", "tekdem", "interurbain", "navette",
    }
    if (set(fixed_qn.split()) & service_words) and not (set(qn.split()) & service_words):
        return True
    return False


def _try_typo_recovery(question: str, qn: str | None = None) -> tuple[str, str, list[tuple[str, str]]] | None:
    qn = qn if qn is not None else _norm(question)
    corrections = _find_typo_corrections(qn)
    if not corrections:
        return None
    fixed_qn = _apply_typo_corrections_qn(qn, corrections)
    if fixed_qn == qn:
        return None
    fixed_q = _rebuild_question_with_corrections(question, corrections)
    return fixed_q, fixed_qn, corrections


def _with_typo_notice(data: dict, notice: str) -> dict:
    if not notice or not data.get("answer"):
        return data
    out = dict(data)
    if not (out["answer"] or "").startswith(notice.strip()[:20]):
        out["answer"] = notice + out["answer"]
    return out


_VOWEL_IN_TOKEN_RE = re.compile(r"[aeiouyàâäéèêëïîôùûüÿœæ]")


def _token_is_consonant_gibberish(tok: str) -> bool:
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

_OFF_TOPIC_REPLY = (
   "En tant qu'assistant de Dakar Dem Dikk, je suis là pour vous accompagner sur tout ce qui concerne nos services😊.\n"
"Je ne suis malheureusement pas en mesure de répondre à cette question."
)

# Réponses de secours si DeepSeek indisponible (API absente / erreur)
_CONVERSATIONAL_FALLBACK = {
    "greeting": (
        "Bonjour ! Je suis Maï, l'assistante de Dakar Dem Dikk 😊 "
        "Dis-moi ce que tu veux savoir sur nos lignes, horaires ou voyages."
    ),
    "thanks": "Avec plaisir ! Je suis là si tu as d'autres questions sur le transport DDD.",
    "identity": (
        "Je m'appelle Maï, l'assistante virtuelle de Dakar Dem Dikk. "
        "Je peux t'aider pour le réseau urbain, l'interurbain, les tarifs, réservations et plus encore."
    ),
    "personal": (
        "Enchanté ! Moi c'est Maï, l'assistante de Dakar Dem Dikk — "
        "comment puis-je t'aider pour tes déplacements ?"
    ),
}

_CONVERSATIONAL_SYSTEM = (
    "Tu es Maï, l'assistante virtuelle chaleureuse de Dakar Dem Dikk (DDD), "
    "société de transport à Dakar et au Sénégal.\n\n"
    "MISSION\n"
    "Répondre avec naturel et sympathie à une salutation, un remerciement, "
    "une question sur ton identité, ou une présentation personnelle de l'utilisateur.\n\n"
    "STYLE\n"
    "– Humaine, bienveillante, concise (1 à 3 phrases).\n"
    "– Adapte le tutoiement ou le vouvoiement à l'utilisateur.\n"
    "– Jamais « En tant qu'assistant », jamais de listes longues.\n"
    "– Pas de markdown (##, **). Un emoji discret max (😊).\n"
    "– Salutation : accueille chaleureusement et propose ton aide transport.\n"
    "– Merci : réponds avec gentillesse, sans « je reste à votre disposition ».\n"
    "– Identité : tu es Maï ; tu aides sur bus, horaires, tarifs, voyages, abonnements…\n"
    "– Présentation perso (« je m'appelle… ») : accueille et oriente vers l'aide transport.\n"
)


def _conversational_kind(question: str, qn: str | None = None) -> str | None:
    """Salutation, remerciement, identité Maï, présentation perso — pas hors-sujet."""
    qn = qn if qn is not None else _norm((question or "").strip())
    if not qn:
        return None
    if qn in ("mai", "mai dem dikk") or re.fullmatch(r"mai(\s+dem\s+dikk)?", qn):
        return "identity"
    if re.search(
        r"(comment\s+tu\s+t[\u2019']?\s*appelles?|quel\s+(est\s+)?ton\s+nom|"
        r"tu\s+es\s+qui|qui\s+es\s+tu|c[\u2019']est\s+quoi\s+ton\s+nom|"
        r"qui\s+etes\s+vous|presente\s+toi|que\s+peux\s+tu\s+faire|"
        r"comment\s+peux\s+tu\s+m[\u2019']?aider)",
        qn,
    ):
        return "identity"
    if re.search(
        r"^("
        r"bonjour|bonsoir|salut|coucou|hello|hi|"
        r"tu\s+vas(\s+bien)?|comment\s+(tu\s+)?vas|comment\s+ca\s+va|ca\s+va|"
        r"tout\s+va(\s+bien)?|"
        r"comment\s+allez[-\s]?vous|vous\s+allez\s+bien|"
        r"bye|au\s+revoir|a\s+bientot"
        r")[\s?!.,;:]*$",
        qn,
    ):
        return "greeting"
    if re.search(
        r"^("
        r"(ok\s+)?merci(\s+(beaucoup|bien|infiniment|mille\s+fois))?|"
        r"(merci\s+)?super(\s+merci)?|(merci\s+)?parfait(\s+merci)?|"
        r"(ok\s+)?(c[\u2019']est\s+bon|cest\s+bon|ca\s+va|c\s+est\s+bon)(\s+merci)?|"
        r"ok(\s+merci)?(\s+bien)?|d[\u2019']accord(\s+merci)?|genial"
        r")[\s?!.,;:]*$",
        qn,
    ):
        return "thanks"
    if re.search(r"(je\s+m[\u2019']appelle|mon\s+nom\s+est)", qn):
        return "personal"
    return None


def _deepseek_simple_chat(
    system: str,
    user: str,
    *,
    max_tokens: int = 220,
    temperature: float = 0.75,
) -> str:
    cfg = _init_deepseek()
    if cfg is None:
        return ""
    try:
        import requests as _requests
        r = _requests.post(
            f"{cfg['base_url']}/chat/completions",
            headers={
                "Authorization": f"Bearer {cfg['api_key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": cfg["model"],
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=min(cfg["timeout_s"], 12),
        )
        if r.status_code >= 400:
            return ""
        payload = r.json() or {}
        choices = payload.get("choices") or []
        if choices:
            return ((choices[0] or {}).get("message") or {}).get("content") or ""
    except Exception:
        pass
    return ""


def _generate_friendly_reply(question: str, client_history: list | None, kind: str) -> dict:
    """Réponse conversationnelle naturelle (salutation, merci, identité…)."""
    history_block = _format_client_history_for_prompt(client_history or [])
    hints = {
        "greeting": "L'utilisateur te salue. Accueille-le chaleureusement.",
        "thanks": "L'utilisateur te remercie ou confirme. Réponds avec bienveillance.",
        "identity": "L'utilisateur veut savoir qui tu es ou ce que tu peux faire.",
        "personal": "L'utilisateur se présente. Accueille-le et propose ton aide transport.",
    }
    user_prompt = hints.get(kind, "")
    if history_block:
        user_prompt = f"{history_block}\n\n{user_prompt}\n\nMessage : {question}"
    else:
        user_prompt = f"{user_prompt}\n\nMessage : {question}"

    text = _deepseek_simple_chat(_CONVERSATIONAL_SYSTEM, user_prompt.strip())
    if not text or len(text.strip()) < 4:
        text = _CONVERSATIONAL_FALLBACK.get(kind, _CONVERSATIONAL_FALLBACK["greeting"])

    return {
        "answer": text.strip(),
        "summary": text.strip()[:200],
        "bullets": [],
        "sources": [{"title": "Maï — Assistant Dakar Dem Dikk", "url": "https://demdikk.sn/", "score": 1.0}],
        "results": [],
        "query_type": kind,
        "needs_clarification": False,
        "has_structured_data": False,
        "is_city_query": False,
        "is_line_query": False,
        "show_more_info": False,
        "llm_provider": "deepseek",
        "llm_enhanced": True,
    }


_LOG_FILE       = os.path.join(_root_dir, "unknown_queries.log")
_UQ_DATA_DIR    = os.path.join(_root_dir, "data")
_UQ_JSON_FILE   = os.path.join(_UQ_DATA_DIR, "unknown_queries.json")
_uq_lock        = __import__("threading").Lock()


def _uq_load() -> dict:
    """Charge unknown_queries.json ; retourne la structure vide si absent/corrompu."""
    try:
        if os.path.exists(_UQ_JSON_FILE):
            with open(_UQ_JSON_FILE, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"queries": {}}


def _uq_save(data: dict) -> None:
    os.makedirs(_UQ_DATA_DIR, exist_ok=True)
    tmp = _UQ_JSON_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _UQ_JSON_FILE)


_UQ_STOPWORDS = frozenset({
    # articles / prépositions / pronoms
    "le","la","les","de","du","des","un","une","au","aux","en","et","est",
    "que","qui","quoi","sur","par","pour","dans","avec","sans","vers","chez",
    "je","il","elle","vous","nous","on","me","se","ce","si","ne","pas","plus",
    # interrogatifs courants
    "comment","quand","pourquoi","combien","quel","quelle","quels","quelles",
    "ou","sont","avoir","etre",
    # formules de politesse / fillers
    "svp","stp","merci","bonjour","bonsoir","salut","ok","oui","non","voila",
    "voudrais","veux","puis","peut","pouvez","faire","aller","savoir",
    "dire","faut","besoin","aider","aide","souhait","souhaite",
})


def _uq_significant_words(question: str) -> tuple:
    """
    Extrait les mots « porteurs de sens » d'une question normalisée,
    triés alphabétiquement — insensible à l'ordre des mots.
    """
    n = _norm(question)
    words = [w for w in n.split()
             if len(w) >= 3 and w not in _UQ_STOPWORDS]
    return tuple(sorted(set(words)))


def _uq_key(question: str) -> str:
    """
    Clé de dédoublonnage basée sur les mots significatifs triés.
    """
    sig = _uq_significant_words(question)
    return " ".join(sig) if sig else _norm(question)[:80]


def _log_unknown_query(question: str, reason: str = "not_found") -> None:
    """
    Enregistre les requêtes sans réponse :
      - unknown_queries.log  (texte brut, filet de sécurité)
      - data/unknown_queries.json  (structuré, dédoublonné, via /admin/unknown-queries)
    """
    import datetime, hashlib
    q = (question or "").strip()
    if not q:
        return
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # ── log texte brut ──────────────────────────────────────────────
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] [{reason}] {q}\n")
    except Exception:
        pass
    # ── JSON structuré avec dédoublonnage ───────────────────────────
    try:
        key = _uq_key(q)
        uid = hashlib.sha1(key.encode()).hexdigest()[:10]
        with _uq_lock:
            data = _uq_load()
            queries = data.setdefault("queries", {})
            if uid in queries:
                queries[uid]["count"]    += 1
                queries[uid]["last_seen"] = ts
                queries[uid]["question"]  = q
            else:
                queries[uid] = {
                    "id":           uid,
                    "question":     q,
                    "reason":       reason,
                    "first_seen":   ts,
                    "last_seen":    ts,
                    "count":        1,
                    "handled":      False,
                    "note":         "",
                    # ── Champs de résolution ──────────────────────────
                    "status":       "en_attente",  # en_attente | repondu | redirige
                    "reponse_text": "",
                    "page_cible_id":  None,
                    "page_cible_url": "",
                }
            _uq_save(data)
    except Exception:
        pass

_LLM_SYSTEM = (
    "Tu es Maï, l'assistante virtuelle du service client de Dakar Dem Dikk (DDD), "
    "la société de transport en commun de Dakar. "
    "Tu t'exprimes comme une conseillère humaine : chaleureuse, directe et professionnelle.\n\n"

    "IDENTITÉ\n"
    "– Tu t'appelles Maï. Présente-toi ainsi si on te le demande.\n\n"

    "TON ET STYLE\n"
    "– Réponds naturellement, sans préambule ni formule robotique.\n"
    "– Jamais : « D'après... », « Selon... », « En tant qu'assistant... », « Les informations indiquent... ».\n"
    "– Si la question est simple → réponse courte et directe.\n"
    "– Si la question est complexe → réponse détaillée, structurée seulement si vraiment utile.\n"
    "– N'utilise JAMAIS de balises markdown (##, ###, **). Utilise des tirets (–) pour les listes.\n"
    "– Ne termine JAMAIS par « N'hésitez pas à me demander », « Je reste à votre disposition » "
    "ou toute formule de politesse de clôture du même type.\n\n"

    "REFORMULATION (rôle principal)\n"
    "– On te fournit un extrait officiel du site Dakar Dem Dikk. Reformule-le en français clair "
    "et naturel, comme le dirait Maï au téléphone.\n"
    "– Intègre les titres de section dans des phrases complètes ; ne renvoie jamais un titre seul.\n"
    "– Garde TOUS les faits du contexte : chiffres, délais, adresses e-mail, numéros, conditions.\n"
    "– Tu peux simplifier la forme, pas le fond : même sens, mêmes données, zéro ajout.\n\n"

    "PRÉCISION ET CLARIFICATION\n"
    "– Si la question est ambiguë ou manque d'un détail essentiel (ex : destination, ligne), "
    "pose UNE SEULE question de précision, courte et naturelle. Exemples :\n"
    "  • « le prix du billet » → réponds : « Pour quelle destination ? »\n"
    "  • « les horaires » → réponds : « Pour quelle ligne ou destination ? »\n"
    "– Ne pose jamais plusieurs questions à la fois.\n\n"

    "CONTEXTE CONVERSATIONNEL\n"
    "– L'historique de la conversation précède les informations. Utilise-le pour comprendre les références "
    "implicites (« et le prix ? », « d'où ça part ? », « c'est tous les jours ? »).\n"
    "– Ne redemande jamais une information déjà donnée dans la conversation.\n\n"

    "PRÉSENTATION DE LA SOCIÉTÉ\n"
    "– Si l'utilisateur demande ce qu'est Dakar Dem Dikk, la présentation, l'histoire ou mentionne "
    "simplement « Dakar Dem Dikk » / « DDD », utilise le contexte fourni pour présenter la société "
    "(création, mission, réseau, services). Ne traite JAMAIS cela comme une question hors-sujet.\n\n"

    "QUESTIONS HORS-SUJET (sans lien avec DDD ou le transport)\n"
    "– Si la question n'a aucun lien avec Dakar Dem Dikk, le transport, les bus ou les voyages "
    "(ex :  texte aléatoire, sujets généraux), réponds simplement :\n"
    "  « En tant qu'assistant de Dakar Dem Dikk, je suis là pour vous accompagner sur tout ce qui concerne nos services😊. "
    "Je ne suis malheureusement pas en mesure de répondre à cette question. »\n"
    "– Ne mentionne JAMAIS le service client, ni « je n'ai pas cette information » "
    "pour ce type de question.\n\n"

    "NE JAMAIS INVENTER — RÈGLE ABSOLUE\n"
    "– Utilise UNIQUEMENT les informations présentes dans le contexte fourni.\n"
    "– N'invente JAMAIS de données : prix, horaires, destinations, noms, conditions, "
    "même si elles semblent logiques ou probables.\n"
    "– Si le contexte indique une FOURCHETTE de prix (ex : « entre 150 et 350 FCFA »), "
    "restitue TOUJOURS la fourchette complète. Ne choisis JAMAIS une seule valeur dans la fourchette.\n"
    "  ✗ « Le ticket est à 350 FCFA »  →  ✓ « Le ticket est entre 150 et 350 FCFA selon le trajet. »\n"
    "  ✗ « Le ticket coûte 150 FCFA »  →  ✓ « Le ticket varie entre 150 et 350 FCFA selon le trajet. »\n"
    "– Si la question concerne DDD mais que l'information n'est PAS dans le contexte, "
    "ne dis JAMAIS que tu n'as pas le détail, que tes informations sont incomplètes, "
    "ou que tu ne disposes pas de l'info (« je n'ai pas le détail… », « dans mes informations… », etc.). "
    "Redirige directement vers le service client, par exemple :\n"
    "  « Pour [reformuler brièvement la demande], je vous invite à contacter "
    "notre service client au +221 33 824 10 10. »\n"
    "– Si tu as déjà donné des informations utiles (tarifs, conditions, contacts), "
    "ne termine JAMAIS par une phrase du type « car je n'ai pas le détail des démarches », "
    "« je n'ai pas les démarches à suivre » ou toute variante de ce genre.\n\n"

    "TOUJOURS DES PHRASES COMPLÈTES\n"
    "– Ne réponds JAMAIS avec un mot seul, un chiffre seul, une liste sèche ou un fragment.\n"
    "– Chaque réponse doit être rédigée en phrases grammaticalement complètes.\n"
    "– Même pour une information simple :\n"
    "  ✗ « 5000 FCFA »  →  ✓ « Le tarif pour Saint-Louis est de 5 000 FCFA. »\n"
    "  ✗ « Tous les jours »  →  ✓ « Les bus partent tous les jours pour cette destination. »\n\n"

    "LANGUE ET FORMAT\n"
    "– Réponds toujours en français.\n"
    "– N'utilise jamais de balises markdown (##, ###, **).\n"
    "– Utilise des tirets (–) pour les listes, seulement si plusieurs éléments distincts."
)

_COMPARISON_LLM_SYSTEM = (
    "Tu es Maï, conseillère Dakar Dem Dikk. Tu réponds au téléphone, en français naturel.\n\n"
    "RÈGLE ABSOLUE — CONTENU\n"
    "– Réponds UNIQUEMENT à ce que l'utilisateur demande (prix, durée, horaires…).\n"
    "– Ne mentionne JAMAIS ce qui manque, ce qui n'est pas indiqué, ou ce que tu ignores.\n"
    "– Ne dis JAMAIS « bloc », « les deux blocs », « informations fournies », "
    "« dans mes données », « par conséquent sur la base des seules informations ».\n"
    "– Si les deux éléments sont identiques sur le point demandé, dis-le simplement "
    "(ex. « Touba et Kaolack coûtent tous les deux 4 000 FCFA depuis Dakar »).\n"
    "– Si une différence existe, énonce-la clairement en une ou deux phrases fluides.\n"
    "– N'ajoute pas horaires, durée ou réservation si la question ne porte que sur le prix.\n"
    "– N'invente rien : uniquement les faits présents dans les deux textes de référence.\n\n"
    "STYLE\n"
    "– Pas de markdown. Pas de listes sèches. Pas de formule de clôture creuse.\n"
    "– Ton direct et humain, comme une conseillère qui connaît son métier."
)

_COMPARISON_ANTI_PATTERNS = (
    re.compile(
        r"Aucune information[^.!?]*[.!?]\s*",
        re.I,
    ),
    re.compile(
        r"[^.!?]*\bles deux blocs\b[^.!?]*[.!?]\s*",
        re.I,
    ),
    re.compile(
        r"Par conséquent,[^.!?]*[.!?]\s*",
        re.I,
    ),
    re.compile(
        r"[^.!?]*n['\u2019](?:est|a)\s+(?:pas\s+)?(?:fournie|indiqu[ée]|mentionn[ée])[^.!?]*[.!?]\s*",
        re.I,
    ),
    re.compile(
        r"Les deux blocs[^.!?]*[.!?]\s*",
        re.I,
    ),
    re.compile(
        r"[^.!?]*informations (?:données|fournies)[^.!?]*[.!?]\s*",
        re.I,
    ),
    re.compile(
        r"[^.!?]*(?:durée|horaires?|services?)[^.!?]*(?:n['\u2019](?:est|a)\s+(?:pas\s+)?(?:fournie|indiqu[ée])|ne\s+(?:sont|figurent)\s+pas)[^.!?]*[.!?]\s*",
        re.I,
    ),
    re.compile(
        r"[^.!?]*\b(?:sur la base des seules|dans mes données|dans les données)\b[^.!?]*[.!?]\s*",
        re.I,
    ),
    re.compile(
        r"[^.!?]*\bdans nos informations officielles\b[^.!?]*[.!?]?\s*",
        re.I,
    ),
    re.compile(
        r"Par contre, je n'ai pas d'[ée]l[ée]ments comparables[^.!?]*[.!?]\s*",
        re.I,
    ),
)


def _format_fcfa(amount: int) -> str:
    return f"{amount:,}".replace(",", " ")


def _extract_fcfa_amount(text: str) -> int | None:
    m = re.search(r"([\d\s\u202f]+)\s*FCFA", text or "", re.I)
    if not m:
        return None
    try:
        return int(re.sub(r"[\s\u202f]", "", m.group(1)))
    except ValueError:
        return None


def _try_deterministic_comparison_answer(data: dict, question: str) -> str | None:
    """Réponse comparative directe quand les faits sont clairs (sans LLM)."""
    left = data.get("comparison_left") or {}
    right = data.get("comparison_right") or {}
    la = (left.get("answer") or "").strip()
    ra = (right.get("answer") or "").strip()
    ll = (left.get("label") or "le premier").strip()
    rl = (right.get("label") or "le second").strip()
    if not la or not ra:
        return None

    focus_fn = getattr(_mod, "_comparison_focus_from_question", None)
    focus = focus_fn(question) if callable(focus_fn) else None
    qn = (question or "").lower()

    if focus == "prix" or any(w in qn for w in ("moins cher", "plus cher", "cher")):
        pl, pr = _extract_fcfa_amount(la), _extract_fcfa_amount(ra)
        if pl is not None and pr is not None:
            lp, rp = _format_fcfa(pl), _format_fcfa(pr)
            if pl == pr:
                return f"{ll} et {rl} coûtent tous les deux {lp} FCFA depuis Dakar."
            if pl < pr:
                return f"C'est {ll} qui est le moins cher ({lp} FCFA), contre {rp} FCFA pour {rl}."
            return f"C'est {rl} qui est le moins cher ({rp} FCFA), contre {lp} FCFA pour {ll}."

    if focus == "duree":
        for pat in (
            r"(environ\s+\d+\s*h(?:\s*\d*\s*min)?(?:\s*de\s+route)?)",
            r"(\d+\s*h(?:\s*\d*\s*min)?(?:\s*de\s+route)?)",
        ):
            ml, mr = re.search(pat, la, re.I), re.search(pat, ra, re.I)
            if ml and mr:
                dl, dr = ml.group(1).strip(), mr.group(1).strip()
                if dl.lower() == dr.lower():
                    return f"La durée est la même pour {ll} et {rl} : {dl}."
                return f"Vers {ll} comptez {dl}, et vers {rl} {dr}."

    if focus is None and left.get("source_type") == "city_info" and right.get("source_type") == "city_info":
        if "réseau interurbain" in la.lower() and "réseau interurbain" in ra.lower():
            return (
                f"Oui, nos bus Dakar Dem Dikk desservent {ll} et {rl} "
                f"sur le réseau interurbain."
            )

    return None


def _strip_comparison_meta(text: str) -> str:
    """Retire les tournures meta (« les deux blocs », « aucune information… »)."""
    out = (text or "").strip()
    if not out:
        return out
    for pat in _COMPARISON_ANTI_PATTERNS:
        out = pat.sub("", out)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,.!?])", r"\1", out)
    return out.strip()


def _enhance_comparison_with_deepseek(data: dict, question: str) -> dict:
    """Fusion comparative via DeepSeek (query_type comparison, mode both)."""
    deterministic = _try_deterministic_comparison_answer(data, question)
    if deterministic:
        out = dict(data)
        out["answer"] = deterministic
        out["llm_enhanced"] = False
        return out

    left = data.get("comparison_left") or {}
    right = data.get("comparison_right") or {}
    left_label = left.get("label", "premier élément")
    right_label = right.get("label", "second élément")
    user_prompt = (
        f"Question : {question}\n\n"
        f"Référence — {left_label} :\n{left.get('answer', '')}\n\n"
        f"Référence — {right_label} :\n{right.get('answer', '')}\n\n"
        "Rédige la réponse à l'utilisateur en une ou deux phrases naturelles. "
        "Compare seulement ce qui est demandé."
    )
    cfg = _init_deepseek()
    if cfg is None:
        out = dict(data)
        out["answer"] = f"{left.get('answer', '')}\n\n{right.get('answer', '')}"
        out["llm_enhanced"] = False
        return out
    try:
        import requests as _requests
        r = _requests.post(
            f"{cfg['base_url']}/chat/completions",
            headers={
                "Authorization": f"Bearer {cfg['api_key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": cfg["model"],
                "messages": [
                    {"role": "system", "content": _COMPARISON_LLM_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.25,
                "max_tokens": 900,
            },
            timeout=cfg["timeout_s"],
        )
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}")
        payload = r.json() or {}
        choices = payload.get("choices") or []
        text = ""
        if choices:
            text = ((choices[0] or {}).get("message") or {}).get("content") or ""
        text = text.strip()
        if text and len(text) >= 20:
            out = dict(data)
            out["answer"] = _strip_comparison_meta(_strip_llm_hedging(text))
            out["llm_enhanced"] = True
            out["llm_provider"] = "deepseek"
            out["gemini_enhanced"] = True
            return out
    except Exception:
        pass
    out = dict(data)
    out["answer"] = f"{left.get('answer', '')}\n\n{right.get('answer', '')}"
    out["llm_enhanced"] = False
    return out


def _init_deepseek():
    """Initialise la config DeepSeek (une seule fois)."""
    global _deepseek_cfg
    if _deepseek_cfg is not None:
        return _deepseek_cfg

    api_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        return None

    base_url = (os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").strip().rstrip("/")
    model = (os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat").strip()
    timeout_s = int((os.environ.get("DEEPSEEK_TIMEOUT_S") or "20").strip() or "20")

    _deepseek_cfg = {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "timeout_s": timeout_s,
    }
    return _deepseek_cfg


def _parse_client_history(history_raw) -> list:
    """
    Normalise le champ JSON « history » (liste de {role, content}).
    Retourne une liste de dicts valides ; vide si absent / invalide (rétrocompat).
    """
    if not history_raw or not isinstance(history_raw, list):
        return []
    out = []
    for item in history_raw[-24:]:  # plafond de sécurité (~12 tours user+assistant)
        if not isinstance(item, dict):
            continue
        role = (item.get("role") or "").strip().lower()
        if role not in ("user", "assistant"):
            continue
        content = (item.get("content") or "").strip()
        if not content:
            continue
        if len(content) > 4000:
            content = content[:4000] + "…"
        out.append({"role": role, "content": content})
    return out


def _format_client_history_for_prompt(entries: list) -> str:
    """Bloc texte optionnel à injecter dans le prompt utilisateur envoyé au LLM."""
    if not entries:
        return ""
    lines = []
    for item in entries:
        role = item.get("role") or ""
        content = (item.get("content") or "").strip()
        label = "Usager" if role == "user" else "Assistant"
        if len(content) > 1200:
            content = content[:1200] + "…"
        lines.append(f"{label} : {content}")
    if not lines:
        return ""
    return (
        "Historique récent de la conversation (pour le fil du dialogue uniquement ; "
        "ne pas en déduire de faits non repris dans les extraits du site ci-dessous) :\n"
        + "\n".join(lines)
    )


def _enhance_with_deepseek(original_data: dict, question: str, client_history: list | None = None) -> dict:
    """
    Reformule la réponse du site de façon fluide avec DeepSeek.
    DeepSeek ne fait que réécrire — toutes les infos viennent du site.
    En cas d'erreur, retourne les données originales inchangées.
    """
    cfg = _init_deepseek()
    if cfg is None:
        return original_data

    # Ne pas reformuler les données structurées (tableaux d'horaires, liste de lignes)
    skip_types = {"all_lines_summary", "line_X", "lines_to_stop"}
    if original_data.get("query_type") in skip_types:
        return original_data

    # Si le module n'a trouvé aucun résultat, retourner un message clair
    no_result_markers = ["pas trouvé", "not found", "aucune information", "pas d'information"]
    summary_lower = (original_data.get("summary") or "").lower()
    if (original_data.get("query_type") == "other"
            and not original_data.get("results")
            and any(m in summary_lower for m in no_result_markers)):
        fallback = dict(original_data)
        fallback["answer"] = _CONTACT_BLOCK
        return fallback

    # Rassembler toutes les informations trouvées sur le site
    # en nettoyant d'abord le contenu de navigation parasite
    context_parts = []
    # Ne pas inclure le message "Je n'ai pas trouvé..." dans le contexte,
    # sinon le LLM a tendance à le recopier même quand on ajoute une source utile.
    ans0 = (original_data.get("answer") or "").strip()
    if ans0 and "je n'ai pas trouv" not in ans0.lower():
        context_parts.append(_strip_nav_content(ans0))
    summ0 = (original_data.get("summary") or "").strip()
    if summ0 and "je n'ai pas trouv" not in summ0.lower():
        context_parts.append(summ0)
    if original_data.get("bullets"):
        context_parts.append("\n".join(f"• {b}" for b in original_data["bullets"]))
    if original_data.get("results"):
        for r in original_data["results"][:3]:
            if isinstance(r, dict) and r.get("content"):
                context_parts.append(_strip_nav_content(r["content"][:800]))

    # Fallback ciblé : certaines questions (abonnement/colis/carte) existent sur la page officielle,
    # mais la recherche interne peut remonter un contexte insuffisant. On ajoute donc cette
    # section au contexte pour permettre une reformulation utile, sans inventer.
    try:
        qn = _norm(question)
        if any(k in qn for k in (
            "abonnement", "abonnements", "colis", "messagerie", "courrier", "tek dem", "carte", "pass",
            "reservation", "reserver", "reservez",
            "directeur", "presentation", "historique", "assane", "thierno",
            "emploi", "recrutement", "candidature",
            "publicite", "partenariat", "annonce", "publicitaire",
            "remboursement", "annulation", "report", "bagage", "bagages",
        )):
            fb = _fallback_from_site(question)
            if fb and fb.get("answer"):
                context_parts.append(fb["answer"])
        if any(
            k in qn
            for k in (
                "senegal dem dikk",
                "sénégal dem dikk",
                "interurbain",
                "interurbains",
                "reseau-interurbain",
                "réseau-interurbain",
                "dieuppeul",
            )
        ):
            fb_i = _fallback_interurban(question)
            if fb_i and fb_i.get("answer"):
                context_parts.append(fb_i["answer"])

        # Fallback ciblé "Afrique Dem Dikk" (ex : Gambie / Banjul)
        if any(k in qn for k in ("afrique dem dikk", "afrique", "gambie", "gambia", "banjul", "senegal")):
            fb_a = _fallback_afrique_dem_dikk(question)
            if fb_a and fb_a.get("answer"):
                context_parts.append(fb_a["answer"])
    except Exception:
        pass

    # ── Synchronisation des deux chemins (RAG ↔ carte structurée) ──────────────
    # Si la question porte sur une ville interurbaine mentionnée dans l'historique
    # mais que le RAG n'a pas retourné la carte structurée (is_city_query est faux),
    # on injecte les données exactes de interurbain_data dans le contexte DeepSeek.
    # Cela évite que le chemin RAG donne des infos incomplètes (ex : point de départ)
    # alors que la carte structurée aurait la réponse précise.
    if client_history and not original_data.get("is_city_query"):
        try:
            impl = sys.modules.get("app_flask_impl")
            if impl:
                _ph = getattr(impl, "_parse_history_entries", None)
                _hlc = getattr(impl, "_history_last_city_section", None)
                _cte = getattr(impl, "_city_token_for_enrichment", None)
                _fca = getattr(impl, "_format_city_answer", None)
                if _ph and _hlc and _cte and _fca:
                    hist_entries = _ph(client_history)
                    hist_city = _hlc(hist_entries)
                    if hist_city:
                        city_key = _cte(hist_city)
                        city_text = _fca(hist_city, city_key)
                        if city_text and city_text not in context:
                            context_parts.append(city_text)
        except Exception:
            pass

    context = "\n\n".join(p for p in context_parts if p).strip()
    if not context or len(context) < 20:
        return original_data

    history_block = _format_client_history_for_prompt(client_history or [])
    if history_block:
        user_prompt = (
            f"{history_block}\n\n"
            f"Contexte (site officiel DDD — ne rien inventer en dehors de ce texte) :\n---\n{context}\n---\n\n"
            f"Question : {question}\n\n"
            f"Reformule le contexte en une réponse claire et naturelle à la question. "
            f"Conserve tous les faits (chiffres, délais, contacts). N'ajoute aucune information absente du contexte."
        )
    else:
        user_prompt = (
            f"Contexte (site officiel DDD — ne rien inventer en dehors de ce texte) :\n---\n{context}\n---\n\n"
            f"Question : {question}\n\n"
            f"Reformule le contexte en une réponse claire et naturelle à la question. "
            f"Conserve tous les faits (chiffres, délais, contacts). N'ajoute aucune information absente du contexte."
        )

    try:
        import requests as _requests
        r = _requests.post(
            f"{cfg['base_url']}/chat/completions",
            headers={
                "Authorization": f"Bearer {cfg['api_key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": cfg["model"],
                "messages": [
                    {"role": "system", "content": _LLM_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 1200,
            },
            timeout=cfg["timeout_s"],
        )
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:500]}")
        payload = r.json() or {}
        choices = payload.get("choices") or []
        text = ""
        if choices and isinstance(choices, list):
            msg = (choices[0] or {}).get("message") or {}
            text = (msg.get("content") or "").strip()
        if text:
            # Vérification réponse tronquée : doit se terminer par une ponctuation finale
            last_char = text.rstrip()[-1] if text.rstrip() else ""
            if last_char not in (".", "?", "!", "»", '"', "'"):
                # Réponse coupée → on complète proprement ou on retourne l'original
                # Si le texte est très court (< 30 chars) c'est suspect → original
                if len(text.strip()) < 30:
                    return original_data
                # Sinon on ajoute un point pour éviter un fragment affiché
                text = text.rstrip() + "."

            text = _strip_llm_hedging(text)

            enhanced = dict(original_data)
            enhanced["answer"] = text
            enhanced["llm_provider"] = "deepseek"
            enhanced["llm_enhanced"] = True
            enhanced["gemini_enhanced"] = True  # compat front/back
            # DeepSeek a produit un texte fluide → on retire les flags "carte structurée"
            # pour que le frontend affiche le texte naturel plutôt qu'un bloc PRIX/DÉPART/…
            if original_data.get("is_city_query") or original_data.get("query_type") == "city_info":
                enhanced["has_structured_data"] = False
                enhanced["is_city_query"] = False
                enhanced["query_type"] = "general"
            # DeepSeek dit « contactez le service client » alors que le contexte contient la réponse
            if _deepseek_missing_info(text):
                fb = _search_chatbot_page_blocks(question)
                if fb and fb.get("answer") and not _answer_looks_like_junk(fb["answer"]):
                    out = dict(original_data)
                    out["answer"] = _clean_raw_answer(fb["answer"])
                    out["sources"] = fb.get("sources", out.get("sources", []))
                    out["results"] = fb.get("results", out.get("results", []))
                    out["llm_enhanced"] = False
                    out["gemini_enhanced"] = False
                    return out
                orig_clean = _clean_raw_answer(original_data.get("answer") or "")
                if (
                    orig_clean
                    and len(orig_clean) >= 40
                    and not _answer_looks_like_junk(orig_clean)
                    and _answer_relevant_to_question(question, orig_clean)
                ):
                    out = dict(original_data)
                    out["answer"] = orig_clean
                    out["llm_enhanced"] = False
                    out["gemini_enhanced"] = False
                    return out
            return enhanced
    except Exception as e:
        err_str = str(e)
        if "429" in err_str:
            print("[DeepSeek] Quota dépassé (rate limit), réponse originale utilisée.")
        else:
            print(f"[DeepSeek] Erreur génération : {e}")
        fallback = dict(original_data)
        fallback["llm_provider"] = "deepseek"
        fallback["llm_enhanced"] = False
        fallback["llm_error"] = "rate_limit" if ("429" in err_str) else "error"
        # Compat front/back : garder les anciens champs
        fallback["gemini_enhanced"] = False
        fallback["gemini_error"] = fallback["llm_error"]
        # Nettoyer la réponse brute pour qu'elle reste lisible sans LLM
        raw = fallback.get("answer", "")
        cleaned = _clean_raw_answer(raw) if raw else ""
        if cleaned:
            fallback["answer"] = cleaned
        elif not cleaned and raw:
            # Si le nettoyage a tout supprimé (réponse = navigation pure),
            # fournir une réponse de contact par défaut
            fallback["answer"] = _CONTACT_BLOCK
        return fallback

    return original_data


_HOMEPAGE_NOISE_RE = re.compile(
    r'^\s*(?:'
    r'[0-9]{1,3}\s*[+%]?'                            # 0, 00, 12, 00 +, 50 %
    r'|[+%]'                                         # + ou %
    r'|voyageurs?\s+annuel(?:\s*\d+)?'
    r'|destinations?(?:\s*\d+)?'
    r'|clients?\s+satisfaits?(?:\s*\d+)?'
    r'|ann[ée]es?\s+d[\u0027]exp[ée]rience(?:\s*\d+)?'
    r"|projets?\s+d[\u0027]innovation(?:\s*\d+)?"
    r'|r[ée]gie\s+publicitaire'
    r'|exp[ée]dition\s+de\s+courriers?'
    r'|transport\s+de\s+marchandises?'
    r'|prestations?\s+m[ée]caniques?'
    r'|express\s+aibd'
    r'|transport\s+interurbain'
    r'|voir\s+toute\s+l[\u0027]actualit[ée]'
    r'|reservez?\s+une\s+place'
    r'|nos\s+derni[èe]res?'
    r'|articles?\s+r[ée]cents?'
    r'|com'                                          # "com" sous une date d'article
    r'|m'                                            # "M" isolé après compteur
    r'|dakar\s+dem\s+dikk\s*,?'
    r'|direction\s+g[ée]n[ée]rale'
    r'|op[ée]rateur\s+public\s+leader.*'
    r'|voyagez\s+avec\s+nous.*'
    r'|vous\s+chercher\s+le\s+meilleur\s+service.*'
    r')\s*$',
    re.IGNORECASE,
)

# Marqueurs typiques du footer WordPress de demdikk.sn — on coupe le texte
# au PREMIER d'entre eux quand on rencontre ces lignes (souvent vidées de leur contexte).
_FOOTER_CUT_RE = re.compile(
    r'^(?:'
    r'Articles?\s+r[ée]cents?'
    r'|Nos\s+derni[èe]res?\s+actualit[ée]s?'
    r'|Voir\s+toute\s+l[\u0027]actualit[ée]'
    r'|RESERVEZ?\s+UNE\s+PLACE'
    r'|Op[ée]rateur\s+public\s+leader\s+des\s+transports'
    r'|Direction\s+G[ée]n[ée]rale'
    r'|Km\s*4[,.]\s*5\s+Avenue\s+Cheikh\s+Anta\s+Diop'
    r')',
    re.IGNORECASE,
)


def _looks_like_seo_keyword_line(line: str) -> bool:
    """Ligne type meta SEO (mots-clés collés, CamelCase, sans connecteurs FR)."""
    s = (line or "").strip()
    if len(s) < 55 or len(s.split()) < 8:
        return False
    if re.search(r"👉|[:«»]", s):
        return False
    if re.search(r"\d+\s*h\d*|\b(premier|dernier|d[ée]part|terminus|depuis)\b", s, re.I):
        return False
    if re.search(
        r"\b(le|la|les|l'|de|des|du|d'|et|à|a|un|une|pour|avec|depuis|dans|sur|est|sans|plus|très|vous|nous|qui|chez|aux|son|leur|cette|ces|ont|sont|sera)\b",
        s,
        re.I,
    ):
        return False
    words = s.split()
    slug_interior = re.compile(r"[a-zà-ÿ][A-ZÀ-Ÿ]")
    alnum_digit_end = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ]+\d+$", re.I)
    sluggy = sum(
        1 for w in words if len(w) > 11 or slug_interior.search(w) or alnum_digit_end.match(w)
    )
    return sluggy >= 5


def _strip_nav_content(text: str) -> str:
    """
    Retire le bloc de navigation/en-tête du site scrappé qui pollue les réponses.
    Ces blocs ressemblent à : 'reseau-urbain-dakar – Dakar Dem Dikk Contactez-nous au...'
    Filtre aussi les compteurs / KPI / mots-clés SEO de la home.
    """
    import re as _re
    if not text:
        return text
    # Bloc complet de navigation (slug – Dakar Dem Dikk ... jusqu'au contenu réel)
    text = _re.sub(
        r'^[a-z0-9\-]+ \u2013 Dakar Dem Dikk\b.*?'
        r'(?:Home\s+[a-z0-9\-]+\s+)?',
        '',
        text,
        flags=_re.DOTALL | _re.IGNORECASE,
    ).strip()
    # Bloc "Contactez-nous au: ... Offres d'emplois Plus de détails"
    text = _re.sub(
        r'Contactez-nous au\s*:.*?(?:Plus de d\u00e9tails|Offres d.emplois)[^\n]*\n?',
        '',
        text,
        flags=_re.DOTALL | _re.IGNORECASE,
    ).strip()
    # Liens du menu de navigation (ligne isolée)
    text = _re.sub(
        r'^(?:Accueil|Offre transport|Services|Info voyageurs|Présentation|Contact|Offres d.emplois)\s*$',
        '',
        text,
        flags=_re.MULTILINE | _re.IGNORECASE,
    )
    # Couper au premier marqueur de footer (Articles récents, RESERVEZ UNE PLACE, Direction G., …)
    cut_idx = None
    for i, ln in enumerate(text.split("\n")):
        if _FOOTER_CUT_RE.match(ln.strip()):
            cut_idx = i
            break
    if cut_idx is not None:
        text = "\n".join(text.split("\n")[:cut_idx])
    # Filtre ligne-à-ligne : compteurs homepage + mots-clés SEO
    kept = []
    prev_blank = False
    for ln in text.split("\n"):
        bare = ln.strip().lstrip("-–•▸").strip()
        if _HOMEPAGE_NOISE_RE.match(bare):
            continue
        # Description de carte tronquée : "Le ... …" ou "Le …" très courte → on supprime
        if (bare.endswith("…") or bare.endswith("...")) and len(bare) < 90:
            continue
        if _looks_like_seo_keyword_line(bare):
            continue
        kept.append(ln)
    text = "\n".join(kept)
    # Nettoyer les espaces restants
    text = _re.sub(r'\n{3,}', '\n\n', text)
    text = _re.sub(r'[ \t]+', ' ', text)
    return text.strip()


def _clean_raw_answer(text: str) -> str:
    """Nettoie le HTML/markdown brut quand Gemini n'est pas disponible."""
    import re
    text = _strip_nav_content(text)
    # Convertir les titres markdown (##, ###, ####) en texte propre sans les #
    text = re.sub(r'#{1,6}\s*\d*\.?\s*', '', text)
    # Convertir les tirets de liste en puces
    text = re.sub(r'^– ', '• ', text, flags=re.MULTILINE)
    text = re.sub(r'^- ', '• ', text, flags=re.MULTILINE)
    # Supprimer les URLs complètes et fragments d'URL tronqués (ex: "tps://...", "ps://...")
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'\btps://\S+', '', text)
    text = re.sub(r'\bps://\S+', '', text)
    text = re.sub(r'\bs://\S+', '', text)
    # Supprimer les fragments résiduels comme "tps://demdikk.sn/reseau-interurbain/,"
    text = re.sub(r'\w+://\S+', '', text)
    # Réduire les espaces et lignes vides répétées
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    # Supprimer les lignes trop courtes (ponctuation/résidus)
    lines = [l for l in text.split('\n') if len(l.strip()) > 3]
    text = '\n'.join(lines)
    # Supprimer la ponctuation et les tirets en début de texte
    text = re.sub(r'^[\s,;.–\-•]+', '', text)
    return text.strip()


def _norm(s: str) -> str:
    import re
    s = (s or "").lower()
    s = s.replace("’", "'")
    s = s.encode("utf-8", "ignore").decode("utf-8", "ignore")
    # enlever accents (sans dépendance externe)
    try:
        import unicodedata
        s = unicodedata.normalize("NFD", s)
        s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    except Exception:
        pass
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    _variants = {
        "contacts": "contact", "services": "service",
        "bagages": "bagage", "abonnements": "abonnement",
        "horaires": "horaire", "lignes": "ligne",
        "tarifs": "tarif", "billets": "billet",
        "tickets": "ticket", "agences": "agence",
        "reservations": "reservation",
        "reserver": "reservation",
        "reservez": "reservation",
        "resereve": "reservation",
        "remboursements": "remboursement",
        "annulations": "annulation",
        "suggestions": "suggestion",
        "tekk dem": "tek dem", "tekdem": "tek dem", "tek-dem": "tek dem",
        "aeroport": "aibd",
        # typos / abréviations fréquentes
        "coli": "colis",
        "objets perdus": "objet perdu",
        "objets": "objet",
        "partenariats": "partenariat",
        "publicites": "partenariat",
        "publicite": "partenariat",
        "locations": "location",
        "location bus": "location de bus",
        # présentation / RH
        "directeurs": "directeur",
        "directeurs generaux": "directeur",
        "dg": "directeur",
        "pdg": "directeur",
        "emplois": "emploi",
        "recrutements": "recrutement",
        "offres emploi": "emploi",
        "offre emploi": "emploi",
        "presentations": "presentation",
        "histoire": "historique",
        "historique": "historique",
        "greve": "perturbation",
        "greves": "perturbation",
        "retard": "perturbation",
        "retards": "perturbation",
        "intemperie": "perturbation",
        "intemperies": "perturbation",
        "enfants": "enfant",
    }
    for variant, canonical in _variants.items():
        s = re.sub(r"\b" + re.escape(variant) + r"\b", canonical, s)
    return s


def _extract_section_for_marker(text: str, marker: str, max_chars: int = 1400) -> str:
    """Extrait une section à partir d'un seul marqueur (titre de rubrique en début de ligne)."""
    if not text or not marker:
        return ""
    t = text.replace("\r\n", "\n").replace("\u2019", "'").replace("\u2018", "'")
    ml = marker.lower().replace("\u2019", "'").replace("\u2018", "'")
    pos = 0
    while True:
        i = t.lower().find(ml, pos)
        if i < 0:
            break
        line_start = t.rfind("\n", 0, i) + 1
        line_end = t.find("\n", i)
        line = t[line_start: line_end if line_end >= 0 else len(t)].strip()
        line_body = _strip_section_number(line)
        if (
            line_body.lower().startswith(ml[: min(len(ml), len(line_body))])
            and len(line) <= len(marker) + 25
        ):
            return t[line_start: line_start + max_chars].strip()
        pos = i + 1
    i = t.lower().find(ml)
    if i >= 0:
        # Marqueurs courts (ex. « CCO ») : pas de recherche au milieu d'un mot (« accompagnement »).
        if len(ml) <= 4:
            return ""
        return t[i: i + max_chars].strip()
    return ""


def _extract_section_priority(text: str, start_markers: tuple[str, ...], max_chars: int = 1400) -> str:
    """Essaie les marqueurs dans l'ordre (rubrique la plus précise en premier)."""
    for m in start_markers:
        section = _extract_section_for_marker(text, m, max_chars)
        if section and len(section.strip()) >= 20:
            return section
    return ""


def _extract_section(text: str, start_markers: tuple[str, ...], max_chars: int = 1400) -> str:
    if not text:
        return ""
    t = text.replace("\r\n", "\n")
    # Préférer un marqueur en début de ligne (titre / FAQ), pas une occurrence au milieu d'une phrase
    best_idx = -1
    for m in start_markers:
        pos = 0
        ml = m.lower()
        while True:
            i = t.lower().find(ml, pos)
            if i < 0:
                break
            line_start = t.rfind("\n", 0, i) + 1
            line_end = t.find("\n", i)
            line = t[line_start: line_end if line_end >= 0 else len(t)].strip()
            if line.lower().startswith(ml[: min(len(ml), len(line))]) and len(line) <= len(m) + 20:
                if best_idx < 0 or i < best_idx:
                    best_idx = line_start
            pos = i + 1
    if best_idx >= 0:
        return t[best_idx: best_idx + max_chars].strip()
    idx = -1
    for m in start_markers:
        i = t.lower().find(m.lower())
        if i >= 0 and (idx < 0 or i < idx):
            idx = i
    if idx < 0:
        return ""
    snippet = t[idx: idx + max_chars]
    return snippet.strip()


_page_cache: dict[str, tuple[float, str]] = {}
_PAGE_CACHE_TTL = 600  # 10 minutes

def _fetch_page_text(url: str) -> str | None:
    """
    Récupère le texte d'une page demdikk.sn en ne gardant que la zone de contenu
    principale (<main>, <article>, .entry-content, …). On EXCLUT header / nav /
    footer / aside / widgets / cartes de la home, qui polluent les réponses.
    """
    import time as _time
    now = _time.time()
    cached = _page_cache.get(url)
    if cached and (now - cached[0]) < _PAGE_CACHE_TTL:
        return cached[1]
    try:
        import requests
        from bs4 import BeautifulSoup

        r = requests.get(url, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Retirer les blocs notoirement parasites
        for sel in (
            "header", "nav", "footer", "aside",
            "script", "style", "noscript",
            ".site-header", ".site-footer", ".elementor-location-header",
            ".elementor-location-footer", ".widget", ".sidebar",
            ".menu", ".navbar", ".breadcrumb", ".breadcrumbs",
            ".elementor-counter", ".odometer",
        ):
            for el in soup.select(sel):
                el.decompose()

        # Privilégier la zone de contenu principale
        container = (
            soup.select_one("main")
            or soup.select_one("article")
            or soup.select_one(".entry-content")
            or soup.select_one(".elementor-location-single")
            or soup.body
            or soup
        )
        text = container.get_text("\n", strip=True)

        # Ne pas tronquer la FAQ chatbot-2303 (sections en bas de page : CCO, syndicats…).
        if "chatbot-2303" not in url:
            # Couper au début du bloc « cartes services / compteurs / articles récents »
            # de la home — ces blocs polluent les pages quand Elementor les inclut.
            cut_patterns = (
                r"\bArticles?\s+r[ée]cents?\b",
                r"\bVoir\s+toute\s+l[\u0027]actualit[ée]\b",
                r"\bNos\s+derni[èe]res?\s+actualit[ée]s?\b",
                r"\bR[ée]gie\s+publicitaire\b",
                r"\bExp[ée]dition\s+de\s+courriers?\b",
                r"\bPrestations?\s+m[ée]caniques?\b",
                r"\bVoyageurs?\s+annuel\b",
                r"\bClients?\s+satisfaits?\b",
                r"\bProjets?\s+d[\u0027]innovation\b",
                r"\bRESERVEZ?\s+UNE\s+PLACE\b",
                r"\bDirection\s+G[ée]n[ée]rale\b",
            )
            earliest = len(text)
            for pat in cut_patterns:
                m = re.search(pat, text, flags=re.IGNORECASE)
                if m and m.start() < earliest:
                    earliest = m.start()
            if earliest < len(text):
                text = text[:earliest].rstrip()

        _page_cache[url] = (now, text)
        return text
    except Exception:
        # En cas d'erreur réseau, retourner le cache expiré s'il existe
        return cached[1] if cached else None


def _fallback_interurban(question: str) -> dict | None:
    """Réponse interurbain — délègue à app_backup (_json_interurban_overview)."""
    qn = _norm(question)
    if not qn:
        return None
    try:
        impl = sys.modules.get("app_flask_impl")
        if impl:
            fn = getattr(impl, "_json_interurban_overview", None)
            if callable(fn):
                return fn(question, qn)
    except Exception:
        pass
    return None


def _is_publicite_query(qn: str) -> bool:
    """Question sur publicité / partenariat commercial (pas « république »)."""
    if not qn or "republique" in qn:
        return False
    if any(k in qn for k in (
        "publicite", "partenariat", "annonce", "publicitaire", "regie publicitaire",
    )):
        return True
    return bool(re.search(r"\bpub\b", qn))


def _fallback_publicite_partenariat(question: str) -> dict | None:
    """Extrait la section Publicité et partenariats depuis chatbot-2303."""
    qn = _norm(question)
    if not _is_publicite_query(qn):
        return None

    url = "https://demdikk.sn/chatbot-2303/"
    page_text = _fetch_page_text(url)
    if not page_text:
        return None

    section = _extract_section(
        page_text,
        (
            "Publicité et partenariats",
            "Publicite et partenariats",
            "Espaces publicitaires disponibles",
            "Devenez partenaire",
        ),
        max_chars=1800,
    )
    if not section or len(section) < 40:
        return None

    for stop in ("\nContact et assistance", "\nProgramme de fid"):
        i = section.find(stop)
        if i > 80:
            section = section[:i].rstrip()

    result = _make_chatbot_result(section)
    result["sources"] = [{
        "title": "Publicité et partenariats – Dakar Dem Dikk",
        "url": url,
        "score": 0.95,
    }]
    if result.get("results"):
        result["results"][0]["url"] = url
        result["results"][0]["title"] = "Publicité et partenariats – DDD"
    return result


_AFRIQUE_NAME_QUERIES = frozenset({
    "afrique dem dikk", "afrique demdikk", "add",
})

_AFRIQUE_SHORT_PRESENTATION = (
    "Afrique Dem Dikk (ADD) est le réseau international de Dakar Dem Dikk. "
    "Il assure des liaisons transfrontalières, notamment Dakar–Banjul (Gambie). "
    "Départs Dakar → Banjul : 7h00 et 9h00 ; Banjul → Dakar : 7h30 et 10h00. "
    "Réservation : application Dem Dikk, agence ou +221 33 824 10 10."
)


def _is_bare_afrique_dem_dikk_query(qn: str) -> bool:
    """ADD / Afrique Dem Dikk seul — synthèse courte (pas le pavé chatbot-2303)."""
    qn = (qn or "").strip()
    return qn in _AFRIQUE_NAME_QUERIES


def _afrique_short_presentation_payload() -> dict:
    url = "https://demdikk.sn/reseau-international/"
    return {
        "answer": _AFRIQUE_SHORT_PRESENTATION,
        "summary": "Afrique Dem Dikk (ADD)",
        "bullets": [],
        "sources": [{"title": "Afrique Dem Dikk – DDD", "url": url, "score": 1.0}],
        "results": [],
        "query_type": "general",
        "needs_clarification": False,
        "has_structured_data": False,
        "is_city_query": False,
        "is_line_query": False,
        "show_more_info": True,
    }


def _fallback_afrique_dem_dikk(question: str) -> dict | None:
    """Extrait du contenu utile sur 'Afrique Dem Dikk' depuis la page officielle chatbot."""
    qn = _norm(question)
    if not qn:
        return None

    triggers = (
        "afrique dem dikk",
        "afrique",
        "gambie",
        "gambia",
        "banjul",
        "senegal",
        "add",
    )
    if not any(t in qn for t in triggers):
        return None

    if _is_bare_afrique_dem_dikk_query(qn):
        return _afrique_short_presentation_payload()

    url = "https://demdikk.sn/chatbot-2303/"
    page_text = _fetch_page_text(url)
    if not page_text:
        return None

    section = _extract_section(
        page_text,
        (
            "Afrique Dem Dikk",
            "AFRIQUE DEM DIKK",
            "Gambie",
            "GAMBIE",
            "Banjul",
            "BANJUL",
        ),
        max_chars=2200,
    )
    if not section or len(section) < 60:
        return None

    return {
        "answer": section,
        "summary": section[:280],
        "bullets": [],
        "sources": [{"title": "Chatbot Dakar Dem Dikk", "url": url, "score": 1.0}],
        "results": [{"url": url, "title": "Chatbot Dakar Dem Dikk", "snippet": section[:500], "full_text": section}],
        "query_type": "general",
        "needs_clarification": False,
        "has_structured_data": False,
        "is_city_query": False,
        "is_line_query": False,
    }


_LEMMES = {
    "rembourse": "remboursement", "rembourser": "remboursement",
    "remboursé": "remboursement", "remboursable": "remboursement",
    "annule": "annulation", "annuler": "annulation", "annulé": "annulation",
    "reserver": "reservation", "réserver": "reservation",
    "reservé": "reservation", "réservé": "reservation", "reservez": "reservation",
    "abonné": "abonnement", "abonner": "abonnement",
    "perdu": "objet perdu", "perdue": "objet perdu",
    "modifier": "modification", "modifié": "modification",
    "contacter": "contact", "contactez": "contact",
    "télécharger": "application", "téléchargement": "application",
    "recharger": "rechargement", "rechargez": "rechargement",
    "voyager": "voyage", "voyagé": "voyage",
    "payer": "paiement", "payé": "paiement", "payez": "paiement",
    "acheter": "achat", "acheté": "achat",
    "perdre": "objet perdu",
}


def _lemmatize(text: str) -> str:
    words = _norm(text).split()
    return " ".join(_LEMMES.get(w, w) for w in words)


_LLM_MISSING_INFO_RE = re.compile(
    r"je n['\u2019]ai pas (?:cette )?information(?: pour le moment)?|"
    r"je n['\u2019]ai pas d['\u2019]information|"
    r"je n['\u2019]ai pas (?:le|les) d[ée]tail|"
    r"je n['\u2019]ai pas trouv|"
    r"pas d['\u2019]information (?:sur|dans|concernant|relative)|"
    r"dans mes informations(?: pour le moment)?|"
    r"je n['\u2019]e dispose pas de (?:la|le|les|l['\u2019])?(?:liste|d[ée]tail|information|poste)",
    re.IGNORECASE,
)

_FAQ_FILLER_WORDS = frozenset({
    "est", "quoi", "que", "quel", "quelle", "quels", "quelles", "sont",
    "cest", "cette", "cela", "comme", "faire", "dire", "savoir", "connaitre",
    "connaître", "signifie", "signifier", "veut", "veux", "peut", "peux",
    "definition", "définition", "explique", "expliquer", "parle", "parler",
})

_STRUCTURED_QUERY_TYPES = frozenset({
    "all_lines_summary", "line_X", "lines_to_stop", "line_details", "city_info",
    "interurban_overview",
})


def _faq_query_tokens(qn: str) -> list[str]:
    """Mots porteurs pour la recherche FAQ (sans « quoi », « est », etc.)."""
    out: list[str] = []
    seen: set[str] = set()
    for w in (qn or "").split():
        if w in _QUERY_STOPWORDS or w in _FAQ_FILLER_WORDS:
            continue
        if len(w) >= 3 or (len(w) >= 2 and w.isalpha()):
            if w not in seen:
                seen.add(w)
                out.append(w)
    return out


def _chatbot_faq_score(result: dict | None) -> float:
    if not result:
        return 0.0
    try:
        return float((result.get("sources") or [{}])[0].get("score") or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_structured_ask_response(data: dict) -> bool:
    if not data:
        return False
    if data.get("is_line_query") or data.get("is_city_query"):
        return True
    return (data.get("query_type") or "") in _STRUCTURED_QUERY_TYPES


def _answer_says_missing(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if _deepseek_missing_info(t):
        return True
    tl = t.lower()
    if "je n'ai pas trouv" in tl or "je n ai pas trouv" in tl:
        return True
    if re.search(r"je n['\u2019 ]ai pas d['\u2019]information", tl):
        return True
    if re.search(r"pas d['\u2019]information (?:sur|dans|concernant|relative)", tl):
        return True
    return False


def _faq_answer_usable(fb: dict | None, question: str, qn: str | None = None) -> bool:
    if not fb or not (fb.get("answer") or "").strip():
        return False
    ans = fb["answer"]
    if _answer_looks_like_junk(ans) or _block_looks_like_nav_junk(ans):
        return False
    qn = qn if qn is not None else _norm(question)
    tokens = _faq_query_tokens(qn)
    if not tokens:
        return True
    ans_n = _lemmatize(ans)
    return any(w in ans_n for w in tokens)

_LLM_HEDGING_PHRASE_RES = (
    re.compile(
        r"(?:,\s*)?(?:car\s+)?\.?\s*je n['\u2019]ai pas (?:le|les) d[ée]tail(?:s)?(?: complet(?:s)?)?"
        r"(?: (?:de|des|du|d['\u2019]) [^.!?]{3,120})?(?: dans mes informations)?(?: pour le moment)?\.?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\.?\s*je n['\u2019]ai pas (?:cette )?information(?: complète)?(?: pour le moment)?"
        r"(?:,?)?(?: dans mes informations)?\.?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\.?\s*je n['\u2019]e dispose pas de (?:la|le|les|l['\u2019])?(?:liste exacte|d[ée]tail(?:s)? complet(?:s)?)"
        r"(?: de [^.!?]{3,120})?(?: pour le moment)?\.?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\.?\s*mes informations (?:ne contiennent pas|ne pr[ée]cisent pas)[^.!?]{3,100}?\.?",
        re.IGNORECASE,
    ),
    re.compile(
        r",?\s*(?:car\s+)?je n['\u2019]ai pas (?:le|les) d[ée]marches(?: [àa] suivre)?(?: pour le moment)?\.?",
        re.IGNORECASE,
    ),
    re.compile(
        r",?\s*(?:car\s+)?(?:les )?d[ée]marches (?:pr[ée]cises )?ne sont pas (?:indiqu[ée]es|pr[ée]cis[ée]es) "
        r"(?:dans le contexte|pour le moment)\.?",
        re.IGNORECASE,
    ),
)


def _strip_llm_hedging(text: str) -> str:
    """Retire les formulations « je n'ai pas le détail… » — garde la redirection service client."""
    out = (text or "").strip()
    if not out:
        return out
    for pat in _LLM_HEDGING_PHRASE_RES:
        out = pat.sub("", out)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,.!?])", r"\1", out)
    out = re.sub(r"\.{2,}", ".", out)
    # Rétablir une ponctuation propre entre deux phrases collées
    out = re.sub(
        r"([a-zàâäéèêëïîôùûüç0-9])\s+(Pour |Je vous invite|Contactez|Vous pouvez)",
        r"\1. \2",
        out,
        flags=re.IGNORECASE,
    )
    return out.strip()

_ANSWER_JUNK_MARKERS = (
    "#demdikk", "#ligne", "encore plus proche de vous",
    "bonne nouvelle |", "alerte info",
    "mobiliteurbaine", "parcellesassainies",
    "nouveaux arrets viennent", "ville de dakar",
)

_QUERY_STOPWORDS = frozenset({
    "le", "la", "les", "de", "du", "des", "un", "une", "et", "en",
    "est", "que", "qui", "sur", "par", "pour", "dans", "avec", "au",
    "je", "il", "elle", "vous", "nous", "on", "ce", "se", "ne", "pas",
    "plus", "quel", "quelle", "quels", "quelles", "comment", "quand",
    "ou", "si", "mais", "donc", "car", "ici", "ya", "a", "mon", "ma",
    "mes", "ton", "ta", "tes", "son", "sa", "ses", "notre", "votre",
})

_COMMON_QUERY_WORDS = frozenset({
    "billet", "ticket", "bus", "ligne", "tarif", "prix", "horaire",
    "service", "dem", "dikk", "demdikk", "transport", "voyage", "ddd",
    "information", "infos", "question", "savoir", "connaitre", "connaître",
})


def _deepseek_missing_info(text: str) -> bool:
    return bool(_LLM_MISSING_INFO_RE.search(text or ""))


def _answer_looks_like_junk(text: str) -> bool:
    """Chunk index / actualités réseaux sociaux — pas une réponse FAQ."""
    if not (text or "").strip():
        return True
    t = _norm(text)
    hits = sum(1 for m in _ANSWER_JUNK_MARKERS if m in t)
    if hits >= 2:
        return True
    if hits >= 1 and ("#" in text or text.count("#") >= 2):
        return True
    if text.count("#") >= 3:
        return True
    return False


def _query_significant_words(qn: str) -> list[str]:
    return [w for w in (qn or "").split() if w not in _QUERY_STOPWORDS and len(w) >= 3]


def _answer_relevant_to_question(question: str, answer: str, qn: str | None = None) -> bool:
    """Au moins un mot distinctif de la question doit apparaître dans la réponse."""
    qn = qn if qn is not None else _norm(question)
    ans_n = _lemmatize(answer)
    faq_tokens = _faq_query_tokens(qn)
    if faq_tokens:
        if not any(w in ans_n for w in faq_tokens):
            return False
        return _answer_has_topic_substance(qn, answer)
    words = _query_significant_words(qn)
    if not words:
        return True
    specific = [w for w in words if w not in _COMMON_QUERY_WORDS]
    check = specific if specific else words
    if not any(w in ans_n for w in check):
        return False
    return _answer_has_topic_substance(qn, answer)


_TOPIC_SUBSTANCE: dict[str, tuple[str, ...]] = {
    "remboursement": (
        "demande de remboursement", "demandes de remboursement",
        "delai indicatif", "jours ouvr", "traitees par le service",
        "mobile money", "3 a 5 jours",
    ),
    "annulation": ("frais", "condition", "24h", "report", "annul"),
    "report": ("frais", "24h", "modification", "report", "voyage"),
    "reservation": ("modalite", "modifier", "reservation", "billet", "application", "cgu"),
}


def _answer_has_topic_substance(qn: str, answer: str) -> bool:
    """Pour les sujets précis, la réponse doit contenir du contenu, pas seulement un titre de rubrique."""
    ans_n = _lemmatize(answer)
    for topic, hints in _TOPIC_SUBSTANCE.items():
        if topic in qn:
            return any(h in ans_n for h in hints)
    return True


def _block_is_title_only(block: str) -> bool:
    """Bloc = titre de section sans corps (ex. « Gestion des réservations… » seul)."""
    lines = [l.strip() for l in (block or "").split("\n") if l.strip()]
    if not lines:
        return True
    if len(lines) == 1:
        return len(lines[0]) < 120
    if len(block or "") < 100:
        return not any(l.startswith(("\u2013", "-", "\u2022")) for l in lines[1:])
    return False


def _expand_block_with_following(blocks: list[str], idx: int, max_chars: int = 1800) -> str:
    """Titres de rubrique → inclure les sous-sections suivantes."""
    parts = [blocks[idx]]
    for j in range(idx + 1, len(blocks)):
        if len("\n\n".join(parts)) >= max_chars:
            break
        nxt = blocks[j]
        if _answer_looks_like_junk(nxt) or _block_looks_like_nav_junk(nxt):
            break
        parts.append(nxt)
        combined = "\n\n".join(parts)
        if not _block_is_title_only(combined) and len(combined) >= 100:
            if j + 1 < len(blocks) and _block_is_title_only(blocks[j + 1]):
                break
    return "\n\n".join(parts)[:max_chars]


def _strip_section_number(line: str) -> str:
    """Enlève un préfixe du type « 8. » ou « 8.2. » laissé sur le site."""
    import re as _re
    return _re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", (line or "").strip())


def _line_looks_like_section_title(stripped: str) -> bool:
    """Titre de rubrique / sous-section (avec ou sans numéro)."""
    import re as _re
    if not stripped or stripped.lower().startswith(("http", "www.", "agent-ia", "home")):
        return False
    if stripped.startswith(("\u2013", "-", "\u2022", ",", ";", ".", ":", "(", "«")):
        return False
    if stripped.endswith(":"):
        return False
    # Numéros de téléphone (+221 …) — pas un titre de section
    if _re.match(r"^\+?\d[\d\s\-().]{6,}\.?$", stripped):
        return False
    # Lignes commençant par « Ou » (suite d'une énumération, pas un titre)
    if _re.match(r"^Ou\s+", stripped, _re.I):
        return False
    if _re.match(r"^\d+\.\s+[A-ZÀ-Ü]", stripped):
        return True
    if _re.match(r"^(Google Play|App Store|Sur iPhone|Sur Android)\b", stripped, _re.I):
        return False
    title = _strip_section_number(stripped)
    if not title or title[0].islower():
        return False
    if len(title.split()) <= 2 and len(title) <= 15:
        return False
    if _re.search(
        r"\b(est|sont|etait|étaient|peut|peuvent|permet|permettent|dispose|assure|"
        r"contactez|suivez|consultez|telecharge|télécharge|acheter|voyager)\b",
        title,
        _re.I,
    ):
        return False
    return 8 <= len(title) <= 72


def _clip_at_next_section_title(text: str) -> str:
    """Coupe après le premier bloc utile, au titre de section suivant."""
    if not text:
        return ""
    lines = text.replace("\r\n", "\n").split("\n")
    if len(lines) <= 2:
        return text.strip()
    first_norm = _norm(_strip_section_number(lines[0].strip()))
    kept = [lines[0]]
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        if (
            len(kept) >= 2
            and _line_looks_like_section_title(stripped)
            and not stripped.endswith("?")
            and _norm(_strip_section_number(stripped)) != first_norm
        ):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def _split_chatbot_page_blocks(page_text: str) -> list[str]:
    """Découpe chatbot-2303 en blocs (titres + contenu, séparateurs souvent \\n simple)."""
    import re as _re

    if not page_text:
        return []
    lines = page_text.replace("\r\n", "\n").split("\n")
    blocks: list[str] = []
    current: list[str] = []

    def _flush():
        nonlocal current
        if current:
            blk = "\n".join(current).strip()
            if len(blk) >= 35:
                blocks.append(blk)
            current = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            _flush()
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        is_header = (
            _line_looks_like_section_title(stripped)
            and (
                nxt.startswith(("\u2013", "-", "\u2022"))
                or _re.match(r"^[A-ZÀ-ÖØ-Þ0-9]", _strip_section_number(nxt))
                or "?" in stripped
            )
        )
        if is_header and current:
            _flush()
        current.append(line)
    _flush()
    return blocks


def _block_looks_like_nav_junk(block: str) -> bool:
    t = (block or "").lower()
    return any(m in t for m in (
        "agent-ia", "guide complet des services", "home\nagent-ia",
        "tout savoir pour vos deplacements",
    ))


def _search_chatbot_page_blocks(question: str) -> dict | None:
    """
    Recherche par blocs sur chatbot-2303.
    Le site n'utilise plus de titres markdown (## / ###) : on découpe le texte
    live par titres de section et questions FAQ.
    """
    qn = _lemmatize(question)
    if not qn or len(qn) < 2:
        return None

    query_words = _faq_query_tokens(qn)
    if not query_words:
        return None

    url = "https://demdikk.sn/chatbot-2303/"
    page_text = _fetch_page_text(url)
    if not page_text:
        return None

    # Extraction directe pour sujets connus
    qn_raw = _norm(question)
    _topic_markers: list[tuple[str, tuple[str, ...]]] = [
        ("perturbation", ("Gestion des perturbations", "Communication de crise", "Incidents techniques")),
        ("enfant", ("carte pour mon enfant", "Puis-je obtenir une carte pour mon enfant")),
        ("bagage", (
            "Politique bagages",
            "Politique des Bagages",
            "Bagages et colis : règles et conditions",
            "Bagages à bord",
        )),
        ("suivre", ("Pour suivre votre colis",)),
        ("suivi", ("Pour suivre votre colis", "Suivi de colis")),
        ("colis", (
            "Tarifs pour l'envoi de colis",
            "Tarifs pour l'envoi de colis",
            "Nos tarifs sont très compétitifs",
            "SERVICE MESSAGERIE EXPRESS (COLIS ET COURRIERS)",
        )),
        ("messagerie", (
            "Tarifs pour l'envoi de colis",
            "SERVICE MESSAGERIE EXPRESS (COLIS ET COURRIERS)",
        )),
        ("courrier", (
            "Tarifs pour l'envoi de colis",
            "SERVICE MESSAGERIE EXPRESS (COLIS ET COURRIERS)",
        )),
        ("tek dem", (
            "Tek Dem : rechargeable",
            "Carte Tek Dem",
            "pass Tek Dem",
        )),
        ("tekdem", (
            "Tek Dem : rechargeable",
            "Carte Tek Dem",
        )),
        ("rechargement", (
            "recharger",
            "rechargement",
            "Tek Dem",
        )),
        ("recrutement", ("recrutement", "Recrutement", "offres d'emploi")),
        ("emploi", ("recrutement", "Recrutement", "offres d'emploi")),
        ("remboursement", (
            "Remboursement",
            "Les demandes de remboursement",
            "Remboursement de billet",
        )),
        ("annulation", ("Annulation et report", "Annulation/Report")),
        ("report", ("Annulation et report", "report de votre voyage")),
        ("reservation", ("Réservation et modification", "Gestion des réservations")),
        ("disponibilite", (
            "Disponibilité de l'application mobile Dem Dikk",
            "Disponibilité de l'application mobile",
        )),
        ("application", (
            "Disponibilité de l'application mobile Dem Dikk",
            "Fonctionnalités de l'application mobile Dem Dikk",
        )),
        ("appli", (
            "Disponibilité de l'application mobile Dem Dikk",
        )),
        ("google play", (
            "Disponibilité de l'application mobile Dem Dikk",
            "Google Play Store",
            "Google Play",
        )),
        ("app store", (
            "Disponibilité de l'application mobile Dem Dikk",
            "App Store",
        )),
        ("cco", (
            "Centre de Contrôle des Opérations (CCO)",
            "Centre de Contrôle des Opérations",
            "Centre de Controle des Operations",
        )),
        ("centre de controle", (
            "Centre de Contrôle des Opérations (CCO)",
            "Centre de Contrôle des Opérations",
            "Centre de Controle des Operations",
        )),
        ("tour de controle", (
            "Centre de Contrôle des Opérations (CCO)",
            "tour de contrôle",
            "tour de controle",
        )),
        ("magal", (
            "MAGAL EDITION 2026",
            "grand Magal de Touba",
            "Magal de Touba",
        )),
        ("tabaski", (
            "Tabaski",
            "offres spéciales de transport",
        )),
        ("gamou", (
            "Gamou",
            "Magal de Touba, Gamou",
        )),
        ("korite", (
            "Korité",
            "Korite",
            "Magal de Touba, Gamou, Korité",
        )),
    ]
    for key, markers in _topic_markers:
        if key in qn_raw:
            section = _extract_section_priority(page_text, markers, max_chars=1800)
            if section and len(section) >= 40 and not _block_looks_like_nav_junk(section):
                section = _clip_at_next_section_title(section)
                section = _faq_clip_for_question_intent(question, qn_raw, section)
                result = _make_chatbot_result(section)
                result["sources"] = [{"title": "FAQ Dakar Dem Dikk", "url": url, "score": 0.95}]
                if result.get("results"):
                    result["results"][0]["url"] = url
                return result

    blocks = _split_chatbot_page_blocks(page_text)
    best = None
    best_score = 0

    for block in blocks:
        if _answer_looks_like_junk(block) or _block_looks_like_nav_junk(block):
            continue
        first = block.split("\n")[0]
        title_n = _lemmatize(first)
        body_n = _lemmatize(block)
        score = 0
        for w in query_words:
            if w in title_n:
                score += 4
            elif w in body_n:
                score += 1
        if not _block_is_title_only(block):
            score += 3
        if any(l.startswith(("\u2013", "-", "\u2022")) for l in block.split("\n")[1:]):
            score += 2
        if _block_is_title_only(block):
            score -= 4
        first_norm = _norm(first)
        for w in query_words:
            if first_norm == w or first_norm.startswith(w + " ") or first_norm.endswith(" " + w):
                score += 6
            if re.search(rf"\({re.escape(w)}\)", first, re.I):
                score += 8
        if score > best_score:
            best_score = score
            best = block

    min_score = 3 if len(query_words) == 1 else 2
    if not best or best_score < min_score:
        return None

    # FAQ : question seule (« … ? ») ou titre de rubrique → inclure les blocs suivants
    try:
        bi = blocks.index(best)
        first_line = best.split("\n")[0].strip()
        if first_line.endswith("?") and bi + 1 < len(blocks):
            nxt = blocks[bi + 1]
            if (
                not _answer_looks_like_junk(nxt)
                and not _block_looks_like_nav_junk(nxt)
                and len(nxt.strip()) >= 20
            ):
                best = best + "\n\n" + nxt.strip()
        elif _block_is_title_only(best):
            best = _expand_block_with_following(blocks, bi)
    except ValueError:
        pass

    section = _clip_at_next_section_title(best[:1800])
    section = _faq_clip_for_question_intent(question, qn_raw, section)
    result = _make_chatbot_result(section)
    result["sources"] = [{
        "title": "FAQ Dakar Dem Dikk",
        "url": url,
        "score": round(best_score / max(len(query_words), 1), 2),
    }]
    if result.get("results"):
        result["results"][0]["url"] = url
    return result


def _smart_search_chatbot_page(question: str) -> dict | None:
    """
    Fallback générique sur la page chatbot-2303 (titres plain-text, pas de ##).
    """
    return _search_chatbot_page_blocks(question)


def _fallback_presentation_page(question: str) -> dict | None:
    """
    Fallback ciblé sur la page présentation de DDD.
    Nom de la société seul → synthèse courte ; sinon scrape de demdikk.sn/presentation/.
    """
    qn = _norm(question)
    if _is_bare_company_name_query(qn):
        return _company_short_presentation_payload()

    import sys as _sys
    url = "https://demdikk.sn/presentation/"
    try:
        _sys.path.insert(0, os.path.join(os.path.dirname(__file__), "data"))
        from scrape_urls import scrape_one as _scrape_one
        doc = _scrape_one(url)
        if not doc or not doc.get("text"):
            return None
        return {
            "answer": doc["text"],
            "summary": "Présentation de Dakar Dem Dikk",
            "sources": [{"title": "Présentation – Dakar Dem Dikk", "url": url, "score": 0.9}],
            "results": [],
            "query_type": "general",
            "has_structured_data": False,
            "is_city_query": False,
            "is_line_query": False,
            "needs_clarification": False,
        }
    except Exception as e:
        print(f"[_fallback_presentation_page] Erreur : {e}", file=_sys.stderr)
        return None


_COMPANY_NAME_QUERIES = frozenset({
    "dakar dem dikk", "dem dikk", "demdikk", "ddd",
    "dakar dem-dikk", "dakar demdikk",
})

_COMPANY_SHORT_PRESENTATION = (
    "Dakar Dem Dikk (DDD) est l'opérateur public de transport en commun au Sénégal. "
    "Créée en janvier 2001, elle gère le réseau urbain de Dakar et sa banlieue, "
    "le réseau interurbain Sénégal Dem Dikk, les navettes AIBD et les liaisons Afrique Dem Dikk. "
    "Assistance : +221 33 824 10 10."
)


def _is_bare_company_name_query(qn: str) -> bool:
    """Nom de la société seul (DDD, Dakar Dem Dikk…) — pas historique / directeurs / mission détaillée."""
    qn = (qn or "").strip()
    if not qn:
        return False
    if qn in _COMPANY_NAME_QUERIES:
        return True
    if qn.replace(" ", "") in {"demdikk", "ddd"}:
        return True
    tokens = [t for t in qn.split() if t not in ("de", "la", "le", "les", "du", "des", "sur", "a", "au")]
    return bool(
        tokens
        and all(t in {"dakar", "dem", "dikk", "demdikk", "ddd"} for t in tokens)
    )


def _company_short_presentation_payload() -> dict:
    url = "https://demdikk.sn/presentation/"
    return {
        "answer": _COMPANY_SHORT_PRESENTATION,
        "summary": "Dakar Dem Dikk (DDD)",
        "sources": [{"title": "Présentation – Dakar Dem Dikk", "url": url, "score": 1.0}],
        "results": [],
        "query_type": "general",
        "has_structured_data": False,
        "is_city_query": False,
        "is_line_query": False,
        "needs_clarification": False,
        "show_more_info": True,
    }


_PRESENTATION_EXCLUDE_HINTS = (
    "cco", "centre de controle", "tour de controle",
    "bagage", "remboursement", "annulation", "report", "reservation",
    "colis", "messagerie", "tek dem", "carte", "pass", "geolocalisation",
    "perturbation", "recrutement", "emploi", "ligne", "horaire", "tarif",
    "billet", "navette", "aibd", "interurbain", "senegal dem dikk",
    "louer un bus", "location", "publicite", "partenariat",
)


def _is_presentation_query(question: str, qn: str | None = None) -> bool:
    """
    Questions sur la société elle-même (présentation, identité, histoire…).
    Ex. « Dakar dem dikk », « c'est quoi DDD ? », « présentation ».
    """
    qn = qn if qn is not None else _norm(question)
    if not qn:
        return False
    if any(h in qn for h in _PRESENTATION_EXCLUDE_HINTS):
        return False
    if qn in _COMPANY_NAME_QUERIES:
        return True
    # Nom de la société seul (tokens stopwords exclus)
    tokens = [t for t in qn.split() if t not in ("de", "la", "le", "les", "du", "des", "sur", "a", "au")]
    if tokens and all(t in {"dakar", "dem", "dikk", "demdikk", "ddd", "senegal", "sénégal"} for t in tokens):
        if "dikk" in tokens or "demdikk" in tokens or "ddd" in tokens:
            return True
    presentation_markers = (
        "presentation", "présentation", "presenter", "présenter",
        "c est quoi", "qu est ce que", "quest ce que", "c est qui",
        "parle moi de", "parlez moi de", "histoire de ddd", "histoire de dem dikk",
        "connaitre ddd", "connaître ddd", "entreprise dem dikk",
        "societe dem dikk", "société dem dikk",
        "mission", "vision", "valeurs", "objectif", "objectifs",
        "histoire", "creation", "création",
    )
    if any(m in qn for m in presentation_markers):
        return True
    if re.search(r"\bmission\b", qn) and any(
        b in qn for b in ("dem dikk", "demdikk", "ddd", "dakar dem")
    ):
        return True
    return False


def _fallback_from_site(question: str) -> dict | None:
    """
    Fallback universel : recherche par blocs sur chatbot-2303 (sans titres ##).
    """
    return _search_chatbot_page_blocks(question)


def _fix_orphan_subitems(text: str) -> str:
    """
    Ajoute le marqueur '–' aux sous-items qui suivent un bullet se terminant par ':'
    mais n'ont pas leur propre marqueur de puce.
    Ex: '— Vous pouvez réserver :\nvia l'application...'
     → '— Vous pouvez réserver :\n– via l'application...'
    """
    import re
    lines = text.split('\n')
    result = []
    in_sublist = False

    for line in lines:
        stripped = line.strip()

        if not stripped:
            result.append(line)
            continue

        # Titre ### ou ## → fin du sous-groupe
        if re.match(r'^#{1,3}', stripped):
            in_sublist = False
            result.append(line)
            continue

        # Ligne avec marqueur de puce existant (–, —, •, -, ▸)
        if re.match(r'^[\u2014\u2013\u2022\-\u25b8]', stripped):
            # Les lignes suivantes sans marqueur sont des sous-items si cette ligne finit par ':'
            in_sublist = stripped.rstrip().endswith(':')
            result.append(line)
            continue

        # Ligne orpheline après un bullet se terminant par ':'
        if in_sublist and stripped:
            result.append('\u2013 ' + stripped)
            continue

        # Ligne normale → fin du mode sous-liste
        in_sublist = False
        result.append(line)

    return '\n'.join(result)


_FAQ_AFFIRMATION_ONLY = frozenset({"oui", "non"})


def _faq_line_is_subsection_label(line: str) -> bool:
    ln = (line or "").strip()
    if not ln.endswith(":"):
        return False
    label = ln[:-1].strip()
    return 4 <= len(label) <= 55 and label[0].isupper()


def _merge_broken_faq_lines(lines: list[str]) -> list[str]:
    """Fusionne les lignes fragmentées par le scraping HTML (ex. « Oui » + « , Dakar… »)."""
    import re

    merged: list[str] = []
    buf = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        low = ln.lower()
        if low in _FAQ_AFFIRMATION_ONLY:
            if buf:
                merged.append(buf.strip())
            buf = "Oui" if low == "oui" else "Non"
            continue
        is_cont = (
            ln.startswith((",", ";", ")", "»", "«", "—", "–", "-", ".", ":"))
            or (buf.endswith(",") or buf.endswith("'") or buf.endswith("d'") or buf.endswith("l'"))
            or (buf and not re.search(r'[.!?»"]\s*$', buf) and len(ln) < 90)
        )
        if is_cont and buf:
            buf += ln if ln.startswith((",", ";", ".", ":", ")", "»", "—", "–", "-")) else f" {ln}"
        elif buf:
            merged.append(buf.strip())
            buf = ln
        else:
            buf = ln
    if buf:
        merged.append(buf.strip())
    return merged


def _format_faq_page_prose(text: str) -> str:
    """Reformate un extrait FAQ chatbot-2303 en prose lisible (sans titres / « Oui » isolés)."""
    import re

    if not (text or "").strip():
        return text or ""
    raw = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()]
    if len(raw) <= 1:
        return re.sub(r"\s+", " ", raw[0] if raw else text).strip()

    title = raw[0]
    body_in = raw[1:]
    chunks = _merge_broken_faq_lines(body_in)
    if not chunks:
        return re.sub(r"\s+", " ", title).strip()

    paragraphs: list[str] = []
    i = 0
    while i < len(chunks):
        c = chunks[i]
        if _faq_line_is_subsection_label(c) and i + 1 < len(chunks):
            label = c[:-1].strip()
            nxt = chunks[i + 1]
            if nxt and nxt[0].isupper():
                nxt = nxt[0].lower() + nxt[1:]
            paragraphs.append(f"{label} : {nxt}")
            i += 2
        else:
            paragraphs.append(c)
            i += 1

    paragraphs = [re.sub(r"\s+", " ", p).strip() for p in paragraphs if p.strip()]
    paragraphs = [
        re.sub(r"\b([ld])'\s+", r"\1'", p, flags=re.I) for p in paragraphs
    ]
    if len(paragraphs) == 1:
        return paragraphs[0]
    return "\n\n".join(paragraphs)


def _light_clean(text: str) -> str:
    """Nettoyage minimal : retire seulement les ## de niveau section (## 8.) mais conserve
    les sous-titres ### et les tirets – pour que le frontend les rende en blocs."""
    import re
    if not text:
        return text
    # Enlever les numéros de section ## N. (ex: "## 8. Bagages…" → "Bagages…")
    text = re.sub(r'#{1,2}\s*\d+\.\s*', '', text)
    # Enlever les balises nav/menu parasites
    text = _strip_nav_content(text)
    # Supprimer le titre orphelin en début de section : si la 1ère ligne non-vide est un
    # court texte sans puce ni ### et qu'elle est immédiatement suivie d'une ligne ###,
    # c'est le vestige de "## N. Titre" après suppression du préfixe "## N."
    _first_lines = text.split('\n')
    _non_empty = [l for l in _first_lines if l.strip()]
    if (len(_non_empty) >= 2
            and _non_empty[0].strip()
            and not _non_empty[0].strip().startswith('###')
            and not re.match(r'^\s*[-\u2013\u2014\u2022\u25b8]', _non_empty[0])
            and len(_non_empty[0].strip()) < 70
            and _non_empty[1].strip().startswith('###')):
        # Retirer la première ligne non-vide (titre orphelin)
        removed = False
        result_lines = []
        for l in _first_lines:
            if not removed and l.strip() == _non_empty[0].strip():
                removed = True
                continue
            result_lines.append(l)
        text = '\n'.join(result_lines)
    # Corriger les sous-items orphelins (sans marqueur de puce)
    text = _fix_orphan_subitems(text)
    _BULLET_CHARS = r'[-\u2013\u2014\u2022\u25b8\u25ba\u25cf]'
    # Supprimer uniquement les lignes "– Référence/Voir : URL" (citations parasites)
    text = re.sub(
        r'^\s*' + _BULLET_CHARS + r'?\s*(?:R[e\u00e9]f[e\u00e9]rence|Voir)\s*:\s*https?://\S+\s*$',
        '', text, flags=re.MULTILINE | re.IGNORECASE
    )
    # Supprimer les fragments d'URL tronqués (ex: "tps://...", "ps://...")
    text = re.sub(r'\b(?:tps|ttp|ps|s)://\S+', '', text)
    # Fusionner les lignes "– label :" avec la ligne d'URL suivante (ex: "– Google Play :" + "– https://...")
    # → "– Google Play : https://..."
    _lines = text.split('\n')
    _merged = []
    _i = 0
    while _i < len(_lines):
        _line = _lines[_i]
        _label_m = re.match(r'^(\s*' + _BULLET_CHARS + r'\s*.{1,60}):\s*$', _line)
        if _label_m and _i + 1 < len(_lines):
            _next = _lines[_i + 1].strip()
            _url_m = re.match(r'^' + _BULLET_CHARS + r'?\s*(https?://\S+)', _next)
            if _url_m:
                _merged.append(_line.rstrip() + ' ' + _url_m.group(1))
                _i += 2
                continue
        _merged.append(_line)
        _i += 1
    text = '\n'.join(_merged)
    # Supprimer les lignes qui ne contiennent plus que la puce/tiret seule
    text = re.sub(
        r'^\s*' + _BULLET_CHARS + r'\s*$',
        '', text, flags=re.MULTILINE
    )
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    # Disclaimer générique en bas de page chatbot-2303
    text = re.sub(
        r"site web officiel de dakar dem dikk\s+pour toute information sensible ou offre officielle\.?",
        "",
        text,
        flags=re.I,
    )
    # Supprimer les puces/tirets isolés en fin de texte
    text = re.sub(r'(\s*' + _BULLET_CHARS + r'\s*)+$', '', text.rstrip())
    return text.strip()


def _faq_clip_for_question_intent(question: str, qn_raw: str, section: str) -> str:
    """Rogne les sections FAQ non pertinentes (ex. tarification après une question suivi colis)."""
    text = (section or "").strip()
    if not text:
        return text
    if any(k in qn_raw for k in ("suivi", "suivre", "tracking")) and "colis" in qn_raw:
        for marker in ("\n\nTarification", "\nTarification", "\n\nLes tarifs", "\n\nNos tarifs"):
            idx = text.find(marker)
            if idx > 0:
                return text[:idx].strip()
    if any(k in qn_raw for k in ("poids", "dimension", "maximum", "tarif", "prix", "combien")):
        if "colis" in qn_raw or "messagerie" in qn_raw:
            for marker in ("\n\nSuivi de colis", "\nSuivi de colis", "\n\nPour suivre"):
                idx = text.find(marker)
                if idx > 0:
                    return text[:idx].strip()
    return text


def _make_chatbot_result(section: str) -> dict:
    """Construit un dict résultat standard depuis un extrait de page officielle."""
    prose = _format_faq_page_prose(section) if section else section
    clean = _light_clean(prose) if prose else prose
    return {
        "answer": clean,
        "summary": (clean or "")[:280],
        "bullets": [],
        "sources": [{"title": "Dakar Dem Dikk", "url": "https://demdikk.sn/", "score": 1.0}],
        "results": [{"url": "https://demdikk.sn/", "title": "Dakar Dem Dikk", "snippet": (clean or "")[:500], "full_text": clean}],
        "query_type": "general",
        "needs_clarification": False,
        "has_structured_data": False,
        "is_city_query": False,
        "is_line_query": False,
    }


def _rag_answer_usable(data: dict) -> bool:
    """
    True si la réponse issue de app_backup (index RAG : data/metadata.json + embeddings.npy)
    est déjà exploitable — dans ce cas on ne doit PAS la remplacer par un scraping live
    (_fallback_from_site / smart search) qui court-circuite l'index.
    """
    if not data:
        return False
    ans = (data.get("answer") or "").strip()
    if not ans or "je n'ai pas trouv" in ans.lower():
        return False
    if data.get("is_line_query") or data.get("is_city_query"):
        return True
    if data.get("has_structured_data"):
        return True
    qtype = (data.get("query_type") or "").strip()
    if qtype in ("all_lines_summary", "line_X", "lines_to_stop", "line_details", "city_info"):
        return True
    src0 = (data.get("sources") or [{}])[0] if data.get("sources") else {}
    score = float(src0.get("score") or 0)
    # Même seuil que app_backup._search (0.30), avec une petite marge
    if qtype == "general" and score >= 0.28 and len(ans) >= 80:
        return True
    return False


def _rag_answer_trustworthy(data: dict, question: str, qn: str | None = None) -> bool:
    """RAG exploitable ET pertinent (pas un chunk actualité / hors-sujet)."""
    if not _rag_answer_usable(data):
        return False
    qn = qn if qn is not None else _norm(question)
    ans = (data.get("answer") or "").strip()
    if _answer_looks_like_junk(ans):
        return False
    return _answer_relevant_to_question(question, ans, qn)


def _prepare_final_answer(data: dict) -> dict:
    """Réponse finale sans reformulation LLM (contenu site / index tel quel, nettoyé)."""
    out = dict(data)
    cleaned = _clean_raw_answer(out.get("answer") or "")
    if cleaned:
        out["answer"] = cleaned
    out["llm_enhanced"] = False
    out["gemini_enhanced"] = False
    return out


def _enhance_if_safe(data: dict, question: str, client_history: list | None = None) -> dict:
    """
    Reformule avec DeepSeek (voix Maï) à partir du contenu site/index.
    Si DeepSeek refuse ou invente → retombe sur le texte site nettoyé ou FAQ live.
    """
    fallback = _prepare_final_answer(data)
    skip_types = set(_STRUCTURED_QUERY_TYPES)
    if data.get("query_type") in skip_types or data.get("is_line_query"):
        return fallback

    if _answer_says_missing(fallback.get("answer") or ""):
        fb = _search_chatbot_page_blocks(question)
        if _faq_answer_usable(fb, question):
            return _prepare_final_answer(fb)

    cfg = _init_deepseek()
    if cfg is None:
        return fallback
    enhanced = _enhance_with_deepseek(data, question, client_history)
    ans = (enhanced.get("answer") or "").strip()
    if (
        enhanced.get("llm_enhanced")
        and ans
        and len(ans) >= 20
        and not _deepseek_missing_info(ans)
        and not _answer_says_missing(ans)
    ):
        return enhanced

    if _answer_says_missing(ans):
        fb = _search_chatbot_page_blocks(question)
        if _faq_answer_usable(fb, question):
            return _prepare_final_answer(fb)

    if ans and not _answer_says_missing(ans):
        return enhanced
    return fallback


# ── Envelopper /ask avec DeepSeek ────────────────────────────────────────────
_original_ask = app.view_functions.get("ask")

if _original_ask:
    @functools.wraps(_original_ask)
    def _ask_with_deepseek():
        from flask import request, jsonify, g
        # Récupérer la question avant l'appel original
        body = request.get_json(silent=True) or {}
        question = body.get("question", "")
        if "history" in body:
            client_history = _parse_client_history(body.get("history"))
        else:
            client_history = _parse_client_history(body.get("conversationHistory"))
        qn = _norm(question)

        recovery = _try_typo_recovery(question, qn)
        if recovery:
            fixed_q, fixed_qn, corrs = recovery
            if _should_apply_typo_fix(question, qn, fixed_q, fixed_qn, corrs):
                question = fixed_q
                qn = fixed_qn

        _expand_acr = getattr(_mod, "_expand_query_acronyms", None)
        if callable(_expand_acr):
            question = _expand_acr(question)
            qn = _norm(question)

        def _reply(data, enhance=True):
            payload = _enhance_if_safe(data, question, client_history) if enhance else data
            return jsonify(payload)

        conv_kind = _conversational_kind(question, qn)
        if conv_kind:
            return jsonify(_generate_friendly_reply(question, client_history, conv_kind))

        # Publicité / partenariat — avant RAG (évite inférence arrêt « de la »)
        if _is_publicite_query(qn):
            fb_pub = _fallback_publicite_partenariat(question)
            if fb_pub and fb_pub.get("answer"):
                return _reply(fb_pub)

        # « Que signifie SDD / DDD / ADD ? » — avant présentation longue
        _acr_fn = getattr(_mod, "_json_acronym_definition", None)
        if callable(_acr_fn):
            _acr_payload = _acr_fn(question, qn)
            if _acr_payload:
                return _reply(_acr_payload, enhance=False)

        # ── Présentation DDD (nom de la société, « c'est quoi DDD », etc.) ───
        if _is_presentation_query(question, qn):
            fb_pres = _fallback_presentation_page(question)
            if fb_pres:
                enhance_pres = not _is_bare_company_name_query(qn)
                return _reply(fb_pres, enhance=enhance_pres)

        # Hors-sujet : mots ambigus (sport, météo…) seulement sans contexte transport DDD
        if _is_strict_off_topic(question, qn):
            _off = {
                "answer": _OFF_TOPIC_REPLY,
                "summary": _OFF_TOPIC_REPLY[:200],
                "bullets": [],
                "sources": [{"title": "Assistant Dakar Dem Dikk", "url": "https://demdikk.sn/", "score": 1.0}],
                "results": [],
                "query_type": "general",
                "needs_clarification": False,
                "show_more_info": False,
            }
            return jsonify(_off)

        # Transmettre la question corrigée (typos) à app_backup — il relit request.get_json() sinon.
        g.resolved_question = question
        original_response = _original_ask()

        # Extraire les données JSON de la réponse
        try:
            rest = ()
            if hasattr(original_response, "get_json"):
                data = original_response.get_json(force=True) or {}
            else:
                # Réponse tuple (response, status_code)
                resp_obj, *rest = original_response if isinstance(original_response, tuple) else (original_response,)
                data = resp_obj.get_json(force=True) or {}

            # Comparaison X vs Y — DeepSeek dédié (pas enhance générique)
            if data.get("query_type") == "comparison":
                if data.get("comparison_mode") == "both":
                    cmp_final = _enhance_comparison_with_deepseek(data, question)
                    return (jsonify(cmp_final), *rest) if rest else jsonify(cmp_final)
                return (jsonify(data), *rest) if rest else jsonify(data)

            # Interurbain structuré : ne pas écraser par fallback site / LLM
            if data.get("query_type") in ("city_info", "interurban_overview"):
                return (_reply(data, enhance=False), *rest) if rest else _reply(data, enhance=False)

            # TRIGGER 1 — AIBD / navette (app.py ~2739, logique intent dans app_backup)
            _aibd_triggers = getattr(_mod, "_AIBD_TRIGGERS", ())
            _aibd_intent = getattr(_mod, "_aibd_has_specific_intent", None)
            _try_aibd = getattr(_mod, "_try_aibd_specific_answer", None)
            _dbg_trigger = getattr(_mod, "_debug_fixed_trigger", lambda *_: None)
            if _aibd_triggers and any(t in qn for t in _aibd_triggers):
                matched = next(t for t in _aibd_triggers if t in qn)
                if callable(_aibd_intent) and _aibd_intent(qn) and callable(_try_aibd):
                    specific, reason = _try_aibd(question, qn)
                    if specific:
                        _dbg_trigger("aibd", f"keyword={matched!r} specific=yes source={reason}")
                        return (_reply(specific, enhance=False), *rest) if rest else _reply(specific, enhance=False)
                fb_aibd = _fallback_from_site(question)
                if fb_aibd:
                    _dbg_trigger("aibd", f"keyword={matched!r} specific=no source=fixe")
                    return (_reply(fb_aibd, enhance=False), *rest) if rest else _reply(fb_aibd, enhance=False)

            interurban_triggers = (
                "senegal dem dikk",
                "sénégal dem dikk",
                "interurbain",
                "interurbains",
                "reseau-interurbain",
                "réseau-interurbain",
                "dieuppeul",
            )
            wants_interurban = any(t in qn for t in interurban_triggers)
            fb_i = _fallback_interurban(question)
            _acr_def_q = getattr(_mod, "_is_acronym_definition_query", None)
            if fb_i and wants_interurban and not (callable(_acr_def_q) and _acr_def_q(qn)):
                qt_i = fb_i.get("query_type") or data.get("query_type")
                # Liste destinations / fiche ville : pas de reformulation LLM
                enhance_i = qt_i not in ("city_info", "interurban_overview")
                return (_reply(fb_i, enhance=enhance_i), *rest) if rest else _reply(fb_i, enhance=enhance_i)

            # Afrique Dem Dikk : prioritaire pour "gambie/senegal/banjul/afrique"
            af_triggers = ("afrique dem dikk", "afrique", "gambie", "gambia", "banjul", "senegal", "add")
            wants_afrique = any(t in qn for t in af_triggers)
            fb_a = _fallback_afrique_dem_dikk(question)
            if fb_a and wants_afrique:
                enhance_a = not _is_bare_afrique_dem_dikk_query(qn)
                return (_reply(fb_a, enhance=enhance_a), *rest) if rest else _reply(fb_a, enhance=enhance_a)

            ans = (data.get("answer") or "").strip()
            if "je n'ai pas trouv" in ans.lower():
                fb = _fallback_from_site(question)
                if fb:
                    data = fb
            if fb_i and "je n'ai pas trouv" in ans.lower():
                data = fb_i

            rag_ok = _rag_answer_trustworthy(data, question, qn)
            is_structured = _is_structured_ask_response(data)

            # Application mobile : toujours préférer l'extrait page officielle (chatbot-2303)
            # lorsqu'il est disponible — l'index peut renvoyer un chunk « acceptable » (score)
            # mais sans répondre à la question (Play Store, fonctionnalités, etc.).
            if any(k in qn for k in ("application", "appli", "google play", "app store")):
                fb_app = _fallback_from_site(question)
                if fb_app:
                    data = fb_app
                    rag_ok = _rag_answer_usable(data)

            # TRIGGER 3 — Tek Dem (app.py ~2900, logique intent dans app_backup)
            _tek_match_fn = getattr(_mod, "_matches_tek_dem_trigger", None)
            _tek_intent = getattr(_mod, "_tek_dem_has_specific_intent", None)
            _try_tek = getattr(_mod, "_try_tek_dem_specific_answer", None)
            _tek_match = _tek_match_fn(qn) if callable(_tek_match_fn) else None
            if _tek_match:
                if callable(_tek_intent) and _tek_intent(qn) and callable(_try_tek):
                    specific, reason = _try_tek(question, qn)
                    if specific:
                        _dbg_trigger("tek_dem", f"keyword={_tek_match!r} specific=yes source={reason}")
                        return (_reply(specific, enhance=False), *rest) if rest else _reply(specific, enhance=False)
                fb_tek = _fallback_from_site(question)
                _tek_fixe = getattr(_mod, "_tek_dem_fixe_payload", None)
                if callable(_tek_fixe) and fb_tek:
                    fb_tek = _tek_fixe(fb_tek)
                if fb_tek:
                    _dbg_trigger("tek_dem", f"keyword={_tek_match!r} specific=no source=fixe")
                    return (_reply(fb_tek, enhance=False), *rest) if rest else _reply(fb_tek, enhance=False)

            # TRIGGER 2 — Colis / messagerie (app.py ~2802, logique intent dans app_backup)
            _colis_keys = getattr(_mod, "_COLIS_TRIGGERS", ("colis", "messagerie", "courrier"))
            _colis_intent = getattr(_mod, "_colis_has_specific_intent", None)
            _try_colis = getattr(_mod, "_try_colis_specific_answer", None)
            if any(k in qn for k in _colis_keys):
                colis_matched = next(k for k in _colis_keys if k in qn)
                if callable(_colis_intent) and _colis_intent(qn) and callable(_try_colis):
                    already_faq = (
                        _faq_answer_usable(data, question, qn)
                        and _chatbot_faq_score(data) >= 0.5
                    )
                    if not already_faq:
                        specific, reason = _try_colis(question, qn)
                        if specific:
                            _dbg_trigger("colis", f"keyword={colis_matched!r} specific=yes source={reason}")
                            return (_reply(specific, enhance=False), *rest) if rest else _reply(specific, enhance=False)
                    else:
                        clip_fn = getattr(_mod, "_colis_clip_answer_for_intent", None)
                        out = dict(data)
                        if callable(clip_fn):
                            clipped = clip_fn(qn, out.get("answer") or "")
                            out["answer"] = clipped
                            out["summary"] = clipped[:200]
                        _dbg_trigger("colis", f"keyword={colis_matched!r} specific=yes source=backup_faq")
                        return (_reply(out, enhance=False), *rest) if rest else _reply(out, enhance=False)
                elif not (callable(_colis_intent) and _colis_intent(qn)):
                    colis_fb = data if (data.get("answer") and _faq_answer_usable(data, question, qn)) else None
                    if not colis_fb:
                        colis_fb = _fallback_from_site(question)
                    if colis_fb:
                        _dbg_trigger("colis", f"keyword={colis_matched!r} specific=no source=fixe")
                        return (_reply(colis_fb, enhance=False), *rest) if rest else _reply(colis_fb, enhance=False)
            if any(k in qn for k in _colis_keys) and not rag_ok:
                fb2 = _fallback_from_site(question)
                if fb2:
                    _dbg_trigger("colis", f"keyword={colis_matched!r} specific=no source=fixe")
                    return (_reply(fb2, enhance=False), *rest) if rest else _reply(fb2, enhance=False)

            # Fallback page officielle (scraping live) seulement si l'index n'a pas déjà répondu correctement.
            # RÈGLE : tout mot-clé qui déclenche un wants_* dans _fallback_from_site
            # doit être listé ici pour le cas « pas trouvé dans l'index ».
            _site_triggers = (
                # Bagages
                "bagage",
                # Remboursement / Annulation / Report
                "remboursement", "rembourser", "rembourse",
                "annulation", "annuler", "annule",
                "report", "reporte", "reporter",
                # Rechargement carte Tek Dem
                "rechargement", "recharger", "recharge", "rechargez",
                # Tek Dem / carte / pass
                "tek dem", "carte", "pass",
                # Géolocalisation
                "geolocalisation", "geolocalisa",
                "suivi bus", "position bus", "temps reel",
                # Contact / assistance
                "contact", "service client", "horaire agence", "assistance",
                # Objets perdus / Carte perdue/volée
                "objet perdu", "objet", "perdu", "perdus",
                "volee", "vole", "duplicata", "opposition",
                # Services spéciaux
                "fess dem",
                "aibd", "aeroport", "blaise diagne",
                # Abonnement / colis / messagerie
                "abonnement",
                "colis", "messagerie", "courrier",
                # Application
                "application", "appli",
                # Location
                "location",
                # Partenariat
                "partenariat", "publicite",
                # Services / offres
                "service", "offre",
                # Réservation / modification de billet
                "reservation", "reserver", "reservez",
                "modifier", "modification", "billet",
                # Présentation / directeurs / historique
                "directeur", "directeurs",
                "presentation", "historique", "histoire",
                "assane", "thierno",
                # Emploi / recrutement
                "emploi", "recrutement", "candidature",
                # Perturbations / crises / communication
                "communication", "crise", "perturbation",
                "incident", "intemperie", "greve",
                "retard", "panne", "maintenance", "innovation",
            )
            # Sujets sensibles liés à la page présentation : toujours forcer le fallback
            # sur la page presentation/ (pas chatbot-2303) car l'index peut ramener
            # un chunk générique avec un score supérieur masquant la vraie réponse.
            _presentation_triggers = (
                "directeur", "directeurs", "dg ", "pdg",
                "predecesseur", "prédécesseur", "successeur", "successeurs",
                "avant lui", "avant elle", "qui etait", "qui était",
                "presentation", "historique", "histoire", "creation",
                "assane", "mbengue", "thierno", "ousmane sylla",
                "conseil d'administration", "actionnariat",
                "emploi", "recrutement", "candidature",
                "fondateur", "capital social", "actionnaire",
                "christian salvy", "moussa diagne", "dame diop",
                "moussa diop", "omar sylla", "mamadou goudiaby",
            )
            if any(k in qn for k in _presentation_triggers):
                fb_pres = _fallback_presentation_page(question)
                if fb_pres:
                    return (_reply(fb_pres), *rest) if rest else _reply(fb_pres)

            if (
                any(k in qn for k in _site_triggers)
                and not rag_ok
                and data.get("query_type") != "city_info"
            ):
                fb3 = _fallback_from_site(question)
                if fb3:
                    return (_reply(fb3, enhance=False), *rest) if rest else _reply(fb3, enhance=False)

            # ── FAQ chatbot-2303 : recherche systématique (hors requêtes structurées) ──
            fb_faq = None
            if not is_structured:
                fb_faq = _search_chatbot_page_blocks(question)
                faq_score = _chatbot_faq_score(fb_faq)
                cur_ans = (data.get("answer") or "").strip()
                ans_weak = (
                    _answer_says_missing(cur_ans)
                    or not cur_ans
                    or not rag_ok
                    or not _answer_relevant_to_question(question, cur_ans, qn)
                )
                if fb_faq and _faq_answer_usable(fb_faq, question, qn) and (faq_score >= 0.5 or ans_weak):
                    return (_reply(fb_faq, enhance=False), *rest) if rest else _reply(fb_faq, enhance=False)

            if rag_ok:
                enhanced = _enhance_if_safe(data, question, client_history)
                ans = (enhanced.get("answer") or "").strip()
                if not _block_looks_like_nav_junk(ans) and not _answer_says_missing(ans):
                    return (_reply(data), *rest) if rest else _reply(data)

            enhanced = _enhance_if_safe(data, question, client_history)
            if _answer_says_missing(enhanced.get("answer") or ""):
                if not fb_faq:
                    fb_faq = _search_chatbot_page_blocks(question)
                if _faq_answer_usable(fb_faq, question, qn):
                    return (_reply(fb_faq, enhance=False), *rest) if rest else _reply(fb_faq, enhance=False)
                _log_unknown_query(question, reason="not_found")
            elif "je n'ai pas trouv" in (enhanced.get("answer") or "").lower():
                _log_unknown_query(question, reason="not_found")
            return (_reply(data), *rest) if rest else _reply(data)
        except Exception:
            return original_response

    _ask_with_deepseek._ddd_wrapper = True  # marqueur pour le diagnostic /api/wrapper_ping
    app.view_functions["ask"] = _ask_with_deepseek


@app.route("/api/wrapper_ping", methods=["GET"])
def _api_wrapper_ping():
    """
    Diagnostic : si cette route répond 404, Gunicorn ne charge pas app.py
    (souvent ExecStart = app_backup:app au lieu de app:app).
    Si 'ask_wrapped_deepseek' = False, c'est que app.view_functions['ask']
    n'a pas pu être remplacé par le wrapper.
    """
    from flask import jsonify

    fn = app.view_functions.get("ask")
    return jsonify(
        {
            "ok": True,
            "wrapper": "app.py",
            "wrapper_file": os.path.abspath(__file__),
            "flask_impl_module": _IMPL_MODULE_NAME,
            "ask_view_qualname": getattr(fn, "__qualname__", None),
            "ask_wrapped_deepseek": bool(getattr(fn, "_ddd_wrapper", False)),
            "deepseek_key_present": bool((os.environ.get("DEEPSEEK_API_KEY") or "").strip()),
        }
    )


print(
    "[dakar_dem_dikk] Wrapper app.py chargé — Gunicorn doit utiliser « app:app » (pas app_backup:app). "
    "Test : GET /api/wrapper_ping",
    file=sys.stderr,
    flush=True,
)


# ── Route /refresh_index (ajout par rapport au backup) ───────────────────────
def _get_refresh_token_from_request(req):
    auth = (req.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    try:
        data = req.get_json(silent=True) or {}
    except Exception:
        data = {}
    return (data.get("token") or "").strip()


@app.route("/refresh_index", methods=["POST"])
def refresh_index():
    """sync_interurbain + scraper + indexer, puis recharge metadata/embeddings en mémoire."""
    from flask import request as _req, jsonify as _jsonify
    expected = (os.environ.get("REFRESH_TOKEN") or "").strip()
    if not expected:
        return _jsonify({"error": "REFRESH_TOKEN not configured"}), 503
    provided = _get_refresh_token_from_request(_req)
    if not provided or provided != expected:
        return _jsonify({"error": "Unauthorized"}), 401
    started = time.time()
    python = sys.executable or "python"
    _root = os.path.dirname(os.path.abspath(__file__))
    _data_dir = os.path.join(_root, "data")
    try:
        os.makedirs(_data_dir, exist_ok=True)
        skip_inter = (os.environ.get("SKIP_SYNC_INTERURBAIN") or "").strip().lower() in (
            "1", "true", "yes",
        )
        if not skip_inter:
            snap = os.path.join(_data_dir, "interurbain_snapshot.json")
            sync_i = _subprocess.run(
                [python, "sync_interurbain.py", "--write", "--json-out", snap],
                cwd=_root,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if sync_i.returncode != 0:
                return _jsonify({
                    "error": "sync_interurbain_failed",
                    "returncode": sync_i.returncode,
                    "stderr": (sync_i.stderr or "")[-4000:],
                }), 500
        scrape = _subprocess.run(
            [python, "scraper.py"],
            cwd=_root,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if scrape.returncode != 0:
            return _jsonify({
                "error": "scraper_failed",
                "returncode": scrape.returncode,
                "stderr": (scrape.stderr or "")[-4000:],
            }), 500
        index = _subprocess.run(
            [python, "indexer.py"],
            cwd=_root,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if index.returncode != 0:
            return _jsonify({
                "error": "indexer_failed",
                "returncode": index.returncode,
                "stderr": (index.stderr or "")[-4000:],
            }), 500
        docs_count, embeddings_loaded = _mod._reload_index_from_disk()
        _mod.last_index_refresh = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        elapsed_ms = int((time.time() - started) * 1000)
        return _jsonify({
            "status": "ok",
            "elapsed_ms": elapsed_ms,
            "documents_count": docs_count,
            "embeddings_loaded": embeddings_loaded,
            "last_index_refresh": _mod.last_index_refresh,
            "interurbain_synced": not skip_inter,
            "note": "Redémarrer le worker Flask si les réponses villes interurbaines ne reflètent pas interurbain_data.py (module chargé au démarrage).",
        })
    except _subprocess.TimeoutExpired:
        return _jsonify({"error": "timeout"}), 504


@app.route("/reload_embeddings", methods=["POST"])
def reload_embeddings():
    """Recharge uniquement metadata/embeddings depuis le disque (après update_from_site.py sur la même machine)."""
    from flask import request as _req, jsonify as _jsonify
    expected = (os.environ.get("REFRESH_TOKEN") or "").strip()
    if not expected:
        return _jsonify({"error": "REFRESH_TOKEN not configured"}), 503
    provided = _get_refresh_token_from_request(_req)
    if not provided or provided != expected:
        return _jsonify({"error": "Unauthorized"}), 401
    try:
        docs_count, embeddings_loaded = _mod._reload_index_from_disk()
        _mod.last_index_refresh = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        return _jsonify({
            "status": "ok",
            "documents_count": docs_count,
            "embeddings_loaded": embeddings_loaded,
            "last_index_refresh": _mod.last_index_refresh,
        })
    except Exception as e:
        return _jsonify({"error": str(e)}), 500


# ── Admin : questions sans réponse ───────────────────────────────────────────

_ADMIN_HTML = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin — Questions sans réponse</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:#f4f6f9;color:#222}
header{background:#d32f2f;color:#fff;padding:1rem 1.5rem;display:flex;align-items:center;gap:1rem;flex-wrap:wrap}
header h1{font-size:1.2rem;flex:1}
.btn-header{background:#fff;color:#d32f2f;border:none;border-radius:6px;padding:.45rem 1rem;cursor:pointer;font-weight:600;font-size:.9rem}
.btn-header:hover{opacity:.85}
.btn-danger{background:#b71c1c;color:#fff}
.btn-danger:hover{background:#7f0000}
.btn-copy{background:none;border:none;cursor:pointer;padding:.15rem .35rem;border-radius:4px;color:#aaa;font-size:.9rem;line-height:1;transition:color .15s}
.btn-copy:hover{color:#d32f2f}
.copied{color:#43a047!important}
.badge{background:#fff3;border-radius:12px;padding:.2rem .7rem;font-size:.85rem}
main{padding:1.5rem;max-width:1100px;margin:auto}
.filters{display:flex;gap:.75rem;flex-wrap:wrap;margin-bottom:1rem;align-items:center}
.filters input,.filters select{padding:.45rem .75rem;border:1px solid #ccc;border-radius:6px;font-size:.9rem}
.filters label{font-size:.9rem;display:flex;align-items:center;gap:.4rem}
table{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 4px #0001}
th{background:#fafafa;padding:.75rem 1rem;text-align:left;font-size:.82rem;color:#555;border-bottom:2px solid #eee}
td{padding:.7rem 1rem;border-bottom:1px solid #f0f0f0;font-size:.88rem;vertical-align:top}
tr:last-child td{border-bottom:none}
tr:hover td{background:#fafef0}
.handled td{opacity:.5}
.badge-count{background:#d32f2f;color:#fff;border-radius:10px;padding:.15rem .55rem;font-size:.8rem;font-weight:700}
.btn-sm{padding:.3rem .7rem;border:none;border-radius:5px;cursor:pointer;font-size:.82rem;font-weight:600}
.btn-handled{background:#43a047;color:#fff}
.btn-handled:hover{background:#2e7d32}
.note-input{width:100%;padding:.3rem .5rem;border:1px solid #ddd;border-radius:4px;font-size:.83rem}
.empty{text-align:center;padding:3rem;color:#999}
.group-block{background:#fff;border-radius:8px;box-shadow:0 1px 4px #0001;margin-bottom:1.2rem;overflow:hidden}
.group-header{background:#ffebee;padding:.6rem 1rem;font-size:.85rem;font-weight:600;color:#c62828;display:flex;justify-content:space-between;align-items:center}
.group-row{padding:.55rem 1rem;border-top:1px solid #f0f0f0;display:flex;gap:.75rem;flex-wrap:wrap;align-items:flex-start}
.group-q{flex:1;font-size:.88rem}
.group-meta{font-size:.78rem;color:#888;white-space:nowrap}
.tabs{display:flex;gap:.5rem;margin-bottom:1rem}
.tab{padding:.45rem 1.1rem;border:none;border-radius:6px 6px 0 0;cursor:pointer;font-size:.9rem;background:#ddd;color:#555}
.tab.active{background:#d32f2f;color:#fff;font-weight:700}
@media print{header .btn-header,header .filters,.tab{display:none}body{background:#fff}main{padding:0}}
</style>
</head>
<body>
<header>
  <h1>Questions sans réponse — Dakar Dem Dikk</h1>
  <span class="badge" id="totalBadge">…</span>
  <button class="btn-header" onclick="exportPDF()">⬇ PDF</button>
  <button class="btn-header" onclick="location.reload()">↺ Actualiser</button>
  <button class="btn-header btn-danger" onclick="clearAll()">🗑 Vider la liste</button>
</header>
<main>
  <div class="filters">
    <input type="text" id="searchInput" placeholder="Rechercher…" oninput="render()">
    <select id="filterStatus" onchange="render()">
      <option value="all">Tous</option>
      <option value="pending">En attente</option>
      <option value="handled">Traités</option>
    </select>
    <label><input type="checkbox" id="groupCheck" onchange="render()"> Regrouper les proches</label>
  </div>
  <div class="tabs">
    <button class="tab active" id="tabGroups" onclick="switchTab('groups')">Groupes</button>
    <button class="tab" id="tabTable" onclick="switchTab('table')">Tableau</button>
  </div>
  <div id="viewGroups"></div>
  <div id="viewTable" style="display:none"></div>
</main>
<script>
const TOKEN = new URLSearchParams(location.search).get('token') || '';
let ALL = [];
let currentTab = 'groups';

function exportPDF(){ window.print(); }

function switchTab(t){
  currentTab = t;
  document.getElementById('tabGroups').classList.toggle('active', t==='groups');
  document.getElementById('tabTable').classList.toggle('active', t==='table');
  document.getElementById('viewGroups').style.display = t==='groups' ? '' : 'none';
  document.getElementById('viewTable').style.display  = t==='table'  ? '' : 'none';
}

const STOP = new Set(['le','la','les','de','du','des','un','une','au','aux','en','et','est',
  'que','qui','quoi','sur','par','pour','dans','avec','sans','vers','chez',
  'je','il','elle','vous','nous','on','me','se','ce','si','ne','pas','plus',
  'comment','quand','pourquoi','combien','quel','quelle','ou','sont',
  'svp','stp','merci','bonjour','bonsoir','salut','ok','oui','non',
  'voudrais','veux','puis','peut','pouvez','faire','aller','savoir','faut','besoin','aide']);

const ACRONYMS = {'ddd':'dakar dem dikk','tek dem':'tek dem','brt':'bus rapid transit'};

function sigWords(q){
  let s = q.toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').replace(/[^a-z0-9 ]/g,' ');
  for(const [k,v] of Object.entries(ACRONYMS)) s = s.replace(new RegExp('\\\\b'+k+'\\\\b','g'),v);
  return new Set(s.split(/\\s+/).filter(w => w.length>=3 && !STOP.has(w)));
}

function similarity(a, b){
  const sa = sigWords(a), sb = sigWords(b);
  if(!sa.size && !sb.size) return 1;
  const inter = [...sa].filter(w=>sb.has(w)).length;
  const union = new Set([...sa,...sb]).size;
  return union===0 ? 0 : inter/union;
}

async function loadData(){
  const url = `/admin/unknown-queries/data?token=${encodeURIComponent(TOKEN)}`;
  const r = await fetch(url);
  if(!r.ok){ document.body.innerHTML = "<p style='padding:2rem;color:red'>Acc\\u00e8s refus\\u00e9 \\u2014 token manquant ou invalide.</p>"; return; }
  const d = await r.json();
  ALL = (d.queries || []).sort((a,b) => b.count - a.count);
  document.getElementById('totalBadge').textContent = ALL.length + ' question(s)';
  render();
}

function filtered(){
  const q = document.getElementById('searchInput').value.toLowerCase();
  const st = document.getElementById('filterStatus').value;
  return ALL.filter(r => {
    if(st==='pending' && r.handled) return false;
    if(st==='handled' && !r.handled) return false;
    if(q && !r.question.toLowerCase().includes(q)) return false;
    return true;
  });
}

function render(){
  const rows = filtered();
  const doGroup = document.getElementById('groupCheck').checked;
  if(currentTab==='groups') renderGroups(rows, doGroup);
  else renderTable(rows);
}

function renderGroups(rows, doGroup){
  const el = document.getElementById('viewGroups');
  if(!rows.length){ el.innerHTML='<p class="empty">Aucune question pour ces filtres.</p>'; return; }
  let groups = [];
  if(doGroup){
    const used = new Array(rows.length).fill(false);
    for(let i=0;i<rows.length;i++){
      if(used[i]) continue;
      const g = [rows[i]]; used[i]=true;
      for(let j=i+1;j<rows.length;j++){
        if(!used[j] && similarity(rows[i].question, rows[j].question)>=0.4){
          g.push(rows[j]); used[j]=true;
        }
      }
      groups.push(g);
    }
  } else {
    groups = rows.map(r=>[r]);
  }
  el.innerHTML = groups.map(g => {
    const total = g.reduce((s,r)=>s+r.count,0);
    const label = g.length>1 ? `Groupe (${g.length} variantes, ${total} fois)` : `x${g[0].count}`;
    const rows2 = g.map(r => `
      <div class="group-row ${r.handled?'handled':''}">
        <div class="group-q">
          ${esc(r.question)}
          <button class="btn-copy" title="Copier" onclick="copyQ(this,'${esc(r.question).replace(/'/g,"\\\\'")}')">⎘</button>
        </div>
        <div class="group-meta">${r.last_seen}<br>
          <span class="badge-count">${r.count}</span>
          ${!r.handled?`<button class="btn-sm btn-handled" onclick="markHandled('${r.id}',this)" style="margin-left:.4rem">✓ Traité</button>`:'<span style="color:#43a047;font-size:.8rem;margin-left:.4rem">✓</span>'}
        </div>
        <input class="note-input" placeholder="Note…" value="${esc(r.note||'')}" onblur="saveNote('${r.id}',this.value)" style="width:180px">
      </div>`).join('');
    return `<div class="group-block"><div class="group-header"><span>${esc(g[0].question)}</span><span>${label}</span></div>${rows2}</div>`;
  }).join('');
}

function renderTable(rows){
  const el = document.getElementById('viewTable');
  if(!rows.length){ el.innerHTML='<p class="empty">Aucune question.</p>'; return; }
  el.innerHTML = `<table>
    <thead><tr><th>Question</th><th>Raison</th><th>Vu</th><th>Dernière fois</th><th>Note</th><th>Action</th></tr></thead>
    <tbody>${rows.map(r=>`<tr class="${r.handled?'handled':''}">
      <td>${esc(r.question)} <button class="btn-copy" title="Copier" onclick="copyQ(this,'${esc(r.question).replace(/'/g,"\\\\'")}')">⎘</button></td><td>${esc(r.reason)}</td>
      <td><span class="badge-count">${r.count}</span></td>
      <td>${r.last_seen}</td>
      <td><input class="note-input" value="${esc(r.note||'')}" onblur="saveNote('${r.id}',this.value)"></td>
      <td>${!r.handled?`<button class="btn-sm btn-handled" onclick="markHandled('${r.id}',this)">✓ Traité</button>`:'<em>ok</em>'}</td>
    </tr>`).join('')}</tbody></table>`;
}

function esc(s){ return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

async function markHandled(id, btn){
  await fetch(`/admin/unknown-queries/${id}/handled?token=${encodeURIComponent(TOKEN)}`,{method:'POST'});
  const rec = ALL.find(r=>r.id===id);
  if(rec) rec.handled = true;
  render();
}

async function saveNote(id, note){
  await fetch(`/admin/unknown-queries/${id}/note?token=${encodeURIComponent(TOKEN)}`,{
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({note})
  });
  const rec = ALL.find(r=>r.id===id);
  if(rec) rec.note = note;
}

function copyQ(btn, text){
  navigator.clipboard.writeText(text).then(() => {
    btn.classList.add('copied');
    btn.textContent = '✓';
    setTimeout(() => { btn.classList.remove('copied'); btn.textContent = '⎘'; }, 1500);
  });
}

async function clearAll(){
  if(!confirm('Vider toute la liste des questions sans réponse ? Cette action est irréversible.')) return;
  const r = await fetch(`/admin/unknown-queries/clear?token=${encodeURIComponent(TOKEN)}`,{method:'POST'});
  if(r.ok){ ALL = []; render(); document.getElementById('totalBadge').textContent = '0 question(s)'; }
  else alert('Erreur lors de la suppression.');
}

loadData();
</script>
</body>
</html>"""


def _admin_check_token(req) -> bool:
    expected = (os.environ.get("REFRESH_TOKEN") or "").strip().rstrip("/")
    if not expected:
        return False
    token = (
        req.args.get("token")
        or (req.get_json(silent=True) or {}).get("token")
        or (req.headers.get("Authorization") or "")[7:]
    ).strip().rstrip("/")
    return token == expected


@app.route("/admin/unknown-queries/clear", methods=["POST"])
def admin_uq_clear():
    if not _admin_check_token(request):
        return jsonify({"error": "Unauthorized"}), 401
    with _uq_lock:
        _uq_save({"queries": {}})
    return jsonify({"status": "ok"})


@app.route("/admin/unknown-queries", methods=["GET"])
def admin_unknown_queries():
    if not _admin_check_token(request):
        return "Accès refusé — ajoutez ?token=VOTRE_REFRESH_TOKEN à l'URL.", 401
    return _ADMIN_HTML, 200, {
        "Content-Type":  "text/html; charset=utf-8",
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma":        "no-cache",
    }


@app.route("/admin/unknown-queries/data", methods=["GET"])
def admin_uq_data():
    if not _admin_check_token(request):
        return jsonify({"error": "Unauthorized"}), 401
    data = _uq_load()
    return jsonify({"queries": list(data.get("queries", {}).values())})


@app.route("/admin/unknown-queries/<uid>/handled", methods=["POST"])
def admin_uq_mark_handled(uid: str):
    if not _admin_check_token(request):
        return jsonify({"error": "Unauthorized"}), 401
    with _uq_lock:
        data = _uq_load()
        q    = data.get("queries", {}).get(uid)
        if not q:
            return jsonify({"error": "not found"}), 404
        q["handled"] = True
        _uq_save(data)
    return jsonify({"status": "ok", "id": uid, "handled": True})


@app.route("/admin/unknown-queries/<uid>/note", methods=["POST"])
def admin_uq_save_note(uid: str):
    if not _admin_check_token(request):
        return jsonify({"error": "Unauthorized"}), 401
    note = ((request.get_json(silent=True) or {}).get("note") or "").strip()[:500]
    with _uq_lock:
        data = _uq_load()
        q    = data.get("queries", {}).get(uid)
        if not q:
            return jsonify({"error": "not found"}), 404
        q["note"] = note
        _uq_save(data)
    return jsonify({"status": "ok"})


# ── Route : résolution d'une question (réponse directe ou redirection WP) ────

@app.route("/admin/unknown-queries/<uid>/resolve", methods=["POST"])
def admin_uq_resolve(uid: str):
    """
    Marque une question comme résolue :
      - status='repondu'  + reponse_text
      - status='redirige' + page_cible_id + page_cible_url
    """
    if not _admin_check_token(request):
        return jsonify({"error": "Unauthorized"}), 401
    body   = request.get_json(silent=True) or {}
    status = (body.get("status") or "").strip()
    if status not in ("repondu", "redirige"):
        return jsonify({"error": "status doit être 'repondu' ou 'redirige'"}), 400

    with _uq_lock:
        data = _uq_load()
        q    = data.get("queries", {}).get(uid)
        if not q:
            return jsonify({"error": "not found"}), 404

        q["status"]  = status
        q["handled"] = True
        if status == "repondu":
            q["reponse_text"]  = (body.get("reponse_text") or "").strip()[:4000]
            q["page_cible_id"] = body.get("page_cible_id")
            q["page_cible_url"] = (body.get("page_cible_url") or "").strip()
        else:  # redirige
            q["page_cible_id"]  = body.get("page_cible_id")
            q["page_cible_url"] = (body.get("page_cible_url") or "").strip()
        _uq_save(data)
    return jsonify({"status": "ok", "id": uid, "resolution": status})


# ── Route : liste des pages WordPress disponibles ────────────────────────────

@app.route("/admin/wp-pages", methods=["GET"])
def admin_wp_pages():
    """
    Retourne les pages publiées du site WordPress (id, title, link).
    Utilisé par le plugin WP pour remplir les listes déroulantes.
    """
    if not _admin_check_token(request):
        return jsonify({"error": "Unauthorized"}), 401
    import urllib.request as _ur
    wp_base = (os.environ.get("WP_API_BASE") or "https://demdikk.sn/wp-json/wp/v2").rstrip("/")
    pages = []
    for endpoint in (f"{wp_base}/pages?per_page=100&status=publish",
                     f"{wp_base}/posts?per_page=100&status=publish"):
        try:
            req = _ur.Request(endpoint, headers={
                "User-Agent": "DDD-Chatbot-Admin/1.0",
                "Accept": "application/json",
            })
            with _ur.urlopen(req, timeout=10) as resp:
                items = json.loads(resp.read())
            for it in items:
                pages.append({
                    "id":    it.get("id"),
                    "title": (it.get("title") or {}).get("rendered", ""),
                    "link":  it.get("link", ""),
                    "type":  "page" if "/pages" in endpoint else "post",
                })
        except Exception:
            pass
    return jsonify({"pages": pages})


# ── Lookup requêtes résolues (utilisé par app_backup.py via import) ───────────

def lookup_resolved_query(question: str) -> dict | None:
    """
    Cherche dans unknown_queries.json si une question identique ou très proche
    a déjà été résolue (status = 'repondu' ou 'redirige').
    Retourne l'entrée trouvée ou None.
    Comparaison : clé exacte d'abord, puis similarité Jaccard ≥ 0.60.
    """
    try:
        data    = _uq_load()
        queries = data.get("queries", {})
        if not queries:
            return None

        key = _uq_key(question)
        # 1. Correspondance exacte sur la clé dédoublonnée
        for entry in queries.values():
            if entry.get("status") in ("repondu", "redirige"):
                if _uq_key(entry.get("question", "")) == key:
                    return entry

        # 2. Similarité Jaccard sur les mots significatifs
        sig_q = set(_uq_significant_words(question))
        if not sig_q:
            return None
        best_score, best_entry = 0.0, None
        for entry in queries.values():
            if entry.get("status") not in ("repondu", "redirige"):
                continue
            sig_e = set(_uq_significant_words(entry.get("question", "")))
            if not sig_e:
                continue
            inter = len(sig_q & sig_e)
            union = len(sig_q | sig_e)
            score = inter / union if union else 0.0
            if score > best_score:
                best_score, best_entry = score, entry
        if best_score >= 0.60:
            return best_entry
    except Exception:
        pass
    return None


# ── Webhook : mise à jour automatique depuis WordPress ───────────────────────

_webhook_lock = __import__("threading").Lock()
_webhook_running = False


@app.route("/webhook/content-updated", methods=["POST"])
def webhook_content_updated():
    """
    Appelé automatiquement par le plugin WordPress à chaque modification
    de page ou d'article publié.
    Lance scraper.py + indexer.py + rechargement des embeddings en arrière-plan.
    """
    if not _admin_check_token(request):
        return jsonify({"error": "Unauthorized"}), 401

    global _webhook_running
    with _webhook_lock:
        if _webhook_running:
            return jsonify({"status": "already_running",
                            "message": "Pipeline déjà en cours, ignoré."}), 202

        _webhook_running = True

    import threading

    def _run_pipeline():
        global _webhook_running
        try:
            import subprocess, sys as _sys
            python = _sys.executable
            root   = _root_dir
            import datetime
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            log_path = os.path.join(root, "webhook_refresh.log")

            def _log(msg):
                try:
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"[{ts}] {msg}\n")
                except Exception:
                    pass

            _log("=== Webhook reçu — pipeline démarré ===")

            # 1. Scraper
            r1 = subprocess.run(
                [python, "scraper.py"], cwd=root,
                capture_output=True, text=True, timeout=180,
            )
            _log(f"scraper.py exit={r1.returncode} {r1.stdout.strip()[-300:]}")

            # 2. Indexer
            r2 = subprocess.run(
                [python, "indexer.py"], cwd=root,
                capture_output=True, text=True, timeout=600,
            )
            _log(f"indexer.py exit={r2.returncode} {r2.stdout.strip()[-300:]}")

            # 3. Recharger les embeddings en mémoire
            _mod = __import__("sys").modules.get("app_backup")
            if _mod and hasattr(_mod, "_load_embeddings"):
                _mod._load_embeddings()
                _log("Embeddings rechargés en mémoire.")
            elif _mod and hasattr(_mod, "load_model"):
                _mod.load_model()
                _log("Modèle rechargé.")

            _log("=== Pipeline terminé ===")

        except Exception as e:
            try:
                log_path = os.path.join(_root_dir, "webhook_refresh.log")
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"[ERREUR] {e}\n")
            except Exception:
                pass
        finally:
            global _webhook_running
            _webhook_running = False

    threading.Thread(target=_run_pipeline, daemon=True).start()

    post_info = (request.get_json(silent=True) or {})
    return jsonify({
        "status":  "rebuilding",
        "message": "Pipeline lancé en arrière-plan (scrape + index + reload).",
        "post_id": post_info.get("post_id"),
        "log":     "webhook_refresh.log",
    }), 202


@app.route("/webhook/status", methods=["GET"])
def webhook_status():
    """Indique si un pipeline est en cours d'exécution."""
    if not _admin_check_token(request):
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify({"running": _webhook_running})


# ── Point d'entrée (développement local) ─────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host='0.0.0.0', port=port, debug=debug)
