"""SIP 認証情報の暗号化保存（issue #87）の結合テスト。

- API で作成した内線/トランクは DB に暗号文で保存され、FreeSWITCH XML と
  credentials API には復号した平文が出る
- 生成 XML は 0600
- 通常ログ（DEBUG まで）に平文が出ない
- 起動時の鍵整合性チェック: 復号できない行があれば ERROR ログ + /healthz degraded、
  FreeSWITCH 設定は書き換えない、credentials API は 503
"""

import json
import logging
import logging.config
import stat
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport
from sqlalchemy import select

from millicall.auth.security import hash_password
from millicall.config import Settings
from millicall.crypto import SecretBox
from millicall.main import create_app
from millicall.models import Extension, Trunk, User
from tests.conftest import CsrfAwareClient


@pytest.fixture(autouse=True)
def _keep_caplog_handler(monkeypatch):
    # alembic/env.py の fileConfig がルートロガーのハンドラを差し替えると、マイグレーション
    # 以降のログが caplog に届かず「平文が出ていない」検査が素通りになるため無効化する。
    monkeypatch.setattr(logging.config, "fileConfig", lambda *a, **k: None)


def _settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        fs_config_dir=tmp_path / "fs",
        cookie_secure=False,
        esl_timeout_seconds=0.2,
    )


@asynccontextmanager
async def _running(tmp_path):
    """同じ data_dir で app を起動する（再起動のシミュレーション用）。"""
    app = create_app(_settings(tmp_path))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with CsrfAwareClient(transport=transport, base_url="http://test") as client:
            yield app, client


async def _login_admin(app, client) -> None:
    async with app.state.sessionmaker() as session:
        if await session.scalar(select(User).where(User.username == "sipadmin")) is None:
            session.add(
                User(
                    username="sipadmin",
                    hashed_password=hash_password("Passw0rd1"),
                    display_name="sipadmin",
                    role="admin",
                    origin="local",
                )
            )
            await session.commit()
    resp = await client.post(
        "/api/auth/login", json={"username": "sipadmin", "password": "Passw0rd1"}
    )
    assert resp.status_code == 200


async def _create_ext_and_trunk(client) -> tuple[int, int]:
    ext = await client.post("/api/extensions", json={"number": "1001", "display_name": "Alice"})
    assert ext.status_code == 201
    trunk = await client.post(
        "/api/trunks",
        json={
            "name": "hgw",
            "display_name": "HGW",
            "host": "192.168.1.1",
            "username": "3",
            "password": "TrunkSecretZ9q8",
        },
    )
    assert trunk.status_code == 201
    return ext.json()["id"], trunk.json()["id"]


