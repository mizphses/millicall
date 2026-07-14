import re
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, PackageLoader
from markupsafe import Markup

# プレフィックスは 2〜8 桁の数字のみ（正規表現インジェクション防止・多層防御）
_SAFE_PREFIX_RE = re.compile(r"^[0-9]{2,8}$")

# match_number は [0-9*#] に検証済み。* は正規表現メタキャラクタのためエスケープが必要。
# re_escape フィルタで二重防御。
_MATCH_NUMBER_SAFE_RE = re.compile(r"^[0-9*#]{1,30}$")


def _re_escape_safe(s: object) -> Markup:
    # Markup は autoescape をバイパスする。[0-9*#] に検証済みの入力のみ安全。
    # 未検証フィールドへの誤用を大声で失敗させるためフィルタ内でも検証する。
    text = str(s)
    if not _MATCH_NUMBER_SAFE_RE.fullmatch(text):
        raise ValueError(f"re_escape filter: unsafe input {text!r}")
    return Markup(re.escape(text))


@dataclass(frozen=True)
class ExtensionConfig:
    number: str
    display_name: str
    sip_password: str = field(repr=False)
    # 発信権限ティア: "internal" / "domestic" / "international"（デフォルト: "domestic"）
    calling_permission: str = "domestic"


@dataclass(frozen=True)
class RingGroupConfig:
    """グループ着信（一斉鳴動）。member_numbers はメンバー内線番号のリスト。"""

    number: str
    name: str
    member_numbers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AiAgentConfig:
    """内線番号を持つ AI エージェント（default コンテキストで answer+park する）。"""

    number: str
    agent_id: int


@dataclass(frozen=True)
class WorkflowConfig:
    """内線番号を持つワークフロー（default コンテキストで answer+park する）。"""

    number: str
    workflow_id: int
    ring_count: int = 0


# トランク名（= sofia gateway 名 / プロファイル名の一部 / ファイル名の一部）に
# 許可する文字。ファイル名インジェクション・XML/シェル混入を防ぐため厳格に制限する。
_SAFE_TRUNK_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,50}$")


@dataclass(frozen=True)
class TrunkConfig:
    name: str
    display_name: str
    host: str
    username: str
    password: str = field(repr=False)
    did_number: str = ""
    caller_id: str = ""
    # 着信転送先の内線番号（統一番号プラン）。空 = 着信を受けない。
    inbound_extension: str = ""
    # 送信元 SIP ポート（明示指定）。None = 自動採番。
    source_port: int | None = None
    # トランク種別: "hgw"（LAN 内 HGW・既定）/ "sip"（インターネット越しの SIP プロバイダ）。
    # テンプレートが種別で DTMF / NAT / 着信 ACL を分岐する。
    trunk_type: str = "hgw"
    # SIP 種別の着信許可 CIDR リスト。空 = ACL を掛けない（ポート開放）。
    inbound_cidrs: list[str] = field(default_factory=list)


def allocate_source_ports(
    trunks: list["TrunkConfig"],
    *,
    external_sip_port: int = 5080,
    internal_sip_port: int = 5060,
) -> dict[str, int]:
    """トランク一覧から各トランクの実効送信元 SIP ポートを決定する（決定論的な純関数）。

    ルール:
      - トランクを name 昇順でソートして処理する。
      - source_port が明示されていればその値を採用する。
      - 未指定(None)には external_sip_port から +2 ずつ候補ポートを生成し、
        (a) internal の sip_port、(b) 他トランクが明示採用済みのポート、
        (c) 既に自動採番で使ったポート、を避けて name 昇順で割り当てる。
      - 結果、トランク 1 本なら 5080 のまま（後方互換）。

    同一ポートの衝突（手動指定同士、または手動指定と internal の衝突）は
    ValueError を送出する。

    戻り値: {trunk.name: 実効送信元ポート}
    """
    ordered = sorted(trunks, key=lambda t: t.name)

    # 明示指定の検証と収集（衝突検出）。internal との衝突もここで弾く。
    reserved: dict[int, str] = {}  # port -> trunk.name（衝突メッセージ用）
    for t in ordered:
        if t.source_port is None:
            continue
        if t.source_port == internal_sip_port:
            raise ValueError(
                f"トランク '{t.name}' の送信元ポート {t.source_port} は "
                f"internal の sip_port と衝突しています"
            )
        if t.source_port in reserved:
            raise ValueError(
                f"送信元ポート {t.source_port} が複数トランクで重複しています "
                f"('{reserved[t.source_port]}' と '{t.name}')"
            )
        reserved[t.source_port] = t.name

    result: dict[str, int] = {}
    used: set[int] = set(reserved) | {internal_sip_port}
    candidate = external_sip_port
    for t in ordered:
        if t.source_port is not None:
            result[t.name] = t.source_port
            continue
        # 予約済み(手動指定/internal)・自動採番済みを避けて次の空きを探す
        while candidate in used:
            candidate += 2
        result[t.name] = candidate
        used.add(candidate)
    return result


