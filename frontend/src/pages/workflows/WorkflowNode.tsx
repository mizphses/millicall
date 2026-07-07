/**
 * xyflow カスタムノードコンポーネント（Phase 4b Task 10）。
 *
 * 上部に target Handle（着信エッジ受け口）、下部に output_handles 分の
 * source Handle を並べる。ノード選択で設定パネルが開く。
 */
import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";

import type { WorkflowNodeData } from "./types";
import { computeDtmfHandles, computeIntentHandles } from "./handleVocab";

/** カテゴリ別アクセントカラー（PandaCSS トークンでなく CSS 変数として直接）。 */
const CATEGORY_COLOR: Record<string, string> = {
  common: "#6366f1",
  ivr: "#0ea5e9",
  ai_workflow: "#10b981",
  special: "#f59e0b",
};

function categoryFromType(nodeType: string): string {
  if (["start", "end", "hangup", "play_audio", "transfer", "condition", "set_variable", "goto"].includes(nodeType)) return "common";
  if (["dtmf_input", "menu", "time_condition", "voicemail"].includes(nodeType)) return "ivr";
  if (["ai_conversation", "intent_detection", "collect_info", "api_call", "email_notify", "human_escalation"].includes(nodeType)) return "ai_workflow";
  return "special";
}


export const WorkflowNodeComponent = memo(function WorkflowNodeComponent({
  data,
  selected,
}: NodeProps & { data: WorkflowNodeData }) {
  const { nodeType, label, config, outputHandles, dynamicHandles } = data;
  const category = categoryFromType(nodeType);
  const accent = CATEGORY_COLOR[category] ?? "#6366f1";

  /** 実際に描画する output handles を決定。 */
  const handles: string[] = dynamicHandles
    ? nodeType === "dtmf_input"
      ? computeDtmfHandles(config)
      : computeIntentHandles(config)
    : outputHandles;

  const hasTarget = nodeType !== "start";

  return (
    <div
      style={{
        minWidth: 160,
        maxWidth: 220,
        background: "#ffffff",
        border: `2px solid ${selected ? accent : "#e2e8f0"}`,
        borderRadius: 8,
        boxShadow: selected
          ? `0 0 0 3px ${accent}33`
          : "0 1px 3px rgba(0,0,0,0.08)",
        overflow: "visible",
        transition: "border-color 0.15s, box-shadow 0.15s",
      }}
    >
      {/* ヘッダ帯 */}
      <div
        style={{
          background: accent,
          color: "#fff",
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: "0.04em",
          padding: "4px 10px",
          borderRadius: "6px 6px 0 0",
          textTransform: "uppercase",
        }}
      >
        {category}
      </div>

      {/* ラベル */}
      <div
        style={{
          padding: "8px 10px 6px",
          fontSize: 13,
          fontWeight: 600,
          color: "#1e293b",
          lineHeight: 1.3,
        }}
      >
        {label}
      </div>

      {/* ノード ID (小さく) */}
      <div
        style={{
          padding: "0 10px 6px",
          fontSize: 10,
          color: "#94a3b8",
          fontFamily: "monospace",
        }}
      >
        {(data as { id?: string }).id ?? ""}
      </div>

      {/* 上部 target handle（start 以外） */}
      {hasTarget && (
        <Handle
          type="target"
          position={Position.Top}
          id="target"
          style={{
            width: 12,
            height: 12,
            background: "#94a3b8",
            border: "2px solid #fff",
            top: -6,
          }}
        />
      )}

      {/* 下部 source handles（output_handles 分） */}
      {handles.map((handleId, idx) => {
        const total = handles.length;
        // 均等配置: 1本なら中央、複数なら等分
        const pct =
          total === 1
            ? 50
            : 10 + ((80 / (total - 1)) * idx);
        return (
          <div key={handleId}>
            <Handle
              type="source"
              position={Position.Bottom}
              id={handleId}
              style={{
                width: 12,
                height: 12,
                background: accent,
                border: "2px solid #fff",
                bottom: -6,
                left: `${pct}%`,
                transform: "translateX(-50%)",
              }}
            />
            {total > 1 && (
              <div
                style={{
                  position: "absolute",
                  bottom: -20,
                  left: `${pct}%`,
                  transform: "translateX(-50%)",
                  fontSize: 9,
                  color: "#64748b",
                  whiteSpace: "nowrap",
                  pointerEvents: "none",
                }}
              >
                {handleId}
              </div>
            )}
          </div>
        );
      })}

      {/* bottom padding for handle labels */}
      {handles.length > 1 && <div style={{ height: 14 }} />}
    </div>
  );
});

WorkflowNodeComponent.displayName = "WorkflowNodeComponent";
