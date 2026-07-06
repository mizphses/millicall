import { css } from "styled-system/css";

import { PageLayout } from "../components/PageLayout";

/**
 * 後続タスクで実装される画面のプレースホルダ。
 * Task 1 ではページ題のみ表示し、ルーティング/シェルの動作を確認する。
 */
export function Placeholder({ title, description }: { title: string; description?: string }) {
  return (
    <PageLayout title={title} description={description}>
      <div
        className={css({
          bg: "white",
          borderWidth: "1px",
          borderStyle: "dashed",
          borderColor: "border.strong",
          borderRadius: "lg",
          p: "10",
          textAlign: "center",
          color: "text.subtle",
          fontSize: "md",
        })}
      >
        この画面は後続タスクで実装されます。
      </div>
    </PageLayout>
  );
}
