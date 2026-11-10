# RCB Website Assistant

This project crawls public pages on `rcb.res.in`, extracts clean text and PDF content, indexes the chunks with semantic embeddings, and answers questions with source links.

## Pipeline

1. `rcb_chatbot/site_crawler.py` seeds discovery from the site's sitemap and follows same-site links, checking `robots.txt` before each page or PDF.
2. The crawler removes navigation and page chrome, extracts the main content, and reads text from linked PDFs.
3. `rcb_chatbot/knowledge_base.py` creates overlapping 750-word chunks with title, URL, section, and content-type metadata.
4. Gemini `gemini-embedding-001` creates semantic vectors. Chroma stores the vectors locally in `data/chroma`.
5. Retrieval combines vector similarity with BM25 keyword search, then sends the best passages to `gemini-3.5-flash-lite`.
6. The assistant gives a direct answer and cites its source pages. The UI keeps recent conversation turns for follow-up questions.

## Windows setup

Install Python and create a Gemini API key in Google AI Studio. Keep the key private; do not paste it into source files or commit it to Git.

In PowerShell, from this project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
notepad .env
.\.venv\Scripts\python.exe -m rcb_chatbot.ingest
.\.venv\Scripts\python.exe app.py
```

Add `GEMINI_API_KEY=your-api-key` to `.env` before running the ingestion command. The app also accepts the existing `RCB_API_Key` variable name.

Open `http://127.0.0.1:8000`. The Gemini API key is used for both embeddings and generated answers. The Gemini Developer API offers a free tier for supported models, with account and rate limits. Free-tier requests may be used by Google to improve its products; review Google's current terms before using non-public data.

## Automatic website updates

When the server starts with `GEMINI_API_KEY` (or the existing `RCB_API_Key`) set, a background worker discovers pages from the sitemap and site links, then refreshes the vector database. It repeats every 24 hours while the server process remains running. The crawler follows only internal RCB links and skips pages disallowed by a successfully fetched `robots.txt`. A 4xx response for `robots.txt` is treated as unavailable under RFC 9309; network and server errors stop the crawl. If a crawl returns no HTML pages, the previous corpus and vector index are kept. Check the refresh logs to confirm that a fresh crawl succeeded; the chatbot cannot answer from pages that were not discovered or fetched.

The crawler writes:

- `data/raw/pages.jsonl` — raw HTML for crawled pages.
- `data/raw/pdf_urls.json` — internal PDF URLs found on the site.
- `data/raw/pdfs/` — downloaded PDFs used for text extraction.
- `rcb_pages.jsonl` — cleaned page and PDF text with source metadata.
- `data/chroma/` — persistent vector database.

You can run one manual refresh with `python -m rcb_chatbot.ingest`. The crawl is capped by `RCB_CRAWL_MAX_PAGES` (default 1500) and waits between requests; tune `RCB_CRAWL_DELAY_SECONDS` to change the delay. Automatic refresh only runs while the application is online.

## Configuration

Environment variables:

- `GEMINI_API_KEY` — required; `GOOGLE_API_KEY` and `RCB_API_Key` are also accepted. Never store the key in source control.
- `GEMINI_CHAT_MODEL` — defaults to `gemini-3.5-flash-lite`.
- `GEMINI_EMBEDDING_MODEL` — defaults to `gemini-embedding-001`.
- `RCB_CRAWL_MAX_PAGES` — maximum pages per crawl, default `1500`.
- `RCB_CRAWL_DELAY_SECONDS` — delay between requests, default `0.5`.
- `RCB_REFRESH_INTERVAL_SECONDS` — automatic refresh interval, default `86400` seconds.
- `CHATBOT_HOST` and `CHATBOT_PORT` — web server binding, default `127.0.0.1:8000`.

The API is `POST /api/chat` with JSON `{ "question": "...", "history": [] }`. `GET /api/health` reports indexed pages, chunks, vector count, and whether the API key is configured.

## Code layout

- `app.py` — starts the web server.
- `rcb_chatbot/site_crawler.py` — robots-aware internal crawler, HTML cleaning, and PDF extraction.
- `rcb_chatbot/knowledge_base.py` — chunking, BM25, embeddings, Chroma storage, and hybrid retrieval.
- `rcb_chatbot/ingest.py` — initial and scheduled crawl/index refresh.
- `rcb_chatbot/chat_service.py` — grounded LLM answers, follow-ups, and inline source citations.
- `rcb_chatbot/job_listings.py` — current vacancy lookup and readable formatting.
- `rcb_chatbot/web_server.py` — chat API and static file routes.
- `components_ui/` — HTML, CSS, and JavaScript chat interface.

## Embedding on the RCB website

Deploy this service on a public HTTPS host with outbound access to `rcb.res.in` and the Gemini API. The widget preview is available at the service root. To add the floating launcher to the RCB site, include this script near the end of the website page (replace the host with the deployed chatbot URL):

```html
<script src="https://YOUR-CHATBOT-HOST.example/widget.js" defer></script>
```

The script adds a small, fixed launcher in the bottom-right corner. Opening it expands the iframe-backed chat panel; minimizing it returns to the launcher. On a local run, the preview is at `http://127.0.0.1:8000`. The site team will need to add the script tag to the RCB site's shared footer or approved tag manager after the chatbot is deployed to HTTPS.
