import requests
import json
import os
import re
import html

WP_BASE = "https://demdikk.sn"
ENDPOINTS = [
    f"{WP_BASE}/wp-json/wp/v2/pages?per_page=100",
    f"{WP_BASE}/wp-json/wp/v2/posts?per_page=100",
]
OUT_SCRAPED = os.path.join("data", "scraped.jsonl")
OUT_META = os.path.join("data", "metadata.json")

MAX_WORDS = 120

# Beaucoup de sites WordPress / CDN renvoient du HTML (403, challenge, page d’erreur)
# si la requête n’a pas d’en-têtes « navigateur » — d’où JSONDecodeError sur r.json().
_WP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Referer": f"{WP_BASE}/",
}


def strip_html(s):
    s = re.sub(r"<script[\s\S]*?</script>", "", s, flags=re.I)
    s = re.sub(r"<style[\s\S]*?</style>", "", s, flags=re.I)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def segment_text(text, max_words=MAX_WORDS):
    words = text.split()
    if not words:
        return []
    segments = []
    start = 0
    N = len(words)
    while start < N:
        end = min(start + max_words, N)
        seg = " ".join(words[start:end]).strip()
        segments.append(seg)
        start = end
    return segments


def fetch_all(url):
    try:
        r = requests.get(url, headers=_WP_HEADERS, timeout=35)
        r.raise_for_status()
        raw = (r.text or "").lstrip()
        ct = (r.headers.get("Content-Type") or "").lower()
        if "application/json" not in ct and not (raw.startswith("[") or raw.startswith("{")):
            preview = raw[:500].replace("\n", " ")
            print(
                f"Échec {url}: corps non-JSON (HTTP {r.status_code}, "
                f"Content-Type={r.headers.get('Content-Type')!r}), début: {preview!r}"
            )
            return []
        try:
            return r.json()
        except json.JSONDecodeError as je:
            preview = raw[:500].replace("\n", " ")
            print(f"Échec {url}: JSON invalide ({je}), début réponse: {preview!r}")
            return []
    except requests.RequestException as e:
        print(f"Échec {url}: {e}")
        return []


def main() -> int:
    os.makedirs("data", exist_ok=True)
    scraped = []
    total_pages = 0
    total_posts = 0

    for ep in ENDPOINTS:
        items = fetch_all(ep)
        if ep.endswith('/pages?per_page=100') or '/pages' in ep:
            total_pages = len(items)
        else:
            total_posts = len(items)
        for it in items:
            # prefer link field
            url = it.get('link') or (it.get('guid') and it.get('guid').get('rendered')) or ''
            title = (it.get('title') or {}).get('rendered') if isinstance(it.get('title'), dict) else it.get('title', '')
            content = (it.get('content') or {}).get('rendered') if isinstance(it.get('content'), dict) else it.get('content', '')
            text = strip_html(content or '')
            title = strip_html(title or '')
            scraped.append({"url": url, "title": title, "text": text})

    if not scraped:
        print(
            "\n❌ Aucune page/post récupéré — les fichiers scraped.jsonl et metadata.json "
            "ne sont PAS écrasés (conservation de l’index précédent si présent)."
        )
        print(
            "   Vérifiez : connexion Internet, pare-feu, VPN, ou ouvrez l’URL dans un navigateur :\n"
            f"   {ENDPOINTS[0]}"
        )
        return 1

    # write scraped.jsonl (overwrite)
    with open(OUT_SCRAPED, 'w', encoding='utf-8') as fout:
        for obj in scraped:
            fout.write(json.dumps(obj, ensure_ascii=False) + "\n")

    # segment and write metadata.json (backup existing if present)
    if os.path.exists(OUT_META):
        import shutil, time
        bak = OUT_META.replace('.json', f'_backup_{int(time.time())}.json')
        shutil.copy2(OUT_META, bak)
        print(f"Backed up existing {OUT_META} to {bak}")

    metadata = []
    for item in scraped:
        url = item.get('url', '')
        title = item.get('title', '')
        text = item.get('text', '')
        if not text:
            continue
        segs = segment_text(text)
        for s in segs:
            metadata.append({"url": url, "title": title, "text": s})

    with open(OUT_META, 'w', encoding='utf-8') as fout:
        json.dump(metadata, fout, ensure_ascii=False, indent=2)

    print(f"Fetched pages: {total_pages}, posts: {total_posts}")
    print(f"Wrote {len(scraped)} items to {OUT_SCRAPED}")
    print(f"Wrote {len(metadata)} segments to {OUT_META}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
