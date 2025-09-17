from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, EmailStr

# PUBLIC_INTERFACE
class HealthResponse(BaseModel):
    """Health check response."""
    message: str = Field(..., description="Status message")

# Auth models
# PUBLIC_INTERFACE
class SignupRequest(BaseModel):
    """Payload for user signup."""
    email: EmailStr = Field(..., description="User email")
    password: str = Field(..., description="User password")
    full_name: Optional[str] = Field(None, description="Optional full name for profile")

# PUBLIC_INTERFACE
class LoginRequest(BaseModel):
    """Payload for user login."""
    email: EmailStr = Field(..., description="User email")
    password: str = Field(..., description="User password")

# PUBLIC_INTERFACE
class TokenResponse(BaseModel):
    """Auth token response from Supabase."""
    access_token: str = Field(..., description="Access token (JWT)")
    token_type: str = Field("bearer", description="Token type")

# PUBLIC_INTERFACE
class Profile(BaseModel):
    """Profile information returned by /me."""
    id: str = Field(..., description="User ID (UUID from Supabase auth.users)")
    email: Optional[EmailStr] = Field(None, description="User email")
    full_name: Optional[str] = Field(None, description="Full name")

# Session models
# PUBLIC_INTERFACE
class SessionCreate(BaseModel):
    """Create a new chat session."""
    title: Optional[str] = Field(None, description="Optional session title")

# PUBLIC_INTERFACE
class SessionUpdate(BaseModel):
    """Update an existing chat session."""
    title: Optional[str] = Field(None, description="New title")

# PUBLIC_INTERFACE
class Session(BaseModel):
    """Session record."""
    id: str = Field(..., description="Session ID (UUID)")
    user_id: str = Field(..., description="Owner user ID")
    title: Optional[str] = Field(None, description="Title")
    created_at: Optional[str] = Field(None, description="Creation timestamp (ISO)")
    updated_at: Optional[str] = Field(None, description="Update timestamp (ISO)")

# PUBLIC_INTERFACE
class SessionsList(BaseModel):
    """List of sessions."""
    items: List[Session] = Field(..., description="Sessions for current user")

# Files models
# PUBLIC_INTERFACE
class FileItem(BaseModel):
    """File metadata stored in DB and in storage."""
    id: str = Field(..., description="File ID (UUID)")
    user_id: str = Field(..., description="Owner user ID")
    session_id: Optional[str] = Field(None, description="Linked session ID")
    name: str = Field(..., description="Original filename")
    storage_path: str = Field(..., description="Storage path/key")
    mime_type: Optional[str] = Field(None, description="MIME type")
    size: Optional[int] = Field(None, description="Size in bytes")
    created_at: Optional[str] = Field(None, description="Upload timestamp (ISO)")

# PUBLIC_INTERFACE
class FilesList(BaseModel):
    """List of files for a user."""
    items: List[FileItem] = Field(..., description="Files for current user")

# PUBLIC_INTERFACE
class FilePinRequest(BaseModel):
    """Pin or unpin file to a session."""
    session_id: str = Field(..., description="Session to pin the file to")
    pinned: bool = Field(..., description="True to pin, False to unpin")

# Chat/Q&A models
# PUBLIC_INTERFACE
class MessageCreate(BaseModel):
    """Create a message in a session."""
    content: str = Field(..., description="User message content")

# PUBLIC_INTERFACE
class MessageItem(BaseModel):
    """Message record in a session."""
    id: str = Field(..., description="Message ID")
    session_id: str = Field(..., description="Session ID")
    user_id: str = Field(..., description="Owner user ID")
    role: str = Field(..., description="Role: user/assistant/system")
    content: str = Field(..., description="Message content")
    created_at: Optional[str] = Field(None, description="Timestamp")

# PUBLIC_INTERFACE
class MessagesList(BaseModel):
    """List of messages for a session."""
    items: List[MessageItem] = Field(..., description="Messages for session")

# PUBLIC_INTERFACE
class AnswerRequest(BaseModel):
    """Request to answer a question within a session."""
    query: str = Field(..., description="User question")
    top_k: int = Field(5, description="Number of retrieved chunks")
    temperature: float = Field(0.2, description="LLM creativity")

# PUBLIC_INTERFACE
class AnswerResponse(BaseModel):
    """Answer from RAG pipeline."""
    answer: str = Field(..., description="Model answer")
    sources: List[Dict[str, Any]] = Field(..., description="Retrieved sources (file_id, chunk_id, score, text)")

# Search models
# PUBLIC_INTERFACE
class SearchRequest(BaseModel):
    """Semantic search request."""
    query: str = Field(..., description="Search query")
    top_k: int = Field(10, description="Number of results")

# PUBLIC_INTERFACE
class SearchResult(BaseModel):
    """Search result item."""
    file_id: Optional[str] = Field(None, description="Source file")
    text: str = Field(..., description="Text of the chunk or row")
    score: float = Field(..., description="Similarity score")

# PUBLIC_INTERFACE
class SearchResponse(BaseModel):
    """Search response with hits."""
    results: List[SearchResult] = Field(..., description="Top hits")

# Admin/Quota/Moderation/Audit models
# PUBLIC_INTERFACE
class AdminStats(BaseModel):
    """Admin monitoring stats."""
    total_users: int = Field(..., description="Total users")
    total_sessions: int = Field(..., description="Total sessions")
    total_files: int = Field(..., description="Total files")
    total_messages: int = Field(..., description="Total messages")
    last_24h_requests: int = Field(..., description="API requests in last 24h")

# PUBLIC_INTERFACE
class QuotaInfo(BaseModel):
    """Quota status for a user."""
    messages_used: int = Field(..., description="Messages used in current period")
    messages_limit: int = Field(..., description="Messages limit in current period")
    storage_used_bytes: int = Field(..., description="Storage used by user")
    storage_limit_bytes: int = Field(..., description="Storage quota")

# PUBLIC_INTERFACE
class ModerationCheckRequest(BaseModel):
    """Moderation request for user content."""
    text: str = Field(..., description="Text to check")

# PUBLIC_INTERFACE
class ModerationCheckResponse(BaseModel):
    """Moderation result."""
    allowed: bool = Field(..., description="Whether content is allowed")
    categories: List[str] = Field(..., description="Matched categories if any")

# PUBLIC_INTERFACE
class AuditLogItem(BaseModel):
    """Audit log entry."""
    id: str = Field(..., description="Log ID")
    user_id: Optional[str] = Field(None, description="User ID if available")
    action: str = Field(..., description="Action type")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Extra metadata")
    created_at: Optional[str] = Field(None, description="Timestamp")

# PUBLIC_INTERFACE
class AuditLogList(BaseModel):
    """List of audit log entries."""
    items: List[AuditLogItem] = Field(..., description="Logs")