def _reload_command_for(name: str, *, present: bool) -> str:
    """トランク名に対する sofia プロファイル操作コマンドを 1 つ返す。

    present=True（現在トランクが存在）→ restart（新規ロード/変更反映）。
    present=False（削除済みで XML/ファイルが無い）→ stop（旧 in-memory
    プロファイルを破棄してゴースト登録を防ぐ）。
    どちらの経路でも名前を検証する。
    """
    if not _SAFE_TRUNK_NAME_RE.match(name):
        raise ValueError(f"不正なトランク名です: {name!r}")
    action = "restart" if present else "stop"
    return f"sofia profile external_{name} {action}"


def build_reload_commands(
    trunk_names: list[str],
    *,
    changed: str | None = None,
) -> list[str]:
    """ESL リロード用の ESL(sofia) コマンド列を組み立てる（純関数・実行はしない）。

    trunk_names は「現在存在するトランク名」の一覧。複数プロファイル
    (external_<name>) 対応。

    - changed 指定時: そのトランクのみを対象にする（保存/削除直後の反映用）。
      changed が trunk_names に**あれば restart**（新規ロード/変更反映）、
      **無ければ stop**（削除済み。restart では XML が無くゴースト登録が残るため、
      旧 in-memory プロファイルを stop で破棄する）。
    - changed=None: 現存する全トランクを name 昇順で restart する。
    """
    if changed is not None:
        return [_reload_command_for(changed, present=changed in set(trunk_names))]
    return [_reload_command_for(name, present=True) for name in sorted(trunk_names)]


