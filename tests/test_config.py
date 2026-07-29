"""测试 config.py — 环境变量加载和向后兼容。"""

import os
from unittest import mock

from src.config import Config, ConfigError, _mask_email, get_config


class TestMaskEmail:
    def test_normal_email(self):
        assert _mask_email("hello@gmail.com") == "hel***@gmail.com"

    def test_short_local(self):
        assert _mask_email("a@b.com") == "a***@b.com"

    def test_no_at(self):
        assert _mask_email("noemail") == "no***"


class TestConfigValidation:
    def test_missing_email_raises(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            c = Config()
            c.email = ""
            try:
                c.validate()
                assert False, "should raise"
            except ConfigError as e:
                assert "NEURUN_EMAIL" in str(e)

    def test_missing_password_raises(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            c = Config()
            c.email = "test@test.com"
            c.password = ""
            try:
                c.validate()
                assert False, "should raise"
            except ConfigError as e:
                assert "NEURUN_PASSWORD" in str(e)

    def test_valid_config_passes(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            c = Config()
            c.email = "test@test.com"
            c.password = "secret"
            c.validate()  # should not raise


class TestBackwardCompat:
    def test_garmin_fallback(self):
        """旧 GARMIN_EMAIL 仍能读取。"""
        with mock.patch.dict(os.environ, {
            "GARMIN_EMAIL": "old@test.com",
            "GARMIN_PASSWORD": "oldpw",
        }, clear=True):
            c = Config()
            assert c.email == "old@test.com"
            assert c.password == "oldpw"

    def test_rundown_preferred(self):
        """新 NEURUN_EMAIL 优先于旧 GARMIN_EMAIL。"""
        with mock.patch.dict(os.environ, {
            "NEURUN_EMAIL": "new@test.com",
            "GARMIN_EMAIL": "old@test.com",
        }, clear=True):
            c = Config()
            assert c.email == "new@test.com"


class TestProviderConfig:
    def test_default_provider(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            c = Config()
            assert c.provider_type == "garmin"

    def test_coros_provider(self):
        with mock.patch.dict(os.environ, {"NEURUN_PROVIDER": "coros"}, clear=True):
            c = Config()
            assert c.provider_type == "coros"

    def test_coros_credential_key_is_forwarded_to_web_user(self):
        with mock.patch.dict(os.environ, {
            "NEURUN_COROS_CREDENTIAL_KEY": "private-fernet-key",
        }, clear=True):
            config = Config()

        assert config.coros_credential_key == "private-fernet-key"
        assert config.for_user("rd_test").coros_credential_key == "private-fernet-key"

    def test_huawei_uses_group_pals_token_not_password(self):
        with mock.patch.dict(os.environ, {
            "NEURUN_PROVIDER": "huawei",
            "GROUP_PALS_TOKEN": "group-token",
        }, clear=True):
            c = Config()
            c.validate()
            assert c.email == ""
            assert c.password == ""
            assert "group-token" not in c.huawei_token_dir
            assert c.huawei_token_dir.endswith("/huawei-tokens")

    def test_web_user_provider_overrides_server_default(self):
        """Web 多用户同步必须使用用户绑定的平台，而不是服务级默认值。"""
        with mock.patch.dict(os.environ, {}, clear=True):
            user_config = Config().for_user("rd_test")

        assert user_config.provider_type == "garmin"
        user_config.provider = "coros"
        assert user_config.provider_type == "coros"

    def test_huawei_web_user_credential_persists_privately(self, tmp_path):
        import stat

        config = Config(data_dir=str(tmp_path))
        user_config = config.for_user("rd_huawei")

        user_config.set_group_pals_token("private-group-token")

        restored = config.for_user("rd_huawei")
        credential_path = tmp_path / "rd_huawei" / "huawei-tokens" / "group-pals-token"
        assert restored.group_pals_token == "private-group-token"
        assert stat.S_IMODE(credential_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(credential_path.stat().st_mode) == 0o600


class TestInviteCodeConfig:
    def test_invite_codes_default_to_web_data_dir(self, tmp_path):
        config = Config(data_dir=str(tmp_path))
        assert config.invite_codes_path == str(tmp_path / "invite-codes.json")

    def test_explicit_invite_codes_file_wins(self, tmp_path):
        custom = tmp_path / "private" / "codes.json"
        config = Config(data_dir=str(tmp_path), invite_codes_file=str(custom))
        assert config.invite_codes_path == str(custom)


class TestWebSyncCapacityConfig:
    def test_defaults_support_one_hundred_admitted_users(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            config = Config()

        assert config.sync_max_concurrency == 4
        assert config.sync_max_pending == 100

    def test_environment_overrides_capacity(self):
        with mock.patch.dict(os.environ, {
            "NEURUN_SYNC_MAX_CONCURRENCY": "6",
            "NEURUN_SYNC_MAX_PENDING": "120",
        }, clear=True):
            config = Config()

        assert config.sync_max_concurrency == 6
        assert config.sync_max_pending == 120


def test_rundown_home_does_not_inherit_global_user_credentials(tmp_path):
    user_home = tmp_path / "user-a"
    user_home.mkdir()
    (user_home / ".env").write_text(
        "NEURUN_PROVIDER=huawei\nGROUP_PALS_TOKEN=user-token\n",
        encoding="utf-8",
    )
    fake_home = tmp_path / "system-home"
    global_dir = fake_home / ".neurun"
    global_dir.mkdir(parents=True)
    (global_dir / ".env").write_text(
        "NEURUN_ACCOUNT=other-user@example.com\nNEURUN_PASSWORD=other-password\n",
        encoding="utf-8",
    )

    with mock.patch.dict(os.environ, {"NEURUN_HOME": str(user_home)}, clear=True), \
            mock.patch("src.config.Path.home", return_value=fake_home):
        config = get_config()

    assert config.provider_type == "huawei"
    assert config.email == ""
    assert config.password == ""
    assert config.data_dir == str(user_home / "data")
    assert config.invite_codes_path == str(user_home / "data" / "invite-codes.json")
