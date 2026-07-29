# Audit de faisabilité — Refonte cascade → scoring multi-source

**Date :** 2026-07-28  
**Périmètre :** analyse uniquement, aucune modification de code  
**Objectif :** évaluer le coût de remplacer la cascade séquentielle « mot-clé → return immédiat » par un modèle où **toutes les sources candidates** sont interrogées, **scorées de façon comparable**, puis la meilleure est choisie.

---

## 1. Inventaire des points de décision actuels

L’architecture réelle est **double couche** :

1. **`app.py`** — wrapper `_ask_with_deepseek` (pré-traitement + post-traitement autour de `app_backup.ask`)
2. **`app_backup.py`** — moteur principal `ask()` + helpers triggers

Légende :
- **Return immédiat** = la requête ne descend pas plus bas dans la cascade
- **Continue** = passe au bloc suivant ou retourne au wrapper pour post-traitement

---

### 1.1 Pré-traitement wrapper (`app.py`)

| # | Bloc | Fichier | Lignes | Condition | Retour | Type |
|---|------|---------|--------|-----------|--------|------|
| W0 | Question vide | `app_backup.py` | 4874–4881 | `not question` | Message « Veuillez poser… » | **Immédiat** |
| W1 | Correction typos | `app.py` | 2835–2840 | `_try_typo_recovery` + `_should_apply_typo_fix` | Modifie `question`/`qn`, **continue** | Continue |
| W2 | Expansion acronymes | `app.py` | 2842–2845 | SDD/DDD/ADD, DG→directeur via `_norm` | Modifie `question`/`qn`, **continue** | Continue |
| W3 | Smalltalk / salutations | `app.py` | 2851–2853 | `_conversational_kind` (bonjour, merci, qui es-tu…) | Réponse amicale DeepSeek ou template | **Immédiat** |
| W4 | Publicité / partenariat | `app.py` | 2855–2859 | `_is_publicite_query(qn)` | `_fallback_publicite_partenariat` | **Immédiat** |
| W5 | Définition sigle | `app.py` | 2861–2866 | `_json_acronym_definition` (SDD, DDD, ADD) | Payload acronyme | **Immédiat** |
| W6 | Présentation early | `app.py` | 2868–2872 | `_is_presentation_query` (nom société, mission sans recrutement exclu…) | `_fallback_presentation_page` | **Immédiat** |
| W7 | Hors-sujet strict | `app.py` | 2874–2886 | `_is_strict_off_topic` (sport, crypto, mots soft sans contexte transport) | `_OFF_TOPIC_REPLY` | **Immédiat** |

> **Note :** W6 et W7 empêchent d’atteindre `app_backup.ask()` pour une partie des questions présentation / hors-sujet.

---

### 1.2 Enrichissement historique (avant cascade backup)

| # | Bloc | Fichier | Lignes | Condition | Effet |
|---|------|---------|--------|-----------|-------|
| H1 | Enrichissement contexte | `app_backup.py` | 4691–4848 | Question courte + historique ville/ligne | Réécrit `question` (ex. « horaires » → « touba horaires ») — **ne return pas**, modifie l’entrée |

Dépendances : bloque l’enrichissement si événement (Magal), service autonome (colis, pub), ou nom société seul.

---

### 1.3 Cascade principale `app_backup.ask()`

