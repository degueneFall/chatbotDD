# Dakar Dem Dikk Chatbot (retrieval)

This project builds a simple retrieval-based chatbot whose responses come from the Dakar Dem Dikk website pages.

Quick steps

1. Create and activate a Python virtual environment.

Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Scrape the site pages:

```powershell
python scraper.py
```

Scraped content will be saved to `data/scraped.jsonl`.

3. Build the index (compute embeddings):

```powershell
python indexer.py
```

This saves `data/embeddings.npy` and `data/metadata.json`.

4. Run the API server:

```powershell
python app.py
```

5. Ask the bot (example using curl):

```powershell
curl -X POST http://localhost:5000/ask -H "Content-Type: application/json" -d '{"question":"Quels sont les services?"}'
```

Notes and next steps

- The current implementation returns concatenated passages from the site as the "answer". If you want a single, polished response, we can add a summarization/generation step using an LLM.
- We can also add caching, more robust HTML selectors, pagination handling, or connect this API to a webchat or Telegram bot.

