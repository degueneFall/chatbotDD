# Règles et logique — Chatbot Maï Dem Dikk

Document de référence pour comprendre l'architecture, les règles métier fixées et le rôle de DeepSeek.  
**Ne contient aucune valeur sensible** (clés API, mots de passe, secrets).

---

## 1. Vue d'ensemble

Le chatbot **Maï** répond aux questions sur **Dakar Dem Dikk (DDD)** : réseau urbain Dakar, interurbain (Sénégal Dem Dikk), FAQ site officiel, services annexes.

### Architecture en deux couches

| Couche | Fichier | Rôle |
|--------|---------|------|
| **Wrapper** | `app.py` | Point d'entrée Flask/Gunicorn (`app:app`). Enrichissement DeepSeek, fallbacks live site, FAQ chatbot-2303, filtres hors-sujet. |
| **Moteur métier** | `app_backup.py` | Route `/ask`, RAG vectoriel, lignes urbaines, villes interurbaines, historique, détection d'intention. |

`app.py` charge `app_backup.py` puis **remplace** la vue Flask `ask` par un wrapper `_ask_with_deepseek`.

### Données

| Source | Fichier / URL | Usage |
|--------|---------------|--------|
| Lignes urbaines | `lines_data.py` | Horaires, arrêts, fiches lignes |
| Interurbain structuré | `interurbain_data.py` | Prix, horaires, contacts par ville |
| Itinéraires / durées | `interurbain_routes.py` | Parse chatbot-2303 + fallback local |
| Index RAG | `data/scraped.jsonl` + embeddings | Recherche sémantique générale |
| FAQ live | `https://demdikk.sn/chatbot-2303/` | Scraping par blocs (prioritaire si RAG faible) |
| Présentation | `https://demdikk.sn/presentation/` | Questions « c'est quoi DDD » |

---

## 2. Flux complet d'une question (`POST /ask`)

Ordre **strict** dans `app.py` (wrapper), puis `app_backup.py` si non intercepté avant.

### Phase A — Wrapper `app.py` (avant `app_backup`)

