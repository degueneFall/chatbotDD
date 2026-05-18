import json
import os
import numpy as np
from sentence_transformers import SentenceTransformer

# Entrée : `data/scraped.jsonl` (produit par `scraper.py` → `data/ingest_wp.py`).
# Sortie : `data/embeddings.npy` + `data/metadata.json` (chunks ; écrase le metadata segmenté d’ingest_wp).
IN_FILE = os.path.join("data", "scraped.jsonl")
OUT_EMB = os.path.join("data", "embeddings.npy")
OUT_META = os.path.join("data", "metadata.json")

MODEL_NAME = "all-MiniLM-L6-v2"
CHUNK_SIZE = 800
CHUNK_OVERLAP = 200


def chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
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


def build_index():
    docs = []
    with open(IN_FILE, "r", encoding="utf-8") as fin:
        for line in fin:
            obj = json.loads(line)
            url = obj.get("url")
            text = obj.get("text", "")
            chunks = chunk_text(text)
            for c in chunks:
                docs.append({"url": url, "text": c})

    print(f"Total chunks: {len(docs)}")

    if not docs:
        print(
            "\n❌ Aucun document dans scraped.jsonl — embeddings.npy / metadata.json ne sont pas écrasés."
        )
        print("   Lancez d’abord un scraper réussi : python scraper.py")
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
