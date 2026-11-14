/**
 * 電話機への内線割当（issue #149）の画面ロジック。DOM に依存しない部分をここに置く。
 *
 * 既定は「既存の内線から選ぶ」。別の端末に割当済みの内線は「(端末 xx に割当済み)」と
 * 表示し、選んだときは付け替えの確認を挟む（API には reassign: true を送る）。
 */
import type { components } from "../../api/schema.d";

type DeviceRead = components["schemas"]["DeviceRead"];
type ExtensionRead = components["schemas"]["ExtensionRead"];

export type AssignMode = "existing" | "new";

/** セレクトの 1 項目。holder は「この内線を使っている別の端末」。 */
export type ExtensionOption = {
  id: number;
  label: string;
  holder: DeviceRead | null;
};

/** 端末の短い表示名（MAC とあればモデル）。 */
export function deviceShortLabel(device: Pick<DeviceRead, "mac_address" | "model">): string {
  return device.model ? `${device.mac_address}（${device.model}）` : device.mac_address;
}

/**
 * 内線セレクトの項目を作る。
 *
 * 割当先の端末（targetId）自身に紐づいている内線は「割当済み」と扱わない。
 * 端末の割当状態は devices 一覧（DeviceRead.extension_id）から求める。
 */
export function extensionOptions(
  extensions: ExtensionRead[],
  devices: DeviceRead[],
  targetId: number,
): ExtensionOption[] {
  const holderByExt = new Map<number, DeviceRead>();
  for (const d of devices) {
    if (d.id !== targetId && d.extension_id != null && !holderByExt.has(d.extension_id)) {
      holderByExt.set(d.extension_id, d);
    }
  }
  return extensions.map((ext) => {
    const holder = holderByExt.get(ext.id) ?? null;
    const base = `${ext.number} ${ext.display_name}`;
    return {
      id: ext.id,
      label: holder ? `${base}（端末 ${deviceShortLabel(holder)} に割当済み）` : base,
      holder,
    };
  });
}

export type AssignForm = {
  mode: AssignMode;
  extension_id: string;
  extension_number: string;
  display_name: string;
};

export function emptyAssignForm(mode: AssignMode = "existing"): AssignForm {
  return { mode, extension_id: "", extension_number: "", display_name: "" };
}

export type AssignErrors = Partial<Record<"extension_id" | "extension_number" | "display_name", string>>;

export function validateAssignForm(form: AssignForm): AssignErrors {
  const errors: AssignErrors = {};
  if (form.mode === "existing") {
    if (!form.extension_id) errors.extension_id = "割り当てる内線を選んでください";
    return errors;
  }
  if (!form.extension_number.trim()) errors.extension_number = "内線番号を入力してください";
  if (!form.display_name.trim()) errors.display_name = "表示名を入力してください";
  return errors;
}

export type QuickProvisionPayload = components["schemas"]["QuickProvisionBody"];

/** quick-provision のリクエストボディを作る（既存選択と新規作成のどちらか一方だけを送る）。 */
export function buildAssignPayload(form: AssignForm, reassign: boolean): QuickProvisionPayload {
  if (form.mode === "existing") {
    return { extension_id: Number(form.extension_id), reassign };
  }
  return {
    extension_number: form.extension_number.trim(),
    display_name: form.display_name.trim(),
    reassign,
  };
}
