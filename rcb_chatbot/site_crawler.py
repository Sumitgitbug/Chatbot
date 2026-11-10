"""Crawl public RCB pages while obeying robots.txt and collect linked PDFs."""

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import deque
from html import unescape
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, unquote, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

from bs4 import BeautifulSoup
from pypdf import PdfReader

from .config import (
    CRAWL_DELAY_SECONDS,
    CRAWL_MAX_PAGES,
    CRAWL_TIMEOUT_SECONDS,
    CRAWL_USER_AGENT,
    DATA_FILE,
    PDF_DIR,
    RAW_DIR,
    SITE_HOSTS,
    SITE_ROOT,
)

RAW_HTML_FILE = RAW_DIR / "pages.jsonl"
PDF_URLS_FILE = RAW_DIR / "pdf_urls.json"


class RobotsPolicyUnavailable(RuntimeError):
    """Raised when robots.txt cannot be read and the crawl must stop."""


def canonical_url(url):
    """Normalize an internal HTTP(S) URL and discard fragments and trackers."""
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"}:
        return None
    host = (parts.hostname or "").lower()
    if host not in SITE_HOSTS:
        return None
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}]
    return urlunsplit(("https", host, path, urlencode(query, doseq=True), ""))


def _fetch(url):
    """Fetch a public RCB resource using a descriptive crawler user agent."""
    request = urllib.request.Request(url, headers={"User-Agent": CRAWL_USER_AGENT})
    with urllib.request.urlopen(request, timeout=CRAWL_TIMEOUT_SECONDS) as response:
        return response.read(), response.headers.get("Content-Type", "").lower()


def _robots_policy(site_root=SITE_ROOT):
    """Load robots.txt and follow RFC 9309 behavior for unavailable 4xx files."""
    robots_url = urljoin(site_root, "robots.txt")
    try:
        body, _ = _fetch(robots_url)
    except urllib.error.HTTPError as error:
        if 400 <= error.code < 500:
            # RFC 9309 treats 4xx robots.txt responses as unavailable; a
            # crawler may access the site. Network/server errors still fail closed.
            parser = RobotFileParser(robots_url)
            parser.parse(["User-agent: *", "Allow: /"])
            return parser
        raise RobotsPolicyUnavailable(
            f"Could not read RCB robots.txt (HTTP {error.code}); crawl stopped."
        ) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise RobotsPolicyUnavailable(
            f"Could not read RCB robots.txt ({error}); crawl stopped."
        ) from error
    parser = RobotFileParser(robots_url)
    parser.parse(body.decode("utf-8", errors="replace").splitlines())
    return parser


def _sitemap_page_urls(robots):
    """Discover same-site URLs from sitemap indexes and urlsets."""
    sitemap_queue = deque(robots.site_maps() or [urljoin(SITE_ROOT, "sitemap.xml")])
    seen_sitemaps = set()
    page_urls = []
    while sitemap_queue and len(seen_sitemaps) < 40:
        sitemap_url = sitemap_queue.popleft()
        if sitemap_url in seen_sitemaps or not robots.can_fetch(CRAWL_USER_AGENT, sitemap_url):
            continue
        seen_sitemaps.add(sitemap_url)
        try:
            body, _ = _fetch(sitemap_url)
            root = ElementTree.fromstring(body)
        except (urllib.error.URLError, TimeoutError, OSError, ElementTree.ParseError):
            continue

        root_name = root.tag.rsplit("}", 1)[-1].lower()
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1].lower() != "loc" or not element.text:
                continue
            location = element.text.strip()
            if root_name == "sitemapindex":
                if len(seen_sitemaps) + len(sitemap_queue) < 40:
                    sitemap_queue.append(location)
                continue
            canonical = canonical_url(location)
            if canonical and canonical not in page_urls:
                page_urls.append(canonical)
    return page_urls, len(seen_sitemaps)