| # | Bloc | Fichier | Lignes | Condition | Retour (`query_type`) | Type |
|---|------|---------|--------|-----------|----------------------|------|
| B1 | Comparaison X vs Y | `app_backup.py` | 4885–4890 | `_detect_comparison_query` | `comparison` | **Immédiat** |
| B2 | Présentation société (company) | `app_backup.py` | 4892–4913 | `_is_company_presentation_query` | `general` (page présentation) | **Immédiat** |
| B3 | Présentation bare / DDD | `app_backup.py` | 4915–4944 | tokens société, « c'est quoi DDD »… | `general` | **Immédiat** |
| B4 | Acronyme SDD/DDD/ADD | `app_backup.py` | 4946–4949 | `_json_acronym_definition` | `general` | **Immédiat** |
| B5 | Interurbain overview | `app_backup.py` | 4951–4954 | `_json_interurban_overview` (SDD, réseau interurbain…) | `interurban_overview` | **Immédiat** |
| B6 | Services DDD | `app_backup.py` | 4956–4959 | `_json_service_payload` (colis, location, pub, AIBD partiel…) | `general` | **Immédiat** |
| B7 | Événements FAQ | `app_backup.py` | 4961–4964 | `_try_event_faq_before_city_info` (Magal, Tabaski…) | `general` | **Immédiat** |
| B8 | Ville interurbaine | `app_backup.py` | 4966–4971 | `_json_interurban_city` + pas service autonome | `city_info` | **Immédiat** |
| B9 | Services (2e passe) | `app_backup.py` | 4973–4977 | mots `_SKIP_IMPLICIT_STOP_KEYWORDS` | `general` | **Immédiat** |
| B10 | Hors-sujet backup | `app_backup.py` | 4979–4980 | `_is_off_topic_question` (délègue à W7 ou règles locales) | `general` off-topic | **Immédiat** |
| B11 | Lookup admin résolu | `app_backup.py` | 4982–5025 | `lookup_resolved_query` (base questions traitées) | `general` | **Immédiat** |
| B12 | Ville (secours) | `app_backup.py` | 5030–5033 | `_json_interurban_city` si raté en B8 | `city_info` | **Immédiat** |
| B13 | Toutes les lignes | `app_backup.py` | 5035–5052 | `qtype == all_lines_summary` | `all_lines_summary` | **Immédiat** |
| B14 | Lignes → arrêt | `app_backup.py` | 5054–5111 | `lines_to_stop` ou inférence arrêt implicite | `lines_to_stop` | **Immédiat** |
| B15 | Ligne horaires | `app_backup.py` | 5117–5132 | `line_X` + `_line_horaires_intent` | `line_horaires` | **Immédiat** |
| B16 | Ligne détails | `app_backup.py` | 5114–5147 | `line_X` + ligne trouvée | `line_details` | **Immédiat** |
| B17 | Ligne inconnue | `app_backup.py` | 5148–5159 | `line_X` + numéro absent | `line_details` (erreur) | **Immédiat** |
| B18 | RAG vectoriel | `app_backup.py` | 5164–5180 | `_search` score ≥ 0.30 | `general` (extrait metadata) | **Immédiat** |
| B19 | Fallback contact | `app_backup.py` | 5182–5192 | aucun match | `other` | **Immédiat** (wrapper peut encore enrichir) |

---

### 1.4 Post-traitement wrapper (`app.py`, après `_original_ask()`)

S’exécute **uniquement** si le backup a retourné sans être intercepté en W3–W7 (ou si B18/B19 a produit une réponse faible).

| # | Bloc | Fichier | Lignes | Condition | Action | Type |
|---|------|---------|--------|-----------|--------|------|
| P0 | Comparaison LLM | `app.py` | 2902–2907 | `query_type == comparison` | DeepSeek comparaison | **Immédiat** |
| P1 | Interurbain protégé | `app.py` | 2909–2911 | `city_info`, `interurban_overview` | Pas d’écrasement triggers | **Immédiat** |
| P2 | **Trigger 1 — AIBD** | `app.py` | 2913–2928 | mot-clé `_AIBD_TRIGGERS` | FAQ extract / fixe site | **Immédiat** |
| P3 | Interurbain fallback | `app.py` | 2930–2946 | triggers SDD/interurbain + `_fallback_interurban` | Overview / FAQ interurbain | **Immédiat** |
| P4 | **Trigger 5 — Afrique** | `app.py` | 2948–2980 | `_matches_afrique_trigger` | FAQ/RAG/fixe ADD | **Immédiat** |
| P5 | RAG « not found » patch | `app.py` | 2982–2988 | answer contient « je n'ai pas trouvé » | `_fallback_from_site` ou interurbain | **Continue** (mutate `data`) |
| P6 | **Trigger 6 — Présentation** | `app.py` | 2993–3033 | `_matches_presentation_trigger` | DG, mission, recrutement, page | **Immédiat** |
| P7 | **Trigger 4 — Application** | `app.py` | 3035–3071 | `_matches_app_trigger` | FAQ appli / fixe | **Immédiat** |
| P8 | **Trigger 3 — Tek Dem** | `app.py` | 3073–3090 | `_matches_tek_dem_trigger` | FAQ Tek Dem / fixe | **Immédiat** |
| P9 | **Trigger 2 — Colis** | `app_backup.py` + `app.py` | 4283–4322, 3092–3128 | `_COLIS_TRIGGERS` | FAQ colis / fixe | **Immédiat** |
| P10 | **Trigger 7 — FAQ §5.1** | `app.py` | 3130–3171 | `_matches_faq7_trigger` (bagages, géoloc, perturbation…) | FAQ extract / curated / fixe | **Immédiat** |
| P11 | Fallback site keywords | `app.py` | 3173–3230 | `_site_triggers` + `not rag_ok` | `_fallback_from_site` (chatbot-2303 blocks) | **Immédiat** |
| P12 | FAQ systématique | `app.py` | 3232–3245 | `not is_structured` + score FAQ ≥ 0.5 ou answer weak | `_search_chatbot_page_blocks` | **Immédiat** |
| P13 | RAG + LLM enhance | `app.py` | 3247–3262 | `rag_ok` → `_enhance_if_safe` (DeepSeek) | Reformulation ou fallback FAQ | **Immédiat** |

