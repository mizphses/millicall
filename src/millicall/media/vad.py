import array
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class VadEvent:
    kind: str  # "speech_start" | "speech_end"
    audio: bytes = b""


def _frame_rms(frame: bytes) -> float:
    """L16（16bit LE モノ）フレームの RMS を返す。空フレームは 0。"""
    samples = array.array("h")
    samples.frombytes(frame if len(frame) % 2 == 0 else frame[:-1])
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


class _WebrtcClassifier:
    def __init__(self, mode: int) -> None:
        import webrtcvad

        self._vad = webrtcvad.Vad(mode)

    def is_speech(self, frame: bytes, rate: int) -> bool:
        return self._vad.is_speech(frame, rate)


class VadSegmenter:
    """連続 PCM を投入し、発話開始/終端イベントを返すヒステリシス付き VAD。

    入力は L16 モノ・sample_rate（既定 8000）。frame_ms 単位でフレーム化する。

    イベントの対称性保証:
        speech_end は speech_start と必ず対になる。
        無音ヒステリシス確定時に発話長が min_speech_ms 未満の場合は
        audio=b"" の speech_end を発行する（下流は audio が空の場合 STT をスキップできる）。
        speech_start が未発行の短ブリップ（連続有声 < speech_start_frames）は
        speech_end も発行しない。
    """

    def __init__(
        self,
        sample_rate: int = 8000,
        mode: int = 2,
        frame_ms: int = 30,
        speech_start_frames: int = 3,
        silence_end_ms: int = 600,
        min_speech_ms: int = 200,
        min_rms: float = 0.0,
        classifier=None,
    ) -> None:
        self._rate = sample_rate
        self._frame_ms = frame_ms
        self._frame_bytes = int(sample_rate * frame_ms / 1000) * 2
        self._start_frames = speech_start_frames
        self._end_frames = max(1, silence_end_ms // frame_ms)
        self._min_speech_frames = max(1, min_speech_ms // frame_ms)
        # エネルギーゲート: webrtcvad が speech と判定しても、フレーム RMS がこの値未満なら
        # 無音/回線ノイズとして非音声扱いにする。近無音（実測 RMS≈8）を発話と誤検出して
        # 再生中に誤バージイン（TTS 途切れ）や空 STT が暴発するのを抑える。0 で無効。
        self._min_rms = min_rms
        self._classifier = classifier or _WebrtcClassifier(mode)

        self._buf = bytearray()
        self._in_speech = False
        self._consec_speech = 0
        self._consec_silence = 0
        self._speech_frames: list[bytes] = []
        self._speech_frame_count = 0

    def push(self, pcm: bytes) -> list[VadEvent]:
        self._buf.extend(pcm)
        events: list[VadEvent] = []
        while len(self._buf) >= self._frame_bytes:
            frame = bytes(self._buf[: self._frame_bytes])
            del self._buf[: self._frame_bytes]
            events.extend(self._process_frame(frame))
        return events

    def _process_frame(self, frame: bytes) -> list[VadEvent]:
        speech = self._classifier.is_speech(frame, self._rate)
        # エネルギーゲート: 音量が閾値未満なら speech 判定を無効化（無音/ノイズ誤検出の抑制）。
        if speech and self._min_rms > 0 and _frame_rms(frame) < self._min_rms:
            speech = False
        events: list[VadEvent] = []
        if not self._in_speech:
            if speech:
                self._consec_speech += 1
                self._speech_frames.append(frame)
                if self._consec_speech >= self._start_frames:
                    self._in_speech = True
                    self._speech_frame_count = self._consec_speech
                    self._consec_silence = 0
                    events.append(VadEvent("speech_start"))
            else:
                self._consec_speech = 0
                self._speech_frames.clear()
        else:
            self._speech_frames.append(frame)
            if speech:
                self._speech_frame_count += 1
                self._consec_silence = 0
            else:
                self._consec_silence += 1
                if self._consec_silence >= self._end_frames:
                    if self._speech_frame_count >= self._min_speech_frames:
                        audio = b"".join(self._speech_frames)
                        events.append(VadEvent("speech_end", audio))
                    else:
                        # speech_start は発行済みのため、対になる speech_end を発行して
                        # イベントの対称性を保証する。audio=b"" = 下流は STT をスキップ可。
                        events.append(VadEvent("speech_end", b""))
                    self._reset_after_utterance()
        return events

    def _reset_after_utterance(self) -> None:
        self._in_speech = False
        self._consec_speech = 0
        self._consec_silence = 0
        self._speech_frames = []
        self._speech_frame_count = 0
