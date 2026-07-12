"""子LAN（NetworkConfig）DB 状態に応じた internal プロファイル切り替えの結合テスト。

regenerate は DB の NetworkConfig（id=1）を読み、applied=True かつ lan_ip 非空なら
internal のバインドIP／ドメインと directory・dialplan のドメインを子LAN GW IP に
揃える。未適用（applied=False／行なし）なら従来どおり sip_bind_ip / sip_domain を使う。
"""

from millicall.models import NetworkConfig


async def _regenerate(app) -> None:
    """listener.regenerate を DB セッションで直接呼び、FS 設定を再生成する。"""
    listener = app.state.change_listener
    sm = app.state.sessionmaker
    async with sm() as session:
        await listener.regenerate(session)


async def _set_network_config(app, *, lan_ip: str, applied: bool) -> None:
    sm = app.state.sessionmaker
    async with sm() as session:
        cfg = await session.get(NetworkConfig, 1)
        if cfg is None:
            cfg = NetworkConfig(id=1)
            session.add(cfg)
        cfg.lan_ip = lan_ip
        cfg.applied = applied
        await session.commit()


async def test_regenerate_uses_child_lan_ip_when_applied(app) -> None:
    """子LAN applied=True: internal の sip-ip とドメイン、dialplan が子LAN GW IP になる。"""
    await _set_network_config(app, lan_ip="172.20.0.1", applied=True)
    await _regenerate(app)

    fs_dir = app.state.settings.fs_config_dir
    internal = (fs_dir / "sip_profiles" / "internal.xml").read_text()
    assert '<param name="sip-ip" value="172.20.0.1"/>' in internal
    assert '<param name="rtp-ip" value="172.20.0.1"/>' in internal
    assert 'name="172.20.0.1"' in internal

    directory = (fs_dir / "directory" / "default.xml").read_text()
    assert '<domain name="172.20.0.1">' in directory

    dialplan = (fs_dir / "dialplan" / "default.xml").read_text()
    assert "user/${destination_number}@172.20.0.1" in dialplan


async def test_regenerate_falls_back_when_not_applied(app) -> None:
    """applied=False: 子LAN IP を使わず従来動作（sip_domain）を維持する。"""
    settings = app.state.settings
    await _set_network_config(app, lan_ip="172.20.0.1", applied=False)
    await _regenerate(app)

    fs_dir = settings.fs_config_dir
    directory = (fs_dir / "directory" / "default.xml").read_text()
    # 未適用なので子LAN IP はドメインに現れない
    assert "172.20.0.1" not in directory
    assert f'<domain name="{settings.sip_domain}">' in directory


async def test_regenerate_falls_back_when_no_network_config_row(app) -> None:
    """NetworkConfig 行が無い場合も従来動作（後方互換）。"""
    settings = app.state.settings
    # 行を作らずに regenerate（デフォルト app fixture では未作成）
    await _regenerate(app)

    fs_dir = settings.fs_config_dir
    directory = (fs_dir / "directory" / "default.xml").read_text()
    assert f'<domain name="{settings.sip_domain}">' in directory
