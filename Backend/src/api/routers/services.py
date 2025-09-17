"""
Shared service utilities for KnowledgeBot backend.

This module encapsulates:
- Supabase storage helpers
- Text/table extraction from uploaded files (PDF/DOCX/TXT/XLSX)
- Embedding generation (nomic-embed-text)
- Vector store (Chroma) management
- LLM answer generation (Gemini 2.5 Flash)
- Quota, moderation, and audit logging helpers

Note: This is a pragmatic initial implementation; some functions are simplified
and should be hardened/optimized for production.
"""
import os
import io
import hashlib
import tempfile
from typing import List, Dict, Any, Optional, Tuple

from fastapi import HTTPException
from supabase import Client

# Text extraction libs
try:
    import docx  # python-docx
except Exception:
    docx = None

try:
    import pdfminer.high_level as pdf_highlevel
except Exception:
    pdf_highlevel = None

try:
    import pandas as pd
except Exception:
    pd = None

# Embeddings + Vector store
try:
    from chromadb import Client as ChromaClient
    from chromadb.config import Settings as ChromaSettings
except Exception:
    ChromaClient = None
    ChromaSettings = None

# Simple nomic embed import (HTTP client fallback if not found could be added)
try:
    from nomic import embed as nomic_embed  # type: ignore
except Exception:
    nomic_embed = None

# Gemini
try:
    import google.generativeai as genai
except Exception:
    genai = None


def _require(cond: bool, msg: str, status: int = 500):
    if not cond:
        raise HTTPException(status_code=status, detail=msg)


# PUBLIC_INTERFACE
def get_storage_bucket_name() -> str:
    """Return the storage bucket name from env."""
    bucket = os.getenv("SUPABASE_BUCKET", "kb-files")
    return bucket


# PUBLIC_INTERFACE
def get_chroma() -> Optional["ChromaClient"]:
    """Create or return a Chroma Client instance."""
    if ChromaClient is None:
        return None
    persist_dir = os.getenv("CHROMA_PERSIST_DIR", ".chroma")
    client = ChromaClient(settings=ChromaSettings(
        is_persistent=True,
        persist_directory=persist_dir
    ))
    return client


# PUBLIC_INTERFACE
def init_gemini() -> None:
    """Configure Gemini SDK if available using GEMINI_API_KEY."""
    if genai is None:
        return
    api_key = os.getenv("GEMINI_API_KEY")
    if api_key:
        genai.configure(api_key=api_key)


# PUBLIC_INTERFACE
def ensure_audit_log(sb: Client, user_id: Optional[str], action: str, metadata: Dict[str, Any]) -> None:
    """Insert an audit trail row into audit_logs (create table via SQL admin)."""
    try:
        sb.table("audit_logs").insert({
            "user_id": user_id,
            "action": action,
            "metadata": metadata,
        }).execute()
    except Exception:
        # best-effort
        pass


# PUBLIC_INTERFACE
def check_quota(sb: Client, user_id: str, bytes_to_add: int = 0) -> None:
    """Check simple quotas based on env-defined limits."""
    storage_limit = int(os.getenv("KB_STORAGE_LIMIT_BYTES", "2147483648"))  # 2GB default
    # compute current storage
    try:
        resp = sb.table("files").select("size").eq("user_id", user_id).execute()
        sizes = [r.get("size") or 0 for r in getattr(resp, "data", []) or []]
        used = sum(int(s) for s in sizes)
        if used + max(0, bytes_to_add) > storage_limit:
            raise HTTPException(status_code=402, detail="Storage quota exceeded")
    except HTTPException:
        raise
    except Exception:
        # if we can't compute, allow to proceed but log
        pass

    # messages limit
    messages_limit = int(os.getenv("KB_MESSAGES_LIMIT", "1000"))
    try:
        resp = sb.rpc("count_user_messages", {"p_user_id": user_id}).execute()
        count = 0
        if hasattr(resp, "data"):
            # supabase-py RPC may return {'count_user_messages': N} or scalar
            data = resp.data
            if isinstance(data, dict):
                count = list(data.values())[0]
            elif isinstance(data, list) and data and isinstance(data[0], dict):
                count = list(data[0].values())[0]
            elif isinstance(data, (int, float)):
                count = int(data)
        if count >= messages_limit:
            raise HTTPException(status_code=402, detail="Message quota exceeded")
    except HTTPException:
        raise
    except Exception:
        # best effort
        pass


# PUBLIC_INTERFACE
def moderate_text(text: str) -> Tuple[bool, List[str]]:
    """Very simple moderation as a placeholder. Replace with a real API if needed."""
    banned = ["hack", "terror", "hate", "bomb"]
    matched = [w for w in banned if w in text.lower()]
    return (len(matched) == 0, matched)


