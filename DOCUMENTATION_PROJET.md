# Documentation du projet — Dakar Dem Dikk Chatbot

Ce document décrit tout ce qui a été réalisé dans le projet **Dakar Dem Dikk Chatbot** jusqu’à ce jour.

---

## 1. Vue d’ensemble

**Objectif** : Assistant conversationnel (chatbot) pour répondre aux questions des usagers sur **Dakar Dem Dikk** (transport public au Sénégal) : lignes urbaines, arrêts, réseau interurbain (villes, prix, horaires, contacts), réservation, etc.

**Stack** : Backend Flask (Python), recherche par similarité (embeddings Sentence Transformers + fallback TF-IDF), données structurées interurbain, interface web (HTML/CSS/JS).

**Sources de données** :
- Site **demdikk.sn** (pages scrapées et indexées)
- Fichier de référence **interurbain_data.py** (prix, horaires, contacts par ville)

---

## 2. Structure du projet

| Fichier / Dossier | Rôle |
|-------------------|------|
| **app.py** | Application Flask principale (~6075 lignes) : API `/ask`, logique de requêtes, extraction lignes/arrêts/villes, recherche, réponses structurées |
| **interurbain_data.py** | Données de référence du réseau interurbain (villes, prix, horaires, lieux de contact) + fonctions `get_section_by_ville`, `get_prix_for_ville`, `get_contact_for_ville` |
| **scraper.py** | Scraping de pages demdikk.sn (info-voyageurs, services, presentation, contact) → `data/scraped.jsonl` |
| **indexer.py** | Découpage des textes en chunks, calcul des embeddings (SentenceTransformer), sauvegarde `data/embeddings.npy` et `data/metadata.json` |
| **split_metadata.py** | Découpage fin des documents (par paragraphes/phrases, MAX_WORDS=120) pour améliorer l’index |
| **ui.html** | Page d’interface du chatbot (titre, zone de chat, formulaire de question) |
| **script.js** | Logique frontend : envoi des questions à `/ask`, affichage des réponses, boutons « Lignes », détails par ligne, phrases courantes (bonjour, merci, aide…) |
| **style.css** | Styles de l’interface (header, chat, formulaire) |
| **requirements.txt** | Dépendances : flask, requests, beautifulsoup4, sentence-transformers, numpy |
| **data/** | Données : `scraped.jsonl`, `metadata.json`, `embeddings.npy` (générés par scraper + indexer) |
| **static/** | Assets (ex. logo Dem Dikk) |
| **test_*.py** | Batterie de tests (API, extraction lignes/arrêts, interurbain, catégorisation, etc.) |
| **check_data.py**, **count_lines.py**, **examine_format.py** | Scripts d’inspection des données et du format |

---

## 3. Backend (app.py)

### 3.1 Configuration et chargement

- **SKIP_MODEL** : variable d’environnement pour ne pas charger SentenceTransformer (ex. en dev).
- **Modèle** : `all-MiniLM-L6-v2` pour les embeddings.
- **Fichiers** : `data/embeddings.npy`, `data/metadata.json` ; si absent, fallback TF-IDF ou recherche par mots.
- **Sources** :
  - Lignes/arrêts urbains : `DEM_DIKK_LINES_URL` = https://demdikk.sn/reseau-urbain-dakar/
  - Interurbain : `INTERURBAIN_URL` = https://demdikk.sn/reseau-interurbain/

### 3.2 Correction des requêtes

- **normalize_query_typos** : correction de fautes courantes (ex. srvice → service, reservaton → réservation) via `QUERY_TYPO_MAP` et distance de Levenshtein sur `QUERY_KNOWN_WORDS`.

### 3.3 Nettoyage et extraction de texte

- **clean_and_deduplicate_text** : suppression HTML, déduplication de paragraphes/phrases, préservation des sections pour la page interurbain (## TOUBA, ## FATICK…).
- **extract_complete_content** : contenu complet + snippet propre (frontières de phrases, pas de coupure au milieu).
- **sanitize_display_text**, **trim_to_sentence_start**, **ensure_sentence_boundaries**, **create_intelligent_snippet** : nettoyage et troncature intelligente pour l’affichage.

### 3.4 Lignes et arrêts (réseau urbain Dakar)

- **extract_complete_line_details** : extraction du bloc d’une ligne (départ ↔ arrivée, liste d’arrêts) à partir du texte de la page réseau urbain ; gestion des variantes (LIGNE 7, 16A, TAF TAF, TAF TAF OUAKAM, TERMINUS RUFISQUE).
- **extract_stops_from_line_content** / **extract_stops_for_line** / **extract_stops_from_text** : extraction des listes d’arrêts à partir du contenu d’une ligne ou du texte global.
- **find_lines_for_stop** : pour une requête du type « quelle ligne pour X » ou « je veux aller à X », trouve les lignes qui desservent l’arrêt X.
- **extract_line_summary** : résumé de toutes les lignes (numéro, départ, arrivée).
- **improve_line_extraction_with_categories** : catégorisation des lignes (urbaines, banlieue, etc.).
- **OFFICIAL_LINE_KEYS** : liste des identifiants de lignes officielles (1, 2, 4, …, TAF TAF, TERMINUS RUFISQUE, etc.).
- **KNOWN_CITIES** / **CITY_VARIANTS** : villes du réseau interurbain avec variantes (accents, orthographes).

### 3.5 Réseau interurbain (villes)

- **extract_city_from_query** : détection de la ville dans la question (ex. Touba, Fatick, Saint-Louis).
- **_get_interurbain_city_block** : isolation du bloc de texte correspondant à une ville sur la page interurbain.
- **extract_structured_info** / **clean_structured_data** : extraction prix, horaires, contacts, départ depuis le texte.
- **extract_city_contact**, **format_structured_to_text**, **create_city_snippet** : mise en forme pour l’affichage.
- **build_structured_info_from_interurbain_list** : construction de la réponse structurée à partir de **interurbain_data.py** (prioritaire quand la ville est dans la liste).

### 3.6 Recherche (retrieve)

- **retrieve(query, k)** : recherche des documents les plus pertinents :
  - Si modèle + embeddings : similarité cosinus (query vs embeddings).
  - Sinon TF-IDF ou score par mots.
  - Agrégation par URL, prise de plusieurs segments par URL (tous pour la page interurbain, sinon `SEGMENTS_PER_URL`).
  - Boosting : ville dans le texte, requête exacte, termes dans titre/URL.
- **build_tfidf_index** / **tfidf_score** : index TF-IDF de secours.
- **is_result_relevant** / **looks_like_gibberish** / **analyze_query_clarity** : filtrage des requêtes et de la pertinence.

### 3.7 Détection du type de requête

- **detect_query_type** : renvoie notamment :
  - `all_lines_summary` : « ligne » / « lignes » seul
  - `line_X` : ligne spécifique (ex. ligne 7, ligne 16A, taf taf, terminus rufisque)
  - `lines_to_stop` : question du type « quelle ligne pour X » / « aller à X »
  - `lines_general` : question générale sur les lignes
  - `other` : autre (général, contact, réservation, etc.)

### 3.8 Endpoints Flask

| Route | Méthode | Description |
|-------|---------|-------------|
| **/ask** | POST, OPTIONS | Question utilisateur → réponse structurée (answer, summary, bullets, sources, results, query_type, is_city_query, is_line_query, lines_summary, etc.) |
| **/full_page/<path:url_encoded>** | GET | Récupération du contenu complet d’une page (pour affichage détaillé) |
| **/health** | GET | Santé + nombre de documents indexés |
| **/cities** | GET | Liste des villes connues (réseau interurbain) |