1. **Normalisation** : `_norm()` (minuscules, sans accents, lemmatisation légère).
2. **Correction de typos** : vocabulaire transport + villes ; notice optionnelle à l'utilisateur.
3. **Conversations sociales** (sans RAG) : salutations, remerciements, identité Maï, présentation perso → DeepSeek léger (`_generate_friendly_reply`), température plus haute.
4. **Publicité / partenariat** → fallback page dédiée.
5. **Présentation société** (« Dakar Dem Dikk », « c'est quoi DDD », mission, histoire…) → page `presentation/`.  
   **Exception** : sujets FAQ explicites (CCO, bagages, remboursement, application…) **ne passent pas** par la présentation.
6. **Hors-sujet strict** (sport, météo, cuisine…) **sans** contexte transport DDD → message de refus standard (`_OFF_TOPIC_REPLY`), **sans** LLM.

### Phase B — `app_backup.py` (`ask()`)

7. **Enrichissement historique** : questions courtes (« la durée », « le prix ») rattachées à la dernière ville/ligne du fil — sauf **nouveau sujet service** (location bus, pub, colis…).
8. **Présentation société** (second filet).
9. **`interurban_overview`** : mot « interurbain » seul → intro Sénégal Dem Dikk + **liste des destinations** (pas le pavé complet par ville).
10. **Services autonomes** (location, pub, messagerie…) **avant** détection ville.
11. **`city_info`** : ville interurbaine détectée → réponse structurée par **aspect** (voir §4).
12. **Base connaissances résolue** (admin) si entrée validée.
13. **Lignes urbaines** : toutes les lignes, arrêt → lignes, fiche ligne, détails.
14. **RAG vectoriel** : index `scraped.jsonl` + seuil de score (~0.28–0.30).
15. **Hors-sujet tardif** si rien trouvé.

### Phase C — Wrapper `app.py` (après réponse `app_backup`)

16. **Ne pas écraser** : `city_info`, `interurban_overview` → `enhance=False` (pas de LLM).
17. **Fallbacks ciblés** : AIBD/navette, interurbain général, Afrique Dem Dikk (Gambie/Banjul), triggers site (bagages, remboursement, Tek Dem, colis, application…).
18. **FAQ chatbot-2303 systématique** (hors requêtes structurées) :
    - Toujours appeler `_search_chatbot_page_blocks`.
    - Prioriser si score ≥ 0,5 **ou** réponse RAG absente / « je n'ai pas… » / non pertinente.
    - Réponse FAQ → **`enhance=False`** (texte officiel, pas de reformulation LLM).
19. **DeepSeek** (`_enhance_if_safe`) si RAG exploitable et non structuré.
20. **Dernier recours FAQ** si après LLM la réponse dit encore « je n'ai pas… ».
21. **Log** des questions sans réponse (`unknown_queries.json`).

---

## 3. Types de requêtes structurées (pas de LLM)

Définis dans `_STRUCTURED_QUERY_TYPES` :

- `all_lines_summary`, `line_X`, `lines_to_stop`, `line_details`
- `city_info`, `interurban_overview`

Critères RAG « exploitable » (`_rag_answer_trustworthy`) :

- Score index suffisant, longueur minimale, **pas** un chunk actualité/réseaux sociaux, **pertinence** vs mots de la question.

---

## 4. Règles interurbain (villes)

### 4.1 Détection

- Ville reconnue via `interurbain_data.py` (`get_section_by_ville`, `_detect_city`).
- **Ville seule** (ex. « touba ») → aspect `clarify` : confirmation + prix + invitation à préciser (**pas** liste d'exemples).

### 4.2 Aspects (`_city_query_aspect`)

| Aspect | Déclencheurs | Contenu réponse |
|--------|--------------|-----------------|
| `full` | Intention voyage (« je veux aller à… », « voyage »…) | Prix + bus (départs, jours, durée) + réservation + contact local. **Sans itinéraire détaillé.** |
| `clarify` | Ville seule | Confirmation + prix + « dites-moi si vous cherchez horaires, réserver… » |
| `prix` | prix, tarif, combien, FCFA… | **Uniquement** le tarif. Ex. « Le trajet vers Touba coûte X FCFA. » |
| `duree` | durée, temps, combien de temps… (**sans** itinéraire) | **Uniquement** la durée. Ex. « Le trajet vers Touba : environ 3 h de route. » |
| `horaires` | horaires, départs… (sans itinéraire) | Phrase bus : lieux de départ, jours, heures (+ durée si dispo). |
| `itineraire_detail` | itinéraire, trajet, route… | Itinéraire + horaires de départ + durée. **Sans prix ni réservation.** |
| `itineraire` | (legacy interne) | Itinéraire seul. |
| `reservation` | réserver, billet, ticket… | Texte réservation standard (`_INTERURBAIN_RESERVATION`). |
| `contact` | contact, téléphone, arrivée… | Lieu d'arrivée / contact local. |

**Priorité aspects** : horaires → durée → prix → itinéraire détaillé.

### 4.3 Format voyage complet (`full`)

Structure fixe en prose (pas de listes à puces) :

1. « Pour aller à {ville}, le trajet coûte {prix}. »
2. Phrase bus : terminus Dakar + point ville, jours, départs, **durée simplifiée** (« environ X h de route » — une seule durée représentative, pas par créneau).
3. Réservation (`_INTERURBAIN_RESERVATION_SHORT`) + contact sur place si connu.

**Pas d'itinéraire** dans le bloc `full`.

### 4.4 Durées et itinéraires

- Source : `interurbain_routes.py` (parse page chatbot-2303) + horaires locaux.
- `_pick_representative_duration` : une durée médiane/représentative.
- Villes sans données chatbot-2303 (ex. Podor, Ndioum, Vélingara) : pas d'itinéraire/durée automatique.

### 4.5 Réseau interurbain seul

Triggers : `interurbain`, `senegal dem dikk`, `réseau-interurbain`, etc.  
**Sans** ville ni intention voyage → `interurban_overview` : intro + liste des ~24 destinations.

### 4.6 Changement de sujet dans l'historique

Si l'utilisateur demande un **service autonome** après une ville (« et pour louer un bus ») :

- `_is_standalone_service_question` → **ne pas** enrichir avec la ville précédente.
- Répondre sur le service, pas sur Touba/Thiès.

---

## 5. FAQ chatbot-2303 (`app.py`)

### 5.1 Recherche

- Page scrapée live (cache ~10 min), **non tronquée** (sections bas de page : CCO, syndicats…).
- **Marqueurs thématiques** prioritaires : CCO, bagages, remboursement, annulation, application mobile, disponibilité appli, etc.
- Scoring par mots dans titre/corps ; acronymes `(CCO)` bonus.
- **Faux positifs évités** : « CCO » ne matche plus dans « accompagnement ».

### 5.2 Formatage prose (`_format_faq_page_prose`)

Avant envoi au frontend :

- Fusion lignes HTML cassées (« Oui » + « , Dakar… »).
- Suppression titre redondant en en-tête vert.
- Sous-sections « Rôle et missions : » intégrées en prose.
- **Pas de LLM** sur ces réponses (`enhance=False`).

### 5.3 Quand la FAQ gagne sur le RAG

- Score FAQ ≥ 0,5, **ou**
- RAG faible : vide, « je n'ai pas trouvé », chunk actualité, hors-sujet.

---

## 6. RAG (index vectoriel)

- Modèle embeddings : `paraphrase-multilingual-MiniLM-L12-v2`.
- Données : ~6 pages site → chunks dans `data/scraped.jsonl`.
- **Limite connue** : un chunk RAG « acceptable » en score mais **non pertinent** (actualités, SEO) peut bloquer la FAQ si `rag_ok=true` — d'où les filtres `_answer_looks_like_junk`, `_answer_relevant_to_question`, et la FAQ systématique en fallback.

---

## 7. DeepSeek — rôles et limites

DeepSeek n'est **pas** un moteur de décision. Il ne choisit pas la route (ville vs FAQ vs ligne). Il intervient **après** que le backend a sélectionné le contenu.

### 7.1 Quand DeepSeek est appelé

| Cas | Fonction | enhance |
|-----|----------|---------|
| Salutations / remerciements / identité | `_deepseek_simple_chat` | N/A (réponse directe) |
| Réponse RAG ou site générale | `_enhance_with_deepseek` via `_enhance_if_safe` | `True` |
| FAQ chatbot-2303, interurbain structuré, fallbacks site triggers | — | **`False`** |
| Lignes / villes structurées | — | **`False`** (skip) |

Si `DEEPSEEK_API_KEY` absente → réponses site/index nettoyées sans reformulation.

### 7.2 Ce que fait `_enhance_with_deepseek`

1. Rassemble le **contexte** : answer, summary, bullets, results RAG, fallbacks site injectés si mots-clés (colis, Tek Dem, remboursement, interurbain…).
2. Injecte l'**historique** client (max ~12 tours) pour références implicites.
3. Injecte données **ville interurbaine** de l'historique si RAG n'a pas renvoyé `is_city_query`.
4. Envoie prompt système `_LLM_SYSTEM` (persona **Maï**) + consigne : **reformuler sans inventer**.
5. Température **0,3**, max ~1200 tokens.
6. Post-traitement : `_strip_llm_hedging` (retire « je n'ai pas le détail… »).
7. Si réponse LLM = « info manquante » mais FAQ disponible → **remplace par FAQ**.
8. Si LLM améliore une réponse `city_info` → flags structurés retirés pour affichage prose front.

### 7.3 Persona Maï (`_LLM_SYSTEM`) — règles clés

- Ton conseillère humaine, français, **pas de markdown** (`##`, `**`).
- **Reformulation uniquement** du contexte fourni.
- **Ne jamais inventer** prix, horaires, destinations.
- Fourchettes de prix : toujours la fourchette complète.
- Info absente du contexte → rediriger vers le service client DDD (numéro officiel sur demdikk.sn), **sans** dire « je n'ai pas l'information ».
- Questions ambiguës → **une seule** question de précision.
- Hors-sujet pur → message refus standard (dans le prompt ; le wrapper filtre déjà en amont).

### 7.4 Ce que DeepSeek ne doit pas faire

- Décider si la question concerne Touba ou les bagages.
- Remplacer les réponses interurbaines structurées (prix/durée seuls).
- Inventer des sections absentes du site (ex. CCO si FAQ non trouvée).

---

## 8. Historique conversationnel

- Champs JSON : `history` ou `conversationHistory` (liste `{role, content}`).
- **Enrichissement** (`_enrich_short_question_from_history`) :
  - « la durée », « le prix », « d'où », « les horaires » → préfixe ville/ligne du fil.
  - Regex `_FAST_REF` pour suivi rapide sans appel LLM.
  - **Exception prix** : ne pas préfixer « ligne X » sur une question tarif (évite fiche ligne urbaine).
- **Nouveau sujet service** : pas d'enrichissement avec ville précédente.

---

## 9. Frontend (`script.js`, `ui.html`)

- `formatResponseText` : titres de section → blocs visuels ; prose interurbaine affichée en paragraphes.
- Heuristique `looksLikeProseNotSectionTitle` : évite titres verts sur « Oui », `(CCO)`, lignes commençant par `,`.
- « voir plus » si réponse tronquée.
- Questions **vagues** côté client (`isVagueQuestion`) : certaines envoyées quand même au backend (villes, « touba », « interurbain »).

---

## 10. Hors-sujet et gibberish

- **Strict** (wrapper) : mots off-topic sans contexte DDD/transport.
- **Tardif** (backup) : après échec RAG/lignes.
- **Gibberish** consonnes : message d'aide, pas d'appel LLM.

---

## 11. Variables d'environnement (noms uniquement)

| Variable | Usage |
|----------|--------|
| `DEEPSEEK_API_KEY` | Clé API DeepSeek (optionnelle — sans clé, pas de reformulation) |
| `DEEPSEEK_BASE_URL` | URL API (défaut : endpoint public DeepSeek) |
| `DEEPSEEK_MODEL` | Modèle (défaut : `deepseek-chat`) |
| `DEEPSEEK_TIMEOUT_S` | Timeout requête LLM |

Fichier `.env` local ; ne jamais committer.

---

## 12. Déploiement production

- Gunicorn doit cibler **`app:app`**, pas `app_backup:app`.
- Diagnostic : `GET /api/wrapper_ping` → `ask_wrapped_deepseek: true`.

---

## 13. Principes directeurs (résumé)

1. **Cascade déterministe d'abord**, LLM ensuite pour la **forme**, pas le **fond**.
2. **Une intention → une réponse minimale** (prix seul, durée seule, etc.).
3. **Interurbain structuré** = prose agent dans `app_backup.py`, jamais écrasé par le wrapper.
4. **FAQ live** prime sur RAG faible ; formatage prose obligatoire.
5. **Historique** : enrichir les suivis courts, respecter les changements de sujet.
6. **Maï** parle humain ; le site officiel demdikk.sn est la source de vérité.

---

## 14. Fichiers clés (index rapide)

| Fichier | Responsabilité |
|---------|----------------|
| `app.py` | Wrapper `/ask`, DeepSeek, FAQ live, fallbacks, présentation, hors-sujet |
| `app_backup.py` | `/ask` métier, RAG, lignes, interurbain, historique |
| `interurbain_data.py` | Données villes (prix, horaires, contacts) |
| `interurbain_routes.py` | Itinéraires et durées depuis chatbot-2303 |
| `lines_data.py` | Réseau urbain Dakar |
| `script.js` | Rendu UI, formatage réponses, historique client |
| `ui.html` | Interface chatbot |
