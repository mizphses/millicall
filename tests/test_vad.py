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


def test_speech_end_emitted_for_short_speech_after_start():
    """speech_start 発行済みの発話が min_speech 未満のまま無音ヒステリシス確定した場合、
    空 audio の speech_end が発行されイベントの対称性を保証する。

    有声 3 フレーム（start 発火）→ 無音 20 フレーム（ヒステリシス確定）のシナリオ。
    _min_speech_frames=6 なので 3 フレームでは min_speech 未満。
    """
    pattern = [True] * 3 + [False] * 20
    seg = VadSegmenter(classifier=_ScriptedClassifier(pattern))
    events = []
    for _ in range(len(pattern)):
        events.extend(seg.push(_FRAME))
    kinds = [e.kind for e in events]
    assert "speech_start" in kinds, "speech_start が発行されるべき"
    assert "speech_end" in kinds, "speech_start と対になる speech_end が発行されるべき"
    end = next(e for e in events if e.kind == "speech_end")
    assert end.audio == b"", "min_speech 未満の場合 speech_end.audio は空であるべき"


def test_push_buffers_partial_frames():
    # 480バイト境界に満たない入力を分割で与えても正しくフレーム化される
    seg = VadSegmenter(classifier=_ScriptedClassifier([True] * 100))
    e1 = seg.push(b"\x00" * 300)
    e2 = seg.push(b"\x00" * 300)  # 合計600 → 1フレーム(480)処理、120余る
    assert e1 == []
    assert isinstance(e2, list)


class _AlwaysSpeech:
    """常に speech=True を返す判定器（エネルギーゲート検証用）。"""

    def is_speech(self, frame: bytes, rate: int) -> bool:
        return True


def test_energy_gate_rejects_low_rms_frames():
    # classifier は常に speech=True だが、min_rms 未満の低音量フレームは非音声化される。
    quiet = b"\x08\x00" * 240  # 振幅 8（実測の回線ノイズ相当）→ RMS 8 < 200
    seg = VadSegmenter(classifier=_AlwaysSpeech(), min_rms=200)
    events = []
    for _ in range(10):
        events.extend(seg.push(quiet))
    assert not any(e.kind == "speech_start" for e in events), (
        "低RMS(ノイズ)は min_rms ゲートで speech とみなされないべき"
    )


def test_energy_gate_allows_loud_frames():
    # 十分な音量（RMS >= min_rms）なら通常どおり speech_start が出る。
    loud = b"\x00\x40" * 240  # 振幅 16384 → RMS 16384 >= 200
    seg = VadSegmenter(classifier=_AlwaysSpeech(), min_rms=200)
    events = []
    for _ in range(5):
        events.extend(seg.push(loud))
    assert any(e.kind == "speech_start" for e in events), "十分な音量なら speech_start が出るべき"


def test_energy_gate_disabled_by_default():
    # min_rms=0（既定）ではゲート無効 → 低音量でも classifier 判定に従う。
    quiet = b"\x08\x00" * 240
    seg = VadSegmenter(classifier=_AlwaysSpeech())  # min_rms 既定 0
    events = []
    for _ in range(5):
        events.extend(seg.push(quiet))
    assert any(e.kind == "speech_start" for e in events)