### 3.9 Comportement de /ask (résumé)

1. **Validation** : question absente, trop courte ou uniquement mots vides → message invitant à préciser.
2. **Typo** : correction via `normalize_query_typos`.
3. **Type** : `detect_query_type` + détection ville (`extract_city_from_query`).
4. **Gibberish** : rejet avec message générique.
5. **Clarification** : si besoin de précision (ex. question trop vague) → `needs_clarification` + `clarification_prompt`.
6. **Réponses dédiées** :
   - **Réservation** : texte fixe (app mobile, 33 824 10 10, guichets).
   - **Assane Mbengue** : courte biographie (DG Dakar Dem Dikk).
7. **Recherche** : `retrieve(q)` sur l’index.
8. **Requête ville (interurbain)** :
   - Si ville dans **interurbain_data** : réponse construite depuis `get_section_by_ville` + `build_structured_info_from_interurbain_list` + `format_structured_to_text`.
   - Sinon : extraction depuis le texte scrapé (page interurbain).
9. **Requête « toutes les lignes »** (`all_lines_summary`) : combinaison des segments de la page réseau urbain, `extract_line_summary` + `improve_line_extraction_with_categories`, renvoi de `lines_summary` et `categorized_lines`.
10. **Requête « lignes vers un arrêt »** (`lines_to_stop`) : `find_lines_for_stop` sur le texte combiné ; si aucun résultat, fallback en requête générale.
11. **Requête « ligne X »** (`line_X`) : combinaison des segments de la page lignes, `extract_complete_line_details` (départ, arrivée, liste d’arrêts), avec fallbacks (extract_stops_for_line, etc.).
12. **Requête générale** : extraction ciblée (`extract_targeted_info`) ou contenu complet, snippet, sources.

