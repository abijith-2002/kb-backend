from typing import Optional, List
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
class FileMeta(BaseModel):
    """File metadata record."""
    id: str = Field(..., description="File ID (UUID)")
    user_id: str = Field(..., description="Owner user ID")
    session_id: Optional[str] = Field(None, description="Linked session ID")
    name: str = Field(..., description="Original filename")
    storage_path: str = Field(..., description="Path within storage bucket")
    mime_type: Optional[str] = Field(None, description="MIME type")
    size: Optional[int] = Field(None, description="File size in bytes")
    created_at: Optional[str] = Field(None, description="Creation timestamp (ISO)")

# PUBLIC_INTERFACE
class FilesList(BaseModel):
    """List of files."""
    items: List[FileMeta] = Field(..., description="Files for current user/session")

# Messages models
# PUBLIC_INTERFACE
class MessageCreate(BaseModel):
    """Create a new message in a session."""
    role: str = Field(..., description="Message role (must be 'user' in Increment 2)")
    content: str = Field(..., description="Message content")

# PUBLIC_INTERFACE
class MessageStubResponse(BaseModel):
    """Assistant stub response for message processing."""
    id: str = Field(..., description="Generated UUID for assistant message")
    role: str = Field(..., description="Role, 'assistant'")
    content: str = Field(..., description="Assistant stub content")
    timestamp: str = Field(..., description="ISO timestamp")
