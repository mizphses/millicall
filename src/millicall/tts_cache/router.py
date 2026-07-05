import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.ai import registry
from millicall.ai.tts.cache import PromptCache
from millicall.crypto import SecretBox
from millicall.deps import get_secret_box, get_session, require_admin
from millicall.models import Provider
from millicall.tts_cache.schemas import SynthesizeRequest, SynthesizeResult

router = APIRouter(
    prefix="/api/tts-cache", tags=["tts-cache"], dependencies=[Depends(require_admin)]
)


@router.post("/synthesize", response_model=SynthesizeResult)
async def synthesize(
    body: SynthesizeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    box: SecretBox = Depends(get_secret_box),
) -> SynthesizeResult:
    p = await session.get(Provider, body.provider_id)
    if p is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="provider not found")
    if p.type != "tts":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="provider is not a tts"
        )
    config = json.loads(p.config_json or "{}")
    api_key = box.decrypt(p.api_key_encrypted) if p.api_key_encrypted else None
    tts = registry.build_tts(p.kind, config, api_key)
    cache = PromptCache(request.app.state.settings.tts_cache_dir / "prompts")
    key = f"{body.provider_id}:{body.text}"
    existed = cache.path_for(key).exists()
    path = await cache.get_or_synth(key, tts, body.text)
    return SynthesizeResult(path=str(path), cached=existed)
