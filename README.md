# Project Repository

This is the initial README file for the project.

## Backend File Storage (Supabase)

The backend exposes authenticated endpoints to:
- Upload files (PDF, DOCX, TXT, XLSX) via multipart/form-data
- Store files in Supabase Storage
- Record metadata in the `public.files` table
- List and delete files owned by the current user

Environment variables required:
- SUPABASE_URL
- SUPABASE_SERVICE_ROLE_KEY (preferred) or SUPABASE_ANON_KEY
- SUPABASE_JWT_SECRET (optional, for local token decoding)
- SUPABASE_STORAGE_BUCKET (optional; defaults to "documents")
- CORS_ORIGINS (optional)

Note: No file processing (parsing, chunking, embeddings, or RAG) is performed in this increment.