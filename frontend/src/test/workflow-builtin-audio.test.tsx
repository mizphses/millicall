// @vitest-environment jsdom
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConfigPanel } from "../pages/workflows/ConfigPanel";
import type { ConfigDrafts } from "../pages/workflows/editorState";
import {
  AGENTS_KEY,
  BUILTIN_AUDIO_KEY,
  PROVIDERS_KEY,
  WORKFLOWS_KEY,
} from "../queryKeys";

const FAX_PATH = "/app/data/freeswitch/tts/builtin/fax_receive_notice.wav";
const changed = vi.fn();
let host: HTMLDivElement, root: Root, client: QueryClient;

function Harness({
  nodeType,
  initial,
}: {
  nodeType: string;
  initial: Record<string, unknown>;
}) {
  const [drafts, setDrafts] = useState<ConfigDrafts>({});
  const [config, setConfig] = useState<Record<string, unknown>>(initial);
  return (
    <ConfigPanel
      nodeId="n1"
      nodeLabel="音声再生"
      nodeType={nodeType}
      config={config}
      schema={[
        {
          key: "text",
          type: "string",
          label: "読み上げテキスト",
          required: false,
        },
        {
          key: "file_path",
          type: "string",
          label: "再生ファイル",
          required: false,
        },
      ]}
      currentWorkflowId={1}
      onChange={(key, value) => {
        changed(key, value);
        setConfig((previous) => ({ ...previous, [key]: value }));
      }}
      onDelete={() => undefined}
      drafts={drafts}
      onDraftChange={(key, draft) =>
        setDrafts((previous) => ({ ...previous, [key]: draft }))
      }
    />
  );
}

async function render(nodeType: string, initial: Record<string, unknown> = {}) {
  await act(async () =>
    root.render(
      <QueryClientProvider client={client}>
        <Harness nodeType={nodeType} initial={initial} />
      </QueryClientProvider>,
    ),
  );
}

function builtinSelect(): HTMLSelectElement | null {
  const label = Array.from(host.querySelectorAll("label")).find((l) =>
    l.textContent?.startsWith("組み込み音声"),
  );
  return label?.querySelector("select") ?? null;
}

function filePathInput(): HTMLInputElement {
  const label = Array.from(host.querySelectorAll("label")).find((l) =>
    l.textContent?.startsWith("再生ファイル"),
  )!;
  return label.querySelector("input")!;
}

async function choose(select: HTMLSelectElement, value: string) {
  await act(async () => {
    select.value = value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

beforeEach(() => {
  changed.mockClear();
  (
    globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
  ).IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  for (const key of [AGENTS_KEY, PROVIDERS_KEY, WORKFLOWS_KEY])
    client.setQueryData(key, []);
  client.setQueryData(BUILTIN_AUDIO_KEY, [
    { id: "fax_receive_notice", label: "FAX受信案内", path: FAX_PATH },
  ]);
});

afterEach(async () => {
  await act(async () => root.unmount());
  client.clear();
  host.remove();
});

describe("play_audio の組み込み音声セレクト", () => {
  it("選ぶと file_path にパスが入り、（指定なし）で空に戻る", async () => {
    await render("play_audio");
    const select = builtinSelect()!;
    expect(select).not.toBeNull();
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual([
      "（指定なし）",
      "FAX受信案内",
    ]);
    expect(select.value).toBe("");

    await choose(select, "fax_receive_notice");
    expect(changed).toHaveBeenLastCalledWith("file_path", FAX_PATH);
    expect(filePathInput().value).toBe(FAX_PATH);
    expect(builtinSelect()!.value).toBe("fax_receive_notice");

    await choose(builtinSelect()!, "");
    expect(changed).toHaveBeenLastCalledWith("file_path", "");
    expect(filePathInput().value).toBe("");
  });

  it("既存の file_path が組み込み音声と一致すればそのラベルを表示する", async () => {
    await render("play_audio", { file_path: FAX_PATH });
    const select = builtinSelect()!;
    expect(select.value).toBe("fax_receive_notice");
    expect(select.selectedOptions[0].textContent).toBe("FAX受信案内");
  });

  it("手入力のパスは（指定なし）を選んでも変更しない", async () => {
    await render("play_audio", { file_path: "/tmp/custom.wav" });
    const select = builtinSelect()!;
    expect(select.value).toBe("");
    await choose(select, "");
    expect(changed).not.toHaveBeenCalled();
    expect(filePathInput().value).toBe("/tmp/custom.wav");
  });

  it("play_audio 以外のノードには出さない", async () => {
    await render("speak");
    expect(builtinSelect()).toBeNull();
  });
});
