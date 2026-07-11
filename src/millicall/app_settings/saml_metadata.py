"""IdP フェデレーションメタデータの取得・パース（SAML 設定の入力補助）。

Entra ID 等が公開する「フェデレーションメタデータ URL」から XML を取得し、
管理画面の SAML 設定フォームに自動入力する値を抽出する:

  - IdP Entity ID   … EntityDescriptor/@entityID
  - IdP SSO URL     … IDPSSODescriptor/SingleSignOnService（HTTP-Redirect 優先、無ければ POST）
  - IdP X.509 証明書 … KeyDescriptor[@use="signing"]（use 属性なしも signing 扱い）の
                       X509Certificate を 64 桁折返し + ヘッダ付き PEM に整形

セキュリティ設計:
  - https のみ許可（http は拒否）
  - SSRF 対策: net_guard.make_pinned_transport でホストを DNS 解決し、
    プライベート/ループバック/リンクローカル/予約 IP なら拒否。解決済み IP に
    接続を固定する（DNS リバインディング対策）。
  - リダイレクトは追わない（follow_redirects=False; 検証済み URL からの横跳び防止）
  - レスポンスサイズ上限 1 MiB / タイムアウト 10 秒
  - XML は defusedxml で検証してから lxml でパースする
    （millicall.auth.saml.router の _safe_parse を再利用）
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse

import httpx

from millicall.auth.saml.router import _q, _safe_parse
from millicall.net_guard import make_pinned_transport

# レスポンスサイズ上限（正常なフェデレーションメタデータは数十 KB 程度）
MAX_METADATA_BYTES = 1024 * 1024

# 取得タイムアウト（秒）
FETCH_TIMEOUT_SECONDS = 10.0

# SAML メタデータ / XML 署名の名前空間
_NS_MD = "urn:oasis:names:tc:SAML:2.0:metadata"
_NS_DS = "http://www.w3.org/2000/09/xmldsig#"

_BINDING_REDIRECT = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
_BINDING_POST = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"


class MetadataFetchError(Exception):
    """メタデータの取得・パースに失敗した（メッセージはそのまま 400 detail に使える）。"""


def _format_cert_pem(cert_text: str) -> str:
    """X509Certificate 要素のテキストを 64 桁折返しのヘッダ付き PEM に整形する。"""
    # メタデータ内の base64 は改行・空白を含むことがあるため除去して折り返し直す
    b64 = "".join(cert_text.split())
    lines = [b64[i : i + 64] for i in range(0, len(b64), 64)]
    return "-----BEGIN CERTIFICATE-----\n" + "\n".join(lines) + "\n-----END CERTIFICATE-----"


def parse_idp_metadata(xml_bytes: bytes) -> dict[str, str]:
    """フェデレーションメタデータ XML から SAML 設定値を抽出する。

    Returns:
        {"idp_entity_id", "idp_sso_url", "idp_x509_cert"} の dict。

    Raises:
        MetadataFetchError: XML が不正、または必要な要素が見つからない場合。
    """
    try:
        root = _safe_parse(xml_bytes)
    except Exception as exc:  # noqa: BLE001 — defusedxml/lxml の例外を一括変換
        raise MetadataFetchError("メタデータ XML のパースに失敗しました") from exc

    # EntityDescriptor を特定する（EntitiesDescriptor で複数含む形式にも対応）
    if root.tag == _q(_NS_MD, "EntityDescriptor"):
        candidates = [root]
    else:
        candidates = root.findall(f".//{_q(_NS_MD, 'EntityDescriptor')}")

    entity = None
    idp = None
    for cand in candidates:
        descriptor = cand.find(_q(_NS_MD, "IDPSSODescriptor"))
        if descriptor is not None:
            entity, idp = cand, descriptor
            break
    if entity is None or idp is None:
        raise MetadataFetchError(
            "IdP メタデータではありません（IDPSSODescriptor が見つかりません）"
        )

    entity_id = (entity.get("entityID") or "").strip()
    if not entity_id:
        raise MetadataFetchError("entityID がメタデータに含まれていません")

    # SSO URL: HTTP-Redirect binding を優先し、無ければ HTTP-POST の Location を使う
    redirect_url = ""
    post_url = ""
    for svc in idp.findall(_q(_NS_MD, "SingleSignOnService")):
        location = (svc.get("Location") or "").strip()
        if not location:
            continue
        binding = svc.get("Binding")
        if binding == _BINDING_REDIRECT and not redirect_url:
            redirect_url = location
        elif binding == _BINDING_POST and not post_url:
            post_url = location
    sso_url = redirect_url or post_url
    if not sso_url:
        raise MetadataFetchError("SingleSignOnService の URL が見つかりません")

    # 署名証明書: use="signing" または use 属性なしの KeyDescriptor から抽出する
    cert_b64 = ""
    for kd in idp.findall(_q(_NS_MD, "KeyDescriptor")):
        if kd.get("use") not in (None, "signing"):
            continue  # use="encryption" 等は対象外
        cert_elem = kd.find(f".//{_q(_NS_DS, 'X509Certificate')}")
        if cert_elem is not None and cert_elem.text and cert_elem.text.strip():
            cert_b64 = cert_elem.text
            break
    if not cert_b64:
        raise MetadataFetchError("署名用 X.509 証明書がメタデータに含まれていません")

    return {
        "idp_entity_id": entity_id,
        "idp_sso_url": sso_url,
        "idp_x509_cert": _format_cert_pem(cert_b64),
    }


async def _download(url: str, transport: httpx.AsyncBaseTransport) -> bytes:
    """メタデータ XML をダウンロードする（サイズ上限・リダイレクト非追従・タイムアウト付き）。

    Raises:
        MetadataFetchError: HTTP エラー・サイズ超過・接続失敗の場合。
    """
    try:
        async with (
            httpx.AsyncClient(
                transport=transport,
                follow_redirects=False,  # 検証済み URL からの横跳び（SSRF バイパス）を防ぐ
                timeout=httpx.Timeout(FETCH_TIMEOUT_SECONDS),
            ) as client,
            client.stream("GET", url) as resp,
        ):
            if resp.status_code != 200:
                raise MetadataFetchError(
                    f"メタデータの取得に失敗しました (HTTP {resp.status_code})"
                )
            buf = bytearray()
            async for chunk in resp.aiter_bytes():
                buf += chunk
                if len(buf) > MAX_METADATA_BYTES:
                    raise MetadataFetchError("メタデータが大きすぎます（上限 1 MiB）")
            return bytes(buf)
    except MetadataFetchError:
        raise
    except httpx.HTTPError as exc:
        raise MetadataFetchError(f"メタデータの取得に失敗しました: {exc}") from exc


async def fetch_idp_metadata(url: str) -> dict[str, str]:
    """フェデレーションメタデータ URL から SAML 設定値を取得する。

    値は保存せず返すのみ（フロントがフォームに反映し、ユーザーが確認して保存する）。

    Raises:
        MetadataFetchError: URL 不正・SSRF ブロック・取得/パース失敗の場合。
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise MetadataFetchError("メタデータ URL は https のみ使用できます")

    # DNS 解決 + 内部アドレス検査 + 解決済み IP への接続固定（ブロッキング解決のためスレッドへ）
    try:
        _ip, transport = await asyncio.to_thread(make_pinned_transport, url)
    except ValueError as exc:
        raise MetadataFetchError(str(exc)) from exc

    xml_bytes = await _download(url, transport)
    return parse_idp_metadata(xml_bytes)