class FreeswitchConfigWriter:
    def __init__(
        self,
        output_dir: Path,
        sip_domain: str,
        esl_password: str,
        sip_port: int = 5060,
        sip_ip: str = "auto",
        rtp_ip: str = "auto",
        event_socket_ip: str = "127.0.0.1",
        event_socket_port: int = 8021,
        sip_bind_ip: str | None = None,
        external_sip_port: int = 5080,
        # SIP 種別トランクの ext-sip-ip/ext-rtp-ip。"auto-nat" は NAT 内でも公開 IP でも機能する。
        sip_external_ip: str = "auto-nat",
        international_allow_prefixes: list[str] | None = None,
        # SIP多層防御 (Phase 6 Task 7): 信頼CIDR と 匿名着信拒否フラグ
        sip_trusted_cidrs: list[str] | None = None,
        sip_reject_anonymous: bool = False,
        # 子LAN（netd DHCP 配下）対応: 子ネットワーク適用時に internal プロファイルの
        # バインドIP／ドメインを子LAN GW IP に切り替えるためのパラメータ。
        # どちらも None のときは従来動作（sip_bind_ip / sip_domain へフォールバック）。
        internal_bind_ip: str | None = None,
        internal_domain: str | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        safe_prefixes: list[str] = international_allow_prefixes or []
        # 多層防御: __init__ 時点でプレフィックスを検証し、不正値を即座に拒否する
        # （service.py でも検証済みだが、このクラスは公開 API のため直接呼び出し時も保護する）
        for p in safe_prefixes:
            if not _SAFE_PREFIX_RE.match(p):
                raise ValueError(
                    f"国際発信allowlistに無効なプレフィックスが含まれています: "
                    f"'{p}' （2〜8桁の数字のみ）"
                )
        # デフォルト: RFC1918プライベート全帯域 + loopback（HGW 192.168.1.1 は192.168.0.0/16に内包）
        _default_cidrs = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.1/32"]
        # internal プロファイルの実効バインドIP／ドメインを決定する。
        # - internal_bind_ip 指定時（子LAN適用時）: そのIPを sip-ip/rtp-ip に使う。
        #   未指定なら従来どおり sip_bind_ip（None なら sip_ip/rtp_ip）へフォールバック。
        # - internal_domain 指定時（子LAN適用時）: そのIPを internal のドメインに使う。
        #   未指定なら従来どおり sip_domain へフォールバック。
        # external プロファイルは常に sip_bind_ip（上流IP）を使うため影響しない。
        effective_internal_bind_ip = (
            internal_bind_ip if internal_bind_ip is not None else sip_bind_ip
        )
        effective_internal_domain = internal_domain if internal_domain is not None else sip_domain
        self._base = {
            "sip_domain": sip_domain,
            "internal_bind_ip": effective_internal_bind_ip,
            "internal_domain": effective_internal_domain,
            "sip_port": sip_port,
            "sip_ip": sip_ip,
            "rtp_ip": rtp_ip,
            "sip_bind_ip": sip_bind_ip,
            "event_socket_ip": event_socket_ip,
            "event_socket_port": event_socket_port,
            "esl_password": esl_password,
            "external_sip_port": external_sip_port,
            "sip_external_ip": sip_external_ip,
            "international_allow_prefixes": safe_prefixes,
            "sip_trusted_cidrs": sip_trusted_cidrs
            if sip_trusted_cidrs is not None
            else _default_cidrs,
            "sip_reject_anonymous": sip_reject_anonymous,
        }
        self._env = Environment(
            loader=PackageLoader("millicall.telephony", "templates"),
            autoescape=True,
            keep_trailing_newline=True,
        )
        # re_escape: match_number には [0-9*#] が許可されており、* は正規表現メタキャラクタのため
        # テンプレート内で expression="^(...)$" に展開する前にエスケープが必要。
        # Markup でラップすることで HTML autoescape による二重エスケープを防ぐ。
        self._env.filters["re_escape"] = _re_escape_safe

    def update_outbound_policy(
        self, international_allow_prefixes: list[str], sip_reject_anonymous: bool
    ) -> None:
        """発信ポリシー（国際発信 allowlist / 匿名着信拒否）を差し替える。

        管理画面からの設定変更（再起動なし反映）用。プレフィックスは __init__ と
        同一規則（2〜8桁の数字のみ）で検証し、不正値は ValueError を送出する。
        """
        for p in international_allow_prefixes:
            if not _SAFE_PREFIX_RE.match(p):
                raise ValueError(
                    f"国際発信allowlistに無効なプレフィックスが含まれています: "
                    f"'{p}' （2〜8桁の数字のみ）"
                )
        self._base["international_allow_prefixes"] = list(international_allow_prefixes)
        self._base["sip_reject_anonymous"] = sip_reject_anonymous

    def set_internal_network(
        self, internal_bind_ip: str | None, internal_domain: str | None
    ) -> None:
        """internal プロファイルの実効バインドIP／ドメインを差し替える。

        子LAN（netd DHCP ネットワーク）適用状態は DB の NetworkConfig で決まるため、
        __init__ 後（regenerate 時）に最新値へ更新するために使う。

        - internal_bind_ip: 子LAN GW IP（適用時）or None → None なら sip_bind_ip
          （さらに None なら sip_ip/rtp_ip）へフォールバック。
        - internal_domain: 子LAN GW IP（適用時）or None → None なら sip_domain へ
          フォールバック。

        external プロファイルは常に sip_bind_ip（上流IP）を使うため影響しない。
        """
        self._base["internal_bind_ip"] = (
            internal_bind_ip if internal_bind_ip is not None else self._base["sip_bind_ip"]
        )
        self._base["internal_domain"] = (
            internal_domain if internal_domain is not None else self._base["sip_domain"]
        )

    @property
    def internal_bind_ip(self) -> str | None:
        """internal プロファイルの実効バインドIP（set_internal_network 反映後の値）。"""
        return self._base["internal_bind_ip"]

    @property
    def internal_domain(self) -> str | None:
        """internal プロファイルの実効ドメイン（set_internal_network 反映後の値）。"""
        return self._base["internal_domain"]

    def _render(self, template: str, extra: dict | None = None) -> str:
        context = dict(self._base)
        if extra:
            context.update(extra)
        return self._env.get_template(template).render(**context)

    def _write(self, rel_path: str, content: str) -> Path:
        path = self.output_dir / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:  # in-place で bind mount inode を保持
            f.write(content)
        return path

    def _clear_user_files(self) -> None:
        user_dir = self.output_dir / "directory" / "default"
        if user_dir.exists():
            for f in user_dir.glob("*.xml"):
                f.unlink()

    def _clear_external_profiles(self) -> None:
        """旧構成の external.xml と全 external_*.xml を削除する。

        削除されたトランクや旧単一プロファイルのファイルが残ると、sofia が
        glob include で再ロードしてゴースト登録が発生するため、write 前に掃除する。
        """
        profiles_dir = self.output_dir / "sip_profiles"
        if not profiles_dir.exists():
            return
        stale = profiles_dir / "external.xml"
        if stale.exists():
            stale.unlink()
        for f in profiles_dir.glob("external_*.xml"):
            f.unlink()

    def write_all(
        self,
        extensions: list[ExtensionConfig],
        trunks: list["TrunkConfig"] | None = None,
        ring_groups: list["RingGroupConfig"] | None = None,
        ai_agents: list["AiAgentConfig"] | None = None,
        workflows: list["WorkflowConfig"] | None = None,
    ) -> list[Path]:
        trunks = trunks or []
        ring_groups = ring_groups or []
        ai_agents = ai_agents or []
        workflows = workflows or []
        (self.output_dir / "directory" / "default").mkdir(parents=True, exist_ok=True)
        self._clear_user_files()
        (self.output_dir / "sip_profiles").mkdir(parents=True, exist_ok=True)
        # stale 掃除: 旧 external.xml と全 external_*.xml を消してから今回分を書く
        self._clear_external_profiles()

        written: list[Path] = []
        for ext in extensions:
            content = self._render(
                "user.xml.j2",
                {
                    "number": ext.number,
                    "display_name": ext.display_name,
                    "sip_password": ext.sip_password,
                    "calling_permission": ext.calling_permission,
                },
            )
            written.append(self._write(f"directory/default/{ext.number}.xml", content))

        written.append(
            self._write("directory/default.xml", self._render("directory_default.xml.j2"))
        )
        written.append(self._write("sip_profiles/internal.xml", self._render("internal.xml.j2")))
        # トランクごとに external_<name>.xml を書く（1 プロファイル = 1 sip-port）。
        # trunks が空なら external プロファイルは 1 つも書かない（掃除は上で済）。
        source_ports = allocate_source_ports(
            trunks,
            external_sip_port=self._base["external_sip_port"],
            internal_sip_port=self._base["sip_port"],
        )
        for trunk in trunks:
            if not _SAFE_TRUNK_NAME_RE.match(trunk.name):
                raise ValueError(
                    f"トランク名 '{trunk.name}' に使用できない文字が含まれています "
                    f"（[A-Za-z0-9_-] のみ）"
                )
            content = self._render(
                "external_trunk.xml.j2",
                {"trunk": trunk, "source_port": source_ports[trunk.name]},
            )
            written.append(self._write(f"sip_profiles/external_{trunk.name}.xml", content))
        # _load_trunks は Trunk.name で ORDER BY 済みだが、直接呼び出し時の順序も保証するため
        # ここでも name でソートして先頭を選ぶ（决定論的なトランク選択）
        outbound_trunk = sorted(trunks, key=lambda t: t.name)[0] if trunks else None
        written.append(
            self._write(
                "dialplan/default.xml",
                self._render(
                    "dialplan_default.xml.j2",
                    {
                        "outbound_trunk": outbound_trunk,
                        "extensions": extensions,
                        "ring_groups": ring_groups,
                        "ai_agents": ai_agents,
                        "workflows": workflows,
                    },
                ),
            )
        )
        written.append(
            self._write("dialplan/public.xml", self._render("public.xml.j2", {"trunks": trunks}))
        )
        written.append(
            self._write(
                "autoload_configs/event_socket.conf.xml",
                self._render("event_socket.xml.j2"),
            )
        )
        # SIP多層防御: ACL設定（millicall_trusted, default=deny, RFC1918+loopback許可）。
        # SIP 種別トランクには trunk_<name>_trusted リスト（プロバイダ IP 帯許可）も併せて出す。
        written.append(
            self._write(
                "autoload_configs/acl.conf.xml",
                self._render("acl.conf.xml.j2", {"trunks": trunks}),
            )
        )
        return written
