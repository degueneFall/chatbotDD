# -*- coding: utf-8 -*-
"""
Données de référence du Réseau Interurbain Dakar Dem Dikk.
Source: demdikk.sn/reseau-interurbain/

Ce fichier peut être régénéré avec :
  python sync_interurbain.py --write
"""

# Liste des sections interurbaines avec prix, horaires, jours, départ, arrivée/contact
INTERURBAIN_SECTIONS = [
{
        'titre': 'FATICK',
        'villes': [
'fatick'
            ],
        'prix': '3000 FCFA',
        'horaires': [
'7h et 15h'
            ],
        'jours': [
'Tous les jours'
            ],
        'depart': 'Dakar Terminus Liberté 5',
        'lieux_contact': [
{
                'lieu': 'Fatick boulevard en face keur Macky Sall',
                'tel': '78 620 04 07'
            }
            ]
    },
{
        'titre': 'PODOR et NDIOUM',
        'villes': [
'podor',
'ndioum'
            ],
        'prix': '8000 FCFA',
        'horaires': [],
        'jours': [
'Tous les jours'
            ],
        'depart': 'Dakar Terminus Liberté 5 à 7h',
        'lieux_contact': [
{
                'lieu': 'Podor lao demba ancien eno pres du stade municipale alassane wade à 6h',
                'tel': '78 620 04 02'
            },
{
                'lieu': 'Ndioum Quartier Nianga, immeuble Harouna Dia Mélou',
                'tel': '78 620 04 03'
            }
            ]
    },
{
        'titre': 'KEDOUGOU',
        'villes': [
'kedougou'
            ],
        'prix': '12000 FCFA',
        'horaires': [],
        'jours': [
'Tous les jours'
            ],
        'depart': 'Dakar Terminus Liberté 5 à 07h',
        'lieux_contact': [
{
                'lieu': 'Kédougou Quartier Gomba Villa 172 à 07h',
                'tel': '77 385 32 61'
            }
            ]
    },
{
        'titre': 'LOUGA et KEBEMER',
        'villes': [
'louga',
'kebemer'
            ],
        'prix': {
            'Louga': '4000 FCFA',
            'Kébémer': '3000 FCFA'
        },
        'horaires': [
'7h et 15h'
            ],
        'jours': [
'Tous les jours'
            ],
        'depart': 'Dakar Terminus Liberté 5',
        'lieux_contact': [
{
                'lieu': 'Louga Quartier Santhiaba Sud Route menant à la Gouvernance et au palais de justice A côté l’Inspection Académique',
                'tel': '77 848 02 40'
            }
            ]
    },
{
        'titre': 'DIOURBEL',
        'villes': [
'diourbel'
            ],
        'prix': '3000 FCFA',
        'horaires': [
'7h et 15h'
            ],
        'jours': [
'Tous les jours'
            ],
        'depart': 'Dakar Terminus Liberté 5',
        'lieux_contact': [
{
                'lieu': 'Diourbel Quartier Cheikh Anta',
                'tel': '77 848 02 36'
            }
            ]
    },
{
        'titre': 'THIES',
        'villes': [
'thies'
            ],
        'prix': '2000 FCFA',
        'horaires': [
'Dakar vers Thiès 7h à 19h',
'Thiès vers Dakar 6h à 19h'
            ],
        'jours': [
'Tous les jours'
            ],
        'depart': 'Dakar Gare de Colobane',
        'lieux_contact': [
{
                'lieu': 'Thiès Grand Standing derrière station EDK OIL',
                'tel': None
            }
            ]
    },
{
        'titre': 'OUROSSOGUI / MATAM',
        'villes': [
'ourossogui',
'matam'
            ],
        'prix': '10 000 FCFA',
        'horaires': [
'Dakar 8h',
'Matam 6h'
            ],
        'jours': [
'Lundi Jeudi et Samedi'
            ],
        'depart': 'Dakar Terminus Liberté 5',
        'lieux_contact': [
{
                'lieu': 'Matam Quartier Moderne 1 derrière hotel OASIS',
                'tel': '78 620 04 04'
            }
            ]
    },
{
        'titre': 'MBOUR',
        'villes': [
'mbour'
            ],
        'prix': '2500 FCFA',
        'horaires': [
'Dakar tous les jours 8h et 16h',
'Mbour vers Dakar 7h à 16h'
            ],
        'jours': [
'Dakar tous les jours 8h et 16h'
            ],
        'depart': 'Dakar Terminus Liberté 5',
        'lieux_contact': [
{
                'lieu': 'Mbour Route nationale en face terrain Akhmadou Barro',
                'tel': '78 620 04 12'
            }
            ]
    },
{
        'titre': 'TAMBACOUNDA',
        'villes': [
'tambacounda'
            ],
        'prix': '9000 FCFA',
        'horaires': [
'Dakar Tous les jours 7h',
'Tambacounda Tous les jours sauf Jeudi 8h30'
            ],
        'jours': [
'Dakar Tous les jours 7h'
            ],
        'depart': 'Dakar Terminus Liberté 5',
        'lieux_contact': [
{
                'lieu': 'Tambacounda Médina Coura en face église Jean XXIII',
                'tel': '78 620 04 11'
            }
            ]
    },
{
        'titre': 'SAINT-LOUIS',
        'villes': [
'saint-louis'
            ],
        'prix': '5 000 FCFA',
        'horaires': [
            'Départ Dakar (Terminus Liberté 5) : 07h00 et 14h00',
            'Départ Saint-Louis (Pikine Guinaw rail, station OIL LYBIA) : 07h00 et 14h00',
        ],
        'jours': [
'Tous les jours'
            ],
        'depart': 'Terminus Liberté 5, Dakar',
        'lieux_contact': [
{
                'lieu': 'Pikine Guinaw rail, derrière la station OIL LYBIA',
                'tel': '78 620 04 01'
            }
            ]
    },
{
        'titre': 'KAOLACK',
        'villes': [
'kaolack'
            ],
        'prix': '4000 FCFA',
        'horaires': [],
        'jours': [
'Tous les jours'
            ],
        'depart': '7h et 15h Dakar Terminus Liberté 5',
        'lieux_contact': [
{
                'lieu': 'Kaolack Quartier Casa ville en face gneti gouy',
                'tel': '78 620 04 08'
            }
            ]
    },
{
        'titre': 'ZIGUINCHOR',
        'villes': [
'ziguinchor'
            ],
        'prix': '9000 FCFA',
        'horaires': [],
        'jours': [
'Tous les jours'
            ],
        'depart': 'Dakar 8h Terminus Liberté 5',
        'lieux_contact': [
{
                'lieu': 'Ziguinchor 7h Quartier Néma, Av. Emile Badiane, en face de la Direction Régionale du Développement Rural (DRDR) ex DERBAC',
                'tel': '78 620 04 09'
            }
            ]
    },
{
        'titre': 'VELINGARA',
        'villes': [
'velingara'
            ],
        'prix': '11 000 FCFA',
        'horaires': [],
        'jours': [
'Tous les jours'
            ],
        'depart': 'Dakar 8h Terminus Liberté 5',
        'lieux_contact': [
{
                'lieu': 'Vélingara 7h Quartier HLM en face Station Elton',
                'tel': '78 460 43 37'
            }
            ]
    },
{
        'titre': 'TOUBA',
        'villes': [
'touba'
            ],
        'prix': '4 000 FCFA',
        'itineraire': 'Dakar – Péage – Ngabou – Ndam – Touba',
        'horaires': [
            'Départ Dakar (Liberté 5) : 07h00 et 15h00',
            'Départ Touba (Rond-Point 28) : 07h00 et 15h00',
            'Arrivée estimée : 11h00 max (bus 07h), 18h00 max (bus 15h)',
        ],
        'jours': [
'Tous les jours sauf Mercredi'
            ],
        'depart': 'Liberté 5, Dakar',
        'arrivee': 'Rond-Point 28, face à la Pharmacie Serigne Fallou Mbacké, Touba',
        'lieux_contact': [
{
                'lieu': 'Touba 28 Pharmacie Sérigne Fallou Mbacké',
                'tel': '78 620 04 06'
            }
            ]
    },
{
        'titre': 'KOLDA',
        'villes': [
'kolda'
            ],
        'prix': '9 000 FCFA',
        'horaires': [],
        'jours': [
'Tous les jours'
            ],
        'depart': 'Dakar 8h Terminus Liberté 5',
        'lieux_contact': [
{
                'lieu': 'Kolda 7h Derrière le stade régional près du pharmacie Tamarassy',
                'tel': '78 620 04 10'
            }
            ]
    },
{
        'titre': 'TIVAOUANE',
        'villes': [
'tivaouane'
            ],
        'prix': '2500 FCFA',
        'horaires': [
'Tivaoune',
'7h  le matin',
'Tivaoune',
'7h  et 17H30',
'Dakar vendredi 10h'
            ],
        'jours': [
'Tous les jours',
'sauf',
'Vendredi (Deux Départs)'
            ],
        'depart': 'Dakar 17h aprés midi Terminus Liberté 5 Quartier El Malick SY 2, route de Ndiassane à côté du Lycée Ababacar SY/ 78 620 04 05',
        'lieux_contact': []
    },
{
        'titre': 'BIGNONA',
        'villes': [
'bignona'
            ],
        'prix': '8 000 FCFA',
        'horaires': [],
        'jours': [
'Tous les jours'
            ],
        'depart': 'Dakar 8h Terminus Liberté 5',
        'lieux_contact': [
{
                'lieu': 'Bignona 8h Près du camp sapeur sur la piste en allant vers le stade',
                'tel': '77 846 02 93'
            }
            ]
    },
{
        'titre': 'SEDHIOU',
        'villes': [
'sedhiou'
            ],
        'prix': '9 000 FCFA',
        'horaires': [],
        'jours': [
'Lundi, Mercredi et Samedi'
            ],
        'depart': 'Dakar 8h Terminus Liberté 5',
        'lieux_contact': [
{
                'lieu': 'Sédhiou 7h Quartier Santassou',
                'tel': '78 460 43 36'
            }
            ]
    },
{
        'titre': 'BAKEL',
        'villes': [
'bakel'
            ],
        'prix': '12 000 FCFA',
        'horaires': [],
        'jours': [
'Mardi , Vendredi , Dimanche'
            ],
        'depart': '7h HLM Grand Yoff',
        'lieux_contact': [
{
                'lieu': 'Bakel 7h HLM Bakel , coté CNCA',
                'tel': '78 184 58 88'
            }
            ]
    },
{
        'titre': 'KAFFRINE',
        'villes': [
'kaffrine'
            ],
        'prix': '5 000 FCFA',
        'horaires': [],
        'jours': [
'Lundi , Mecredi , Vendredi',
'Mardi , Jeudi , Samedi'
            ],
        'depart': 'Dakar 7h HLM Grand Yoff',
        'lieux_contact': [
{
                'lieu': 'Kaffrine 7 h Cité Millionnaire (NDJIGUI 2)',
                'tel': '77 589 36 28'
            }
            ]
    },
{
        'titre': 'KIDIRA',
        'villes': [
'kidira'
            ],
        'prix': '13 000 FCFA',
        'horaires': [
'07H'
            ],
        'jours': [
'Lundi, Mercredi et Samedi'
            ],
        'depart': 'Dakar Terminus Hlm Grand Yoff',
        'lieux_contact': [
{
                'lieu': 'KIDIRA Quartier Plateau, en face ancienne gare ferroviaire Centre d’appel',
                'tel': '338241010'
            }
            ]
    }
    ]