---

### 1.5 Triggers métier (logique dans `app_backup.py`, invoqués par wrapper ou B6)

| Trigger | Fonctions clés | Match | Sources internes |
|---------|----------------|-------|------------------|
| T1 AIBD | `_try_aibd_specific_answer` | ~650–850 | FAQ chatbot-2303, RAG |
| T2 Colis | `_try_colis_specific_answer` | ~900–1100 | FAQ, RAG, `_try_ddd_service_fallback` |
| T3 Tek Dem | `_try_tek_dem_specific_answer` | ~1050–1150 | FAQ, RAG |
| T4 Application | `_try_app_specific_answer` | ~1232–1338 | FAQ extract, RAG, `_app_fixe_payload` |
| T5 Afrique | `_try_afrique_specific_answer` | ~1517–1613 | FAQ ADD, tarif Banjul curated, RAG |
| T6 Présentation | `_try_presentation_specific_answer` | ~1980–2089 | FAQ, page présentation, RAG, curated DG/recrutement |
| T7 FAQ7 | `_try_faq7_specific_answer` | ~2193–2264 | FAQ extract, RAG, `_FAQ7_CURATED` |

Chaque trigger implémente en interne une **mini-cascade** : extract live → search FAQ blocks → RAG → curated/fixe.

---

### 1.6 Synthèse quantitative

| Métrique | Valeur estimée |
|----------|----------------|
| Points de `return` immédiat (backup) | ~20 |
| Points de `return` immédiat (wrapper pré) | 5–7 |
| Points de `return` immédiat (wrapper post) | ~15 |
| Triggers numérotés post-RAG | 7 |
| Chemins dupliqués présentation | 3 (W6, B2/B3, P6) |
| Chemins dupliqués acronyme | 2 (W5, B4) |
| Chemins dupliqués hors-sujet | 2 (W7, B10) |

---

## 2. Dépendances entre ces points

### 2.1 Ordre imposé par design (non interchangeable aujourd’hui)

```
W3 smalltalk ──► bypass total backup
W4 pub ──► avant backup (évite inférence arrêt « de la » sur « publicité »)
W7 off-topic ──► avant backup (sinon RAG répondrait sur sport/météo)
H1 enrichissement ──► avant detect_query_type (sinon « horaires » seul ≠ ville)
B7 événements ──► AVANT B8 city_info (sinon « magal touba » → fiche Touba)
B8 city_info ──► AVANT B14 inférence arrêt (ville ≠ arrêt urbain)
B10 off-topic ──► AVANT B14 (sinon « restaurant » → arrêt implicite)
B14 arrêt ──► AVANT B16 line_X (sinon « Sandaga » peut devenir ligne)
P1 city_info protégé ──► empêche triggers d’écraser données structurées interurbaines
P2–P10 triggers ──► ordre fixe : AIBD → interurbain → Afrique → présentation → appli → tek → colis → faq7
P11–P13 ──► ne s’exécutent que si aucun trigger n’a return
```

### 2.2 Dépendances par bloc — ce qui casse si l’ordre change

