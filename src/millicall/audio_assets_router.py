from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from millicall.audio_assets import builtin_audio_dir, list_builtin_audio
from millicall.deps import get_current_user

router = APIRouter(prefix="/api/audio", tags=["audio"], dependencies=[Depends(get_current_user)])


class BuiltinAudioOut(BaseModel):
    id: str
    label: str
    path: str


@router.get("/builtin", response_model=list[BuiltinAudioOut])
async def list_builtin(request: Request) -> list[BuiltinAudioOut]:
    """同梱の組み込み音声。path は play_audio の file_path にそのまま入る絶対パス。"""
    base = builtin_audio_dir(request.app.state.settings.tts_cache_dir)
    return [
        BuiltinAudioOut(id=a.id, label=a.label, path=str(base / a.file))
        for a in list_builtin_audio()
    ]
