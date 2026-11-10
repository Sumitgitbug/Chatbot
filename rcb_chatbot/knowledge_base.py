"""Build the text chunks, keyword index, and semantic vector search for RCB."""

import hashlib
import json
import logging
import math
import re
import threading
import time
from collections import Counter

from .config import (
    CHUNK_OVERLAP,
    CHUNK_WORDS,
    DATA_FILE,
    EMBEDDING_MODEL,
    GEMINI_API_KEY,
    STOP_WORDS,
    TOP_K,
    VECTOR_DIR,
)

COLLECTION_NAME = "rcb_website"
EMBEDDING_BATCH_SIZE = 6
EMBEDDING_BATCH_PAUSE_SECONDS = 13
LOGGER = logging.getLogger("rcb_chatbot.knowledge_base")
_GEMINI_CLIENT = None
_GEMINI_CLIENT_LOCK = threading.Lock()


def tokenize(text):
    """Normalize text into keyword-search terms."""
    return [
        word
        for word in re.findall(r"\w+", text.lower())
        if word not in STOP_WORDS and (len(word) > 1 or word.isdigit())
    ]


def chunk_text(text):
    """Split text into overlapping passages and preserve nearby context."""
    words = text.split()
    step = max(CHUNK_WORDS - CHUNK_OVERLAP, 1)
    for start in range(0, max(len(words), 1), step):
        passage = " ".join(words[start:start + CHUNK_WORDS])
        if passage:
            yield passage
        if start + CHUNK_WORDS >= len(words):
            break


def _stable_chunk_id(title, url, section, text):
    """Create a stable vector ID from the source and its exact passage."""
    payload = "\0".join((title, url, section, text))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_chunks():
    """Load cleaned page records and attach source metadata to each chunk."""
    chunks = []
    seen_urls = set()
    if not DATA_FILE.exists():
        raise FileNotFoundError(f"Website data file not found: {DATA_FILE.name}")

    with DATA_FILE.open(encoding="utf-8") as source:
        for line in source:
            try:
                page = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue

            url = (page.get("url") or "").split("#")[0].rstrip("/")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            title = (page.get("title") or url).strip()
            kind = page.get("kind") or ("pdf" if url.lower().endswith(".pdf") else "html")
            blocks = page.get("blocks") or []
            sections = []
            for block in blocks:
                section = re.sub(r"\s+", " ", block.get("section") or title).strip()
                text = re.sub(r"\s+", " ", block.get("text") or "").strip()
                if not text:
                    continue
                if sections and sections[-1]["section"] == section:
                    sections[-1]["text"] += " " + text
                else:
                    sections.append({"section": section, "text": text})

            if not sections:
                text = re.sub(r"\s+", " ", page.get("text") or page.get("content") or "").strip()
                if text:
                    sections = [{"section": page.get("section") or title, "text": text}]

            for section_data in sections:
                section = section_data["section"]
                for passage in chunk_text(section_data["text"]):
                    chunk_id = _stable_chunk_id(title, url, section, passage)
                    chunks.append({
                        "id": chunk_id,
                        "title": title,
                        "url": url,
                        "section": section,
                        "kind": kind,
                        "text": passage,
                    })
    return chunks


