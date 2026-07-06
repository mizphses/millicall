import { Component, type ErrorInfo, type ReactNode } from "react";

import { css } from "styled-system/css";
import { button } from "styled-system/recipes";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

/**
 * アプリ全体をラップするエラーバウンダリ。
 * 子ツリーの未捕捉エラーを補足し、日本語メッセージ + 再読み込みボタンを表示する。
 */
export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[ErrorBoundary]", error, info.componentStack);
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null });
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      return (
        <div
          className={css({
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            minH: "100dvh",
            gap: "4",
            px: "6",
            textAlign: "center",
          })}
        >
          <h1 className={css({ fontSize: "2xl", fontWeight: "700", color: "text" })}>
            予期せぬエラーが発生しました
          </h1>
          <p className={css({ color: "text.muted", maxW: "md" })}>
            {this.state.error?.message ?? "不明なエラーです。ページを再読み込みしてください。"}
          </p>
          <button
            type="button"
            className={button({ variant: "primary" })}
            onClick={this.handleReset}
          >
            ページを再読み込み
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
