"""微信机器人防检测模块。

封装已有的 RateLimiter、random_delay、CircuitBreaker 工具类，
提供统一的防检测接口，从配置文件读取参数。

用于规避微信客户端对机器人行为的检测:
- 频率限制: 防止发送过快
- 随机延迟: 模拟人类打字节奏
- 熔断保护: 连续失败时暂停发送
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Optional, Union

from app.config import get_config
from app.utils.rate_limiter import (
    CircuitBreaker,
    RateLimiter,
    random_delay,
)

logger = logging.getLogger(__name__)


@dataclass
class AntiDetectConfig:
    """防检测配置参数。"""

    # 频率限制
    max_messages_per_minute: int = 20
    min_send_interval: float = 15.0  # 最小发送间隔 (秒)
    max_send_interval: float = 45.0  # 最大发送间隔 (秒)

    # 会话冷却
    cooldown_per_session: float = 30.0  # 每个会话冷却时间 (秒)

    # 熔断器
    circuit_breaker_threshold: int = 3  # 连续失败阈值
    circuit_breaker_cooldown: float = 300.0  # 熔断冷却时间 (秒)

    # 平台特定最小间隔
    platform_min_interval: float = 8.0


class AntiDetect:
    """防检测管理器。

    封装 RateLimiter、随机延迟、CircuitBreaker，
    提供统一的消息发送前检查和发送后反馈接口。

    使用方式:
        anti = AntiDetect.from_config()
        if anti.before_send("wxid_xxx"):
            result = await sender.send_text(...)
            if result:
                anti.after_send_success()
            else:
                anti.after_send_failure()
    """

    def __init__(self, config: Optional[AntiDetectConfig] = None):
        """
        Args:
            config: 防检测配置，为 None 时从全局配置读取。
        """
        if config is None:
            config = AntiDetect._load_from_app_config()

        self._config = config

        # 初始化内部组件
        self._rate_limiter = RateLimiter(
            max_per_minute=config.max_messages_per_minute,
        )
        self._circuit_breaker = CircuitBreaker(
            threshold=config.circuit_breaker_threshold,
            cooldown=config.circuit_breaker_cooldown,
        )

        # 运行时状态
        self._last_send_times: dict[str, float] = {}
        self._session_counts: dict[str, int] = {}
        self._total_sent: int = 0
        self._total_blocked: int = 0

    @classmethod
    def from_config(cls) -> "AntiDetect":
        """从全局应用配置创建实例。"""
        return cls(AntiDetect._load_from_app_config())

    # --- 公共接口 ---

    def before_send(self, session_key: str) -> bool:
        """发送前检查: 频率限制 + 随机延迟。

        Args:
            session_key: 会话标识 (wxid 或 room_id)。

        Returns:
            True 表示允许发送，False 表示被限制。
        """
        # 1. 检查熔断器
        if self.is_blocked():
            logger.warning(
                f"熔断器开启，拒绝向 {session_key[:20]}... 发送"
            )
            self._total_blocked += 1
            return False

        # 2. 检查频率限制
        if not self._rate_limiter.can_send(
            session_key, cooldown=self._config.cooldown_per_session
        ):
            logger.debug(
                f"频率限制触发，拒绝向 {session_key[:20]}... 发送"
            )
            self._total_blocked += 1
            return False

        # 3. 执行随机延迟 (使用平台配置的最小间隔)
        self._apply_random_delay()
        self._last_send_times[session_key] = time.monotonic()
        self._session_counts[session_key] = (
            self._session_counts.get(session_key, 0) + 1
        )

        return True

    async def before_send_async(self, session_key: str) -> bool:
        """发送前检查的异步版本。

        使用 asyncio.sleep 代替 time.sleep 执行延迟，
        避免阻塞 event loop。

        Args:
            session_key: 会话标识。

        Returns:
            True 表示允许发送。
        """
        if self.is_blocked():
            logger.warning(
                f"熔断器开启，拒绝向 {session_key[:20]}... 发送"
            )
            self._total_blocked += 1
            return False

        if not self._rate_limiter.can_send(
            session_key, cooldown=self._config.cooldown_per_session
        ):
            logger.debug(
                f"频率限制触发，拒绝向 {session_key[:20]}... 发送"
            )
            self._total_blocked += 1
            return False

        # 异步延迟
        delay = random.uniform(
            self._config.min_send_interval,
            self._config.max_send_interval,
        )
        logger.debug(f"防检测异步延迟: {delay:.1f}s")
        await asyncio.sleep(delay)

        self._last_send_times[session_key] = time.monotonic()
        self._session_counts[session_key] = (
            self._session_counts.get(session_key, 0) + 1
        )
        return True

    def after_send_success(self) -> None:
        """发送成功后调用，重置熔断器失败计数。"""
        self._circuit_breaker.record_success()
        self._total_sent += 1
        logger.debug("发送成功，熔断器已记录")

    def after_send_failure(self) -> None:
        """发送失败后调用，增加熔断器失败计数。"""
        self._circuit_breaker.record_failure()
        logger.warning(
            f"发送失败，熔断器失败计数: {self._circuit_breaker.failures}"
        )

    def is_blocked(self) -> bool:
        """检查熔断器是否开启。

        Returns:
            True 表示当前不允许发送。
        """
        return self._circuit_breaker.is_open()

    def reset(self) -> None:
        """重置所有限制器状态 (用于测试或手动恢复)。"""
        self._circuit_breaker.record_success()
        self._last_send_times.clear()
        self._session_counts.clear()
        self._total_blocked = 0
        logger.info("防检测模块已重置")

    # --- 属性 ---

    @property
    def stats(self) -> dict:
        """获取统计信息。"""
        return {
            "total_sent": self._total_sent,
            "total_blocked": self._total_blocked,
            "circuit_open": self._circuit_breaker.open,
            "circuit_failures": self._circuit_breaker.failures,
            "active_sessions": len(self._session_counts),
        }

    @property
    def is_circuit_open(self) -> bool:
        return self._circuit_breaker.open

    # --- 内部方法 ---

    def _apply_random_delay(self) -> None:
        """应用随机延迟模拟人类行为。

        使用平台最小间隔覆盖配置中的最小间隔。
        """
        min_interval = max(
            self._config.min_send_interval,
            self._config.platform_min_interval,
            self._config.max_messages_per_minute / 60.0,  # 确保不超过频率限制
        )
        max_interval = self._config.max_send_interval

        if max_interval < min_interval:
            max_interval = min_interval + random.uniform(0, 5)

        random_delay(min_interval, max_interval)

    @staticmethod
    def _load_from_app_config() -> AntiDetectConfig:
        """从全局应用配置加载防检测参数。"""
        app_config = get_config()
        anti_cfg = (
            app_config.anti_detect
            if hasattr(app_config, "anti_detect")
            else {}
        )

        # 平台特定配置
        macos_min = 8.0
        is_macos = app_config.get_platform() == "darwin"
        if is_macos and "macos" in anti_cfg:
            macos_cfg = anti_cfg["macos"]
            macos_min = float(macos_cfg.get("min_send_interval", 8.0))

        return AntiDetectConfig(
            max_messages_per_minute=int(
                anti_cfg.get("max_messages_per_minute", 20)
            ),
            min_send_interval=float(
                anti_cfg.get("min_send_interval", 15)
            ),
            max_send_interval=float(
                anti_cfg.get("max_send_interval", 45)
            ),
            cooldown_per_session=float(
                anti_cfg.get("cooldown_per_session", 30)
            ),
            circuit_breaker_threshold=int(
                anti_cfg.get("circuit_breaker_threshold", 3)
            ),
            platform_min_interval=macos_min,
        )

    # --- 便捷方法 ---

    def get_delay_range(self) -> tuple[float, float]:
        """获取当前有效的延迟范围。

        Returns:
            (min_delay, max_delay) 元组。
        """
        return (
            self._config.min_send_interval,
            self._config.max_send_interval,
        )

    def can_send_immediately(self, session_key: str) -> bool:
        """检查是否可以立即发送 (不执行延迟)。

        Args:
            session_key: 会话标识。

        Returns:
            True 表示不受速率限制。
        """
        if self.is_blocked():
            return False
        return self._rate_limiter.can_send(
            session_key, cooldown=self._config.cooldown_per_session
        )

    def estimate_wait_time(self, session_key: str) -> float:
        """估算需要等待的时间。

        Args:
            session_key: 会话标识。

        Returns:
            估计等待秒数，0 表示可立即发送。
        """
        if self.is_blocked():
            return self._config.circuit_breaker_cooldown

        if self.can_send_immediately(session_key):
            return 0.0

        return self._config.cooldown_per_session
