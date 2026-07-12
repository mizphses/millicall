from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 発信権限の許可値（トールフラウド対策 §7）
CallingPermission = Literal["internal", "domestic", "international"]


class ExtensionCreate(BaseModel):
    # sip_password は受け付けない（extra フィールドは無視）。
    model_config = ConfigDict(extra="ignore")

    number: str = Field(..., pattern=r"^[0-9]{2,6}$")
    display_name: str = Field(..., min_length=1, max_length=100)
    # 省略時は "domestic"（国際発信デフォルト禁止の原則に従う）
    calling_permission: CallingPermission = "domestic"


class ExtensionUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    calling_permission: CallingPermission | None = None


class ExtensionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    number: str
    display_name: str
    enabled: bool
    calling_permission: str


class ExtensionCredentials(BaseModel):
    """内線の SIP 接続情報（ソフトフォン手動設定用・管理者専用）。

    平文パスワードを含むため、専用エンドポイント（GET /api/extensions/{id}/credentials）
    でのみ返す。一覧・取得（ExtensionRead）には決して含めない。
    """

    number: str  # SIP ユーザー名 / 認証ID
    password: str  # sip_password 平文
    sip_server: str  # ソフトフォンの接続先ホスト（internal が待ち受ける IP）
    sip_port: int  # SIP ポート（通常 5060）
    domain: str  # SIP レルム / ドメイン
    display_name: str
    transport: str = "UDP"  # トランスポート（UDP 固定）