| Bloc amont | Bloc aval | Risque si amont supprimé / retardé |
|------------|-----------|-------------------------------------|
| W7 hors-sujet | B18 RAG | RAG répond sur foot, crypto, météo avec chunks site |
| W4 publicité | B14 arrêt | « publicité de la ligne » → inférence arrêt « de la » |
| H1 enrichissement | B8 city_info | « prix » seul après « touba » ne reçoit plus le contexte ville |
| B7 événements | B8 city_info | « magal 2026 » après « touba » enrichi en question ville |
| B8 city_info | P6 présentation | « touba » match parfois présentation si mal ordonné |
| B5 overview | P3 interurbain | Double réponse SDD possible |
| B18 RAG | P2–P10 triggers | RAG générique « gagne » sur FAQ spécialisée (mot-clé faible score) |
| P1 protection | P6–P7 | Trigger écrase fiche ville structurée (prix/horaires exacts) |
| `_faq7_blocked_by_other_trigger` | P10 | FAQ7 bagages bloquée si « colis » ou « appli » matche avant |
| B16 line_details | frontend | UI attend `line_details` JSON — horaires intent ajouté récemment en B15 |

### 2.3 Effets de bord documentés (dette actuelle)

- **Double présentation** : W6, B2/B3 et P6 peuvent tous toucher la présentation avec logiques différentes.
- **Scoring incomparable** : FAQ block score (0–1 Jaccard-like), RAG cosine (0–4+), trigger « déjà FAQ » (seuil 0.5) — pas sur la même échelle.
- **Winner-takes-all** : le premier trigger qui matche un mot-clé return, même si une autre source serait plus pertinente (ex. « mission dem dikk » capté early présentation vs FAQ mission).

---

## 3. Ce qui doit RESTER déterministe (non négociable)

Ces cas exigent une **correspondance exacte** ou des **données structurées indexées** — jamais un LLM seul, jamais un score sémantique approximatif comme arbitre final.

### 3.1 Données structurées exactes (JSON / tables)

| Cas | Source | Raison |
|-----|--------|--------|
| Numéro de ligne reconnu (`line_X`) | `lines_data.py` / `_URBAN_LINES` | Liste d’arrêts exacte, terminus, catégorie |
| Ligne inconnue | IDEM | Message d’erreur déterministe |
| Lignes desservant un arrêt | `find_lines_for_stop()` | Correspondance nom arrêt → lignes |
| Toutes les lignes | `_URBAN_LINES` | Comptage et listes |
| Ville interurbaine reconnue | `INTERURBAIN_SECTIONS` / snapshot | Horaires, tarifs, contacts par ville |
| Aspect ville (horaires/prix/durée) | `_format_city_response_prose(aspect=…)` | Champs structurés, pas paraphrase |
| Comparaison X vs Y (données) | `_resolve_subquery_context` × 2 | Agrégation factuelle |
| Tarif Banjul curated | constante `_AFRIQUE_BANJUL_PRICE` | Montant FCFA officiel |
| Acronymes SDD/DDD/ADD | `_ACRONYM_DEFINITIONS` | Définitions figées |
| Lookup admin `repondu` | base `unknown_queries` | Texte validé humain |

### 3.2 Identifiants et match binaires (pas de score flou)

| Cas | Règle |
|-----|-------|
| `_detect_line_number` | Regex stricte « ligne N » — match ou non |
| `_detect_city` / `get_section_by_ville` | Nom ville dans whitelist |
| `_matches_*_trigger` | Sous-chaîne mot-clé (avec exclusions `_faq7_blocked…`) |
| `_is_strict_off_topic` | Tokens hard/soft + contexte transport |
| `_is_acronym_definition_query` | Marqueurs « que signifie » + acronyme |
| Événement Magal/Tabaski | `_detect_event_intent` avant city |

### 3.3 Reformulation LLM interdite (`enhance=False` ou skip)

D’après `_enhance_if_safe` et les `return _reply(..., enhance=False)` :

- `city_info`, `interurban_overview`
- Tous les triggers T1–T7 (FAQ extract, curated)
- Acronymes, comparaisons partielles
- `line_details`, `line_horaires`, `all_lines_summary`, `lines_to_stop`
- Hors-sujet, publicité, smalltalk (templates)

### 3.4 Ce qui PEUT passer par scoring / LLM

