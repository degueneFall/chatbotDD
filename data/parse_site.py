import json
import re
from typing import Dict, List
import requests
from bs4 import BeautifulSoup

def parse_demdikk_site() -> List[Dict]:
    """
    Parse le site demdikk.sn pour extraire les informations structurées
    """
    url = "https://demdikk.sn/reseau-interurbain/"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except Exception as e:
        print(f"Erreur lors du chargement du site: {e}")
        return []
    
    soup = BeautifulSoup(response.content, 'html.parser')
    
    # Chercher le contenu principal
    content = soup.find('div', class_='entry-content') or soup.find('div', class_='content')
    
    if not content:
        print("Contenu principal non trouvé")
        return []
    
    text = content.get_text(separator='\n', strip=True)
    
    # Analyser la structure des données
    documents = parse_city_data(text)
    
    return documents

def parse_city_data(text: str) -> List[Dict]:
    """
    Parse le texte pour extraire les informations par ville
    """
    # Séparer par ville (mots en majuscules suivis d'un retour à la ligne)
    city_pattern = re.compile(r'\n([A-ZÀ-É][A-ZÀ-É\s\-]+)\n')
    
    # Diviser le texte par ville
    parts = city_pattern.split(text)
    
    documents = []
    
    for i in range(1, len(parts), 2):
        city_name = parts[i].strip()
        city_content = parts[i+1] if i+1 < len(parts) else ""
        
        # Nettoyer le nom de la ville
        city_name_clean = clean_city_name(city_name)
        
        if not city_name_clean:
            continue
        
        # Extraire les informations structurées
        city_info = extract_city_info(city_name_clean, city_content)
        
        # Créer un document pour cette ville
        doc = {
            "url": "https://demdikk.sn/reseau-interurbain/",
            "title": f"Horaires et prix pour {city_name_clean}",
            "text": city_info["full_text"],
            "structured_info": city_info
        }
        
        documents.append(doc)
    
    return documents

def clean_city_name(city_name: str) -> str:
    """
    Nettoie le nom de la ville
    """
    # Supprimer "et" et les mots qui suivent (ex: "LOUGA et KEBEMER" -> "LOUGA")
    city_name = re.sub(r'\s+et\s+.*', '', city_name, flags=re.IGNORECASE)
    
    # Supprimer les caractères spéciaux et normaliser
    city_name = re.sub(r'[^A-ZÀ-É\s\-]', '', city_name)
    city_name = city_name.strip()
    
    # Convertir en minuscule pour la recherche
    return city_name.lower()

def extract_city_info(city_name: str, content: str) -> Dict:
    """
    Extrait les informations structurées pour une ville
    """
    lines = content.split('\n')
    
    info = {
        "city": city_name,
        "price": "",
        "schedule": [],
        "departure": [],
        "arrival": [],
        "contact": [],
        "full_text": content[:1000]  # Texte complet limité
    }
    
    current_section = None
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        line_lower = line.lower()
        
        # Détecter les sections
        if 'prix' in line_lower and ('fcfa' in line_lower or re.search(r'\d+\s*FCFA', line)):
            info["price"] = extract_price(line)
            current_section = "price"
        
        elif any(word in line_lower for word in ['horaire', 'heure', 'départ', 'arrivée']):
            schedule_info = extract_schedule(line)
            if schedule_info:
                info["schedule"].append(schedule_info)
            current_section = "schedule"
        
        elif 'dakar' in line_lower:
            departure_info = extract_departure(line)
            if departure_info:
                info["departure"].append(departure_info)
        
        elif city_name.lower() in line_lower:
            arrival_info = extract_arrival(line, city_name)
            if arrival_info:
                info["arrival"].append(arrival_info)
        
        elif re.search(r'\d{2}\s*\d{3}\s*\d{2}\s*\d{2}', line):
            phone = extract_phone(line)
            if phone:
                info["contact"].append(phone)
    
    return info

def extract_price(text: str) -> str:
    """Extrait le prix du texte"""
    match = re.search(r'(\d[\d\s,]*)\s*FCFA', text, re.IGNORECASE)
    return match.group(0) if match else ""

def extract_schedule(text: str) -> str:
    """Extrait les informations d'horaire"""
    # Extraire les heures
    hours = re.findall(r'\b\d{1,2}h(?:\s*\d{0,2})?\b', text)
    
    # Extraire les jours
    days = []
    if 'tous les jours' in text.lower():
        days.append('Tous les jours')
    if 'sauf' in text.lower():
        exception = re.search(r'sauf\s+(\w+)', text.lower())
        if exception:
            days.append(f'Sauf {exception.group(1)}')
    
    schedule_parts = []
    if hours:
        schedule_parts.append(f"Heures: {', '.join(sorted(set(hours)))}")
    if days:
        schedule_parts.append(f"Jours: {', '.join(days)}")
    
    return ' / '.join(schedule_parts) if schedule_parts else ""

def extract_departure(text: str) -> str:
    """Extrait le lieu de départ"""
    if 'terminus' in text.lower() and 'liberté' in text.lower():
        return "Dakar Terminus Liberté 5"
    elif 'dakar' in text.lower():
        return "Dakar"
    return ""

def extract_arrival(text: str, city_name: str) -> str:
    """Extrait le lieu d'arrivée"""
    # Chercher une description après le nom de la ville
    pattern = rf'{city_name}[^,\.]*'
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        arrival = match.group().strip()
        # Nettoyer
        arrival = re.sub(r'\bprix.*', '', arrival, flags=re.IGNORECASE)
        arrival = re.sub(r'\d+\s*FCFA.*', '', arrival)
        arrival = re.sub(r'\s+', ' ', arrival).strip()
        return arrival
    return ""

def extract_phone(text: str) -> str:
    """Extrait un numéro de téléphone"""
    match = re.search(r'(?:\+221\s*)?\d{2}\s*\d{3}\s*\d{2}\s*\d{2}', text)
    return match.group() if match else ""

# Exécuter le parsing
if __name__ == "__main__":
    print("Parsing du site demdikk.sn...")
    documents = parse_demdikk_site()
    
    print(f"✓ {len(documents)} villes extraites")
    
    # Sauvegarder dans metadata.json
    with open("data/metadata.json", "w", encoding="utf-8") as f:
        json.dump(documents, f, ensure_ascii=False, indent=2)
    
    print("✓ Données sauvegardées dans data/metadata.json")
    
    # Afficher un exemple
    if documents:
        print("\nExemple pour Touba:")
        print(json.dumps(documents[0], indent=2, ensure_ascii=False))