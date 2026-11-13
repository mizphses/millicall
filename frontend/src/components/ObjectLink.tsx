import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { css } from "styled-system/css";

export function ObjectLink({
  to,
  id,
  children,
  newTab = false,
}: {
  to: string;
  id?: number | string;
  children: ReactNode;
  newTab?: boolean;
}) {
  return (
    <Link
      to={to}
      search={id == null ? undefined : ({ selected: String(id) } as never)}
      target={newTab ? "_blank" : undefined}
      rel={newTab ? "noopener" : undefined}
      onClick={(event) => event.stopPropagation()}
      className={css({
        color: "accent.text",
        textDecoration: "underline",
        textUnderlineOffset: "3px",
      })}
    >
      {children}
      {newTab && " ↗"}
    </Link>
  );
}
export function NumberPlanLink({
  entry,
}: {
  entry: { kind: string; id: number; number: string; label?: string };
}) {
  const to = (
    {
      extension: "/extensions",
      ai_agent: "/ai-agents",
      ring_group: "/routes",
      workflow: `/workflows/${entry.id}`,
    } as Record<string, string>
  )[entry.kind];
  return to ? (
    <ObjectLink to={to} id={entry.kind === "workflow" ? undefined : entry.id}>
      {entry.number}
      {entry.label ? ` · ${entry.label}` : ""}
    </ObjectLink>
  ) : (
    <>{entry.number}</>
  );
}
