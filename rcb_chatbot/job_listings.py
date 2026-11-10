"""Fetch current RCB vacancies, exclude expired rows, and format the reply."""

import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin

CURRENT_OPPORTUNITIES_BASE = "https://rcb.res.in/current-opportunities"
CURRENT_OPPORTUNITIES_URL = CURRENT_OPPORTUNITIES_BASE + "?page=1"
JOB_CACHE_SECONDS = 600
INDIA_TIME = timezone(timedelta(hours=5, minutes=30))

# Last verified official rows are a fallback for hosts without outbound access.
JOB_SNAPSHOT = [
    {
        "title": "Project Research Associate-I (Advertisement No. 41/Project/2026/HR)",
        "deadline": "01-11-2026 17:00",
        "detail": "https://rcb.res.in/files/advertisement-files/advt._no._41_project_2026_hr.pdf",
    },
    {
        "title": "Project-Junior Research Fellow (Advertisement No. 40/Project/2026/HR)",
        "deadline": "01-11-2026 17:00",
        "detail": "https://rcb.res.in/files/advertisement-files/advt._no._40_project_2026_hr.pdf",
    },
    {
        "title": "Project Technical Support-III (Non-Medical) (Advt. No. 39/Project/2026/HR)",
        "deadline": "20-10-2026 17:00",
        "detail": "https://rcb.res.in/files/advertisement-files/advt._no._39_project_2026_hr.pdf",
    },
    {
        "title": "Vacancy for Administrative Officer (Contractual), IBDC Phase-1 (Advt. No. RCB/IBDC/01/2026/Rectt./HR)",
        "deadline": "28-10-2026 23:59",
        "detail": "https://rcb.res.in/files/advertisement-files/advt._no._rcb_ibdc_01_2026_rectt._hr.pdf",
    },
]

_cache = {"checked_at": 0, "rows": None, "is_live": False}
_cache_lock = threading.Lock()


class JobTableParser(HTMLParser):
    """Collect text and links from table cells in the official opportunities page."""

    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = {"text": "", "links": []}
        elif tag == "a" and self.cell is not None and attributes.get("href"):
            self.cell["links"].append(attributes["href"])

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"] += data

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.cell["text"] = re.sub(r"\s+", " ", self.cell["text"]).strip()
            self.row.append(self.cell)
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None


def _parse_deadline(date_text):
    """Parse a published deadline and attach the site's India time zone."""
    date_match = re.search(r"(\d{2}-\d{2}-\d{4})(?:\s+(\d{2}:\d{2}(?::\d{2})?))?", date_text)
    if not date_match:
        return None
    date_part, time_part = date_match.groups()
    time_part = time_part or "23:59:59"
    if time_part.count(":") == 1:
        time_part += ":00"
    return datetime.strptime(date_part + " " + time_part, "%d-%m-%Y %H:%M:%S").replace(tzinfo=INDIA_TIME)


def _read_page(url):
    """Download one public RCB opportunities page."""
    request = urllib.request.Request(url, headers={"User-Agent": "RCB-Website-Assistant/1.0"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.read().decode("utf-8", errors="replace")


def _rows_from_page(url, html):
    """Convert an HTML table into active job entries with dates and links."""
    parser = JobTableParser()
    parser.feed(html)
    rows = []
    for cells in parser.rows:
        title_cell = next(
            (cell for cell in cells if re.search(r"(position|vacan|recruit|advertisement|advt\.)", cell["text"], re.I)),
            None,
        )
        if not title_cell:
            continue

        title = title_cell["text"]
        # These entries belong to admissions and training, not job vacancies.
        if re.search(r"\b(admission|ph\.d|phd|postgraduate diploma|research training|training program)\b", title, re.I):
            continue

        deadline = _parse_deadline(" ".join(cell["text"] for cell in cells))
        if not deadline or deadline < datetime.now(INDIA_TIME):
            continue

        detail_url = urljoin(url, (title_cell["links"] or [""])[0])
        apply_url = ""
        for cell in reversed(cells):
            for link in reversed(cell["links"]):
                resolved_link = urljoin(url, link)
                if "apply" in cell["text"].lower() or "forms.gle" in resolved_link or "jobs-nt" in resolved_link:
                    apply_url = resolved_link
                    break
            if apply_url:
                break

        rows.append({
            "title": title,
            "deadline": deadline.strftime("%d-%m-%Y %H:%M IST"),
            "detail": detail_url or CURRENT_OPPORTUNITIES_URL,
            "apply": apply_url,
        })
    return rows


def fetch_current_jobs():
    """Refresh the public jobs table every ten minutes and drop expired roles."""
    with _cache_lock:
        if _cache["rows"] is not None and time.time() - _cache["checked_at"] < JOB_CACHE_SECONDS:
            return _cache["rows"]

    rows, reached_site = [], False
    # Page 1 currently holds newer listings; older pages are checked for any still-open roles.
    for page in (1, 0, 2):
        page_url = CURRENT_OPPORTUNITIES_BASE if page == 0 else f"{CURRENT_OPPORTUNITIES_BASE}?page={page}"
        try:
            html = _read_page(page_url)
        except (urllib.error.URLError, TimeoutError, OSError):
            break
        reached_site = True
        rows.extend(_rows_from_page(page_url, html))

    # Use the verified snapshot only when the host cannot reach the public site.
    if not reached_site:
        for item in JOB_SNAPSHOT:
            deadline = _parse_deadline(item["deadline"])
            if deadline and deadline >= datetime.now(INDIA_TIME):
                rows.append({**item, "deadline": deadline.strftime("%d-%m-%Y %H:%M IST"), "apply": ""})

    unique_rows = []
    for row in rows:
        if not any(existing["title"] == row["title"] for existing in unique_rows):
            unique_rows.append(row)
    with _cache_lock:
        _cache.update({"checked_at": time.time(), "rows": unique_rows, "is_live": reached_site})
    return unique_rows


def current_jobs_answer():
    """Return a readable vacancy list with links embedded in each job title."""
    rows = fetch_current_jobs()
    if not rows:
        return {
            "answer": "I could not check the live RCB job list from this server. For the latest vacancies, visit the [RCB Current Opportunities page](" + CURRENT_OPPORTUNITIES_URL + ").",
            "sources": [],
        }

    bullets = []
    for row in rows:
        detail_url = row.get("detail") or CURRENT_OPPORTUNITIES_URL
        job_line = f"- [{row['title']}]({detail_url}) — Last date: {row['deadline']}"
        if row.get("apply"):
            job_line += f" ([Apply]({row['apply']}))"
        bullets.append(job_line)
    prefix = "" if _cache["is_live"] else "The live site could not be reached from this server. This list was last verified on 19 October 2026.\n\n"
    answer = prefix + "These job openings are listed on the RCB website:\n\n" + "\n\n".join(bullets)
    answer += "\n\nAdmissions and training programmes are excluded from this job list."

    return {"answer": answer, "sources": []}
