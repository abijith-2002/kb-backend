# Backend Dependencies Notes

This backend uses the following major libraries:

- FastAPI (0.115.6) with Starlette (0.40.0) and Pydantic v2.
- Supabase Python client v2 for Auth, Database, and Storage.
- ChromaDB (0.5.17) for vector storage and retrieval.
- nomic (3.2.28) for `nomic-embed-text` embeddings.
- google-generativeai (0.8.3) for Gemini models.
- File parsing stack: python-docx, pdfminer.six, pandas, openpyxl.
- Uvicorn with optional uvloop/httptools/websockets/watchfiles.

Key notes:
- We avoid pinning low-level HTTP/transitive dependencies (httpx/httpcore/anyio/sniffio/idna/certifi) to prevent resolver conflicts with FastAPI/Starlette/Supabase/Chroma.
- `uvloop` and `httptools` are optional speedups. If installation fails on non-Linux platforms, you can remove them; uvicorn works without them.
- Ensure you set environment variables in `.env` for Supabase and Gemini:
  - SUPABASE_URL
  - SUPABASE_ANON_KEY or SUPABASE_SERVICE_ROLE_KEY
  - SUPABASE_BUCKET (default: kb-files)
  - GEMINI_API_KEY (optional; without it, the app returns a fallback message)

Startup command example:
  uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload

Troubleshooting:
- If build wheels fail for scientific packages, make sure `setuptools` and `wheel` are recent (added in requirements).
- If Chroma persists locally, ensure CHROMA_PERSIST_DIR is writable (default: .chroma).
- If you modify embedding dimension, set EMBEDDING_DIM env to match your model.