Réponses JSON incluent : `answer`, `summary`, `bullets`, `sources`, `results`, `needs_clarification`, `query_type`, `has_structured_data`, `is_city_query`, `is_line_query`, et selon le cas `lines_summary`, `categorized_lines`, `line_numbers`, `stop_requested`, `total_lines`, etc.

---

## 4. Données interurbain (interurbain_data.py)

- **INTERURBAIN_SECTIONS** : liste de sections (une entrée par ville ou groupe : Fatick, Podor/Ndioum, Kédougou, Louga/Kébémer, Diourbel, Thiès, Ourossogui/Matam, Mbour, Tambacounda, Saint-Louis, Kaolack, Ziguinchor, Vélingara, Touba, Kolda, Tivaouane, Bignona, Sédhiou, Bakel, Kaffrine, Kidira, etc.).
- Chaque section contient : **titre**, **villes**, **prix** (chaîne ou dict pour plusieurs villes), **horaires**, **jours**, **depart**, **lieux_contact** (liste de {lieu, tel}).
- **get_section_by_ville(ville)** : retourne la section correspondante ou None.
- **get_prix_for_ville(ville)** : prix affiché pour la ville (gère Louga/Kébémer).
- **get_contact_for_ville(ville)** : liste des lieux/contacts pour la ville (filtrage pour sections partagées).

---

## 5. Scraping et indexation

- **scraper.py** : URLs fixes (info-voyageurs, services, presentation, contact, chatbot-2303) ; extraction du texte (balises `<p>` dans `main`/`article`, sinon fallback `get_text`) ; écriture en JSONL dans `data/scraped.jsonl`.
- **indexer.py** : lecture de `scraped.jsonl`, découpage en chunks (CHUNK_SIZE=800, CHUNK_OVERLAP=200), encodage avec SentenceTransformer, sauvegarde `data/embeddings.npy` et `data/metadata.json`.
- En production, les pages **reseau-urbain-dakar** et **reseau-interurbain** sont supposées présentes dans `metadata.json` (ajout manuel ou via un scraper étendu non présent dans le dépôt actuel).

---

## 6. Interface utilisateur

- **ui.html** : structure (header avec logo Dem Dikk, zone `#chat`, formulaire avec champ question et bouton Envoyer), liens vers `style.css` et `script.js`.
- **script.js** : envoi POST vers `/ask`, affichage des réponses (résumé, bullets, sources, cartes lignes, boutons « Voir détails » par ligne), gestion des formules de politesse et d’aide, `askForLineDetails(lineNumber)` et `showAllLines()` pour les actions sur les lignes.
- **style.css** : mise en forme de l’en-tête, du conteneur de chat et du formulaire (police Inter, responsive).

---

## 7. Tests et scripts utilitaires

- **test_ask.py**, **test_api.py** : tests de l’API `/ask`.
- **test_interurbain.py** : données et logique interurbain.
- **test_lines_to_stop.py**, **test_lines_stops.py** : lignes vers un arrêt et extraction d’arrêts.
- **test_extract.py**, **test_line_extraction.py**, **test_line7.py**, **test_ligne4_debug.py** : extraction de lignes et arrêts.
- **test_categorization*.py**, **test_categories.py**, **test_final_categorization.py** : catégorisation des lignes.
- **test_validation_complete.py**, **test_specific_lines.py**, **test_real_data.py**, **test_debug*.py** : validation et debug.
- **count_lines.py** : comptage des lignes dans les données.
- **check_data.py** : recherche de pages contenant « reseau » ou « ligne » dans les métadonnées.
- **examine_format.py** : inspection du format des données.
- **split_metadata.py** : re-découpage fin de `metadata.json` (paragraphes/phrases, max 120 mots).

---

## 8. Résumé des fonctionnalités livrées

- Chatbot en français pour Dakar Dem Dikk (lignes, arrêts, villes, réservation, contact).
- Recherche sémantique (embeddings) avec fallback TF-IDF et agrégation par URL.
- Réponses dédiées : réservation, Assane Mbengue, clarification.
- Requêtes par **ville** (interurbain) : prix, horaires, jours, départ, contacts (données de référence + extraction depuis le site).
- Requêtes **lignes** : liste de toutes les lignes, détail d’une ligne (arrêts), lignes desservant un arrêt.
- Gestion des variantes de lignes (TAF TAF, TAF TAF OUAKAM, TERMINUS RUFISQUE, 16A, etc.) et des variantes de villes (accents, orthographes).
- Correction des fautes de frappe courantes dans la requête.
- API REST : `/ask`, `/health`, `/cities`, `/full_page/<url>`.
- Interface web avec chat, boutons « Lignes » et « Détails » par ligne.

---

*Document généré pour le projet Dakar Dem Dikk Chatbot — récapitulatif de l’existant.*
