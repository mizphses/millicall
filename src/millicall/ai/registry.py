"""(kind, config, api_key) からプロバイダ実体を生成するファクトリ。

各プロバイダ実装タスク（Task 4/5/7/8/9/10）が対応 kind の分岐をここに追加する。
"""


class UnknownProviderKind(Exception):  # noqa: N818  # 後続タスクが依存する確定インターフェイス名
    pass


def build_llm(kind: str, config: dict, api_key: str | None):
    if kind == "openai_compatible":
        from millicall.ai.llm.openai_compat import OpenAICompatibleLLM

        return OpenAICompatibleLLM(
            base_url=config.get("base_url", "https://api.openai.com/v1"),
            api_key=api_key,
            model=config.get("model", "gpt-4o-mini"),
            temperature=config.get("temperature", 0.7),
            max_tokens=config.get("max_tokens", 500),
        )
    if kind == "anthropic":
        from millicall.ai.llm.anthropic import AnthropicLLM

        return AnthropicLLM(
            api_key=api_key,
            model=config.get("model", "claude-sonnet-4-20250514"),
            max_tokens=config.get("max_tokens", 500),
        )
    if kind == "gemini":
        from millicall.ai.llm.gemini import GeminiLLM

        return GeminiLLM(
            api_key=api_key,
            model=config.get("model", "gemini-2.5-flash"),
            temperature=config.get("temperature", 0.7),
        )
    raise UnknownProviderKind(kind)


def build_tts(kind: str, config: dict, api_key: str | None):
    raise UnknownProviderKind(kind)


def build_stt(kind: str, config: dict, api_key: str | None):
    raise UnknownProviderKind(kind)
