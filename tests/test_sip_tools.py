"""SIP 認証情報の運用 CLI（issue #87）のテスト。

- recover_sip: 鍵喪失時に、復号できない内線を再生成・トランクを無効化（パスワード再入力待ち）
- rotate_sip_passwords: 内線 SIP パスワードの一括再生成
いずれも既定は dry-run で、平文のパスワードを出力しない。
"""

import argparse

import pytest
from sqlalchemy import select

from millicall.crypto import SecretBox
from millicall.models import Extension, Trunk
from millicall.tools import recover_sip, rotate_sip_passwords
from millicall.tools.recover_sip import recover_sip_credentials
from millicall.tools.rotate_sip_passwords import rotate_extension_passwords
from tests.test_sip_credentials_encryption import (  # noqa: F401 — autouse fixture を取り込む
    _corrupt_key,
    _create_ext_and_trunk,
    _keep_caplog_handler,
    _login_admin,
    _running,
    _settings,
)


async def test_recover_sip_regenerates_extensions_and_disables_trunks(tmp_path, capsys):
    async with _running(tmp_path) as (app, client):
        await _login_admin(app, client)
        await _create_ext_and_trunk(client)
    await _corrupt_key(tmp_path)

    settings = _settings(tmp_path)
    # dry-run は何も変えない
    assert await recover_sip._run(_ns(yes=False), settings) == 0
    out = capsys.readouterr().out
    assert "1001" in out and "hgw" in out and "--yes" in out

    assert await recover_sip._run(_ns(yes=True), settings) == 0
    out = capsys.readouterr().out
    assert "再生成しました" in out and "millicallctl restart" in out

    async with _running(tmp_path) as (app, client):
        assert app.state.sip_credentials_status.ok
        assert (await client.get("/healthz")).json() == {"status": "ok"}
        async with app.state.sessionmaker() as session:
            trunk = await session.scalar(select(Trunk).where(Trunk.name == "hgw"))
        assert trunk.enabled is False
        assert trunk.password_encrypted == ""
        # 内線は新しいパスワードで XML が再生成されている
        assert (tmp_path / "fs" / "directory" / "default" / "1001.xml").exists()
        # パスワード未設定のまま有効化はできない
        await _login_admin(app, client)
        resp = await client.patch(f"/api/trunks/{trunk.id}", json={"enabled": True})
        assert resp.status_code == 422
        resp = await client.patch(
            f"/api/trunks/{trunk.id}", json={"enabled": True, "password": "ReEntered123"}
        )
        assert resp.status_code == 200
        assert resp.json()["has_password"] is True


async def test_recover_sip_without_key_dry_run_lists_all(tmp_path):
    async with _running(tmp_path) as (app, _client):
        async with app.state.sessionmaker() as session:
            session.add(
                Extension(
                    number="2001",
                    display_name="X",
                    sip_password_encrypted=SecretBox(app.state.secrets.master_key).encrypt("pw"),
                )
            )
            await session.commit()
        async with app.state.sessionmaker() as session:
            result = await recover_sip_credentials(session, None, apply=False)
    assert result.regenerated_extensions == ["2001"]
    with pytest.raises(ValueError):
        await recover_sip_credentials(session, None, apply=True)


async def test_rotate_sip_passwords(tmp_path, capsys):
    async with _running(tmp_path) as (app, client):
        await _login_admin(app, client)
        ext_id, _ = await _create_ext_and_trunk(client)
        old_pw = (await client.get(f"/api/extensions/{ext_id}/credentials")).json()["password"]
        box = SecretBox(app.state.secrets.master_key)
        async with app.state.sessionmaker() as session:
            numbers = await rotate_extension_passwords(session, box, None, apply=False)
        assert numbers == ["1001"]
        # dry-run では変わらない
        same = (await client.get(f"/api/extensions/{ext_id}/credentials")).json()["password"]
        assert same == old_pw

    settings = _settings(tmp_path)
    assert await rotate_sip_passwords._run(_ns(yes=True, number=["1001", "9999"]), settings) == 0
    out = capsys.readouterr().out
    assert "1001" in out and "存在しない内線: 9999" in out
    assert "再起動" in out and "resync" in out
    assert old_pw not in out

    async with _running(tmp_path) as (app, client):
        await _login_admin(app, client)
        new_pw = (await client.get(f"/api/extensions/{ext_id}/credentials")).json()["password"]
        assert new_pw != old_pw
        user_xml = tmp_path / "fs" / "directory" / "default" / "1001.xml"
        assert f'value="{new_pw}"' in user_xml.read_text(encoding="utf-8")


def _ns(**kwargs):
    kwargs.setdefault("yes", False)
    kwargs.setdefault("number", None)
    return argparse.Namespace(**kwargs)
