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
    "Vous pouvez contacter Dakar Dem Dikk directement :\n"
    "– Téléphone : +221 33 824 10 10 / +221 33 865 15 55\n"
    "– Email : info@demdikk.sn / contact@demdikk.sn\n"
    "– Adresse : Km 4,5 Avenue Cheikh Anta Diop, dépôt Ouakam, Dakar\n"
    "– Horaires : Lundi – Vendredi, 08h – 17h\n"
    "– Site web : demdikk.sn"
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
_OFF_TOPIC_WORDS = frozenset([
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
])

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
    "Tu es l'assistant de Dakar Dem Dikk. "
    "Ton UNIQUE rôle est de reformuler en français fluide et naturel "
    "les informations que le système t'a déjà trouvées sur le site de Dakar Dem Dikk. "
    "RÈGLES ABSOLUES : "
    "1. Tu ne peux utiliser QUE les informations du contexte fourni — jamais d'inventions. "
    "2. Si le contexte contient la réponse, reformule-la de façon fluide et agréable à lire. "
    "3. Si le contexte ne contient pas la réponse à la question, réponds EXACTEMENT ce texte "
    "(sans rien ajouter ni modifier) : "
    "'Je n\\'ai pas trouvé cette information sur le site de Dakar Dem Dikk.\\n"
    "Vous pouvez les contacter directement :\\n"
    "– Téléphone : +221 33 824 10 10 / +221 33 865 15 55\\n"
    "– Email : info@demdikk.sn / contact@demdikk.sn\\n"
    "– Adresse : Km 4,5 Avenue Cheikh Anta Diop, dépôt Ouakam, Dakar\\n"
    "– Horaires : Lundi – Vendredi, 08h – 17h\\n"
    "– Site web : demdikk.sn' "
    "4. Ne complète jamais avec tes propres connaissances. "
    "5. Réponds toujours en français, jamais en anglais. "
    "6. N'utilise JAMAIS de balises markdown comme ##, ###, **. Écris en texte clair avec des tirets (–) pour les listes."
)

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

    context = "\n\n".join(p for p in context_parts if p).strip()
    if not context or len(context) < 20:
        return original_data

    history_block = _format_client_history_for_prompt(client_history or [])
    if history_block:
        user_prompt = (
            f"{history_block}\n\n"
            f"Voici les informations trouvées sur le site de Dakar Dem Dikk :\n"
            f"---\n{context}\n---\n\n"
            f"Question actuelle de l'usager : {question}\n\n"
            "Reformule ces informations en une réponse fluide et naturelle en français "
            "(3 à 5 phrases). Utilise UNIQUEMENT ce qui est écrit dans le bloc « site » ci-dessus "
            "(pas d'invention ; l'historique ne constitue pas une source factuelle). "
            "Si l'information demandée n'est pas dans le texte du site ci-dessus, dis-le clairement."
        )
    else:
        user_prompt = (
            f"Voici les informations trouvées sur le site de Dakar Dem Dikk :\n"
            f"---\n{context}\n---\n\n"
            f"Question de l'usager : {question}\n\n"
            "Reformule ces informations en une réponse fluide et naturelle en français "
            "(3 à 5 phrases). Utilise UNIQUEMENT ce qui est écrit ci-dessus. "
            "Si l'information demandée n'est pas dans le texte ci-dessus, dis-le clairement."
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
                "max_tokens": 800,
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
            enhanced = dict(original_data)
            enhanced["answer"] = text
            enhanced["llm_provider"] = "deepseek"
            enhanced["llm_enhanced"] = True
            # Compat front/back : garder les anciens champs
            enhanced["gemini_enhanced"] = True
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
    }
    for variant, canonical in _variants.items():
        s = re.sub(r"\b" + re.escape(variant) + r"\b", canonical, s)
    return s


def _extract_section(text: str, start_markers: tuple[str, ...], max_chars: int = 1400) -> str:
    if not text:
        return ""
    t = text.replace("\r\n", "\n")
    idx = -1
    for m in start_markers:
        i = t.lower().find(m.lower())
        if i >= 0 and (idx < 0 or i < idx):
            idx = i
    if idx < 0:
        return ""
    snippet = t[idx: idx + max_chars]
    # Ne pas couper immédiatement au prochain "###" :
    # sur la page DDD, les sous-sections utiles (ex: dépôt/réception/suivi) sont aussi en "###".
    return snippet.strip()


