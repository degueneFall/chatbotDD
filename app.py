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
    "Je n'ai pas trouvé cette information sur le site de Dakar Dem Dikk.\n"
    "Vous pouvez les contacter directement :\n"
    "– Téléphone : +221 33 824 10 10 / +221 33 865 15 55\n"
    "– Email : info@demdikk.sn / contact@demdikk.sn\n"
    "– Adresse : Km 4,5 Avenue Cheikh Anta Diop, dépôt Ouakam, Dakar\n"
    "– Horaires : Lundi – Vendredi, 08h – 17h\n"
    "– Site web : demdikk.sn"
)

# Mots-clés indiquant une question hors du périmètre DDD
_OFF_TOPIC_WORDS = frozenset([
    "meteo", "weather", "temperature", "pluie", "soleil",
    "politique", "president", "gouvernement", "election",
    "macky", "sall", "sonko", "wade",
    "football", "sport", "match",
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

_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "unknown_queries.log")

def _log_unknown_query(question: str, reason: str = "not_found") -> None:
    """Enregistre les requêtes sans réponse dans unknown_queries.log."""
    try:
        import datetime
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{reason}] {question.strip()}\n"
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
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


def _enhance_with_deepseek(original_data: dict, question: str) -> dict:
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


def _smart_search_chatbot_page(question: str) -> dict | None:
    """
    Fallback générique à deux niveaux :
      1) Chercher la sous-section ### la plus ciblée (si le titre matche)
      2) Si aucune ne matche, chercher la section ## la plus pertinente
    Élimine le besoin de fallback manuel pour chaque nouveau sujet.
    """
    import re as _re

    qn = _norm(question)
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
        n = _norm(text)
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
    Fallback ultra-ciblé quand la recherche interne répond 'pas trouvé'
    alors que l'info existe sur la page officielle chatbot.
    """
    qn = _norm(question)
    if not qn:
        return None

    wants_abonnement = ("abonnement" in qn) or ("abonnements" in qn)
    wants_colis = ("colis" in qn) or ("messagerie" in qn) or ("courrier" in qn)
    # "carte perdue/volée/duplicata" → Erreurs courantes, PAS Tek Dem
    _carte_probleme = (
        qn in ("duplicata", "opposition", "carte perdue", "carte volee", "carte vole")
        or ("carte" in qn and any(k in qn for k in ("perdu", "perdue", "volee", "vole", "duplicata", "opposition")))
    )
    wants_tekdem = (
        (("tek dem" in qn) or ("tekk dem" in qn) or ("carte" in qn) or ("pass" in qn))
        and not _carte_probleme
    )
    wants_app = ("application" in qn) or ("appli" in qn) or ("google play" in qn) or ("app store" in qn)
    wants_bagages = ("bagage" in qn) or ("bagages" in qn)
    wants_refund = (
        ("remboursement" in qn)
        or ("rembourser" in qn)
        or ("rembourse" in qn)
        or ("annulation" in qn)
        or ("annuler" in qn)
        or ("annule" in qn)
        or ("report" in qn)
        or ("reporte" in qn)
        or ("reporter" in qn)
        or ("modifier" in qn and ("billet" in qn or "reservation" in qn))
        or ("modification" in qn and ("billet" in qn or "reservation" in qn))
    )
    wants_rechargement = (
        ("rechargement" in qn)
        or ("recharger" in qn)
        or ("recharge" in qn)
        or ("rechargez" in qn)
    )
    wants_geoloc = (
        ("geolocalisation" in qn)
        or ("géolocalisation" in qn)
        or ("geolocali" in qn)
        or ("suivi bus" in qn)
        or ("position bus" in qn)
        or ("temps reel" in qn)
        or ("temps réel" in qn)
    )
    wants_contact = (
        qn in ("contact", "contacts")
        or ("contact service" in qn)
        or ("service client" in qn)
        or ("assistance" in qn and "contact" in qn)
        or ("horaire agence" in qn)
        or ("horaires agence" in qn)
        or ("email" in qn and "dakar dem dikk" in qn)
    )
    wants_objet_perdu = (
        ("objet perdu" in qn)
        or (qn in ("objet", "perdu", "perdus", "objet perdu", "objets perdus"))
        or ("perdu" in qn and "bord" in qn)
        or ("perdu" in qn and not any(k in qn for k in ("carte", "tek dem", "pass", "billet")))
        or ("volee" in qn and not any(k in qn for k in ("carte", "tek dem")))
    )
    wants_fess_dem = ("fess dem" in qn) or ("thies" in qn and "dakar" in qn) or ("thies" in qn and "prix" in qn)
    wants_aibd = ("aibd" in qn) or ("aeroport" in qn) or ("blaise diagne" in qn)
    wants_location = ("location" in qn and ("bus" in qn or "vehicule" in qn or qn == "location"))
    wants_partenariat = ("partenariat" in qn) or ("publicite" in qn) or ("partenariat publicite" in qn) or ("publicite partenariat" in qn)
    wants_services_list = (qn in ("service", "services", "offre", "offres", "offre transport", "offres transport"))
    wants_reservation = (
        ("reservation" in qn)
        or ("reserver" in qn)
        or ("reservez" in qn)
        or ("billet" in qn and ("acheter" in qn or "achat" in qn or "comment" in qn))
    )
    wants_presentation = (
        ("presentation" in qn)
        or ("directeur" in qn)
        or ("historique" in qn)
        or ("histoire" in qn)
        or ("creation" in qn and "dakar" in qn)
        or ("fondation" in qn)
        or ("assane" in qn)
        or ("thierno" in qn)
        or qn in ("ddd", "dakar dem dikk", "qui sommes nous", "qui etes vous")
    )
    wants_emploi = (
        ("emploi" in qn)
        or ("recrutement" in qn)
        or ("offres d emploi" in qn)
        or ("offre d emploi" in qn)
        or ("travailler" in qn and "dakar dem dikk" in qn)
        or ("candidature" in qn)
        or ("poste" in qn and "dakar dem dikk" in qn)
    )
    wants_perturbation = (
        ("communication" in qn)
        or ("crise" in qn)
        or ("perturbation" in qn)
        or ("incident" in qn)
        or ("intemperie" in qn)
        or ("greve" in qn)
        or ("retard" in qn)
        or ("panne" in qn)
        or ("maintenance" in qn)
        or ("innovation" in qn)
        or ("paiement" in qn and ("mobile" in qn or "dematerialise" in qn or "wave" in qn or "orange" in qn))
    )
    if not (
        wants_abonnement or wants_colis or wants_tekdem or wants_app
        or wants_bagages or wants_refund or wants_rechargement
        or wants_geoloc or wants_contact or wants_objet_perdu
        or wants_fess_dem or wants_aibd or wants_location
        or wants_partenariat or wants_services_list
        or wants_reservation or wants_presentation or wants_emploi
        or wants_perturbation or _carte_probleme
    ):
        return None

    url = "https://demdikk.sn/chatbot-2303/"
    page_text = _fetch_page_text(url)
    if not page_text:
        return None

    def _section_is_substantial(section: str, must_contain: tuple = ()) -> bool:
        """Refuse une section trop courte ou sans info métier (cas des cartes home)."""
        if not section:
            return False
        # Retirer le titre éventuel (1ère ligne courte sans verbe)
        clean = (section or "").strip()
        if len(clean) < 150:
            return False
        low = clean.lower()
        # Au moins un terme métier réel (chiffre / prix / verbe d'action / etc.)
        metier = (
            "fcfa", "f cfa", "prix", "tarif", "mensuel", "annuel", "carte",
            "réservation", "reservation", "billet", "ticket", "horaire",
            "agence", "guichet", "ligne", "départ", "depart", "arrivée", "arrivee",
            "contact", "téléphone", "telephone", "email", "@",
            "abonnement", "messagerie", "expédition", "expedition", "colis",
            "service", "modalité", "modalite",
        )
        if not any(m in low for m in metier):
            return False
        if must_contain and not any(m.lower() in low for m in must_contain):
            return False
        return True

    if _carte_probleme:
        section = _extract_section(
            page_text,
            ("### Erreurs courantes", "Erreurs courantes", "Carte non reconnue", "Carte perdue"),
            max_chars=1000,
        )
        section = _clip_at_next_subheading(section)
        if section:
            return _make_chatbot_result(section)

    if wants_abonnement:
        section = _extract_section(
            page_text,
            ("Abonnements mensuels", "Abonnement mensuel", "Abonnement"),
            max_chars=2200,
        )
        section = _clip_at_next_top_heading(section)
        # Exiger une section substantielle (mot 'abonnement' + prix/carte/mensuel/etc.)
        # sinon laisser la recherche vectorielle prendre le relais (chunk /services/).
        if _section_is_substantial(section, must_contain=("abonnement",)):
            return _make_chatbot_result(section)

    if wants_colis:
        section = _extract_section(
            page_text,
            ("Service Messagerie Express", "messagerie express", "colis et courriers"),
            max_chars=2400,
        )
        section = _clip_at_next_top_heading(section)
        if section:
            return _make_chatbot_result(section)

    if wants_tekdem:
        section = _extract_section(
            page_text,
            ("Carte Tek Dem", "Tek Dem", "pass Tek Dem", "Frais de carte"),
            max_chars=2200,
        )
        if not section:
            section = _extract_section(page_text, ("Carte Tek Dem",), max_chars=2600)
        section = _clip_at_next_top_heading(section)
        if section and "tek dem" in _norm(section):
            return _make_chatbot_result(section)

    if wants_app:
        # Chercher en priorité le titre exact de la section Application
        section = _extract_section(
            page_text,
            (
                "### Application Dem Dikk",
                "## 15. Fidélité et application mobile",
                "## 15. Fidelite et application mobile",
                "Fidélité et application mobile",
                "Fidelite et application mobile",
            ),
            max_chars=2000,
        )
        section = _clip_at_next_top_heading(section)
        if not section:
            # Fallback : partir de l'URL Play Store mais vers l'avant, pas l'arrière
            pt_low = page_text.lower()
            idx = pt_low.find("play.google.com")
            if idx < 0:
                idx = pt_low.find("apps.apple.com")
            if idx >= 0:
                # Chercher le début de la section (### ou ## avant l'URL)
                start = page_text.rfind("###", 0, idx)
                if start < 0:
                    start = page_text.rfind("##", 0, idx)
                if start < 0:
                    start = max(0, idx - 200)
                section = page_text[start: start + 2000].strip()
                section = _clip_at_next_top_heading(section)
        if section:
            return _make_chatbot_result(section)

    if wants_refund:
        qn_ref = _norm(question)
        only_remb = (
            ("remboursement" in qn_ref or "rembourser" in qn_ref or "rembourse" in qn_ref)
            and "annulation" not in qn_ref
            and "report" not in qn_ref
        )
        only_annul = (
            ("annulation" in qn_ref or "annuler" in qn_ref or "annule" in qn_ref
             or "report" in qn_ref or "reporte" in qn_ref or "reporter" in qn_ref)
            and "remboursement" not in qn_ref
            and "modifier" not in qn_ref
            and "modification" not in qn_ref
        )
        if only_remb:
            # Extraire uniquement la sous-section Remboursement
            section = _extract_section(
                page_text,
                ("### Remboursement", "Remboursement\n", "Remboursement "),
                max_chars=800,
            )
            section = _clip_at_next_subheading(section)
            if not section:
                section = _extract_section(
                    page_text,
                    ("Remboursement",),
                    max_chars=600,
                )
        elif only_annul:
            # Extraire uniquement la sous-section Annulation et report
            # Utiliser un marqueur précis pour éviter de matcher le titre de la grande section
            section = _extract_section(
                page_text,
                ("### Annulation et report", "### Annulation"),
                max_chars=800,
            )
            section = _clip_at_next_subheading(section)
            if not section:
                # Chercher la ligne contenant "Annulation et report" comme titre de sous-section
                import re as _re
                m = _re.search(r'(Annulation et report\s*\n.{20,})', page_text, _re.DOTALL | _re.IGNORECASE)
                if m:
                    section = m.group(0)[:800]
        else:
            # Requête mixte ou générale → toute la section gestion
            section = _extract_section(
                page_text,
                (
                    "## 7. Gestion des réservations, annulations, reports et remboursements",
                    "## 7. Gestion des reservations, annulations, reports et remboursements",
                    "Gestion des réservations, annulations, reports et remboursements",
                    "Gestion des reservations, annulations, reports et remboursements",
                ),
                max_chars=2600,
            )
            section = _clip_at_next_top_heading(section)
        if section:
            return _make_chatbot_result(section)

    if wants_bagages:
        section = _extract_section(
            page_text,
            (
                "## 8. Bagages et colis : règles et conditions",
                "## 8. Bagages et colis : regles et conditions",
                "Bagages et colis : règles et conditions",
                "Bagages et colis : regles et conditions",
                "Bagages à bord",
                "Bagages a bord",
            ),
            max_chars=2200,
        )
        section = _clip_at_next_top_heading(section)
        if section:
            return _make_chatbot_result(section)

    if wants_rechargement:
        section = _extract_section(
            page_text,
            (
                "### Rechargement de la carte Tek Dem",
                "Rechargement de la carte Tek Dem",
                "### Rechargement",
            ),
            max_chars=1000,
        )
        section = _clip_at_next_subheading(section)
        if section:
            return _make_chatbot_result(section)

    if wants_geoloc:
        section = _extract_section(
            page_text,
            (
                "Géolocalisation temps réel",
                "Geolocalisation temps reel",
                "Géolocalisation en temps réel",
                "Geolocalisation en temps reel",
                "## 13.",
            ),
            max_chars=1600,
        )
        section = _clip_at_next_top_heading(section)
        if section:
            return _make_chatbot_result(section)

    if wants_contact:
        section = _extract_section(
            page_text,
            (
                "17. Contact et assistance humaine",
                "Contact et assistance humaine",
                "### Service client",
            ),
            max_chars=1600,
        )
        section = _clip_at_next_top_heading(section)
        if section:
            return _make_chatbot_result(section)

    if wants_objet_perdu:
        section = _extract_section(
            page_text,
            ("Objets perdus", "objet perdu"),
            max_chars=1200,
        )
        section = _clip_at_next_top_heading(section)
        if section:
            return _make_chatbot_result(section)

    if wants_fess_dem:
        section = _extract_section(
            page_text,
            ("Service Fess Dem", "Fess Dem", "## 5."),
            max_chars=1200,
        )
        section = _clip_at_next_top_heading(section)
        if section:
            return _make_chatbot_result(section)

    if wants_aibd:
        section = _extract_section(
            page_text,
            (
                "## 3. Service AIBD",
                "Service AIBD",
                "Horaires des navettes",
                "Premier départ : 4h",
                "Premier depart : 4h",
            ),
            max_chars=1400,
        )
        section = _clip_at_next_top_heading(section)
        if section:
            return _make_chatbot_result(section)

    if wants_location:
        section = _extract_section(
            page_text,
            (
                "Location de bus",
                "location de bus",
                "Location",
                "événements privés",
                "evenements prives",
                "navettes spéciales",
            ),
            max_chars=1200,
        )
        section = _clip_at_next_top_heading(section)
        if section:
            return _make_chatbot_result(section)

    if wants_partenariat:
        section = _extract_section(
            page_text,
            (
                "Publicité et partenariats",
                "Publicite et partenariats",
                "## 16.",
                "Devenez partenaire",
                "Espaces publicitaires",
                "partenariat@demdikk",
            ),
            max_chars=1400,
        )
        section = _clip_at_next_top_heading(section)
        if section:
            return _make_chatbot_result(section)

    if wants_services_list:
        # Construire une liste des services depuis les sections disponibles
        services_summary = (
            "Dakar Dem Dikk propose les services suivants :\n"
            "– Réseau urbain Dakar (lignes de bus, abonnements, carte Tek Dem)\n"
            "– Sénégal Dem Dikk – liaisons interurbaines vers les régions du Sénégal\n"
            "– Afrique Dem Dikk – liaisons internationales (ex : Gambie/Banjul)\n"
            "– Service AIBD – navettes vers l'Aéroport International Blaise Diagne (4h–22h, 6 000 FCFA)\n"
            "– Fess Dem – liaison Dakar–Thiès (2 000 FCFA, départ gare Colobane)\n"
            "– Messagerie Express – envoi de colis et courriers vers les régions\n"
            "– Location de bus – événements privés, scolaires, navettes spéciales (sur devis)\n"
            "– Publicité & Partenariats – espaces à bord, en agence et sur l'application\n"
            "\nPour plus d'informations :\n"
            "– Téléphone : +221 33 824 10 10 / +221 33 865 15 55\n"
            "– Email : info@demdikk.sn\n"
            "– Site web : demdikk.sn"
        )
        return _make_chatbot_result(services_summary)

    if wants_reservation:
        section = _extract_section(
            page_text,
            (
                "### Réservation",
                "### Reservation",
                "## 7. Gestion des réservations",
                "## 7. Gestion des reservations",
                "Réservation et modification",
                "Reservation et modification",
                "Vous pouvez réserver",
                "Vous pouvez reserver",
            ),
            max_chars=2000,
        )
        section = _clip_at_next_top_heading(section)
        if section:
            return _make_chatbot_result(section)

    if wants_presentation:
        pres_url = "https://demdikk.sn/presentation/"
        pres_text = _fetch_page_text(pres_url)
        if pres_text:
            # Si on cherche les directeurs spécifiquement
            if "directeur" in qn or "assane" in qn or "thierno" in qn:
                section = _extract_section(
                    pres_text,
                    (
                        "directeurs généraux",
                        "directeurs generaux",
                        "Directeur Général",
                        "Directeur General",
                        "six (06) directeurs",
                        "directeurs g",
                    ),
                    max_chars=2000,
                )
                if section:
                    return {
                        "answer": section,
                        "summary": section[:280],
                        "bullets": [],
                        "sources": [{"title": "Présentation – Dakar Dem Dikk", "url": pres_url, "score": 1.0}],
                        "results": [{"url": pres_url, "title": "Présentation – Dakar Dem Dikk", "snippet": section[:500], "full_text": section}],
                        "query_type": "general",
                        "needs_clarification": False,
                        "has_structured_data": False,
                        "is_city_query": False,
                        "is_line_query": False,
                    }
            # Présentation générale
            section = _extract_section(
                pres_text,
                (
                    "Dakar Dem Dikk",
                    "présentation",
                    "Présentation",
                    "historique",
                    "Historique",
                    "création",
                    "Creation",
                ),
                max_chars=2500,
            )
            if not section or len(section) < 80:
                section = pres_text[:2500].strip()
            if section:
                return {
                    "answer": section,
                    "summary": section[:280],
                    "bullets": [],
                    "sources": [{"title": "Présentation – Dakar Dem Dikk", "url": pres_url, "score": 1.0}],
                    "results": [{"url": pres_url, "title": "Présentation – Dakar Dem Dikk", "snippet": section[:500], "full_text": section}],
                    "query_type": "general",
                    "needs_clarification": False,
                    "has_structured_data": False,
                    "is_city_query": False,
                    "is_line_query": False,
                }

    if wants_emploi:
        # Essayer de récupérer la page emploi officielle
        emploi_url = "https://demdikk.sn/offres-demploi/"
        emploi_text = _fetch_page_text(emploi_url)
        if not emploi_text or len(emploi_text) < 100:
            emploi_url2 = "https://demdikk.sn/offres-emploi/"
            emploi_text = _fetch_page_text(emploi_url2)
        if emploi_text and len(emploi_text) > 100:
            section = emploi_text[:3000].strip()
            return {
                "answer": section,
                "summary": section[:280],
                "bullets": [],
                "sources": [{"title": "Offres d'emploi – Dakar Dem Dikk", "url": emploi_url, "score": 1.0}],
                "results": [{"url": emploi_url, "title": "Offres d'emploi – Dakar Dem Dikk", "snippet": section[:500], "full_text": section}],
                "query_type": "general",
                "needs_clarification": False,
                "has_structured_data": False,
                "is_city_query": False,
                "is_line_query": False,
            }
        # Fallback statique si la page emploi n'est pas accessible
        emploi_info = (
            "Pour consulter les offres d'emploi de Dakar Dem Dikk :\n"
            "– Site web : demdikk.sn/offres-demploi/\n"
            "– Candidature spontanée : envoyez un email ou rendez-vous au siège\n"
            "– Téléphone : +221 33 824 10 10 / +221 33 865 15 55\n"
            "– Email : info@demdikk.sn / contact@demdikk.sn\n"
            "– Adresse : Km 4,5 Avenue Cheikh Anta Diop, dépôt Ouakam, Dakar\n"
            "– Horaires : Lundi – Vendredi, 08h – 17h"
        )
        return _make_chatbot_result(emploi_info)

    if wants_perturbation:
        qn_pert = _norm(question)
        # Sous-section spécifique selon le mot-clé
        if "communication" in qn_pert or "crise" in qn_pert:
            section = _extract_section(
                page_text,
                ("### Communication de crise", "Communication de crise"),
                max_chars=900,
            )
            section = _clip_at_next_subheading(section)
        elif "intemperie" in qn_pert:
            section = _extract_section(
                page_text,
                ("### Intempéries", "### Intemperies", "Intempéries", "Intemperies"),
                max_chars=900,
            )
            section = _clip_at_next_subheading(section)
        elif "incident" in qn_pert or "panne" in qn_pert or "retard" in qn_pert:
            section = _extract_section(
                page_text,
                ("### Incidents techniques", "Incidents techniques"),
                max_chars=900,
            )
            section = _clip_at_next_subheading(section)
        elif "maintenance" in qn_pert or "innovation" in qn_pert or "paiement" in qn_pert:
            section = _extract_section(
                page_text,
                (
                    "## 13. Informations techniques et innovation",
                    "## 13. Informations techniques",
                    "Informations techniques et innovation",
                    "### Maintenance",
                    "### Paiement",
                ),
                max_chars=1800,
            )
            section = _clip_at_next_top_heading(section)
        else:
            # Toute la section "Gestion des perturbations et des crises"
            section = _extract_section(
                page_text,
                (
                    "## 14. Gestion des perturbations et des crises",
                    "## 14. Gestion des perturbations",
                    "Gestion des perturbations et des crises",
                    "Gestion des perturbations",
                ),
                max_chars=2000,
            )
            section = _clip_at_next_top_heading(section)
        if section:
            return _make_chatbot_result(section)

    return None


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
        qn = _norm(question)

        # ── Détection hors-sujet / charabia (aligné sur app_backup) ───────────
        _qwords = set(qn.split())
        _transport_ctx = (
            "bus", "ligne", "transport", "voyage", "dem dikk", "demdikk",
            "reservation", "billet", "ticket", "abonnement", "tek dem",
            "carte", "colis", "horaire", "tarif", "prix", "contact", "agence",
            "interurbain", "touba", "thiès", "thies", "saint-louis", "fatick",
        )
        _off_topic_like = (
            _question_looks_gibberish_normed(qn)
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
            _log_unknown_query(
                question,
                reason="gibberish" if _question_looks_gibberish_normed(qn) else "off_topic",
            )
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
                enhanced = _enhance_with_deepseek(fb_i, question)
                return (jsonify(enhanced), *rest) if rest else jsonify(enhanced)

            # Afrique Dem Dikk : prioritaire pour "gambie/senegal/banjul/afrique"
            af_triggers = ("afrique dem dikk", "afrique", "gambie", "gambia", "banjul", "senegal")
            wants_afrique = any(t in qn for t in af_triggers)
            fb_a = _fallback_afrique_dem_dikk(question)
            if fb_a and wants_afrique:
                enhanced = _enhance_with_deepseek(fb_a, question)
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
                    enhanced3 = _enhance_with_deepseek(fb3, question)
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

            enhanced = _enhance_with_deepseek(data, question)
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


# ── Point d'entrée (développement local) ─────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host='0.0.0.0', port=port, debug=debug)
