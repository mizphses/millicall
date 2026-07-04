import importlib

import millicall


def test_version() -> None:
    assert millicall.__version__ == "0.1.0"


def test_all_core_modules_importable() -> None:
    for name in (
        "telephony",
        "media",
        "ai",
        "workflows",
        "provisioning",
        "network",
        "auth",
        "mcp",
        "system",
        "extensions",
    ):
        module = importlib.import_module(f"millicall.{name}")
        assert module is not None
