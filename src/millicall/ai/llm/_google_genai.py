"""Google 生成 API（Gemini / Vertex AI）共通のワイヤ形式ヘルパ。

Gemini (generativelanguage) と Vertex AI (aiplatform) は generateContent の
リクエスト JSON（contents / systemInstruction / generationConfig）と SSE レスポンス
（candidates[].content.parts[].text）が同形式なので、その組み立て・解釈だけを共有する。
認証や URL は各クライアント側の責務として分離したまま（無理な抽象化はしない）。
"""

import json

from millicall.ai.llm.base import ChatMessage


def build_generate_content_payload(messages: list[ChatMessage], temperature: float) -> dict:
    """ChatMessage 列を generateContent の JSON ペイロードへ変換する。

    system ロールは systemInstruction へまとめ、user/assistant は contents へ。
    """
    system = "\n".join(m.content for m in messages if m.role == "system")
    contents = [
        {
            "role": "model" if m.role == "assistant" else "user",
            "parts": [{"text": m.content}],
        }
        for m in messages
        if m.role in ("user", "assistant")
    ]
    payload: dict = {
        "contents": contents,
        "generationConfig": {"temperature": temperature},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    return payload


def parse_sse_texts(line: str) -> list[str]:
    """SSE の 1 行から candidates[].content.parts[].text を抽出する。

    ``data:`` 行以外や JSON 解釈不能な行は空リストを返す。
    """
    if not line or not line.startswith("data:"):
        return []
    data = line[len("data:") :].strip()
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return []
    texts: list[str] = []
    for cand in obj.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            text = part.get("text")
            if text:
                texts.append(text)
    return texts
