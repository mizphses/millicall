import { describe, expect, it } from "vitest";

import {
  normalizeContainer,
  normalizeContainers,
  stateTone,
  systemInfoEntries,
} from "../pages/system/format";

describe("normalizeContainer", () => {
  it("期待フィールドを取り出し、欠落は空文字にする", () => {
    expect(
      normalizeContainer({ name: "core", image: "millicall/core:1", state: "running", status: "Up 2h" })
    ).toEqual({ name: "core", image: "millicall/core:1", state: "running", status: "Up 2h", managed: false });
  });

  it("managed / is_managed / restartable のいずれかで managed を判定する", () => {
    expect(normalizeContainer({ name: "a", managed: true }).managed).toBe(true);
    expect(normalizeContainer({ name: "b", is_managed: true }).managed).toBe(true);
    expect(normalizeContainer({ name: "c", restartable: true }).managed).toBe(true);
    expect(normalizeContainer({ name: "d" }).managed).toBe(false);
  });

  it("配列を一括正規化する", () => {
    const rows = normalizeContainers([{ name: "x" }, { name: "y", state: "exited" }]);
    expect(rows).toHaveLength(2);
    expect(rows[1].state).toBe("exited");
  });
});

describe("stateTone", () => {
  it("running は success、exited/dead は danger", () => {
    expect(stateTone("running")).toBe("success");
    expect(stateTone("exited")).toBe("danger");
    expect(stateTone("dead")).toBe("danger");
  });

  it("restarting/created/paused は warn、不明は neutral", () => {
    expect(stateTone("restarting")).toBe("warn");
    expect(stateTone("paused")).toBe("warn");
    expect(stateTone("weird")).toBe("neutral");
  });
});

describe("systemInfoEntries", () => {
  it("キーをラベル化し、オブジェクトは JSON 文字列化する", () => {
    const entries = systemInfoEntries({ docker_version: "24.0", nested: { a: 1 }, missing: null });
    expect(entries).toContainEqual(["Docker Version", "24.0"]);
    expect(entries).toContainEqual(["Nested", JSON.stringify({ a: 1 })]);
    expect(entries).toContainEqual(["Missing", "—"]);
  });
});
