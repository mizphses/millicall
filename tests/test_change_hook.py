async def test_create_extension_writes_fs_config(auth_client_with_telephony, app) -> None:
    resp = await auth_client_with_telephony.post(
        "/api/extensions", json={"number": "1001", "display_name": "Alice"}
    )
    assert resp.status_code == 201
    fs_dir = app.state.settings.fs_config_dir
    assert (fs_dir / "directory" / "default" / "1001.xml").exists()
    assert (fs_dir / "sip_profiles" / "internal.xml").exists()


async def test_delete_extension_removes_user_file(auth_client_with_telephony, app) -> None:
    created = await auth_client_with_telephony.post(
        "/api/extensions", json={"number": "1002", "display_name": "Bob"}
    )
    ext_id = created.json()["id"]
    fs_dir = app.state.settings.fs_config_dir
    assert (fs_dir / "directory" / "default" / "1002.xml").exists()
    await auth_client_with_telephony.delete(f"/api/extensions/{ext_id}")
    assert not (fs_dir / "directory" / "default" / "1002.xml").exists()


async def test_initial_config_written_on_startup(app) -> None:
    # lifespan 起動時に内線ゼロでも静的設定が生成される
    fs_dir = app.state.settings.fs_config_dir
    assert (fs_dir / "autoload_configs" / "event_socket.conf.xml").exists()