# Si le scrape n'a rien produit, données de secours (villes courantes)
if not INTERURBAIN_SECTIONS:
    try:
        from interurbain_fallback_sections import INTERURBAIN_FALLBACK_SECTIONS as _INT_FB
        INTERURBAIN_SECTIONS.extend(_INT_FB)
    except ImportError:
        pass


def get_section_by_ville(ville: str) -> dict | None:
    """Retourne la section interurbaine pour une ville (ex: 'fatick', 'kebemer', 'ndioum')."""
    ville_lower = ville.lower().strip()
    for section in INTERURBAIN_SECTIONS:
        if ville_lower in [v.lower() for v in section["villes"]]:
            return section
    return None


def get_prix_for_ville(ville: str) -> str | None:
    """Retourne le prix affiché pour une ville (gère Louga/Kébémer)."""
    section = get_section_by_ville(ville)
    if not section:
        return None
    p = section.get("prix")
    if isinstance(p, dict):
        for k, v in p.items():
            if k.lower() == ville.lower() or (ville.lower() == "kebemer" and "ébémer" in k):
                return v
        return next(iter(p.values()), None)
    return p


def get_contact_for_ville(ville: str) -> list[dict]:
    """Retourne les lieux/contacts pour une ville (liste de {lieu, tel})."""
    section = get_section_by_ville(ville)
    if not section:
        return []
    contacts = section.get("lieux_contact", [])
    ville_lower = ville.lower()
    # Pour les sections partagées (Louga/Kébémer, Podor/Ndioum), retourner uniquement l'entrée de la ville demandée
    if len(section.get("villes", [])) > 1:
        for lc in contacts:
            lieu = (lc.get("lieu") or "").lower()
            if ville_lower in lieu or (ville_lower == "kebemer" and "kébémer" in lieu):
                return [lc]
        return contacts[:1] if contacts else []
    return contacts
