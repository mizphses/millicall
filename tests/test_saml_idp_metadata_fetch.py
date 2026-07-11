"""IdP フェデレーションメタデータ取込 API（POST /api/settings/saml/fetch-idp-metadata）のテスト。

確認事項:
  - Entra ID 形式のメタデータ XML から entityID / SSO URL / 署名証明書を抽出できること
  - HTTP-Redirect binding を優先し、無ければ HTTP-POST の Location を使うこと
  - use 属性なしの KeyDescriptor も signing として扱うこと
  - https 以外の URL は 400
  - SSRF 対策: プライベート/ループバック/メタデータ IP へ解決されるホストは 400
  - レスポンスサイズ上限（1 MiB）超過はエラー
  - リダイレクトは追わない（302 はエラー）
  - admin 専用（未認証 401 / user ロール 403）
  - 監査ログには URL のみ記録し、証明書等の内容は記録しないこと
"""

import socket
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select

from millicall.app_settings import saml_metadata
from millicall.app_settings.saml_metadata import (
    MAX_METADATA_BYTES,
    MetadataFetchError,
    parse_idp_metadata,
)
from millicall.models import AuditLog

# --------------------------------------------------------------------------- #
# フィクスチャ: Entra ID 形式のフェデレーションメタデータ XML
# --------------------------------------------------------------------------- #

# ダミー証明書 base64（PEM 折返しテスト用に 64 桁を超える長さにする）
_CERT_B64 = "MIIC" + "A" * 200

_ENTRA_METADATA = f"""<?xml version="1.0" encoding="utf-8"?>
<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata"
    entityID="https://sts.windows.net/11111111-2222-3333-4444-555555555555/">
  <IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <KeyDescriptor use="signing">
      <KeyInfo xmlns="http://www.w3.org/2000/09/xmldsig#">
        <X509Data>
          <X509Certificate>{_CERT_B64}</X509Certificate>
        </X509Data>
      </KeyInfo>
    </KeyDescriptor>
    <SingleSignOnService
        Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
        Location="https://login.microsoftonline.com/tenant/saml2-post"/>
    <SingleSignOnService
        Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
        Location="https://login.microsoftonline.com/tenant/saml2"/>
  </IDPSSODescriptor>
</EntityDescriptor>
"""

# HTTP-POST binding しか無いメタデータ（Redirect フォールバックの確認用）
_POST_ONLY_METADATA = f"""<?xml version="1.0" encoding="utf-8"?>
<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata" entityID="https://idp.example.com/">
  <IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <KeyDescriptor>
      <KeyInfo xmlns="http://www.w3.org/2000/09/xmldsig#">
        <X509Data><X509Certificate>{_CERT_B64}</X509Certificate></X509Data>
      </KeyInfo>
    </KeyDescriptor>
    <SingleSignOnService
        Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
        Location="https://idp.example.com/sso-post"/>
  </IDPSSODescriptor>
</EntityDescriptor>
"""


def _mock_getaddrinfo(ip: str):
    """net_guard の DNS 解決をモックして特定 IP を返す（test_ai_provider_ssrf.py と同パターン）。"""
    return patch(
        "millicall.net_guard.socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 443))],
    )


# --------------------------------------------------------------------------- #
# parse_idp_metadata（純関数）
# --------------------------------------------------------------------------- #


def test_parse_extracts_entity_id_sso_url_and_cert():
    """Entra 形式のメタデータから entityID / Redirect SSO URL / 署名証明書を抽出する。"""
    result = parse_idp_metadata(_ENTRA_METADATA.encode())
    assert (
        result["idp_entity_id"] == "https://sts.windows.net/11111111-2222-3333-4444-555555555555/"
    )
    # HTTP-Redirect を優先する（POST が先に並んでいても）
    assert result["idp_sso_url"] == "https://login.microsoftonline.com/tenant/saml2"
    cert = result["idp_x509_cert"]
    assert cert.startswith("-----BEGIN CERTIFICATE-----\n")
    assert cert.endswith("-----END CERTIFICATE-----")
    body_lines = cert.splitlines()[1:-1]
    assert all(len(line) <= 64 for line in body_lines)
    assert "".join(body_lines) == _CERT_B64


def test_parse_falls_back_to_post_binding_and_untyped_keydescriptor():
    """Redirect が無ければ POST の Location を使い、use 属性なし KeyDescriptor も signing 扱い。"""
    result = parse_idp_metadata(_POST_ONLY_METADATA.encode())
    assert result["idp_sso_url"] == "https://idp.example.com/sso-post"
    assert _CERT_B64[:32] in result["idp_x509_cert"]


def test_parse_rejects_metadata_without_idp_descriptor():
    """IDPSSODescriptor が無い XML はエラー。"""
    xml = b'<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata" entityID="x"/>'
    with pytest.raises(MetadataFetchError):
        parse_idp_metadata(xml)


def test_parse_rejects_broken_xml():
    """壊れた XML はエラー（defusedxml/lxml の例外を MetadataFetchError に変換）。"""
    with pytest.raises(MetadataFetchError):
        parse_idp_metadata(b"<not-xml")


