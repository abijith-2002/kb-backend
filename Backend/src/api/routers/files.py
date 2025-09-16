from fastapi import APIRouter, Depends

from ..deps import get_current_user

router = APIRouter(prefix="/files", tags=["Files"])

# PUBLIC_INTERFACE
@router.get("", summary="List files (stub)", description="Protected stub endpoint for listing files.")
def list_files(user=Depends(get_current_user)):
    return {"items": [], "note": "Files endpoints are stubs. To be implemented."}

# PUBLIC_INTERFACE
@router.post("", summary="Upload file (stub)", description="Protected stub endpoint for uploading a file.")
def upload_file(user=Depends(get_current_user)):
    return {"success": True, "note": "Upload stub. To be implemented."}

# PUBLIC_INTERFACE
@router.delete("/{file_id}", summary="Delete file (stub)", description="Protected stub endpoint for deleting a file.")
def delete_file(file_id: str, user=Depends(get_current_user)):
    return {"success": True, "note": f"Delete stub for {file_id}. To be implemented."}
