import json
import os
import re
import numpy as np
from sentence_transformers import SentenceTransformer

# Entrée : `data/scraped.jsonl` (produit par `scraper.py` → `data/ingest_wp.py`).
# Sortie : `data/embeddings.npy` + `data/metadata.json` (chunks ; écrase le metadata segmenté d'ingest_wp).
IN_FILE = os.path.join("data", "scraped.jsonl")
OUT_EMB = os.path.join("data", "embeddings.npy")
OUT_META = os.path.join("data", "metadata.json")

MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
CHUNK_SIZE = 1200   # augmenté pour garder les sections entières (ex: liste directeurs ~900 chars)
CHUNK_OVERLAP = 200

# Ligne de spam à exclure de l'index (liens hors-sujet injectés dans certaines pages)
_SPAM_LINE_RE = re.compile(
    r'toto|togel|slot\s*(gacor|online|88|4d)?|sontogel|hantutogel|judi\s*bola|'
    r'situs\s*toto|gampang\s*menang|crossover\.org|foundvinylrecords|shopjoli|'
    r'bubutoto|asupantoto|aishe-j\.org|juara288',
    re.IGNORECASE,
)

# Regex détectant un titre de section (ligne seule en majuscules ou mot-clé structurant connu)
_SECTION_HEADER_RE = re.compile(
    r'^(CRÉATION|ACTIONNARIAT|OBJET SOCIAL|EXPLOITATION|PROJETS?|'
    r'DIRECTEURS?\s+GÉNÉRAUX?|CONSEIL\s+D.ADMINISTRATION|'
    r'LIGNES?|HORAIRES?|TARIFS?|SERVICES?|CONTACT|PRÉSENTATION|VISION|MISSION|'
    r'AMBITION|NOS?\s+VALEURS?|BAGAGES?|RÉSERVATION|ABONNEMENT|COLIS|MESSAGERIE|'
    r'[A-ZÀÂÇÉÈÊËÎÏÔÙÛÜ][A-ZÀÂÇÉÈÊËÎÏÔÙÛÜ\s]{4,})$',
    re.IGNORECASE,
)


def _clean_spam(text: str) -> str:
    """Supprime les lignes contenant du spam (liens hors-sujet)."""
    lines = text.splitlines()
    clean = [ln for ln in lines if not re.search(_SPAM_LINE_RE, ln)]
    return "\n".join(clean)


def chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Chunking fixe par nombre de caractères (fallback)."""
    chunks = []
    start = 0
    L = len(text)
    while start < L:
        end = min(start + size, L)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == L:
            break
        start = max(0, end - overlap)
    return chunks


def chunk_text_smart(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """
    Chunking intelligent ligne par ligne.
    Regroupe les lignes par section logique (détectée via _SECTION_HEADER_RE).
    Si une section dépasse `size` caractères, elle est subdivisée.
    """
    lines = text.splitlines()

    sections = []          # liste de listes de lignes
    current_section = []

    for line in lines:
        stripped = line.strip()
        if re.match(_SECTION_HEADER_RE, stripped) and stripped:
            # Nouveau titre de section → sauvegarder la section en cours
            if current_section:
                sections.append(current_section)
            current_section = [stripped]
        else:
            current_section.append(line)

    if current_section:
        sections.append(current_section)

    chunks = []
    for sect_lines in sections:
        section_text = "\n".join(sect_lines).strip()
        if not section_text:
            continue
        if len(section_text) <= size:
            chunks.append(section_text)
        else:
            sub = chunk_text(section_text, size, overlap)
            chunks.extend(sub)

    return chunks if chunks else chunk_text(text, size, overlap)


def build_index():
    docs = []
    with open(IN_FILE, "r", encoding="utf-8") as fin:
        for line in fin:
            obj = json.loads(line)
            url = obj.get("url")
            title = obj.get("title", "")
            text = obj.get("text", "")
            # Nettoyer le spam avant chunking
            text = _clean_spam(text)
            chunks = chunk_text_smart(text)
            for c in chunks:
                docs.append({"url": url, "title": title, "text": c})

    print(f"Total chunks: {len(docs)}")

    if not docs:
        print(
            "\n Aucun document dans scraped.jsonl -- embeddings.npy / metadata.json ne sont pas ecrases."
        )
        print("   Lancez d'abord un scraper reussi : python scraper.py")
        return 1

    model = SentenceTransformer(MODEL_NAME)
    texts = [d["text"] for d in docs]
    embeddings = model.encode(texts, show_progress_bar=True, convert_to_numpy=True)

    np.save(OUT_EMB, embeddings)
    with open(OUT_META, "w", encoding="utf-8") as fout:
        json.dump(docs, fout, ensure_ascii=False, indent=2)

    print("Index built and saved to data/")
    return 0


if __name__ == "__main__":
    raise SystemExit(build_index() or 0)
