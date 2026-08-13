from app.config import Config, load_config, save_config


def test_load_config_creates_defaults_when_missing(tmp_path):
    path = tmp_path / "config.yaml"
    config = load_config(path)
    assert config.casino_name == "CASSINO"
    assert path.exists()


def test_save_and_reload_roundtrip(tmp_path):
    path = tmp_path / "config.yaml"
    config = Config(casino_name="Cassino Teste", history_size=20)
    save_config(config, path)

    reloaded = load_config(path)
    assert reloaded.casino_name == "Cassino Teste"
    assert reloaded.history_size == 20


def test_validate_clamps_invalid_pin_to_default(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("admin_pin: 'abcd'\n", encoding="utf-8")
    config = load_config(path)
    assert config.admin_pin == "1234"


def test_validate_clamps_nonpositive_history_size(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("history_size: -5\n", encoding="utf-8")
    config = load_config(path)
    assert config.history_size == 1
