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
class RouteConfig:
    match_number: str
    target_type: str
    target_value: str
    ring_count: int = 0


@dataclass(frozen=True)
class TrunkConfig:
    name: str
    display_name: str
    host: str
    username: str
    password: str = field(repr=False)
    did_number: str = ""
    caller_id: str = ""


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
        international_allow_prefixes: list[str] | None = None,
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
        self._base = {
            "sip_domain": sip_domain,
            "sip_port": sip_port,
            "sip_ip": sip_ip,
            "rtp_ip": rtp_ip,
            "sip_bind_ip": sip_bind_ip,
            "event_socket_ip": event_socket_ip,
            "event_socket_port": event_socket_port,
            "esl_password": esl_password,
            "external_sip_port": external_sip_port,
            "international_allow_prefixes": safe_prefixes,
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

    def write_all(
        self,
        extensions: list[ExtensionConfig],
        trunks: list["TrunkConfig"] | None = None,
        routes: list["RouteConfig"] | None = None,
    ) -> list[Path]:
        trunks = trunks or []
        routes = routes or []
        (self.output_dir / "directory" / "default").mkdir(parents=True, exist_ok=True)
        self._clear_user_files()

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
        written.append(
            self._write(
                "sip_profiles/external.xml", self._render("external.xml.j2", {"trunks": trunks})
            )
        )
        # _load_trunks は Trunk.name で ORDER BY 済みだが、直接呼び出し時の順序も保証するため
        # ここでも name でソートして先頭を選ぶ（决定論的なトランク選択）
        outbound_trunk = sorted(trunks, key=lambda t: t.name)[0] if trunks else None
        written.append(
            self._write(
                "dialplan/default.xml",
                self._render(
                    "dialplan_default.xml.j2",
                    {"outbound_trunk": outbound_trunk, "extensions": extensions},
                ),
            )
        )
        written.append(
            self._write("dialplan/public.xml", self._render("public.xml.j2", {"routes": routes}))
        )
        written.append(
            self._write(
                "autoload_configs/event_socket.conf.xml",
                self._render("event_socket.xml.j2"),
            )
        )
        return written
