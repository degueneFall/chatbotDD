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

## Automation (scheduled refresh)

The app can rebuild **interurbain snapshot**, **scraped pages**, and **vector index** in one shot.

### 1. HTTP (recommended while `python app.py` is running)

1. Set a secret in `.env` (or the environment of the Flask process):

   `REFRESH_TOKEN=choose-a-long-random-string`

2. On a schedule (e.g. Windows Task Scheduler nightly), call:

   ```powershell
   $env:REFRESH_TOKEN = "choose-a-long-random-string"
   .\scripts\Invoke-RefreshIndex.ps1 -BaseUrl "http://127.0.0.1:5000"
   ```

   This hits `POST /refresh_index` with `Authorization: Bearer …`, runs `sync_interurbain.py`, `scraper.py`, `indexer.py`, then reloads embeddings in memory. Optional: set `SKIP_SYNC_INTERURBAIN=1` on the server to skip the interurban step.

   After a **local** scrape/index only, reload vectors in the running app without re-running the pipeline:

   `.\scripts\Invoke-RefreshIndex.ps1 -ReloadOnly`

### 2. Local scripts (server stopped or CI)

```powershell
.\scripts\run_pipeline_local.ps1
# Skip interurban sync only:
.\scripts\run_pipeline_local.ps1 -SkipInterurban
```

If Flask is already running and you only re-ran scrape/index on disk (without `/refresh_index`), call `POST /reload_embeddings` with the same bearer token, or restart the server. Interurban **Python** data (`interurbain_data.py`) is only refreshed in memory after a **process restart** unless you reload that module yourself.