def _clip_at_next_top_heading(text: str) -> str:
    """
    Coupe une section au prochain titre de niveau "##" ou au séparateur "—"
    (tiret long) qui marque la fin d'une section sur la page chatbot-2303.
    """
    if not text:
        return ""
    t = text.replace("\r\n", "\n")
    # Couper au prochain titre ## (nouvelle section de haut niveau)
    j = t.find("\n## ", 4)
    # Couper aussi au séparateur "—" suivi d'une ligne vide (fin de section)
    # On cherche "\n—\n" ou "\n—\n\n" ou "\n\n—\n"
    for sep in ("\n—\n", "\n\n—\n", "\n— \n"):
        k = t.find(sep, 60)  # ignorer les 60 premiers chars pour ne pas couper trop tôt
        if k >= 0 and (j < 0 or k < j):
            j = k
    if j >= 0:
        t = t[:j]
    return t.strip()


def _clip_at_next_subheading(text: str) -> str:
    """
    Coupe le texte au prochain sous-titre de niveau "###" (sous-section).
    Utilisé pour isoler une seule sous-section (ex: Remboursement, Annulation).
    """
    if not text:
        return ""
    t = text.replace("\r\n", "\n")
    # Sauter le titre de départ (première occurrence de ###) — chercher le suivant
    first = t.find("###")
    if first >= 0:
        j = t.find("###", first + 3)
    else:
        j = t.find("###", 4)
    if j >= 0:
        t = t[:j]
    # Aussi couper au prochain titre ##
    k = t.find("\n## ", 4)
    if k >= 0:
        t = t[:k]
    return t.strip()


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
    """Extrait du contenu utile depuis la page officielle réseau interurbain."""
    qn = _norm(question)
    if not qn:
        return None
    triggers = (
        "senegal dem dikk",
        "sénégal dem dikk",
        "interurbain",
        "interurbains",
        "reseau-interurbain",
        "réseau-interurbain",
        "dieuppeul",
        "gare routiere de dieuppeul",
        "gare routière de dieuppeul",
    )
    if not any(t in qn for t in triggers):
        return None

    url = "https://demdikk.sn/reseau-interurbain/"
    page_text = _fetch_page_text(url)
    if not page_text:
        return None

    section = _extract_section(
        page_text,
        (
            "Réseau Sénégal Dem Dikk",
            "Reseau Senegal Dem Dikk",
            "Sénégal Dem Dikk",
            "Senegal Dem Dikk",
            "Interurbain",
        ),
        max_chars=3200,
    )
    if not section or len(section) < 80:
        section = page_text[:3200].strip()

    return {
        "answer": section,
        "summary": section[:280],
        "bullets": [],
        "sources": [{"title": "Dakar Dem Dikk", "url": "https://demdikk.sn/", "score": 1.0}],
        "results": [{"url": "https://demdikk.sn/", "title": "Dakar Dem Dikk", "snippet": section[:500], "full_text": section}],
        "query_type": "general",
        "needs_clarification": False,
        "has_structured_data": False,
        "is_city_query": False,
        "is_line_query": False,
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
    )
    if not any(t in qn for t in triggers):
        return None

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


