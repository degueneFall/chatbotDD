# Documentation du projet — Dakar Dem Dikk Chatbot

Document de **référence fonctionnelle** (architecture et flux), aligné sur la structure actuelle du dépôt.  
**Sans détail sensible** : pas de clés API, secrets ou procédures d’authentification précises — uniquement ce qui est public dans le code (noms de variables d’environnement, rôles des modules).

---

## 1. Vue d’ensemble

**Objectif** : assistant web pour les usagers **Dakar Dem Dikk** (lignes urbaines, arrêts, interurbain, thématiques type abonnement / contact / réservation, etc.).

**Stack** : Python **Flask**, interface **HTML / CSS / JavaScript**, index **vectoriel** (embeddings + fallback mots-clés), données structurées **lignes** et **interurbain**, enrichissement optionnel des réponses via un **LLM** configuré côté serveur (reformulation, pas de « connaissance » hors contexte indexé).

**Sources principales** :

- Pages **demdikk.sn** scrapées puis indexées (`data/scraped.jsonl` → `metadata.json` + `embeddings.npy`).
- **Lignes urbaines** : fichier Python de données (`lines_data.py`), aligné sur la page réseau urbain.
- **Interurbain** : `interurbain_data.py` (et éventuellement snapshot JSON / secours selon la configuration du dépôt).

### Ce que le chatbot fait concrètement pour l’usager

Le document ci-dessous reste une **doc technique** ; voici en revanche la **promesse fonctionnelle** telle qu’implémentée dans le code (résumé, non exhaustif mot à mot) :

- **Réseau urbain Dakar** : obtenir la **liste des lignes**, le **détail d’une ligne** (terminus, sens, **liste d’arrêts**), ou les **lignes qui passent par un arrêt / un lieu** (y compris certaines questions très courtes reconnues comme nom d’arrêt).
- **Réseau interurbain** : pour une **ville** reconnue, obtenir **prix, horaires, jours, point de départ, contacts** structurés à partir des données de référence (avec secours si la synchro site est incomplète).
- **Questions « générales »** : réponses construites à partir de **passages du site** préalablement indexés (recherche par similarité, avec secours par mots-clés si le modèle d’embeddings n’est pas disponible).
- **Sujets souvent demandés** (abonnement, Tek Dem, application, contact, remboursement, etc.) : le **wrapper** peut **compléter le contexte** avec des extraits ciblés d’une **page officielle type FAQ**, puis éventuellement **reformuler** la réponse avec un LLM **sans inventer** hors de ce contexte.
- **Hors sujet** (sport, météo, etc.) : réponse de **refus poli** plutôt qu’une réponse transport inventée.
- **Aucune info fiable trouvée** : message orientant vers les **canaux officiels** (téléphone, email, site), plutôt que de deviner.

Ce n’est **pas** une liste de toutes les formulations possibles ni un catalogue marketing : le détail des mots-clés et règles est dans le code (`app_backup.py`, `app.py`, `script.js`).

---

## 2. Architecture logicielle

Le projet suit un **découpage en deux couches** :

| Couche | Fichier | Rôle |
|--------|---------|------|
| **Cœur API** | `app_backup.py` | Application Flask « métier » : `/ask`, recherche dans l’index, lignes / arrêts / villes, réponses JSON structurées. Peut être chargée seule ou via le wrapper. |
| **Enrichissement** | `app.py` | Point d’entrée **recommandé** en production : importe le module ci-dessus, enveloppe `/ask` (reformulation, fallbacks ciblés, recherche sur contenu officiel type page FAQ), routes utilitaires (santé, rafraîchissement d’index, diagnostic). |

**Important** : lancer **`python app.py`** (et en production **`gunicorn app:app`**) pour bénéficier du comportement complet documenté dans le README. Un démarrage direct sur l’ancien module seul peut omettre l’enrichissement.

---

## 3. Structure des fichiers (principaux)

| Élément | Rôle |
|---------|------|
| `app.py` | Wrapper Flask : chargement dynamique de l’implémentation, LLM optionnel, fallbacks, routes d’administration technique (voir §6). |
| `app_backup.py` | Routes `/ask`, `/health`, `/cities`, `/full_page`, logique RAG + urbain + interurbain. |
| `lines_data.py` | Liste structurée des **lignes urbaines** (numéro, terminus, arrêts). |
| `interurbain_data.py` | Sections **interurbaines** (villes, prix, horaires, contacts, etc.). |
| `interurbain_fallback_sections.py` | Données de **secours** si la synchronisation / le parse interurbain échoue. |
| `sync_interurbain.py` | Synchronisation / extraction depuis le site vers données ou snapshot (selon options de ligne de commande). |
| `scraper.py` | Collecte de textes sur des URLs cibles → `data/scraped.jsonl`. |
| `indexer.py` | Construction de `data/metadata.json` et `data/embeddings.npy`. |
| `split_metadata.py` | Script optionnel de re-découpage des segments d’index (affiner la granularité). |
| `ui.html`, `script.js`, `style.css` | Interface utilisateur (chat, affichage des types de réponse, saisie, **dictée** si le navigateur supporte l’API Web Speech). |
| `static/` | Assets (ex. logo). |
| `data/` | Fichiers générés (scrapé, métadonnées, embeddings, éventuel snapshot interurbain). |
| `requirements.txt` | Dépendances Python. |
| `README.md` | Installation, pipeline scrape → index, **automatisation** (rafraîchissement). |
| `scripts/` | Scripts d’aide au déploiement / rafraîchissement (selon ce qui est versionné : shell, PowerShell, etc.). |