# --------------------------------------------------------------------------- #
# _download（サイズ上限・リダイレクト非追従）
# --------------------------------------------------------------------------- #


async def test_download_rejects_oversized_response():
    """1 MiB を超えるレスポンスはサイズ上限エラーになる。"""
    big_body = b"x" * (MAX_METADATA_BYTES + 1)
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=big_body))
    with pytest.raises(MetadataFetchError, match="大きすぎ"):
        await saml_metadata._download("https://idp.example.com/metadata.xml", transport)


async def test_download_does_not_follow_redirects():
    """302 リダイレクトは追わずエラーにする（SSRF 対策のバイパス防止）。"""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(302, headers={"location": "http://169.254.169.254/"})
    )
    with pytest.raises(MetadataFetchError):
        await saml_metadata._download("https://idp.example.com/metadata.xml", transport)


# --------------------------------------------------------------------------- #
# エンドポイント: 認可
# --------------------------------------------------------------------------- #


async def test_fetch_requires_auth(client):
    """未認証は 401。"""
    res = await client.post(
        "/api/settings/saml/fetch-idp-metadata",
        json={"url": "https://login.microsoftonline.com/t/federationmetadata.xml"},
    )
    assert res.status_code == 401


async def test_fetch_requires_admin_role(client, user_factory):
    """user ロールは 403。"""
    username, password = await user_factory(username="plainuser", role="user")
    await client.post("/api/auth/login", json={"username": username, "password": password})
    res = await client.post(
        "/api/settings/saml/fetch-idp-metadata",
        json={"url": "https://login.microsoftonline.com/t/federationmetadata.xml"},
    )
    assert res.status_code == 403


# --------------------------------------------------------------------------- #
# エンドポイント: セキュリティ検証
# --------------------------------------------------------------------------- #


async def test_fetch_rejects_http_url(auth_client):
    """https 以外の URL は 400（DNS 解決前に拒否）。"""
    res = await auth_client.post(
        "/api/settings/saml/fetch-idp-metadata",
        json={"url": "http://login.microsoftonline.com/t/federationmetadata.xml"},
    )
    assert res.status_code == 400
    assert "https" in res.json()["detail"]


async def test_fetch_rejects_private_ip(auth_client):
    """プライベート IP (192.168.x.x) に解決されるホストは 400。"""
    with _mock_getaddrinfo("192.168.1.10"):
        res = await auth_client.post(
            "/api/settings/saml/fetch-idp-metadata",
            json={"url": "https://internal.example.com/metadata.xml"},
        )
    assert res.status_code == 400


async def test_fetch_rejects_loopback_and_metadata_ip(auth_client):
    """loopback / クラウドメタデータ IP に解決されるホストは 400。"""
    for ip in ("127.0.0.1", "169.254.169.254"):
        with _mock_getaddrinfo(ip):
            res = await auth_client.post(
                "/api/settings/saml/fetch-idp-metadata",
                json={"url": "https://evil.example.com/metadata.xml"},
            )
        assert res.status_code == 400, ip


# --------------------------------------------------------------------------- #
# エンドポイント: 成功パス + 監査
# --------------------------------------------------------------------------- #


async def test_fetch_success_returns_parsed_values_without_saving(auth_client, app):
    """成功時はパース結果を返すのみで、設定には保存しない。監査ログは URL のみ記録する。"""
    url = (
        "https://login.microsoftonline.com/tenant/federationmetadata/2007-06/federationmetadata.xml"
    )

    async def _fake_download(_url: str, _transport) -> bytes:
        return _ENTRA_METADATA.encode()

    with (
        _mock_getaddrinfo("20.190.128.1"),
        patch.object(saml_metadata, "_download", _fake_download),
    ):
        res = await auth_client.post("/api/settings/saml/fetch-idp-metadata", json={"url": url})

    assert res.status_code == 200
    body = res.json()
    assert body["idp_entity_id"] == "https://sts.windows.net/11111111-2222-3333-4444-555555555555/"
    assert body["idp_sso_url"] == "https://login.microsoftonline.com/tenant/saml2"
    assert body["idp_x509_cert"].startswith("-----BEGIN CERTIFICATE-----")

    # 設定には保存されていない（ユーザーが確認して保存する設計）
    settings_res = await auth_client.get("/api/settings")
    assert settings_res.json()["values"]["saml_idp_entity_id"] == ""
    assert "saml_idp_entity_id" not in settings_res.json()["overridden"]

    # 監査ログ: URL のみ。証明書・entityID 等の取得内容は記録しない
    async with app.state.sessionmaker() as session:
        logs = (
            await session.scalars(
                select(AuditLog).where(AuditLog.action == "settings.saml_metadata_fetch")
            )
        ).all()
    assert len(logs) == 1
    detail = logs[0].detail or ""
    assert url in detail
    assert _CERT_B64[:32] not in detail
    assert "sts.windows.net" not in detail
