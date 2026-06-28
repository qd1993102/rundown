"""测试 config.py — 环境变量加载和向后兼容。"""

import os
from unittest import mock

from src.config import Config, ConfigError, _mask_email


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
                assert "RUNDOWN_EMAIL" in str(e)

    def test_missing_password_raises(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            c = Config()
            c.email = "test@test.com"
            c.password = ""
            try:
                c.validate()
                assert False, "should raise"
            except ConfigError as e:
                assert "RUNDOWN_PASSWORD" in str(e)

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
        """新 RUNDOWN_EMAIL 优先于旧 GARMIN_EMAIL。"""
        with mock.patch.dict(os.environ, {
            "RUNDOWN_EMAIL": "new@test.com",
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
        with mock.patch.dict(os.environ, {"RUNDOWN_PROVIDER": "coros"}, clear=True):
            c = Config()
            assert c.provider_type == "coros"