class BM25:
    """Small keyword search index used alongside semantic vector search."""

    def __init__(self, documents, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.term_counts = [Counter(document) for document in documents]
        self.lengths = [len(document) for document in documents]
        self.average_length = sum(self.lengths) / max(len(self.lengths), 1)
        document_frequency = Counter()
        for term_count in self.term_counts:
            document_frequency.update(term_count.keys())
        total = len(documents)
        self.idf = {
            term: math.log(1 + (total - count + 0.5) / (count + 0.5))
            for term, count in document_frequency.items()
        }

    def score(self, query_terms):
        """Return one relevance score per chunk."""
        scores = []
        for term_count, length in zip(self.term_counts, self.lengths):
            score = 0.0
            for term in query_terms:
                if term not in term_count:
                    continue
                frequency = term_count[term]
                normalization = 1 - self.b + self.b * length / max(self.average_length, 1)
                score += self.idf.get(term, 0) * frequency * (self.k1 + 1) / (frequency + self.k1 * normalization)
            scores.append(score)
        return scores


class KnowledgeBase:
    """Keep clean website chunks and semantic vectors current."""

    def __init__(self):
        self.lock = threading.RLock()
        self.vector_lock = threading.RLock()
        self.signature = None
        self.chunks, self.index = [], None
        self._client = None
        self._collection = None
        self.ensure_fresh()

    def ensure_fresh(self):
        """Rebuild keyword search when the cleaned corpus file changes."""
        stat = DATA_FILE.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        with self.lock:
            if signature != self.signature:
                chunks = load_chunks()
                documents = [tokenize(chunk["title"] + " " + chunk["section"] + " " + chunk["text"]) for chunk in chunks]
                self.chunks = chunks
                self.index = BM25(documents)
                self.signature = signature
        return len(self.chunks)

    def _vector_collection(self):
        """Open the persistent Chroma collection only when vector search is used."""
        if self._collection is not None:
            return self._collection
        try:
            import chromadb
        except ImportError as error:
            raise RuntimeError("Install the project dependencies with: pip install -r requirements.txt") from error
        VECTOR_DIR.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(VECTOR_DIR))
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        return self._collection

    def vector_count(self):
        """Return the number of stored vector chunks, or zero before first ingest."""
        try:
            return self._vector_collection().count()
        except RuntimeError:
            return 0

    @staticmethod
    def _gemini_client():
        """Create a Google GenAI client from the environment without storing secrets."""
        global _GEMINI_CLIENT
        if _GEMINI_CLIENT is not None:
            return _GEMINI_CLIENT
        if not GEMINI_API_KEY:
            raise RuntimeError("Set GEMINI_API_KEY in .env before building the RCB knowledge index or asking AI questions.")
        with _GEMINI_CLIENT_LOCK:
            if _GEMINI_CLIENT is None:
                try:
                    from google import genai
                except ImportError as error:
                    raise RuntimeError("Install the project dependencies with: pip install -r requirements.txt") from error
                _GEMINI_CLIENT = genai.Client(api_key=GEMINI_API_KEY)
        return _GEMINI_CLIENT

    @classmethod
    def _embed_texts(cls, texts, task_type="RETRIEVAL_DOCUMENT"):
        """Create Gemini vectors for a batch of source passages or search queries."""
        from google.genai import types

        response = cls._gemini_client().models.embed_content(
            model=EMBEDDING_MODEL,
            contents=texts,
            config=types.EmbedContentConfig(task_type=task_type),
        )
        return [item.values for item in response.embeddings]

    def sync_vectors(self):
        """Embed new chunks, remove stale vectors, and keep the index persistent."""
        self.ensure_fresh()
        collection = self._vector_collection()
        chunks = list(self.chunks)
        chunk_by_id = {chunk["id"]: chunk for chunk in chunks}
        current_ids = set(chunk_by_id)

        with self.vector_lock:
            existing = collection.get(include=["metadatas"])
            existing_ids = set(existing.get("ids") or [])
            stale_ids = sorted(existing_ids - current_ids)
            if stale_ids:
                collection.delete(ids=stale_ids)

            pending_ids = sorted(current_ids - existing_ids)
            offsets = range(0, len(pending_ids), EMBEDDING_BATCH_SIZE)
            total_batches = (len(pending_ids) + EMBEDDING_BATCH_SIZE - 1) // EMBEDDING_BATCH_SIZE
            for batch_number, offset in enumerate(offsets, start=1):
                if batch_number > 1:
                    time.sleep(EMBEDDING_BATCH_PAUSE_SECONDS)
                ids = pending_ids[offset:offset + EMBEDDING_BATCH_SIZE]
                batch = [chunk_by_id[chunk_id] for chunk_id in ids]
                LOGGER.info("Embedding Gemini batch %s/%s (%s passages)", batch_number, total_batches, len(batch))
                embeddings = self._embed_texts([chunk["text"] for chunk in batch])
                collection.upsert(
                    ids=ids,
                    embeddings=embeddings,
                    documents=[chunk["text"] for chunk in batch],
                    metadatas=[{
                        "title": chunk["title"],
                        "url": chunk["url"],
                        "section": chunk["section"],
                        "kind": chunk["kind"],
                    } for chunk in batch],
                )
        return {"chunks": len(current_ids), "embedded": len(pending_ids), "removed": len(stale_ids)}

    def _keyword_results(self, question, limit):
        """Return keyword-ranked chunks for exact names, titles, and acronyms."""
        query_terms = tokenize(question)
        if not query_terms:
            return []
        with self.lock:
            scores = self.index.score(query_terms)
            order = sorted(range(len(self.chunks)), key=lambda index: scores[index], reverse=True)
            hits, page_counts = [], Counter()
            for index in order:
                if scores[index] <= 0 or len(hits) >= limit * 3:
                    break
                chunk = self.chunks[index]
                if page_counts[chunk["url"]] >= 2:
                    continue
                page_counts[chunk["url"]] += 1
                hits.append(chunk)
        return hits

    def _semantic_results(self, question, limit):
        """Return nearest vector chunks for semantically phrased questions."""
        if not GEMINI_API_KEY:
            raise RuntimeError("Set GEMINI_API_KEY to enable semantic search and generated answers.")
        collection = self._vector_collection()
        if collection.count() == 0:
            raise RuntimeError("The RCB vector index is empty. Run: python -m rcb_chatbot.ingest")
        query_vector = self._embed_texts([question], task_type="RETRIEVAL_QUERY")[0]
        results = collection.query(
            query_embeddings=[query_vector],
            n_results=min(max(limit * 3, limit), collection.count()),
            include=["documents", "metadatas", "distances"],
        )
        matches = []
        for index, chunk_id in enumerate(results["ids"][0]):
            metadata = results["metadatas"][0][index]
            matches.append({
                "id": chunk_id,
                "title": metadata["title"],
                "url": metadata["url"],
                "section": metadata.get("section", metadata["title"]),
                "kind": metadata.get("kind", "html"),
                "text": results["documents"][0][index],
                "distance": results["distances"][0][index],
            })
        return matches

    def retrieve(self, question, limit=TOP_K):
        """Fuse semantic and keyword rankings to retrieve grounded context."""
        self.ensure_fresh()
        keyword_hits = self._keyword_results(question, limit)
        semantic_hits = self._semantic_results(question, limit)
        by_id = {chunk["id"]: chunk for chunk in self.chunks}
        fused_scores = Counter()
        for ranking in (keyword_hits, semantic_hits):
            for rank, hit in enumerate(ranking, start=1):
                fused_scores[hit["id"]] += 1 / (60 + rank)

        output, page_counts = [], Counter()
        for chunk_id, _ in fused_scores.most_common():
            chunk = by_id.get(chunk_id)
            if not chunk:
                # A crawler may have updated Chroma while a request was in flight.
                chunk = next((hit for hit in semantic_hits if hit["id"] == chunk_id), None)
            if not chunk or page_counts[chunk["url"]] >= 2:
                continue
            output.append(chunk)
            page_counts[chunk["url"]] += 1
            if len(output) >= limit:
                break
        return output