async def test_api_create_stores_ciphertext_and_xml_gets_plaintext(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    async with _running(tmp_path) as (app, client):
        await _login_admin(app, client)
        ext_id, trunk_id = await _create_ext_and_trunk(client)

        cred = await client.get(f"/api/extensions/{ext_id}/credentials")
        assert cred.status_code == 200
        ext_pw = cred.json()["password"]
        assert len(ext_pw) == 24

        box = SecretBox(app.state.secrets.master_key)
        async with app.state.sessionmaker() as session:
            ext = await session.get(Extension, ext_id)
            trunk = await session.get(Trunk, trunk_id)
        assert ext.sip_password_encrypted != ext_pw
        assert box.decrypt(ext.sip_password_encrypted) == ext_pw
        assert box.decrypt(trunk.password_encrypted) == "TrunkSecretZ9q8"

        trunk_read = await client.get(f"/api/trunks/{trunk_id}")
        assert trunk_read.json()["has_password"] is True
        assert "TrunkSecretZ9q8" not in trunk_read.text

        user_xml = tmp_path / "fs" / "directory" / "default" / "1001.xml"
        trunk_xml = tmp_path / "fs" / "sip_profiles" / "external_hgw.xml"
        assert f'value="{ext_pw}"' in user_xml.read_text(encoding="utf-8")
        assert "TrunkSecretZ9q8" in trunk_xml.read_text(encoding="utf-8")
        for p in (user_xml, trunk_xml):
            assert stat.S_IMODE(p.stat().st_mode) == 0o600

        # トランクのパスワード変更も暗号化されて XML に反映される
        patched = await client.patch(f"/api/trunks/{trunk_id}", json={"password": "NewTrunkPw42x"})
        assert patched.status_code == 200
        assert "NewTrunkPw42x" in trunk_xml.read_text(encoding="utf-8")

    # 通常ログに平文の認証情報が出ていない（設定再生成後のログまで捕捉できていることも確認）
    assert "reloadxml skipped" in caplog.text
    for secret in (ext_pw, "TrunkSecretZ9q8", "NewTrunkPw42x"):
        assert secret not in caplog.text
    # DB ファイル（+WAL）にも平文が無い
    raw = (tmp_path / "test.db").read_bytes()
    wal = tmp_path / "test.db-wal"
    if wal.exists():
        raw += wal.read_bytes()
    assert ext_pw.encode() not in raw
    assert b"TrunkSecretZ9q8" not in raw


async def test_healthz_ok_when_credentials_decryptable(tmp_path):
    async with _running(tmp_path) as (app, client):
        await _login_admin(app, client)
        await _create_ext_and_trunk(client)
    async with _running(tmp_path) as (app, client):
        assert app.state.change_listener.sip_credential_status.ok
        assert (await client.get("/healthz")).json() == {"status": "ok"}


async def _corrupt_key(tmp_path) -> None:
    """鍵不一致をシミュレートする: secrets.json の master_key を別の値に差し替える。"""
    path = tmp_path / "secrets.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["master_key"] = "z" * 48
    path.write_text(json.dumps(data), encoding="utf-8")


async def test_key_mismatch_excludes_undecryptable_entries(tmp_path, caplog):
    """鍵不一致: 復号できない内線・トランクだけを XML から除外し、変更は常に反映する。"""
    async with _running(tmp_path) as (app, client):
        await _login_admin(app, client)
        ext_id, trunk_id = await _create_ext_and_trunk(client)
    user_dir = tmp_path / "fs" / "directory" / "default"
    trunk_xml = tmp_path / "fs" / "sip_profiles" / "external_hgw.xml"
    assert (user_dir / "1001.xml").exists() and trunk_xml.exists()

    await _corrupt_key(tmp_path)
    caplog.set_level(logging.ERROR)
    async with _running(tmp_path) as (app, client):
        status = app.state.change_listener.sip_credential_status
        assert status.undecryptable_extensions == ["1001"]
        assert status.undecryptable_trunks == ["hgw"]
        # 番号はログにだけ出る
        assert "SIP 認証情報を復号できません" in caplog.text
        assert "内線=1001 トランク=hgw" in caplog.text

        # healthz は 200 のまま、件数だけを返す
        health = await client.get("/healthz")
        assert health.status_code == 200
        assert health.json() == {
            "status": "degraded",
            "sip_credentials": "undecryptable",
            "undecryptable_extensions": 1,
            "undecryptable_trunks": 1,
        }

        # 復号できない項目は設定から外れる（空パスワードでは書かない = 登録できない）
        assert not (user_dir / "1001.xml").exists()
        assert not trunk_xml.exists()

        await _login_admin(app, client)
        # 個別の平文が必要な credentials API は 503
        cred = await client.get(f"/api/extensions/{ext_id}/credentials")
        assert cred.status_code == 503
        # 変更 API は成功し、FreeSWITCH にも反映される（DB と FS をずらさない）
        add = await client.post("/api/extensions", json={"number": "1002", "display_name": "B"})
        assert add.status_code == 201
        assert (user_dir / "1002.xml").exists()
        assert (await client.get("/healthz")).json()["undecryptable_extensions"] == 1
        # 復号できない内線の削除も反映され、degraded の件数も減る
        assert (await client.delete(f"/api/extensions/{ext_id}")).status_code == 204
        assert (
            await client.patch(f"/api/trunks/{trunk_id}", json={"enabled": False})
        ).status_code == 200
        assert (await client.get("/healthz")).json() == {"status": "ok"}


def test_extension_repr_excludes_password():
    ext = Extension(number="1", display_name="A", sip_password_encrypted="gAAAA-secret-token")
    assert "gAAAA-secret-token" not in repr(ext)
    assert "sip_password_encrypted" not in repr(ext)
