from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from millicall.models import Trunk

# トランク種別: hgw=LAN 内 NTT フレッツ光 HGW（既定）/ sip=インターネット越しの SIP プロバイダ。
TrunkType = Literal["hgw", "sip"]


class TrunkCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., pattern=r"^[A-Za-z0-9_-]{1,50}$")
    display_name: str = Field(..., min_length=1, max_length=100)
    host: str = Field(..., min_length=1, max_length=100)
    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1, max_length=128)
    did_number: str = Field(default="", max_length=30)
    caller_id: str = Field(default="", max_length=30)
    # 着信転送先の内線番号（統一番号プラン）。空 = 着信を受けない。
    inbound_extension: str = Field(default="", pattern=r"^(\d{2,6})?$")
    # 送信元 SIP ポート（任意）。None = 自動採番。範囲は 1024〜65535。
    source_port: int | None = Field(default=None, ge=1024, le=65535)
    # トランク種別。既定 hgw（既存挙動を維持）。sip はインターネット SIP プロバイダ用。
    trunk_type: TrunkType = "hgw"
    # SIP 種別の着信許可 CIDR リスト（プロバイダ IP 帯）。空 = ACL を掛けない。
    inbound_cidrs: list[str] = Field(default_factory=list)
    enabled: bool = True


class TrunkUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    host: str | None = Field(default=None, min_length=1, max_length=100)
    username: str | None = Field(default=None, min_length=1, max_length=50)
    password: str | None = Field(default=None, min_length=1, max_length=128)
    did_number: str | None = Field(default=None, max_length=30)
    caller_id: str | None = Field(default=None, max_length=30)
    # None = 変更しない / "" = 着信を受けない / "NNN" = その内線へ転送
    inbound_extension: str | None = Field(default=None, pattern=r"^(\d{2,6})?$")
    # 未指定 = 変更しない / null = 自動採番に戻す / 数値 = そのポートを明示指定。
    # 「未指定」と「null 明示」は router 側で model_fields_set により区別する。
    source_port: int | None = Field(default=None, ge=1024, le=65535)
    # None = 変更しない。
    trunk_type: TrunkType | None = None
    inbound_cidrs: list[str] | None = None
    enabled: bool | None = None


class TrunkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    display_name: str
    host: str
    username: str
    did_number: str
    caller_id: str
    inbound_extension: str
    # 送信元 SIP ポート。null = 自動採番。
    source_port: int | None
    trunk_type: TrunkType
    # SIP 種別の着信許可 CIDR リスト（保存はカンマ区切り文字列）。
    inbound_cidrs: list[str]
    enabled: bool
    has_password: bool

    @classmethod
    def from_orm_trunk(cls, t: Trunk) -> "TrunkRead":
        return cls(
            id=t.id,
            name=t.name,
            display_name=t.display_name,
            host=t.host,
            username=t.username,
            did_number=t.did_number,
            caller_id=t.caller_id,
            inbound_extension=t.inbound_extension,
            source_port=t.source_port,
            trunk_type=t.trunk_type,  # type: ignore[arg-type]
            inbound_cidrs=[c for c in t.inbound_cidrs.split(",") if c],
            enabled=t.enabled,
            has_password=bool(t.password),
        )