def _smart_search_chatbot_page(question: str) -> dict | None:
    """
    Fallback générique à deux niveaux :
      1) Chercher la sous-section ### la plus ciblée (si le titre matche)
      2) Si aucune ne matche, chercher la section ## la plus pertinente
    Élimine le besoin de fallback manuel pour chaque nouveau sujet.
    """
    import re as _re

    qn = _lemmatize(question)
    if not qn or len(qn) < 3:
        return None

    _STOPWORDS = {
        "le", "la", "les", "de", "du", "des", "un", "une", "et", "en",
        "est", "que", "qui", "sur", "par", "pour", "dans", "avec", "au",
        "je", "il", "elle", "vous", "nous", "on", "ce", "se", "ne", "pas",
        "plus", "quel", "quelle", "quels", "quelles", "comment", "quand",
        "ou", "si", "mais", "donc", "car", "ici", "ya", "a",
    }
    query_words = [w for w in qn.split() if w not in _STOPWORDS and len(w) >= 3]
    if not query_words:
        return None

    url = "https://demdikk.sn/chatbot-2303/"
    page_text = _fetch_page_text(url)
    if not page_text:
        return None

    def _word_score(text: str, title_bonus: int = 1) -> int:
        n = _lemmatize(text)
        return sum(title_bonus for w in query_words if w in n)

    # ── Niveau 1 : sous-sections ### (réponse la plus précise) ───────────────
    subsections = _re.split(r'\n(?=### )', page_text)
    best_sub = None
    best_sub_score = 0

    for raw in subsections:
        if not raw.strip() or len(raw) < 50:
            continue
        first_line = raw.split('\n')[0]
        # Exiger que le titre ### lui-même contienne un mot-clé
        title_score = _word_score(first_line, title_bonus=3)
        if title_score == 0:
            continue
        total = title_score + _word_score(raw, title_bonus=1)
        if total > best_sub_score:
            best_sub_score = total
            best_sub = raw

    if best_sub and best_sub_score >= 3:
        section = best_sub[:1500]
        section = _clip_at_next_subheading(section)
        if section and len(section) >= 60:
            result = _make_chatbot_result(section)
            confidence = round(best_sub_score / max(len(query_words), 1), 2)
            result["sources"] = [{"title": "Dakar Dem Dikk", "url": url, "score": confidence}]
            result["results"][0]["url"] = url
            return result

    # ── Niveau 2 : sections ## (réponse plus large) ───────────────────────────
    sections_raw = _re.split(r'\n(?=## \d+\.)', page_text)
    best_section = None
    best_score = 0

    for raw in sections_raw:
        if not raw.strip():
            continue
        cleaned = _strip_nav_content(raw)
        if not cleaned or len(cleaned) < 80:
            continue
        first_line = cleaned.split('\n')[0]
        total = _word_score(first_line, title_bonus=3) + _word_score(cleaned, title_bonus=1)
        if total > best_score:
            best_score = total
            best_section = cleaned

    if best_score < 1 or not best_section:
        return None

    section = best_section[:2500]
    section = _clip_at_next_top_heading(section)
    if not section or len(section) < 60:
        return None

    result = _make_chatbot_result(section)
    confidence = round(best_score / max(len(query_words), 1), 2)
    result["sources"] = [{"title": "Dakar Dem Dikk", "url": url, "score": confidence}]
    result["results"][0]["url"] = url
    return result


def _fallback_from_site(question: str) -> dict | None:
    """
    Fallback universel : cherche la meilleure section sur la page chatbot-2303
    en comparant les mots de la question aux titres et contenus des sections.
    Plus besoin de lister des mots-clés manuellement.
    """
    url = "https://demdikk.sn/chatbot-2303/"
    page_text = _fetch_page_text(url)
    if not page_text:
        return None
    return _smart_search_chatbot_page(question)


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
    # Supprimer les puces/tirets isolés en fin de texte
    text = re.sub(r'(\s*' + _BULLET_CHARS + r'\s*)+$', '', text.rstrip())
    return text.strip()


def _make_chatbot_result(section: str) -> dict:
    """Construit un dict résultat standard depuis un extrait de page officielle."""
    clean = _light_clean(section) if section else section
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


# ── Envelopper /ask avec DeepSeek ────────────────────────────────────────────
_original_ask = app.view_functions.get("ask")

