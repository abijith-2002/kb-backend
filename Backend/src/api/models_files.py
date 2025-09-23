from typing import Optional, List
from pydantic import BaseModel, Field

# PUBLIC_INTERFACE
class FileItem(BaseModel):
    """Metadata for a stored file belonging to a user."""
    id: str = Field(..., description="File ID (UUID)")
    user_id: str = Field(..., description="Owner user ID")
    session_id: Optional[str] = Field(None, description="Optional session ID the file is linked to")
    name: str = Field(..., description="Original filename")
    storage_path: str = Field(..., description="Path of the file in Supabase Storage")
    mime_type: Optional[str] = Field(None, description="MIME type of the file")
    size: Optional[int] = Field(None, description="Size in bytes")
    created_at: Optional[str] = Field(None, description="Upload timestamp (ISO)")

# PUBLIC_INTERFACE
class FilesList(BaseModel):
    """List of files for current user."""
    items: List[FileItem] = Field(..., description="Files for current user")

# PUBLIC_INTERFACE
class FileUploadResponse(BaseModel):
    """Response returned after a successful file upload."""
    id: str = Field(..., description="File ID (UUID)")
    name: str = Field(..., description="Original filename")
    storage_path: str = Field(..., description="Supabase Storage path")
    mime_type: Optional[str] = Field(None, description="MIME type")
    size: Optional[int] = Field(None, description="Size in bytes")
    session_id: Optional[str] = Field(None, description="Linked session ID, if provided")