Les noms exacts sous `scripts/` peuvent évoluer ; se référer au dépôt et au README section automation.

---

## 4. Types de requêtes (`query_type`)

Le backend classe la question pour adapter la réponse et le front. Exemples courants gérés dans `app_backup.py` :

| `query_type` | Usage typique |
|----------------|----------------|
| `all_lines_summary` | Liste / réseau urbain global. |
| `lines_to_stop` | Lignes desservant un **arrêt** ou un lieu (formulation explicite ou inférence sur un nom court). |
| `line_details` | Détail d’une **ligne** (numéro, TAF TAF, etc.) : terminus, liste d’arrêts. |
| `city_info` | **Ville** interurbaine reconnue. |
| `general` | Réponse issue surtout de la **recherche dans l’index** (chunk du site). |
| `other` | Aucun résultat satisfaisant dans le cœur ; **réponse de secours** (ex. orienter vers les canaux officiels). |

Le **wrapper** (`app.py`) peut ensuite reformuler une réponse déjà construite (LLM), sans changer le `query_type` dans tous les cas. Le front (`script.js`) reconnaît aussi des variantes comme **`line_summary_only`** lorsqu’elles apparaissent dans les réponses.

---

## 5. Flux simplifié de `/ask`

1. Réception JSON (`question`, éventuellement `city` ou autres champs selon le client).
2. Normalisation légère des fautes / forme de la question (module cœur).
3. Filtrage **hors sujet** / requêtes manifestement non transport (règles communes cœur + wrapper).
4. Détection **ville interurbaine** → réponse structurée si correspondance.
5. Détection **réseau urbain** : toutes les lignes, **une ligne**, **lignes à un arrêt** (données `lines_data.py` + règles de correspondance).
6. Sinon **recherche** dans l’index (similarité ou secours mots-clés).
7. Le **wrapper** peut enrichir le contexte (extraits page officielle, interurbain « Afrique », etc.) puis appeler le **LLM** pour reformuler **uniquement** à partir du contexte fourni (comportement décrit dans le code, pas reproduit ici mot pour mot).

Les champs JSON habituels incluent : `answer`, `summary`, `sources`, `results`, `query_type`, drapeaux `is_city_query`, `is_line_query`, `has_structured_data`, et selon les cas `lines`, `lines_summary`, `stop_requested`, `line_details`, etc.

---

## 6. Routes HTTP (aperçu)

**Cœur (`app_backup.py`)**  

- `POST /ask` — question / réponse.  
- `GET /health` — état du service et de l’index.  
- `GET /cities` — villes interurbaines connues.  
- `GET /full_page/...` — contenu d’une page encodée dans l’URL (usage interne / debug).  
- `GET /` — racine (selon configuration).

**Wrapper (`app.py`)** — en complément  

- `GET /api/wrapper_ping` — diagnostic : vérifier que le wrapper est bien chargé en production.  
- `POST /refresh_index` — relance pipeline type synchronisation interurbaine + scrape + indexation + rechargement des embeddings **en mémoire** (protégé par une **authentification côté serveur** via variable d’environnement ; détail volontairement non documenté ici).  
- `POST /reload_embeddings` — rechargement disque → mémoire sans refaire tout le pipeline.

Pour la configuration d’exploitation (noms des variables, en-têtes), se reporter au **code source** et au **README** sur une machine déjà configurée, sans les commiter dans la doc.

---

## 7. Interface utilisateur

- **Responsive** : balise viewport + media queries dans `style.css` (petits écrans, tablettes).
- **Micro** : dictée via **Web Speech API** si le navigateur l’expose ; sinon le bouton est désactivé (comportement prévu dans `script.js`).
- **API** : l’URL de base peut être forcée en JavaScript pour les environnements où la page statique et l’API ne partagent pas le même hôte/port (voir commentaires dans `ui.html`).

---

## 8. Mise à jour des données et exploitation

- Pipeline classique : **scraper** → **indexer** ; interurbain : script dédié selon README.
- **Production** : après déploiement (`git pull`), **redémarrer ou recharger** le processus WSGI (ex. Gunicorn) pour prendre en compte le code Python ; les fichiers statiques peuvent être mis en cache par le navigateur (rechargement forcé utile).
- **Automatisation** : décrite dans `README.md` (appels HTTP sécurisés ou scripts locaux selon l’environnement).

---

## 9. Évolution et maintenance de ce document

- Ce fichier décrit l’**architecture actuelle** (module cœur + wrapper).  
- En cas de refactor majeur, mettre à jour **ce document** et le **README** en parallèle.  
- Ne pas y coller de **secrets** ni d’extraits de configuration de production.

---

*Documentation projet — niveau architecture / exploitation, sans secrets.*