- Réponses RAG `general` sans structure (score ≥ 0.30)
- FAQ chatbot-2303 prose (après extract + `_format_faq_page_prose`)
- Reformulation DeepSeek **sur contenu déjà validé** (`_enhance_if_safe`)
- Smalltalk / comparaison `both` (DeepSeek dédié avec garde-fous)

---

## 4. Proposition d'architecture cible (sans implémentation)

### 4.1 Principe : retrieve → score → arbitrate → render

```
Question (+ historique enrichi)
    │
    ├─► [Phase 0] Garde-fous synchrones (non scorés)
    │       smalltalk, hors-sujet strict, gibberish, question vide
    │
    ├─► [Phase 1] Retrieve parallèle (async / thread pool)
    │       ├── Structured: line?, city?, stop?, event?, acronym?
    │       ├── Triggers: T1..T7 candidats (sans return)
    │       ├── FAQ blocks search (chatbot-2303)
    │       ├── RAG top-k (metadata embeddings)
    │       └── Live extract (presentation, ADD, appli…) si mot-clé faible
    │
    ├─► [Phase 2] Scoring unifié
    │       Chaque candidat → CandidateAnswer {
    │         source_type, payload, confidence, exact_match: bool,
    │         intent_coverage: float, structured: bool
    │       }
    │
    ├─► [Phase 3] Arbitrage
    │       if any exact_match && structured → winner = max(confidence) among structured
    │       elif event_intent → boost FAQ event
    │       else → winner = argmax(composite_score)
    │
    └─► [Phase 4] Render
            structured → JSON inchangé + enhance=False
            prose → _prepare_final_answer, LLM optionnel si safe
```

### 4.2 Sources à interroger en parallèle (par type de question)

| Famille question | Candidats retrieve |
|------------------|-------------------|
| Toute question | RAG top-5, FAQ blocks top-3 |
| Mot transport + ville | `city_info` (tous aspects), event FAQ |
| « ligne N » | `line_details`, `line_horaires`, RAG ligne |
| Arrêt / terminus | `lines_to_stop`, RAG |
| SDD / interurbain | `interurban_overview`, FAQ SDD, RAG |
| Mot-clé trigger | Module T correspondant (sans early return) |
| Présentation / DG / mission | Extract présentation, FAQ, RAG |
| Service (colis, appli…) | Trigger + FAQ + RAG |

### 4.3 Score comparable — proposition de composite

Normaliser toutes les sources sur **[0, 1]** :

```
composite = w_intent * intent_coverage
          + w_match  * entity_match      # ville/ligne/trigger exact = 1.0
          + w_sem    * semantic_score    # RAG cosine rescaled, FAQ block score
          + w_quality * answer_quality   # longueur min, pas junk, satisfies_intent()
          - w_conflict * conflict_penalty # ex. city vs line vs faq7 block list
```

**Règles de priorité hard (override score) :**

1. `exact_match && structured` → toujours gagner sur RAG/LLM
2. `event_intent && faq_event.ok` → gagner sur `city_info` pour même toponyme
3. `off_topic` → aucun candidat ne sort du pipeline
4. Trigger exclusif (`_faq7_blocked_by_other_trigger`) → pénalité -∞ sur FAQ7 si trigger prioritaire actif

**Calibration nécessaire :** corpus de ~50–100 questions étiquetées (déjà listées en tests manuels) + régression automatique.

### 4.4 Distinction structuré vs LLM dans la cible

| Étape | Structuré exact | Prose |
|-------|-----------------|-------|
| Arbitrage | `enhance=False` forcé | score seuil |
| Render | Payload JSON + templates frontend | `_format_faq_page_prose` |
| LLM | **Jamais** | Uniquement si `llm_safe=true` ET `structured=false` ET pas de trigger curated |

---

## 5. Estimation de l'effort

Légende effort : **P** = petit (< 1 j), **M** = moyen (1–3 j), **G** = gros (> 3 j)  
Risque régression : **Faible / Moyen / Élevé**

