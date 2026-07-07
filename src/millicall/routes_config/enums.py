import enum


class RouteTargetType(enum.StrEnum):
    EXTENSION = "extension"
    AI_AGENT = "ai_agent"
    # Phase 4b Task 2 で有効化（ワークフロー着信）。dialplan 分岐は Task 9 が追加する。
    # NOTE(Task 9 調整): このメンバ追加は Task 2 が所有（プラン Task 2 のファイル一覧に記載）。
    WORKFLOW = "workflow"
    # --- 将来拡張 ---
    # RING_GROUP = "ring_group"
