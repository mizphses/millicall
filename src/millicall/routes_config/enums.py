import enum


class RouteTargetType(enum.StrEnum):
    EXTENSION = "extension"
    # --- 将来拡張（Phase 4 以降で有効化）---
    # RING_GROUP = "ring_group"
    # WORKFLOW = "workflow"
    # AI_AGENT = "ai_agent"
