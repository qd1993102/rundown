"""数据源 Provider 抽象层。

支持: garmin (默认) | coros | huawei
"""

from .base import DataProvider, ActivityData, DailyHealth


def get_provider(config) -> DataProvider:
    """根据配置创建对应的数据源 Provider。"""
    provider_type = getattr(config, 'provider_type', 'garmin')

    if provider_type == "coros":
        from .coros import CorosProvider
        return CorosProvider(config)
    elif provider_type == "huawei":
        from .huawei import HuaweiProvider
        return HuaweiProvider(config)
    else:
        from .garmin import GarminProvider
        return GarminProvider(config)
