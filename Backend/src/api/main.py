import os
from typing import List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from supabase import create_client, Client

from .models import HealthResponse
from .routers.auth import router as auth_router
from .routers.sessions import router as sessions_router
from .routers.files import router as files_router
from .routers.chat import router as chat_router
from .routers.admin import router as admin_router
from .routers.misc import router as misc_router

# Load environment variables
load_dotenv()

def get_cors_origins() -> List[str]:
    origins = os.getenv("CORS_ORIGINS", "")
    if not origins:
        return ["*"]
    return [o.strip() for o in origins.split(",") if o.strip()]

def init_supabase() -> Optional[Client]:
    """Initialize a Supabase client to verify configuration at startup.

    Reads SUPABASE_URL and SUPABASE_ANON_KEY (or SUPABASE_SERVICE_ROLE_KEY if available).
    """
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        # Lazy fail: allow app to start; endpoints using Supabase will error clearly with guidance.
        return None
    try:
        return create_client(url, key)
    except Exception:
        return None

app = FastAPI(
    title="KnowledgeBot Backend",
    description="FastAPI backend for KnowledgeBot with Supabase Auth, sessions, file processing, embeddings, Chroma search, and Gemini answer generation.",
    version="0.2.0",
    openapi_tags=[
        {"name": "Auth", "description": "Authentication endpoints (Supabase Auth)"},
        {"name": "Sessions", "description": "Chat session management"},
        {"name": "Files", "description": "File management and ingestion"},
        {"name": "Chat", "description": "Messages, RAG answer, semantic search"},
        {"name": "Admin", "description": "Admin monitoring"},
        {"name": "Misc", "description": "Quota, moderation, audit logs"},
    ],
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Supabase (optional check)
_sb = init_supabase()

@app.get("/", summary="Health Check", tags=["Auth"], response_model=HealthResponse)
def health_check():
    """Health check endpoint to verify the service is running."""
    return {"message": "Healthy"}

# Register routers
app.include_router(auth_router)
app.include_router(sessions_router)
app.include_router(files_router)
app.include_router(chat_router)
app.include_router(admin_router)
app.include_router(misc_router)
