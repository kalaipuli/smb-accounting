from src.main import get_app_info


def test_get_app_info_returns_name_and_version() -> None:
    info = get_app_info()

    assert info["name"] == "SMB Account Software"
    assert info["version"]
