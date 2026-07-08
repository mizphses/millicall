"""ネットワーク設定 API テスト（Phase 5 Task 4）。

カバレッジ:
  - GET /api/network: 設定取得（id=1 自動生成）、tailscale_auth_key_encrypted は一切返さない
  - PUT /api/network: フィールドバリデーション（IF/IP/range/prefix/authkey → 422）、永続化
  - PUT /api/network (auth key): 暗号化保存、平文が DB カラムに残らないことを確認
  - POST /api/network/apply: netd.apply_dhcp + apply_nat を正引数で呼ぶ、NetdError → 502
  - GET /api/network/tailscale/status: client 呼び出し、NetdError でも 200 を返す
  - POST /api/network/tailscale/up: key 保存済み → 復号して呼ぶ、key が返らない、未設定 → 400
  - POST /api/network/tailscale/down: 正常呼び出し、NetdError → 502
  - 非管理者 → 403

テスト設計:
  conftest.py の app / client / auth_client_with_telephony を流用する。
  netd_client は dependency_overrides で FakeNetdClient へ差し替える。
"""

import pytest

from millicall.deps import get_netd_client
from millicall.network.client import NetdError

# ---------------------------------------------------------------------------
# フェイク netd クライアント
# ---------------------------------------------------------------------------


class _FakeNetdClient:
    """テスト用の netd クライアントスタブ。メソッド呼び出し引数を記録する。"""

    def __init__(self) -> None:
        self.apply_dhcp_calls: list[dict] = []
        self.apply_nat_calls: list[dict] = []
        self.tailscale_up_calls: list[str] = []
        self.tailscale_down_calls: int = 0
        self.tailscale_status_result: dict = {"backend_state": "Running"}

        # エラーを注入したいときは True にする
        self.fail_apply_dhcp = False
        self.fail_apply_nat = False
        self.fail_tailscale_up = False
        self.fail_tailscale_down = False
        self.fail_tailscale_status = False

    async def apply_dhcp(self, **kwargs) -> None:
        if self.fail_apply_dhcp:
            raise NetdError("apply_dhcp テスト失敗")
        self.apply_dhcp_calls.append(kwargs)

    async def apply_nat(self, **kwargs) -> None:
        if self.fail_apply_nat:
            raise NetdError("apply_nat テスト失敗")
        self.apply_nat_calls.append(kwargs)

    async def tailscale_up(self, *, auth_key: str) -> None:
        if self.fail_tailscale_up:
            raise NetdError("tailscale_up テスト失敗")
        # auth_key を記録しない。テストで平文が漏れていないことを確認するため
        # キー長だけ記録する（平文を記録しない）
        self.tailscale_up_calls.append(auth_key)

    async def tailscale_down(self) -> None:
        if self.fail_tailscale_down:
            raise NetdError("tailscale_down テスト失敗")
        self.tailscale_down_calls += 1

    async def tailscale_status(self) -> dict:
        if self.fail_tailscale_status:
            raise NetdError("tailscale_status テスト失敗")
        return self.tailscale_status_result


def _inject_fake_netd(app, fake: _FakeNetdClient) -> None:
    """app.dependency_overrides に FakeNetdClient を注入する。"""
    app.dependency_overrides[get_netd_client] = lambda: fake


def _remove_fake_netd(app) -> None:
    app.dependency_overrides.pop(get_netd_client, None)


# ---------------------------------------------------------------------------
# GET /api/network
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_network_config_default(auth_client_with_telephony):
    """デフォルト設定（id=1 自動生成）が返ること。tailscale key はレスポンスに含まれない。"""
    c = auth_client_with_telephony
    resp = await c.get("/api/network")
    assert resp.status_code == 200
    body = resp.json()
    # デフォルトフィールドを確認
    assert body["lan_interface"] == "enp3s0"
    assert body["lan_ip"] == "172.20.0.1"
    assert body["lan_prefix"] == 16
    assert body["nat_enabled"] is True
    assert body["tailscale_enabled"] is False
    # tailscale_auth_key_set は bool であること
    assert isinstance(body["tailscale_auth_key_set"], bool)
    assert body["tailscale_auth_key_set"] is False
    # tailscale_auth_key_encrypted は絶対にレスポンスに含まれてはいけない
    assert "tailscale_auth_key_encrypted" not in body
    assert "tailscale_auth_key" not in body