if _original_ask:
    @functools.wraps(_original_ask)
    def _ask_with_deepseek():
        from flask import request, jsonify
        # Récupérer la question avant l'appel original
        body = request.get_json(silent=True) or {}
        question = body.get("question", "")
        if "history" in body:
            client_history = _parse_client_history(body.get("history"))
        else:
            client_history = _parse_client_history(body.get("conversationHistory"))
        qn = _norm(question)

        # ── Détection hors-sujet / charabia (aligné sur app_backup) ───────────
        _qwords = set(qn.split())
        _transport_ctx = (
            "bus", "ligne", "transport", "voyage", "dem dikk", "demdikk",
            "reservation", "billet", "ticket", "abonnement", "tek dem",
            "carte", "colis", "horaire", "tarif", "prix", "contact", "agence",
            "interurbain", "touba", "thiès", "thies", "saint-louis", "fatick",
        )
        _smalltalk = getattr(_mod, "_is_smalltalk_question", None)
        _off_topic_like = (
            (_smalltalk and _smalltalk(question))
            or _question_looks_gibberish_normed(qn)
            or (
                _qwords & _OFF_TOPIC_WORDS
                and not any(k in qn for k in _transport_ctx)
            )
        )
        if _off_topic_like:
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

        # Appeler le handler original
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
            if fb_i and wants_interurban:
                enhanced = _enhance_with_deepseek(fb_i, question, client_history)
                return (jsonify(enhanced), *rest) if rest else jsonify(enhanced)

            # Afrique Dem Dikk : prioritaire pour "gambie/senegal/banjul/afrique"
            af_triggers = ("afrique dem dikk", "afrique", "gambie", "gambia", "banjul", "senegal")
            wants_afrique = any(t in qn for t in af_triggers)
            fb_a = _fallback_afrique_dem_dikk(question)
            if fb_a and wants_afrique:
                enhanced = _enhance_with_deepseek(fb_a, question, client_history)
                return (jsonify(enhanced), *rest) if rest else jsonify(enhanced)

            ans = (data.get("answer") or "").strip()
            if "je n'ai pas trouv" in ans.lower():
                fb = _fallback_from_site(question)
                if fb:
                    data = fb
            if fb_i and "je n'ai pas trouv" in ans.lower():
                data = fb_i

            # Priorité index RAG (scraper.py → data/scraped.jsonl → indexer.py → metadata.json + embeddings.npy).
            # Ne pas remplacer par du scraping live (_fallback_from_site / smart search) si la réponse
            # issue de app_backup est déjà exploitable.
            rag_ok = _rag_answer_usable(data)

            # Application mobile : toujours préférer l'extrait page officielle (chatbot-2303)
            # lorsqu'il est disponible — l'index peut renvoyer un chunk « acceptable » (score)
            # mais sans répondre à la question (Play Store, fonctionnalités, etc.).
            if any(k in qn for k in ("application", "appli", "google play", "app store")):
                fb_app = _fallback_from_site(question)
                if fb_app:
                    data = fb_app
                    rag_ok = _rag_answer_usable(data)

            # Colis / messagerie : idem — ne court-circuite pas un bon chunk indexé
            if any(k in qn for k in ("colis", "messagerie", "courrier")) and not rag_ok:
                fb2 = _fallback_from_site(question)
                if fb2:
                    return (jsonify(fb2), *rest) if rest else jsonify(fb2)

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
            if any(k in qn for k in _site_triggers) and not rag_ok:
                fb3 = _fallback_from_site(question)
                if fb3:
                    enhanced3 = _enhance_with_deepseek(fb3, question, client_history)
                    return (jsonify(enhanced3), *rest) if rest else jsonify(enhanced3)

            # ── Recherche générique intelligente (smart search) ───────────────
            # Toujours tenter une recherche par mots-clés sur la page chatbot-2303
            # SAUF pour les données structurées (lignes, arrêts, horaires).
            # Cela couvre automatiquement tous les sujets présents sur le site
            # sans nécessiter de fallback manuel pour chaque nouveau sujet.
            _structured_types = {"all_lines_summary", "line_X", "lines_to_stop", "line_details"}
            is_structured = data.get("query_type") in _structured_types or data.get("is_line_query") or data.get("is_city_query")
            cur_ans = (data.get("answer") or "").strip()
            ans_seems_weak = (
                "je n'ai pas trouv" in cur_ans.lower()
                or not cur_ans
                or (data.get("sources", [{}])[0].get("title", "") in ("Source", "", None) and not data.get("results"))
            )
            if not is_structured:
                fb_smart = _smart_search_chatbot_page(question)
                # Utiliser le smart search si :
                # - la réponse actuelle est faible/absente, OU
                # - le smart search a trouvé une section très pertinente (score >= 2 mots)
                smart_score = (fb_smart or {}).get("sources", [{}])[0].get("score", 0)
                if fb_smart and not rag_ok and (ans_seems_weak or smart_score >= 0.5):
                    return (jsonify(fb_smart), *rest) if rest else jsonify(fb_smart)

            enhanced = _enhance_with_deepseek(data, question, client_history)
            # Logger les requêtes sans réponse
            if "je n'ai pas trouv" in (enhanced.get("answer") or "").lower():
                _log_unknown_query(question, reason="not_found")
            return (jsonify(enhanced), *rest) if rest else jsonify(enhanced)
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
        <div class="group-q">${esc(r.question)}</div>
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
      <td>${esc(r.question)}</td><td>${esc(r.reason)}</td>
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

loadData();
</script>
</body>
</html>"""


def _admin_check_token(req) -> bool:
    expected = (os.environ.get("REFRESH_TOKEN") or "").strip()
    if not expected:
        return False
    token = (
        req.args.get("token")
        or (req.get_json(silent=True) or {}).get("token")
        or (req.headers.get("Authorization") or "")[7:]
    ).strip()
    return token == expected


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


# ── Point d'entrée (développement local) ─────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host='0.0.0.0', port=port, debug=debug)