def _main_text(html):
    """Remove navigation and page chrome, then retain headings and main content."""
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.find_all(("script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "form", "iframe")):
        element.decompose()

    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    main = soup.find("main") or soup.find(attrs={"role": "main"}) or soup.find("article") or soup.body or soup
    blocks = []
    section = title
    for element in main.find_all(("h1", "h2", "h3", "h4", "p", "li", "td", "th", "dt", "dd")):
        text = unescape(re.sub(r"\s+", " ", element.get_text(" ", strip=True))).strip()
        if not text:
            continue
        if element.name in {"h1", "h2", "h3", "h4"}:
            section = text
        blocks.append({"section": section, "text": text})

    if not blocks:
        text = unescape(re.sub(r"\s+", " ", main.get_text(" ", strip=True))).strip()
        if text:
            blocks.append({"section": title, "text": text})
    clean_text = "\n".join(block["text"] for block in blocks)
    return title, clean_text, blocks


def _pdf_document(url, body):
    """Extract text from a linked PDF while preserving its source URL."""
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    filename = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(unquote(urlsplit(url).path)).name) or "document.pdf"
    local_path = PDF_DIR / filename
    local_path.write_bytes(body)
    reader = PdfReader(str(local_path))
    pages = [page.extract_text() or "" for page in reader.pages]
    text = "\n".join(re.sub(r"\s+", " ", page).strip() for page in pages).strip()
    title = Path(filename).stem.replace("_", " ").strip() or "RCB PDF document"
    return {"title": title, "url": url, "kind": "pdf", "section": "PDF document", "text": text}


def _write_jsonl(path, records):
    """Atomically replace a JSONL file after its complete export is ready."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as output:
        for record in records:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def crawl_rcb_site():
    """Crawl allowed internal pages, save raw HTML, PDFs, and cleaned text."""
    robots = _robots_policy()
    robots_by_host = {urlsplit(SITE_ROOT).hostname: robots}

    def can_fetch(url):
        host = (urlsplit(url).hostname or "").lower()
        if host not in SITE_HOSTS:
            return False
        if host not in robots_by_host:
            try:
                robots_by_host[host] = _robots_policy(f"https://{host}/")
            except RobotsPolicyUnavailable:
                # Fail closed for a secondary host without interrupting the main RCB crawl.
                robots_by_host[host] = None
        policy = robots_by_host[host]
        return bool(policy and policy.can_fetch(CRAWL_USER_AGENT, url))

    start_url = canonical_url(SITE_ROOT)
    sitemap_urls, sitemap_count = _sitemap_page_urls(robots)
    # Seed overview pages that may be omitted from the main site sitemap.
    priority_urls = (
        "https://rcb.res.in/bsc-bionest-bio-incubator",
        "https://rcb.res.in/genomics",
        # Seed the current ATPC application pages directly. The old index.php
        # and facilityc3c9.php URLs no longer expose the current facility list.
        "https://atpc.rcb.res.in/app/",
        "https://atpc.rcb.res.in/app/facilities.php",
        "https://atpc.rcb.res.in/app/charges.php",
        "https://atpc.rcb.res.in/app/people.php",
        "https://atpc.rcb.res.in/app/sample_submission_forms.php",
    )
    queue = deque([start_url, *(canonical_url(url) for url in priority_urls), *sitemap_urls])
    queued = set(queue)
    visited = set()
    pdf_urls = set()
    raw_pages = []
    clean_documents = []

    while queue and len(visited) < CRAWL_MAX_PAGES:
        url = queue.popleft()
        if url in visited or not can_fetch(url):
            continue
        visited.add(url)
        try:
            body, content_type = _fetch(url)
        except (urllib.error.URLError, TimeoutError, OSError):
            continue

        if "application/pdf" in content_type or urlsplit(url).path.lower().endswith(".pdf"):
            pdf_urls.add(url)
            try:
                document = _pdf_document(url, body)
                if document["text"]:
                    clean_documents.append(document)
            except Exception:
                continue
            time.sleep(CRAWL_DELAY_SECONDS)
            continue

        if "html" not in content_type and b"<html" not in body[:1000].lower():
            continue
        html = body.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        title, text, blocks = _main_text(html)
        raw_pages.append({"url": url, "title": title, "html": html})
        if text:
            clean_documents.append({
                "url": url,
                "title": title or url,
                "kind": "html",
                "section": title,
                "text": text,
                "blocks": blocks,
            })

        for link in soup.find_all("a", href=True):
            target = canonical_url(urljoin(url, link["href"].strip()))
            if not target:
                continue
            if urlsplit(target).path.lower().endswith(".pdf"):
                if can_fetch(target):
                    pdf_urls.add(target)
                continue
            if target not in queued and can_fetch(target):
                queued.add(target)
                queue.append(target)
        time.sleep(CRAWL_DELAY_SECONDS)

    # Fetch PDF targets separately so their URLs remain available as a clean list.
    for url in sorted(pdf_urls):
        if not can_fetch(url):
            continue
        try:
            body, content_type = _fetch(url)
            if "application/pdf" not in content_type and not urlsplit(url).path.lower().endswith(".pdf"):
                continue
            document = _pdf_document(url, body)
            if document["text"]:
                clean_documents.append(document)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            continue
        time.sleep(CRAWL_DELAY_SECONDS)

    if not raw_pages:
        raise RuntimeError("RCB crawl returned no HTML pages; keeping the previous website index.")

    _write_jsonl(RAW_HTML_FILE, raw_pages)
    PDF_URLS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PDF_URLS_FILE.write_text(json.dumps(sorted(pdf_urls), ensure_ascii=False, indent=2), encoding="utf-8")
    _write_jsonl(DATA_FILE, clean_documents)
    return {
        "pages": len(raw_pages),
        "pdfs": len(pdf_urls),
        "documents": len(clean_documents),
        "sitemaps": sitemap_count,
    }
