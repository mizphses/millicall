from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, PackageLoader


@dataclass(frozen=True)
class ExtensionConfig:
    number: str
    display_name: str
    sip_password: str = field(repr=False)


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
            "international_allow_prefixes": international_allow_prefixes or [],
        }
        self._env = Environment(
            loader=PackageLoader("millicall.telephony", "templates"),
            autoescape=True,
            keep_trailing_newline=True,
        )

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
    ) -> list[Path]:
        trunks = trunks or []
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
        outbound_trunk = trunks[0] if trunks else None
        written.append(
            self._write(
                "dialplan/default.xml",
                self._render("dialplan_default.xml.j2", {"outbound_trunk": outbound_trunk}),
            )
        )
        written.append(
            self._write(
                "autoload_configs/event_socket.conf.xml",
                self._render("event_socket.xml.j2"),
            )
        )
        return written
