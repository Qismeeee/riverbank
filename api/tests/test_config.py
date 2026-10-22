import importlib
import sys


def _reload(monkeypatch, **env):
    for k in ("RIVERBANK_ENV", "ADDLIVETAG_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    sys.modules.pop("config", None)
    return importlib.import_module("config")


def test_optional_secret_missing_does_not_crash_startup(monkeypatch):
    import config as cfg

    def boom(secret_id):
        raise RuntimeError(f"ResourceNotFoundException {secret_id}")

    monkeypatch.setattr(cfg, "_secret", boom)
    monkeypatch.setattr(cfg, "ENV", "prod")
    monkeypatch.delenv("X", raising=False)
    assert cfg._from_env_or_secret("X", "riverbank/x", "X", required=False) == ""
    try:
        cfg._from_env_or_secret("X", "riverbank/x", "X")
    except RuntimeError:
        pass
    else:
        raise AssertionError("secret bắt buộc phải raise")


def test_local_env_never_touches_secrets(monkeypatch):
    cfg = _reload(monkeypatch, RIVERBANK_ENV="local")
    assert cfg.ADDLIVETAG_API_KEY == "" and cfg.OPENAI_API_KEY == ""
