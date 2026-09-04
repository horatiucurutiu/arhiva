import configparser
import os
import stat

from scripts.set_password import hash_password, write_credentials


def test_hash_password_produces_verifiable_bcrypt_hash():
    import bcrypt

    hashed = hash_password("hunter2")
    assert bcrypt.checkpw(b"hunter2", hashed.encode())


def test_write_credentials_creates_auth_section(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text("[Paths]\nVIDEO_DIR = /tmp\n")

    write_credentials(str(config_path), "alice", "hunter2")

    config = configparser.ConfigParser()
    config.read(config_path)
    assert config["Auth"]["USERNAME"] == "alice"
    assert config["Auth"]["SECRET_KEY"]
    import bcrypt
    assert bcrypt.checkpw(b"hunter2", config["Auth"]["PASSWORD_HASH"].encode())


def test_write_credentials_preserves_existing_secret_key(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text("[Auth]\nSECRET_KEY = keep-me\n")

    write_credentials(str(config_path), "alice", "hunter2")

    config = configparser.ConfigParser()
    config.read(config_path)
    assert config["Auth"]["SECRET_KEY"] == "keep-me"


def test_write_credentials_restricts_config_permissions(tmp_path):
    """config.ini holds SECRET_KEY (signs session cookies) — must not be world-readable."""
    config_path = tmp_path / "config.ini"
    config_path.write_text("[Paths]\nVIDEO_DIR = /tmp\n")
    os.chmod(config_path, 0o644)

    write_credentials(str(config_path), "alice", "hunter2")

    mode = stat.S_IMODE(os.stat(config_path).st_mode)
    assert mode == 0o600
