"""HTTP routes for preview mode: read it, switch it on, or Go (send the preview live)."""

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, StrictBool

from .preview import PreviewService


class PreviewState(BaseModel):
    active: bool


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    active: StrictBool  # True = start preview, False = Go


def build_router(preview: PreviewService) -> APIRouter:
    router = APIRouter(prefix="/api/preview")

    @router.get("", response_model=PreviewState)
    def get_preview() -> PreviewState:
        return PreviewState(active=preview.active())

    @router.post("", response_model=PreviewState)
    def set_preview(request: PreviewRequest) -> PreviewState:
        if request.active:
            preview.start()
        else:
            preview.go()
        return PreviewState(active=preview.active())

    return router
