from millicall.media.vad import VadSegmenter

_FRAME = b"\x00\x00" * 240  # 30ms @8k = 240 samples = 480 bytes


class _ScriptedClassifier:
    """フレーム順に True/False を返す判定器。パターンを繰り返す。"""

    def __init__(self, pattern: list[bool]):
        self._pattern = pattern
        self._i = 0

    def is_speech(self, frame: bytes, rate: int) -> bool:
        val = self._pattern[min(self._i, len(self._pattern) - 1)]
        self._i += 1
        return val


def test_detects_start_and_end():
    # 3フレーム無音 → 10フレーム発話(=300ms) → 20フレーム無音(=600ms) で終端
    pattern = [False] * 3 + [True] * 10 + [False] * 20
    seg = VadSegmenter(classifier=_ScriptedClassifier(pattern))
    events = []
    for _ in range(len(pattern)):
        events.extend(seg.push(_FRAME))
    kinds = [e.kind for e in events]
    assert "speech_start" in kinds
    assert "speech_end" in kinds
    end = next(e for e in events if e.kind == "speech_end")
    # 発話全体(10フレーム分の PCM=480*10 bytes)が入っている
    assert len(end.audio) >= 480 * 10


def test_short_blip_below_min_speech_is_ignored():
    # 1フレームだけ True（<200ms） → speech_start 未満、終端も出ない
    pattern = [False] * 2 + [True] * 1 + [False] * 25
    seg = VadSegmenter(classifier=_ScriptedClassifier(pattern))
    events = []
    for _ in range(len(pattern)):
        events.extend(seg.push(_FRAME))
    assert not any(e.kind == "speech_end" for e in events)


def test_push_buffers_partial_frames():
    # 480バイト境界に満たない入力を分割で与えても正しくフレーム化される
    seg = VadSegmenter(classifier=_ScriptedClassifier([True] * 100))
    e1 = seg.push(b"\x00" * 300)
    e2 = seg.push(b"\x00" * 300)  # 合計600 → 1フレーム(480)処理、120余る
    assert e1 == []
    assert isinstance(e2, list)