| Route / bloc actuel | Décision refonte | Effort | Risque | Commentaire |
|---------------------|------------------|--------|--------|-------------|
| W0 question vide | Garder tel quel | P | Faible | Hors scope scoring |
| W1–W2 typos / acronymes | Garder tel quel | P | Faible | Pré-normalisation |
| W3 smalltalk | Garder tel quel | P | Faible | Avant retrieve |
| W4 publicité | Adapter légèrement | P | Faible | Candidat score élevé si mot-clé |
| W5/B4 acronyme | Garder tel quel | P | Faible | Match binaire |
| W6/B2/B3 présentation early | **Réécrire** | M | Moyen | Fusionner en 1 candidat « presentation » |
| W7/B10 hors-sujet | Garder tel quel | P | Moyen | Doit rester phase 0 |
| H1 enrichissement | Adapter légèrement | M | **Élevé** | Contexte influence tous les scores |
| B1 comparaison | Adapter légèrement | M | Moyen | 2 retrieve parallèles + compose |
| B5 interurbain overview | Adapter légèrement | P | Faible | Candidat structuré |
| B6/B9 services | Adapter légèrement | M | Moyen | Déjà proche mini-scoring interne |
| B7 événements | Adapter légèrement | M | Moyen | Boost intent, pas ordre fixe |
| B8/B12 city_info | **Garder deterministe** | M | **Élevé** | Cœur métier — wrapper scoring autour |
| B13 all_lines | Garder tel quel | P | Faible | Structuré exact |
| B14 lines_to_stop | Garder tel quel | M | Moyen | Inférence arrêt fragile |
| B15/B16 line_X | Garder tel quel | M | Moyen | Intent horaires vs détails |
| B18 RAG backup | **Réécrire** | G | **Élevé** | Devient candidat, pas default |
| B19 fallback contact | Garder tel quel | P | Faible | Dernier recours |
| P1 protection interurbain | Intégrer à arbitrage | M | Moyen | Règle hard override |
| P2–P10 triggers T1–T7 | **Réécrire** | **G** | **Élevé** | ~7× mini-cascades → fonctions `candidate_*` |
| P11 site triggers | Fusionner dans FAQ retrieve | M | Moyen | Doublon avec P12 |
| P12 FAQ systématique | **Réécrire** | M | Moyen | Devient canal principal prose |
| P13 LLM enhance | Adapter légèrement | M | Moyen | Phase render seulement |
| Frontend `script.js` | Adapter légèrement | M | Moyen | `query_type` nouveaux, pas de cascade |

### 5.1 Estimation globale

| Scénario | Effort calendaire (1 dev) | Risque global |
|----------|---------------------------|---------------|
| Refonte complète big-bang | **6–10 semaines** | Élevé |
| Migration progressive (voir §6) | **8–12 semaines** étalées | Moyen |
| Module scoring + 2 triggers pilotes | **2–3 semaines** (POC) | Faible–moyen |

---

## 6. Recommandation

**Migration progressive route par route**, en conservant la cascade actuelle comme **fallback** tant que le score composite n’est pas validé sur un corpus de régression.

Justification : le système actuel encode ~40 points de décision avec des **dépendances d’ordre implicites** (événement avant ville, off-topic avant arrêt, protection interurbain avant triggers). Une refonte big-bang remettrait en cause des centaines de cas edge validés manuellement (Magal/Touba, « de la », DG/présentation, FAQ7 vs colis/appli). Un POC limité — **city_info + FAQ7 + RAG en parallèle avec arbitrage** — permettrait de valider le scoring comparable sur ~30 requêtes avant d’étendre aux triggers T1–T6.

**Ce chantier vaut le coup** si la maintenance de la cascade devient insoutenable (chaque nouveau mot-clé = nouveau conflit d’ordre). **Il ne vaut pas le coup en one-shot** sans corpus de tests automatisés : le coût de régression dépasse probablement le gain à court terme par rapport à l’ajout continu de patches triggers.

---

## Annexe — Fichiers clés à lire pour la refonte

| Fichier | Rôle |
|---------|------|
| `app.py` L2820–3267 | Wrapper pré/post |
| `app_backup.py` L4851–5192 | Cascade `ask()` |
| `app_backup.py` L650–2264 | Triggers T1–T7 |
| `app.py` L2053–2289 | FAQ blocks search |
| `app.py` L2780–2816 | LLM enhance guard |
| `script.js` L29–180 | Rendu par `query_type` |
| `regles.md` | Règles métier documentées |

---

*Document généré par audit statique du code — aucune modification appliquée au dépôt.*
