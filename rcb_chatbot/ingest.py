"""Refresh RCB pages and their Chroma vector index."""

import logging
import threading
import time

from .config import GEMINI_API_KEY, REFRESH_INTERVAL_SECONDS
from .knowledge_base import KnowledgeBase
from .site_crawler import RobotsPolicyUnavailable, crawl_rcb_site

LOGGER = logging.getLogger("rcb_chatbot.ingest")


def refresh_site_and_index():
    """Refresh allowed site pages, then index fresh or previously cached content."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is required before RCB pages can be embedded.")
    knowledge_base = KnowledgeBase()
    try:
        crawl_stats = crawl_rcb_site()
    except RobotsPolicyUnavailable as error:
        cached_chunks = knowledge_base.ensure_fresh()
        if not cached_chunks:
            raise RuntimeError(
                f"{error} No cached RCB website content is available to index."
            ) from error
        cached_pages = len({chunk["url"] for chunk in knowledge_base.chunks})
        LOGGER.warning(
            "%s Using the existing cached corpus (%s pages) to build the vector index; "
            "website content may be out of date.",
            error,
            cached_pages,
        )
        crawl_stats = {
            "crawl_status": "blocked_by_robots",
            "cached_pages": cached_pages,
        }
    vector_stats = knowledge_base.sync_vectors()
    return {**crawl_stats, **vector_stats}


def start_refresh_scheduler():
    """Run an initial refresh and repeat it daily in a background thread."""
    if not GEMINI_API_KEY:
        LOGGER.warning("Automatic site refresh is disabled until GEMINI_API_KEY is configured.")
        return None

    def refresh_loop():
        while True:
            try:
                stats = refresh_site_and_index()
                LOGGER.info("RCB site refresh complete: %s", stats)
            except RobotsPolicyUnavailable as error:
                LOGGER.warning("%s The previous knowledge index remains available.", error)
            except Exception as error:
                LOGGER.error(
                    "RCB refresh failed; the previous knowledge index remains available: %s",
                    error,
                )
            time.sleep(REFRESH_INTERVAL_SECONDS)

    worker = threading.Thread(target=refresh_loop, name="rcb-site-refresh", daemon=True)
    worker.start()
    return worker


def main():
    """Run one complete crawl and vector-index refresh from the command line."""
    logging.basicConfig(level=logging.INFO)
    try:
        stats = refresh_site_and_index()
    except Exception as error:
        raise SystemExit(f"RCB refresh failed: {error}") from None
    print(f"RCB refresh complete: {stats}")


if __name__ == "__main__":
    main()
