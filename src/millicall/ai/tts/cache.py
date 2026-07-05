import hashlib
from pathlib import Path

from millicall.ai.audio import pcm8k_to_wav


class PromptCache:
    def __init__(self, cache_dir) -> None:
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self._dir / f"{digest}.wav"

    async def get_or_synth(self, key: str, tts, text: str) -> Path:
        path = self.path_for(key)
        if path.exists():
            return path
        pcm = await tts.synthesize(text)
        path.write_bytes(pcm8k_to_wav(pcm))
        return path
