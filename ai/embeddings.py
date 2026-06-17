"""
ai/embeddings.py  —  ChromaDB vector store + nomic-embed-text embeddings
=========================================================================
Provides a tenant-namespaced ChromaDB collection for RAG (Retrieval-
Augmented Generation). Each tenant's data is stored in a separate
collection: "tenant_{tenant_id}_sales", "tenant_{tenant_id}_purchases".

Pipeline
--------
  1. embed_dataframe()   — take a sales/purchase DataFrame → embed rows → store in ChromaDB
  2. query()             — semantic similarity search → return top-k matching rows as text
  3. The text results are injected as context into the Groq chat prompt (in rag.py)

Embedding model
---------------
  nomic-embed-text (via Ollama local inference) is the default.
  Falls back to a simple TF-IDF similarity if Ollama is not running.

Setup
-----
  Install: pip install chromadb
  Ollama:  curl -fsSL https://ollama.ai/install.sh | sh && ollama pull nomic-embed-text
  OR set:  EMBEDDING_PROVIDER=openai and OPENAI_API_KEY for OpenAI embeddings
"""

import os
import logging
import hashlib
import json
from typing import Optional, Any
import pandas as pd

logger = logging.getLogger(__name__)

CHROMA_DIR         = os.getenv("CHROMA_DIR",         "./chroma_db")
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "ollama")   # "ollama" | "openai" | "simple"
OLLAMA_BASE        = os.getenv("OLLAMA_BASE",         "http://localhost:11434")
OLLAMA_MODEL       = os.getenv("OLLAMA_MODEL",        "nomic-embed-text")
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY",      "")
TOP_K              = int(os.getenv("RAG_TOP_K",        "5"))

try:
    import chromadb
    from chromadb.config import Settings
    HAS_CHROMA = True
except ImportError:
    HAS_CHROMA = False
    logger.warning("[embeddings] chromadb not installed — RAG disabled. "
                   "Run: pip install chromadb")

_chroma_client: Optional[Any] = None


def _get_client():
    """Return (or initialise) the ChromaDB client."""
    global _chroma_client
    if _chroma_client is not None:
        return _chroma_client
    if not HAS_CHROMA:
        return None
    os.makedirs(CHROMA_DIR, exist_ok=True)
    _chroma_client = chromadb.PersistentClient(
        path=CHROMA_DIR,
        settings=Settings(anonymized_telemetry=False),
    )
    logger.info("[embeddings] ChromaDB client initialised at %s", CHROMA_DIR)
    return _chroma_client


def _collection_name(tenant_id: int, data_type: str) -> str:
    """Namespaced collection name per tenant."""
    return f"tenant_{tenant_id}_{data_type}"


# ═════════════════════════════════════════════════════════════════════════════
# Embedding functions
# ═════════════════════════════════════════════════════════════════════════════

