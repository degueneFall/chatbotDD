# -*- coding: utf-8 -*-
"""
Données interurbaines de secours si `sync_interurbain.py --write` n’a rien extrait
(site Elementor modifié, réseau, etc.). Même structure que le parseur officiel.

À jour : relancer `python sync_interurbain.py --write` quand le scrape refonctionne.
Les tarifs / horaires ci‑dessous sont indicatifs — renvoyer l’usager vers la page officielle.
"""
from __future__ import annotations

_TEL = "+221 33 824 10 10"
_AG = "Agence / information voyageurs Dakar Dem Dikk"

# Villes les plus demandées (slug = clé de détection dans app_backup._detect_city)
INTERURBAIN_FALLBACK_SECTIONS: list[dict] = [
    {
        "titre": "FATICK",
        "villes": ["fatick"],
        "prix": "Consulter la grille tarifaire à jour sur demdikk.sn/reseau-interurbain/",
        "horaires": ["Départs réguliers depuis Dakar — horaires affichés en gare et sur le site officiel."],
        "jours": ["Lundi au dimanche (sauf indication contraire en période de fête)."],
        "depart": "Dakar — Terminus Liberté 5 / Gare routière de Dieuppeul (selon destination).",
        "lieux_contact": [{"lieu": _AG, "tel": _TEL}],
    },
    {
        "titre": "THIÈS",
        "villes": ["thies", "thiès"],
        "prix": "Consulter la grille tarifaire sur demdikk.sn/reseau-interurbain/",
        "horaires": ["Fréquence élevée sur l’axe Dakar — Thiès — voir site pour les créneaux."],
        "jours": ["Lundi au dimanche."],
        "depart": "Dakar — Terminus Liberté 5 / Dieuppeul.",
        "lieux_contact": [{"lieu": _AG, "tel": _TEL}],
    },
    {
        "titre": "TOUBA",
        "villes": ["touba"],
        "prix": "Consulter la grille tarifaire sur demdikk.sn/reseau-interurbain/",
        "horaires": ["Lignes dédiées — horaires en gare et sur le site officiel."],
        "jours": ["Selon calendrier affiché (pic aux dates religieuses)."],
        "depart": "Dakar — voir point de départ indiqué sur votre billet / en agence.",
        "lieux_contact": [{"lieu": _AG, "tel": _TEL}],
    },
    {
        "titre": "SAINT-LOUIS",
        "villes": ["saint-louis", "saint louis"],
        "prix": "Consulter la grille tarifaire sur demdikk.sn/reseau-interurbain/",
        "horaires": ["Trajet nord — horaires sur le site ou en agence."],
        "jours": ["Lundi au dimanche."],
        "depart": "Dakar — Terminus Liberté 5 / Dieuppeul.",
        "lieux_contact": [{"lieu": _AG, "tel": _TEL}],
    },
    {
        "titre": "MBOUR",
        "villes": ["mbour"],
        "prix": "Consulter la grille tarifaire sur demdikk.sn/reseau-interurbain/",
        "horaires": ["Axe côtier — voir site pour départs."],
        "jours": ["Lundi au dimanche."],
        "depart": "Dakar — Terminus Liberté 5 / Dieuppeul.",
        "lieux_contact": [{"lieu": _AG, "tel": _TEL}],
    },
    {
        "titre": "KAOLACK",
        "villes": ["kaolack"],
        "prix": "Consulter la grille tarifaire sur demdikk.sn/reseau-interurbain/",
        "horaires": ["Départs journaliers — détail sur demdikk.sn."],
        "jours": ["Lundi au dimanche."],
        "depart": "Dakar — Terminus Liberté 5 / Dieuppeul.",
        "lieux_contact": [{"lieu": _AG, "tel": _TEL}],
    },
    {
        "titre": "ZIGUINCHOR",
        "villes": ["ziguinchor"],
        "prix": "Consulter la grille tarifaire sur demdikk.sn/reseau-interurbain/",
        "horaires": ["Longue distance — horaires et durée en agence ou sur le site."],
        "jours": ["Selon grille affichée."],
        "depart": "Dakar — voir billet / agence.",
        "lieux_contact": [{"lieu": _AG, "tel": _TEL}],
    },
]
