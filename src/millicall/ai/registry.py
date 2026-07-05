"""(kind, config, api_key) からプロバイダ実体を生成するファクトリ。

各プロバイダ実装タスク（Task 4/5/7/8/9/10）が対応 kind の分岐をここに追加する。
"""


class UnknownProviderKind(Exception):  # noqa: N818  # 後続タスクが依存する確定インターフェイス名
    pass


def build_llm(kind: str, config: dict, api_key: str | None):
    raise UnknownProviderKind(kind)


def build_tts(kind: str, config: dict, api_key: str | None):
    raise UnknownProviderKind(kind)


def build_stt(kind: str, config: dict, api_key: str | None):
    raise UnknownProviderKind(kind)
