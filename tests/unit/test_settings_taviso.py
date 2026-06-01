"""Unit tests for parsing the external taviso MySQL settings from the environment."""

from settings import Settings


def test_taviso_settings_parsed_from_env(monkeypatch):
    monkeypatch.setenv("MYSQL_TAVISO_HOST", "taviso-host")
    monkeypatch.setenv("MYSQL_TAVISO_PORT", "3307")
    monkeypatch.setenv("MYSQL_TAVISO_DATABASE", "taviso_externa")
    monkeypatch.setenv("MYSQL_TAVISO_USER", "taviso_ro")
    monkeypatch.setenv("MYSQL_TAVISO_PASSWORD", "secret")

    settings = Settings()

    assert settings.mysql_taviso_host == "taviso-host"
    assert settings.mysql_taviso_port == 3307
    assert settings.mysql_taviso_database == "taviso_externa"
    assert settings.mysql_taviso_user == "taviso_ro"
    assert settings.mysql_taviso_password == "secret"


def test_taviso_settings_have_safe_defaults(monkeypatch):
    for var in (
        "MYSQL_TAVISO_HOST",
        "MYSQL_TAVISO_PORT",
        "MYSQL_TAVISO_DATABASE",
        "MYSQL_TAVISO_USER",
        "MYSQL_TAVISO_PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)

    settings = Settings()

    assert settings.mysql_taviso_host == ""
    assert settings.mysql_taviso_port == 3306
    assert settings.mysql_taviso_database == ""
    assert settings.mysql_taviso_user == ""
    assert settings.mysql_taviso_password == ""
