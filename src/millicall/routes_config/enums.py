import enum


class RouteTargetType(enum.StrEnum):
    EXTENSION = "extension"
    AI_AGENT = "ai_agent"
    # --- 将来拡張（Phase 4 以降で有効化）---
    # RING_GROUP = "ring_group"
    # WORKFLOW = "workflow"
