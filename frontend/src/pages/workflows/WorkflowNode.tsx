import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { css } from "styled-system/css";
import { badge, button } from "styled-system/recipes";
import type { WorkflowNodeData } from "./types";
import { computeDtmfHandles, computeIntentHandles } from "./handleVocab";
import { CATEGORY_LABEL } from "./editorState";

const HANDLE_LABEL: Record<string, string> = {
  default: "通常",
  next: "次へ",
  success: "成功",
  error: "エラー",
  timeout: "時間切れ",
  true: "一致",
  false: "不一致",
  no_match: "該当なし",
};

export const WorkflowNodeComponent = memo(function WorkflowNodeComponent({
  data,
  selected,
}: NodeProps & { data: WorkflowNodeData }) {
  const handles = data.dynamicHandles
    ? data.nodeType === "dtmf_input"
      ? computeDtmfHandles(data.config)
      : computeIntentHandles(data.config)
    : data.outputHandles;
  const open = data.onOpen as (() => void) | undefined;
  return (
    <div
      className={css({
        minWidth: "180px",
        maxWidth: "240px",
        bg: "white",
        borderWidth: "2px",
        borderStyle: "solid",
        borderColor: "border",
        borderRadius: "md",
        p: "3",
        position: "relative",
        "&[data-selected=true]": {
          borderColor: "accent",
          outline: "2px solid token(colors.accent.soft)",
        },
      })}
      data-selected={selected}
    >
      <span className={badge({ tone: "neutral" })}>
        {CATEGORY_LABEL[String(data.category)] ?? "共通"}
      </span>
      <p
        className={css({
          fontWeight: "600",
          color: "text",
          fontSize: "md",
          my: "2",
        })}
      >
        {data.label}
      </p>
      {open && (
        <button
          type="button"
          className={`nodrag nopan ${button({ variant: "secondary", size: "sm" })}`}
          aria-label={`${data.label}の設定`}
          onClick={(event) => {
            event.stopPropagation();
            open();
          }}
        >
          設定
        </button>
      )}
      {data.nodeType !== "start" && (
        <Handle
          type="target"
          position={Position.Top}
          id="target"
          className={css({
            bg: "text.muted!",
            width: "12px!",
            height: "12px!",
          })}
        />
      )}
      {handles.map((handleId, index) => {
        const left =
          handles.length === 1 ? 50 : 10 + (80 / (handles.length - 1)) * index;
        return (
          <div key={handleId}>
            <Handle
              type="source"
              position={Position.Bottom}
              id={handleId}
              style={{ left: `${left}%` }}
              className={css({
                bg: "accent!",
                width: "12px!",
                height: "12px!",
              })}
            />
            {handles.length > 1 && (
              <span
                style={{ left: `${left}%` }}
                className={css({
                  position: "absolute",
                  bottom: "-24px",
                  transform: "translateX(-50%)",
                  fontSize: "xs",
                  color: "text.muted",
                  whiteSpace: "nowrap",
                  pointerEvents: "none",
                })}
              >
                {HANDLE_LABEL[handleId] ?? handleId}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
});