# PUBLIC_INTERFACE
def extract_text_from_file_bytes(filename: str, content: bytes) -> str:
    """Extract text from supported file types. Fallback to utf-8 for .txt."""
    name = filename.lower()
    if name.endswith(".txt"):
        try:
            return content.decode("utf-8", errors="ignore")
        except Exception:
            return content.decode("latin-1", errors="ignore")

    if name.endswith(".docx") and docx is not None:
        f = io.BytesIO(content)
        doc = docx.Document(f)
        return "\n".join([p.text for p in doc.paragraphs])

    if name.endswith(".pdf") and pdf_highlevel is not None:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
            tmp.write(content)
            tmp.flush()
            try:
                return pdf_highlevel.extract_text(tmp.name) or ""
            except Exception:
                return ""

    if (name.endswith(".xlsx") or name.endswith(".xls")) and pd is not None:
        f = io.BytesIO(content)
        try:
            df_dict = pd.read_excel(f, sheet_name=None)
            parts = []
            for sheet, df in df_dict.items():
                parts.append(f"# Sheet: {sheet}")
                parts.append(df.to_csv(index=False))
            return "\n".join(parts)
        except Exception:
            return ""

    # unsupported - best effort
    return ""


# PUBLIC_INTERFACE
def chunk_text(text: str, chunk_size: int = 800, overlap: int = 100) -> List[str]:
    """Simple text chunker."""
    chunks: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + chunk_size)
        chunks.append(text[start:end])
        start = end - overlap
        if start < 0:
            start = 0
    return [c for c in chunks if c.strip()]


# PUBLIC_INTERFACE
def embed_texts(texts: List[str]) -> List[List[float]]:
    """Generate embeddings using nomic-embed-text model.

    This function expects that the environment allows nomic embedding.
    If unavailable, returns zero vectors to avoid runtime crash during initial integration.
    """
    dim = int(os.getenv("EMBEDDING_DIM", "768"))
    if nomic_embed is None:
        # fallback: zero vectors
        return [[0.0] * dim for _ in texts]
    try:
        # The nomic SDK embed API may differ; this is a placeholder interface.
        # Replace with the correct call to fetch embeddings for 'nomic-embed-text'.
        result = nomic_embed.text(texts, model="nomic-embed-text")
        vectors = result["embeddings"] if isinstance(result, dict) else result.embeddings
        return vectors
    except Exception:
        return [[0.0] * dim for _ in texts]


# PUBLIC_INTERFACE
def ensure_collection(client: "ChromaClient", name: str):
    """Get or create a Chroma collection."""
    try:
        return client.get_or_create_collection(name=name)
    except Exception:
        return client.create_collection(name=name)


# PUBLIC_INTERFACE
def store_embeddings(client: Optional["ChromaClient"], user_id: str, session_id: str, file_id: str, chunks: List[str], vectors: List[List[float]]):
    """Store embeddings into a user/session scoped collection."""
    if client is None:
        return
    collection_name = f"kb_{user_id}_{session_id}"
    col = ensure_collection(client, collection_name)
    ids = [hashlib.sha1(f"{file_id}-{i}".encode()).hexdigest() for i in range(len(chunks))]
    metadatas = [{"file_id": file_id, "chunk_index": i} for i in range(len(chunks))]
    col.upsert(ids=ids, embeddings=vectors, metadatas=metadatas, documents=chunks)


# PUBLIC_INTERFACE
def query_embeddings(client: Optional["ChromaClient"], user_id: str, session_id: str, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """Query Chroma for relevant chunks."""
    if client is None:
        return []
    col = ensure_collection(client, f"kb_{user_id}_{session_id}")
    qvec = embed_texts([query])[0]
    try:
        res = col.query(query_embeddings=[qvec], n_results=top_k, include=["documents", "metadatas", "distances"])
        results: List[Dict[str, Any]] = []
        docs = res.get("documents", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        dists = res.get("distances", [[]])[0]
        for doc, meta, dist in zip(docs, metas, dists):
            results.append({
                "text": doc,
                "file_id": meta.get("file_id"),
                "chunk_index": meta.get("chunk_index"),
                "score": float(dist),
            })
        return results
    except Exception:
        return []


# PUBLIC_INTERFACE
def answer_with_gemini(context: List[str], question: str, temperature: float = 0.2) -> str:
    """Generate an answer using Gemini given retrieved context."""
    if genai is None:
        # fallback deterministic answer for initial integration
        return "Context-aware answer could not be generated because GEMINI_API_KEY is not configured."
    init_gemini()
    model_name = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
    try:
        model = genai.GenerativeModel(model_name)
        prompt = "You are a helpful assistant. Answer the question using only the context provided.\n\n"
        prompt += "Context:\n" + "\n\n".join(context[:10]) + "\n\n"
        prompt += f"Question: {question}\nAnswer:"
        resp = model.generate_content(prompt, generation_config={"temperature": temperature})
        return (resp.text or "").strip()
    except Exception:
        return "Unable to generate answer at this time."