def _embed_ollama(texts: list[str]) -> list[list[float]]:
    """Generate embeddings via Ollama (nomic-embed-text)."""
    import urllib.request
    embeddings = []
    for text in texts:
        try:
            payload = json.dumps({"model": OLLAMA_MODEL, "prompt": text}).encode()
            req     = urllib.request.Request(
                f"{OLLAMA_BASE}/api/embeddings",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                embeddings.append(result.get("embedding", []))
        except Exception as exc:
            logger.error("[embeddings] Ollama error: %s", exc)
            embeddings.append([])
    return embeddings


def _embed_openai(texts: list[str]) -> list[list[float]]:
    """Generate embeddings via OpenAI text-embedding-3-small."""
    if not OPENAI_API_KEY:
        return [[] for _ in texts]
    import urllib.request
    payload = json.dumps({"model": "text-embedding-3-small", "input": texts}).encode()
    req     = urllib.request.Request(
        "https://api.openai.com/v1/embeddings",
        data=payload,
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            return [d["embedding"] for d in result.get("data", [])]
    except Exception as exc:
        logger.error("[embeddings] OpenAI error: %s", exc)
        return [[] for _ in texts]


def _embed_simple(texts: list[str]) -> list[list[float]]:
    """
    Simple deterministic embedding fallback using character n-gram hashing.
    Produces 128-dimensional vectors. Useful for development / no-GPU setup.
    Not suitable for semantic search — words must overlap to match.
    """
    from collections import Counter
    import math

    def _hash_ngrams(text: str, n: int = 3, dim: int = 128) -> list[float]:
        vec = [0.0] * dim
        words = text.lower().split()
        ngrams = [text[i:i+n] for text in words for i in range(len(text)-n+1)]
        ngrams += words  # also include full words
        for ng in ngrams:
            idx = int(hashlib.md5(ng.encode()).hexdigest(), 16) % dim
            vec[idx] += 1.0
        norm = math.sqrt(sum(v*v for v in vec)) or 1.0
        return [v/norm for v in vec]

    return [_hash_ngrams(t) for t in texts]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts using the configured provider."""
    if not texts:
        return []
    if EMBEDDING_PROVIDER == "openai":
        return _embed_openai(texts)
    elif EMBEDDING_PROVIDER == "ollama":
        try:
            embs = _embed_ollama(texts)
            if all(e for e in embs):
                return embs
        except Exception:
            pass
        logger.warning("[embeddings] Ollama unavailable, falling back to simple embeddings.")
        return _embed_simple(texts)
    else:
        return _embed_simple(texts)


# ═════════════════════════════════════════════════════════════════════════════
# DataFrame → text → ChromaDB
# ═════════════════════════════════════════════════════════════════════════════

def _sales_row_to_text(row: pd.Series) -> str:
    """Convert a sales row to a searchable text string."""
    parts = []
    if "bill_date" in row and pd.notna(row["bill_date"]):
        parts.append(f"Date: {row['bill_date']}")
    if "branch" in row and pd.notna(row.get("branch")):
        parts.append(f"Branch: {row['branch']}")
    if "net_amount" in row:
        parts.append(f"Sales: ₹{float(row['net_amount'] or 0):,.0f}")
    if "margin_pct" in row and pd.notna(row.get("margin_pct")):
        parts.append(f"Margin: {float(row['margin_pct']):.1f}%")
    if "total_bills" in row and pd.notna(row.get("total_bills")):
        parts.append(f"Bills: {int(row['total_bills'])}")
    if "cash_sales" in row:
        parts.append(f"Cash: ₹{float(row.get('cash_sales') or 0):,.0f}")
    if "credit_sales" in row:
        parts.append(f"Credit: ₹{float(row.get('credit_sales') or 0):,.0f}")
    return " | ".join(parts)


def _purchase_row_to_text(row: pd.Series) -> str:
    """Convert a purchase row to a searchable text string."""
    parts = []
    if "grn_date" in row and pd.notna(row.get("grn_date")):
        parts.append(f"Date: {row['grn_date']}")
    if "supplier_name" in row and pd.notna(row.get("supplier_name")):
        parts.append(f"Supplier: {row['supplier_name']}")
    if "net_amount" in row:
        parts.append(f"Amount: ₹{float(row.get('net_amount') or 0):,.0f}")
    if "total_gst" in row and pd.notna(row.get("total_gst")):
        parts.append(f"GST: ₹{float(row['total_gst']):,.0f}")
    if "branch" in row and pd.notna(row.get("branch")):
        parts.append(f"Branch: {row['branch']}")
    return " | ".join(parts)


def embed_dataframe(
    df:         pd.DataFrame,
    tenant_id:  int,
    data_type:  str = "sales",   # "sales" | "purchases"
    batch_size: int = 50,
) -> bool:
    """
    Embed a DataFrame and store in ChromaDB.
    Existing collection data is replaced (upserted by row hash).
    Returns True on success.
    """
    client = _get_client()
    if not client or df.empty:
        return False

    collection_name = _collection_name(tenant_id, data_type)
    try:
        collection = client.get_or_create_collection(
            name=collection_name,
            metadata={"tenant_id": str(tenant_id), "data_type": data_type},
        )
    except Exception as exc:
        logger.error("[embeddings] get_or_create_collection: %s", exc)
        return False

    # Convert rows to text
    row_to_text = _sales_row_to_text if data_type == "sales" else _purchase_row_to_text
    texts = [row_to_text(row) for _, row in df.iterrows()]
    ids   = [
        hashlib.md5(f"{tenant_id}_{data_type}_{i}_{t}".encode()).hexdigest()
        for i, t in enumerate(texts)
    ]
    metadatas = [{"tenant_id": str(tenant_id), "row": i} for i in range(len(texts))]

    # Process in batches
    total = 0
    for start in range(0, len(texts), batch_size):
        batch_texts     = texts[start:start+batch_size]
        batch_ids       = ids[start:start+batch_size]
        batch_meta      = metadatas[start:start+batch_size]
        batch_embeddings= embed_texts(batch_texts)

        # Filter out empty embeddings
        valid = [(t, i, m, e) for t, i, m, e in
                 zip(batch_texts, batch_ids, batch_meta, batch_embeddings) if e]
        if not valid:
            continue
        t_, i_, m_, e_ = zip(*valid)

        try:
            collection.upsert(
                ids=list(i_),
                embeddings=list(e_),
                documents=list(t_),
                metadatas=list(m_),
            )
            total += len(valid)
        except Exception as exc:
            logger.error("[embeddings] upsert batch error: %s", exc)

    logger.info("[embeddings] Embedded %d/%d rows for tenant=%s type=%s",
                total, len(texts), tenant_id, data_type)
    return total > 0


def query(
    query_text: str,
    tenant_id:  int,
    data_type:  str = "sales",
    top_k:      int = TOP_K,
) -> list[str]:
    """
    Semantic similarity search.
    Returns a list of matching row text strings (top_k results).
    """
    client = _get_client()
    if not client:
        return []

    collection_name = _collection_name(tenant_id, data_type)
    try:
        collection = client.get_or_create_collection(name=collection_name)
    except Exception as exc:
        logger.error("[embeddings] query get_collection: %s", exc)
        return []

    # Check collection has data
    try:
        count = collection.count()
        if count == 0:
            return []
    except Exception:
        return []

    # Embed the query
    query_embeddings = embed_texts([query_text])
    if not query_embeddings or not query_embeddings[0]:
        return []

    try:
        results = collection.query(
            query_embeddings=query_embeddings,
            n_results=min(top_k, count),
            include=["documents", "distances"],
        )
        docs = results.get("documents", [[]])[0]
        return docs
    except Exception as exc:
        logger.error("[embeddings] query error: %s", exc)
        return []


def query_multi(
    query_text: str,
    tenant_id:  int,
    top_k:      int = TOP_K,
) -> list[str]:
    """Search both sales and purchases, return combined results."""
    sales_results    = query(query_text, tenant_id, "sales",     top_k)
    purchase_results = query(query_text, tenant_id, "purchases", top_k)
    # Interleave results
    combined = []
    for s, p in zip(sales_results, purchase_results):
        combined.extend([s, p])
    combined += sales_results[len(purchase_results):]
    combined += purchase_results[len(sales_results):]
    return combined[:top_k]


def delete_tenant_data(tenant_id: int) -> None:
    """Remove all ChromaDB collections for a tenant (GDPR data deletion)."""
    client = _get_client()
    if not client:
        return
    for data_type in ("sales", "purchases", "inventory"):
        name = _collection_name(tenant_id, data_type)
        try:
            client.delete_collection(name)
            logger.info("[embeddings] Deleted collection: %s", name)
        except Exception:
            pass


def get_embedding_stats(tenant_id: int) -> dict:
    """Return embedding statistics for the admin UI."""
    client = _get_client()
    if not client:
        return {"status": "ChromaDB not available"}
    stats = {"provider": EMBEDDING_PROVIDER, "collections": {}}
    for data_type in ("sales", "purchases"):
        name = _collection_name(tenant_id, data_type)
        try:
            col   = client.get_or_create_collection(name)
            count = col.count()
            stats["collections"][data_type] = count
        except Exception:
            stats["collections"][data_type] = 0
    stats["total"] = sum(stats["collections"].values())
    return stats