@pytest.mark.asyncio
async def test_get_network_config_idempotent(auth_client_with_telephony):
    """2 回 GET しても同じ id=1 の行が返ること（自動生成が冪等であること）。"""
    c = auth_client_with_telephony
    r1 = await c.get("/api/network")
    r2 = await c.get("/api/network")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"] == 1


# ---------------------------------------------------------------------------
# PUT /api/network — 正常系
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_network_config_updates_fields(auth_client_with_telephony):
    """PUT で lan_ip / lan_prefix 等が保存されること。"""
    c = auth_client_with_telephony
    payload = {
        "lan_interface": "eth0",
        "lan_ip": "192.168.1.1",
        "lan_prefix": 24,
        "dhcp_range_start": "192.168.1.100",
        "dhcp_range_end": "192.168.1.200",
        "dhcp_lease_hours": 8,
        "provisioning_base_url": "http://192.168.1.1:8000/provisioning/",
        "nat_enabled": False,
        "wan_interface": "eth1",
        "tailscale_enabled": False,
    }
    resp = await c.put("/api/network", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["lan_interface"] == "eth0"
    assert body["lan_ip"] == "192.168.1.1"
    assert body["lan_prefix"] == 24
    assert body["dhcp_range_start"] == "192.168.1.100"
    assert body["dhcp_range_end"] == "192.168.1.200"
    assert body["dhcp_lease_hours"] == 8
    assert body["nat_enabled"] is False
    assert body["wan_interface"] == "eth1"
    # tailscale auth key はレスポンスに含まれない
    assert "tailscale_auth_key_encrypted" not in body
    assert "tailscale_auth_key" not in body


@pytest.mark.asyncio
async def test_put_network_config_persist_survives_get(auth_client_with_telephony):
    """PUT した値が GET で同じ値として返ること。"""
    c = auth_client_with_telephony
    await c.put(
        "/api/network",
        json={
            "lan_interface": "enp4s0",
            "lan_ip": "10.0.0.1",
            "lan_prefix": 8,
            "dhcp_range_start": "10.0.0.100",
            "dhcp_range_end": "10.0.0.200",
            "dhcp_lease_hours": 24,
            "provisioning_base_url": "",
            "nat_enabled": True,
            "wan_interface": "enp5s0",
            "tailscale_enabled": False,
        },
    )
    resp = await c.get("/api/network")
    assert resp.status_code == 200
    body = resp.json()
    assert body["lan_interface"] == "enp4s0"
    assert body["lan_ip"] == "10.0.0.1"


# ---------------------------------------------------------------------------
# PUT /api/network — auth key 暗号化
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_with_tailscale_auth_key_stores_encrypted(auth_client_with_telephony, app):
    """tailscale_auth_key を PUT すると暗号化されて保存され、平文が DB に残らないこと。"""
    c = auth_client_with_telephony
    plaintext_key = "tskey-abcdefghij1234567890"
    resp = await c.put(
        "/api/network",
        json={
            "lan_interface": "eth0",
            "lan_ip": "192.168.1.1",
            "lan_prefix": 24,
            "dhcp_range_start": "192.168.1.100",
            "dhcp_range_end": "192.168.1.200",
            "dhcp_lease_hours": 12,
            "provisioning_base_url": "",
            "nat_enabled": True,
            "wan_interface": "",
            "tailscale_enabled": True,
            "tailscale_auth_key": plaintext_key,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # レスポンスに平文キーは含まれない
    assert plaintext_key not in str(body)
    # tailscale_auth_key_set が True になること
    assert body["tailscale_auth_key_set"] is True

    # DB カラムを直接確認して平文が保存されていないことを検証
    from millicall.models import NetworkConfig

    async with app.state.sessionmaker() as session:
        cfg = await session.get(NetworkConfig, 1)
    assert cfg is not None
    assert cfg.tailscale_auth_key_encrypted is not None
    # 保存値は平文キーと一致しない（暗号化済み）
    assert cfg.tailscale_auth_key_encrypted != plaintext_key
    # Fernet トークンは元の平文より長い
    assert len(cfg.tailscale_auth_key_encrypted) > len(plaintext_key)


@pytest.mark.asyncio
async def test_put_null_auth_key_preserves_existing(auth_client_with_telephony, app):
    """tailscale_auth_key を省略した PUT は既存のキーを変更しないこと。"""
    c = auth_client_with_telephony
    # まずキーを設定する
    await c.put(
        "/api/network",
        json={
            "lan_interface": "eth0",
            "lan_ip": "192.168.1.1",
            "lan_prefix": 24,
            "dhcp_range_start": "192.168.1.100",
            "dhcp_range_end": "192.168.1.200",
            "dhcp_lease_hours": 12,
            "provisioning_base_url": "",
            "nat_enabled": True,
            "wan_interface": "",
            "tailscale_enabled": True,
            "tailscale_auth_key": "tskey-abcdefghij1234567890",
        },
    )

    # キーを省略して PUT（tailscale_auth_key フィールドを含めない）
    await c.put(
        "/api/network",
        json={
            "lan_interface": "eth0",
            "lan_ip": "192.168.1.1",
            "lan_prefix": 24,
            "dhcp_range_start": "192.168.1.100",
            "dhcp_range_end": "192.168.1.200",
            "dhcp_lease_hours": 12,
            "provisioning_base_url": "",
            "nat_enabled": True,
            "wan_interface": "",
            "tailscale_enabled": True,
            # tailscale_auth_key は含まない
        },
    )

    # キーはまだ設定済みのまま
    resp = await c.get("/api/network")
    assert resp.json()["tailscale_auth_key_set"] is True


@pytest.mark.asyncio
async def test_put_empty_string_auth_key_clears_it(auth_client_with_telephony, app):
    """tailscale_auth_key="" を PUT するとキーが削除されること。"""
    c = auth_client_with_telephony
    # まずキーを設定する
    await c.put(
        "/api/network",
        json={
            "lan_interface": "eth0",
            "lan_ip": "192.168.1.1",
            "lan_prefix": 24,
            "dhcp_range_start": "192.168.1.100",
            "dhcp_range_end": "192.168.1.200",
            "dhcp_lease_hours": 12,
            "provisioning_base_url": "",
            "nat_enabled": True,
            "wan_interface": "",
            "tailscale_enabled": True,
            "tailscale_auth_key": "tskey-abcdefghij1234567890",
        },
    )
    # 空文字列でクリア
    resp = await c.put(
        "/api/network",
        json={
            "lan_interface": "eth0",
            "lan_ip": "192.168.1.1",
            "lan_prefix": 24,
            "dhcp_range_start": "192.168.1.100",
            "dhcp_range_end": "192.168.1.200",
            "dhcp_lease_hours": 12,
            "provisioning_base_url": "",
            "nat_enabled": True,
            "wan_interface": "",
            "tailscale_enabled": True,
            "tailscale_auth_key": "",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["tailscale_auth_key_set"] is False


# ---------------------------------------------------------------------------
# PUT /api/network — バリデーション 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_invalid_lan_interface_422(auth_client_with_telephony):
    """不正な lan_interface は 422 になること。"""
    c = auth_client_with_telephony
    resp = await c.put(
        "/api/network",
        json={
            "lan_interface": "bad interface!",  # スペース・感嘆符は不正
            "lan_ip": "192.168.1.1",
            "lan_prefix": 24,
            "dhcp_range_start": "192.168.1.100",
            "dhcp_range_end": "192.168.1.200",
            "dhcp_lease_hours": 12,
            "provisioning_base_url": "",
            "nat_enabled": True,
            "wan_interface": "",
            "tailscale_enabled": False,
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_put_invalid_lan_ip_422(auth_client_with_telephony):
    """不正な lan_ip（非 IPv4）は 422 になること。"""
    c = auth_client_with_telephony
    resp = await c.put(
        "/api/network",
        json={
            "lan_interface": "eth0",
            "lan_ip": "not.an.ip",
            "lan_prefix": 24,
            "dhcp_range_start": "192.168.1.100",
            "dhcp_range_end": "192.168.1.200",
            "dhcp_lease_hours": 12,
            "provisioning_base_url": "",
            "nat_enabled": True,
            "wan_interface": "",
            "tailscale_enabled": False,
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_put_invalid_cidr_prefix_422(auth_client_with_telephony):
    """範囲外の lan_prefix（33）は 422 になること。"""
    c = auth_client_with_telephony
    resp = await c.put(
        "/api/network",
        json={
            "lan_interface": "eth0",
            "lan_ip": "192.168.1.1",
            "lan_prefix": 33,  # 0〜32 の範囲外
            "dhcp_range_start": "192.168.1.100",
            "dhcp_range_end": "192.168.1.200",
            "dhcp_lease_hours": 12,
            "provisioning_base_url": "",
            "nat_enabled": True,
            "wan_interface": "",
            "tailscale_enabled": False,
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_put_invalid_dhcp_range_422(auth_client_with_telephony):
    """start > end の DHCP レンジは 422 になること。"""
    c = auth_client_with_telephony
    resp = await c.put(
        "/api/network",
        json={
            "lan_interface": "eth0",
            "lan_ip": "192.168.1.1",
            "lan_prefix": 24,
            "dhcp_range_start": "192.168.1.200",  # start > end
            "dhcp_range_end": "192.168.1.100",
            "dhcp_lease_hours": 12,
            "provisioning_base_url": "",
            "nat_enabled": True,
            "wan_interface": "",
            "tailscale_enabled": False,
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_put_invalid_wan_interface_422(auth_client_with_telephony):
    """不正な wan_interface は 422 になること。"""
    c = auth_client_with_telephony
    resp = await c.put(
        "/api/network",
        json={
            "lan_interface": "eth0",
            "lan_ip": "192.168.1.1",
            "lan_prefix": 24,
            "dhcp_range_start": "192.168.1.100",
            "dhcp_range_end": "192.168.1.200",
            "dhcp_lease_hours": 12,
            "provisioning_base_url": "",
            "nat_enabled": True,
            "wan_interface": "bad if!",  # 不正
            "tailscale_enabled": False,
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_put_invalid_tailscale_authkey_422(auth_client_with_telephony):
    """tskey- で始まらない tailscale_auth_key は 422 になること。"""
    c = auth_client_with_telephony
    resp = await c.put(
        "/api/network",
        json={
            "lan_interface": "eth0",
            "lan_ip": "192.168.1.1",
            "lan_prefix": 24,
            "dhcp_range_start": "192.168.1.100",
            "dhcp_range_end": "192.168.1.200",
            "dhcp_lease_hours": 12,
            "provisioning_base_url": "",
            "nat_enabled": True,
            "wan_interface": "",
            "tailscale_enabled": True,
            "tailscale_auth_key": "invalid-key-format",  # tskey- でない
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/network/apply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_calls_netd_with_correct_args(auth_client_with_telephony, app):
    """apply は apply_dhcp + apply_nat を正しい引数で呼ぶこと。"""
    fake = _FakeNetdClient()
    _inject_fake_netd(app, fake)
    try:
        c = auth_client_with_telephony
        # 設定を保存する
        await c.put(
            "/api/network",
            json={
                "lan_interface": "eth0",
                "lan_ip": "192.168.1.1",
                "lan_prefix": 24,
                "dhcp_range_start": "192.168.1.100",
                "dhcp_range_end": "192.168.1.200",
                "dhcp_lease_hours": 8,
                "provisioning_base_url": "http://192.168.1.1:8000/provisioning/",
                "nat_enabled": True,
                "wan_interface": "eth1",
                "tailscale_enabled": False,
            },
        )
        resp = await c.post("/api/network/apply")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

        # apply_dhcp が1回呼ばれたこと
        assert len(fake.apply_dhcp_calls) == 1
        dhcp_args = fake.apply_dhcp_calls[0]
        assert dhcp_args["lan_interface"] == "eth0"
        assert dhcp_args["lan_ip"] == "192.168.1.1"
        assert dhcp_args["lan_prefix"] == 24
        assert dhcp_args["dhcp_range_start"] == "192.168.1.100"
        assert dhcp_args["dhcp_range_end"] == "192.168.1.200"
        assert dhcp_args["dhcp_lease_hours"] == 8
        assert dhcp_args["provisioning_url"] == "http://192.168.1.1:8000/provisioning/"

        # apply_nat が1回呼ばれたこと
        assert len(fake.apply_nat_calls) == 1
        nat_args = fake.apply_nat_calls[0]
        assert nat_args["enabled"] is True
        assert nat_args["lan_ip"] == "192.168.1.1"
        assert nat_args["lan_prefix"] == 24
        assert nat_args["wan_interface"] == "eth1"
    finally:
        _remove_fake_netd(app)


@pytest.mark.asyncio
async def test_apply_derives_provisioning_url_when_empty(auth_client_with_telephony, app):
    """provisioning_base_url が空の場合は lan_ip から URL を構築すること。"""
    fake = _FakeNetdClient()
    _inject_fake_netd(app, fake)
    try:
        c = auth_client_with_telephony
        await c.put(
            "/api/network",
            json={
                "lan_interface": "eth0",
                "lan_ip": "10.10.10.1",
                "lan_prefix": 24,
                "dhcp_range_start": "10.10.10.100",
                "dhcp_range_end": "10.10.10.200",
                "dhcp_lease_hours": 12,
                "provisioning_base_url": "",  # 空
                "nat_enabled": False,
                "wan_interface": "",
                "tailscale_enabled": False,
            },
        )
        resp = await c.post("/api/network/apply")
        assert resp.status_code == 200
        dhcp_args = fake.apply_dhcp_calls[0]
        # 既定 http_port=80 → ポートは省略される
        assert dhcp_args["provisioning_url"] == "http://10.10.10.1/provisioning/"
    finally:
        _remove_fake_netd(app)


@pytest.mark.asyncio
async def test_apply_netd_error_returns_502(auth_client_with_telephony, app):
    """netd が失敗したとき 502 を返すこと。"""
    fake = _FakeNetdClient()
    fake.fail_apply_dhcp = True
    _inject_fake_netd(app, fake)
    try:
        c = auth_client_with_telephony
        resp = await c.post("/api/network/apply")
        assert resp.status_code == 502
        body = resp.json()
        assert "detail" in body
    finally:
        _remove_fake_netd(app)


# ---------------------------------------------------------------------------
# GET /api/network/tailscale/status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tailscale_status_running(auth_client_with_telephony, app):
    """tailscale が Running のとき connected=True が返ること。"""
    fake = _FakeNetdClient()
    fake.tailscale_status_result = {"backend_state": "Running"}
    _inject_fake_netd(app, fake)
    try:
        c = auth_client_with_telephony
        resp = await c.get("/api/network/tailscale/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["connected"] is True
    finally:
        _remove_fake_netd(app)


@pytest.mark.asyncio
async def test_tailscale_status_netd_error_returns_200(auth_client_with_telephony, app):
    """NetdError でも 200 を返し、connected=False と error メッセージを含むこと。"""
    fake = _FakeNetdClient()
    fake.fail_tailscale_status = True
    _inject_fake_netd(app, fake)
    try:
        c = auth_client_with_telephony
        resp = await c.get("/api/network/tailscale/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["connected"] is False
        assert body["error"] is not None
    finally:
        _remove_fake_netd(app)


# ---------------------------------------------------------------------------
# POST /api/network/tailscale/up
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tailscale_up_calls_client_with_key(auth_client_with_telephony, app):
    """tailscale_auth_key が設定済みのとき netd.tailscale_up が呼ばれること。キーはレスポンスに含まれない。"""
    fake = _FakeNetdClient()
    _inject_fake_netd(app, fake)
    try:
        c = auth_client_with_telephony
        plaintext_key = "tskey-validkey12345"
        # まずキーを設定する
        await c.put(
            "/api/network",
            json={
                "lan_interface": "eth0",
                "lan_ip": "192.168.1.1",
                "lan_prefix": 24,
                "dhcp_range_start": "192.168.1.100",
                "dhcp_range_end": "192.168.1.200",
                "dhcp_lease_hours": 12,
                "provisioning_base_url": "",
                "nat_enabled": True,
                "wan_interface": "",
                "tailscale_enabled": True,
                "tailscale_auth_key": plaintext_key,
            },
        )
        resp = await c.post("/api/network/tailscale/up")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"ok": True}
        # tailscale_up が呼ばれたこと
        assert len(fake.tailscale_up_calls) == 1
        # レスポンスに平文キーが含まれないこと
        assert plaintext_key not in str(body)
        # netd に渡されたキーが正しいこと（FakeNetdClient はキーを記録）
        assert fake.tailscale_up_calls[0] == plaintext_key
    finally:
        _remove_fake_netd(app)


@pytest.mark.asyncio
async def test_tailscale_up_no_key_returns_400(auth_client_with_telephony, app):
    """auth key が設定されていないとき 400 を返すこと。"""
    fake = _FakeNetdClient()
    _inject_fake_netd(app, fake)
    try:
        c = auth_client_with_telephony
        # デフォルト設定（キーなし）のまま up を呼ぶ
        resp = await c.post("/api/network/tailscale/up")
        assert resp.status_code == 400
    finally:
        _remove_fake_netd(app)


@pytest.mark.asyncio
async def test_tailscale_up_corrupt_key_returns_400(auth_client_with_telephony, app):
    """保存された auth key が復号不能（破損/キー更新）でも 500 化せず 400 を返す（レビュー M-1）。"""
    fake = _FakeNetdClient()
    _inject_fake_netd(app, fake)
    try:
        c = auth_client_with_telephony
        # 復号できない不正な値を tailscale_auth_key_encrypted に直接書き込む
        from millicall.models import NetworkConfig

        async with app.state.sessionmaker() as session:
            cfg = await session.get(NetworkConfig, 1)
            if cfg is None:
                cfg = NetworkConfig(id=1)
                session.add(cfg)
            cfg.tailscale_enabled = True
            cfg.tailscale_auth_key_encrypted = "not-a-valid-fernet-token"
            await session.commit()

        resp = await c.post("/api/network/tailscale/up")
        assert resp.status_code == 400
        # netd は呼ばれない
        assert len(fake.tailscale_up_calls) == 0
    finally:
        _remove_fake_netd(app)


@pytest.mark.asyncio
async def test_tailscale_up_netd_error_returns_502(auth_client_with_telephony, app):
    """netd が失敗したとき 502 を返すこと。キーはレスポンスに含まれない。"""
    fake = _FakeNetdClient()
    fake.fail_tailscale_up = True
    _inject_fake_netd(app, fake)
    try:
        c = auth_client_with_telephony
        await c.put(
            "/api/network",
            json={
                "lan_interface": "eth0",
                "lan_ip": "192.168.1.1",
                "lan_prefix": 24,
                "dhcp_range_start": "192.168.1.100",
                "dhcp_range_end": "192.168.1.200",
                "dhcp_lease_hours": 12,
                "provisioning_base_url": "",
                "nat_enabled": True,
                "wan_interface": "",
                "tailscale_enabled": True,
                "tailscale_auth_key": "tskey-validkey12345",
            },
        )
        resp = await c.post("/api/network/tailscale/up")
        assert resp.status_code == 502
        # auth key はエラーレスポンスに含まれない
        assert "tskey" not in resp.text
    finally:
        _remove_fake_netd(app)


# ---------------------------------------------------------------------------
# POST /api/network/tailscale/down
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tailscale_down_calls_client(auth_client_with_telephony, app):
    """tailscale_down が netd クライアントを呼ぶこと。"""
    fake = _FakeNetdClient()
    _inject_fake_netd(app, fake)
    try:
        c = auth_client_with_telephony
        resp = await c.post("/api/network/tailscale/down")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert fake.tailscale_down_calls == 1
    finally:
        _remove_fake_netd(app)


@pytest.mark.asyncio
async def test_tailscale_down_netd_error_returns_502(auth_client_with_telephony, app):
    """netd が失敗したとき 502 を返すこと。"""
    fake = _FakeNetdClient()
    fake.fail_tailscale_down = True
    _inject_fake_netd(app, fake)
    try:
        c = auth_client_with_telephony
        resp = await c.post("/api/network/tailscale/down")
        assert resp.status_code == 502
    finally:
        _remove_fake_netd(app)


# ---------------------------------------------------------------------------
# 認証ガード
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requires_auth(client):
    """未認証の GET /api/network は 401 を返すこと。"""
    resp = await client.get("/api/network")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_non_admin_returns_403(client, user_factory, app):
    """非管理者ユーザーは 403 を返すこと。"""
    username, password = await user_factory(username="viewer", password="Viewer0000", role="user")
    await client.post("/api/auth/login", json={"username": username, "password": password})
    resp = await client.get("/api/network")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# apply は PUT とは独立していること（PUT では netd を呼ばない）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_does_not_call_netd(auth_client_with_telephony, app):
    """PUT /api/network は netd を呼ばないこと（apply エンドポイントが明示呼び出し専用）。"""
    fake = _FakeNetdClient()
    _inject_fake_netd(app, fake)
    try:
        c = auth_client_with_telephony
        await c.put(
            "/api/network",
            json={
                "lan_interface": "eth0",
                "lan_ip": "192.168.1.1",
                "lan_prefix": 24,
                "dhcp_range_start": "192.168.1.100",
                "dhcp_range_end": "192.168.1.200",
                "dhcp_lease_hours": 12,
                "provisioning_base_url": "",
                "nat_enabled": True,
                "wan_interface": "",
                "tailscale_enabled": False,
            },
        )
        # PUT 後 apply は一度も呼ばれていないこと
        assert len(fake.apply_dhcp_calls) == 0
        assert len(fake.apply_nat_calls) == 0
    finally:
        _remove_fake_netd(app)
