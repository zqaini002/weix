"""Windows 平台 WeChat 消息发送器。

通过 httpx 异步 HTTP 客户端调用 WeChatFerry (WCF) HTTP API，
实现消息发送功能。
"""

import asyncio
import logging
import os
from typing import Optional

import httpx

from app.core.base import BaseMessageSender
from app.config import get_config

logger = logging.getLogger(__name__)

# WCF API 端点
ENDPOINT_SEND_TXT = "/wcf/send_txt"
ENDPOINT_SEND_IMG = "/wcf/send_img"
ENDPOINT_IS_LOGIN = "/wcf/is_login"
ENDPOINT_GET_CONTACTS = "/wcf/get_contacts"
ENDPOINT_GET_INFO = "/wcf/get_self_info"


class WindowsSender(BaseMessageSender):
    """Windows 平台消息发送器。

    通过 WeChatFerry HTTP API 控制微信客户端发送消息。
    支持自动重试和错误处理。
    """

    def __init__(self, max_retries: int = 3):
        config = get_config()
        wcf_cfg = config.wcf if hasattr(config, "wcf") else {}

        self._host = wcf_cfg.get("host", "127.0.0.1")
        self._port = wcf_cfg.get("port", 10010)
        self._base_url = f"http://{self._host}:{self._port}"
        self._max_retries = max_retries
        self._client: Optional[httpx.AsyncClient] = None

    # --- 公共接口 ---

    async def send_text(
        self,
        msg: str,
        receiver: str,
        aters: str = "",
        force_skip: bool = False,
        is_group: bool = False,
    ) -> bool:
        """发送文本消息。

        Args:
            msg: 消息内容。
            receiver: 接收者 wxid 或群聊 id。
            aters: 需要 @ 的用户 wxid，多个以逗号分隔。
            force_skip: 兼容 macOS 接口，Windows 下忽略。
            is_group: 是否为群聊，用于自动设置 aters。

        Returns:
            True 表示发送成功。
        """
        if not msg or not receiver:
            logger.error("消息内容或接收者为空")
            return False

        # 群聊场景下，ats 参数为空时自动 @ 所有人
        actual_aters = aters
        if is_group and not actual_aters:
            actual_aters = "notify@all"

        payload = {
            "msg": msg,
            "receiver": receiver,
            "aters": actual_aters,
        }

        return await self._post_with_retry(
            ENDPOINT_SEND_TXT, payload, "发送文本消息"
        )

    async def open_chat(self, receiver: str) -> bool:
        """打开指定聊天（Windows WCF 无需此操作，始终返回 True）。"""
        logger.debug(f"Windows 平台无需手动打开聊天: {receiver}")
        return True

    def reset_search_state(self) -> None:
        """重置搜索状态（Windows WCF 无需此操作）。"""
        pass

    async def send_image(self, path: str, receiver: str) -> bool:
        """发送图片消息。

        Args:
            path: 图片文件路径。
            receiver: 接收者 wxid 或群聊 id。

        Returns:
            True 表示发送成功。
        """
        if not os.path.exists(path):
            logger.error(f"图片文件不存在: {path}")
            return False

        if not receiver:
            logger.error("接收者为空")
            return False

        payload = {
            "path": path,
            "receiver": receiver,
        }

        return await self._post_with_retry(
            ENDPOINT_SEND_IMG, payload, "发送图片消息"
        )

    async def is_wechat_running(self) -> bool:
        """检查微信是否在线（已登录）。

        Returns:
            True 表示微信已登录且就绪。
        """
        try:
            client = await self._get_client()
            response = await client.post(
                f"{self._base_url}{ENDPOINT_IS_LOGIN}",
                timeout=httpx.Timeout(5.0),
            )
            if response.status_code == 200:
                data = response.json()
                is_login = data.get("status") == 1 or data.get("data", {}).get(
                    "status"
                ) == 1
                return bool(is_login)
            return False
        except httpx.ConnectError:
            logger.debug("WCF 服务未连接")
            return False
        except Exception as exc:
            logger.error(f"检查微信状态失败: {exc}")
            return False

    async def get_contacts(self) -> list[dict]:
        """获取联系人列表 (通过 WCF API)。

        Returns:
            联系人字典列表。
        """
        try:
            client = await self._get_client()
            response = await client.post(
                f"{self._base_url}{ENDPOINT_GET_CONTACTS}",
                timeout=httpx.Timeout(10.0),
            )
            if response.status_code == 200:
                data = response.json()
                contacts = data.get("data", data.get("contacts", []))
                if isinstance(contacts, list):
                    return contacts
            return []
        except Exception as exc:
            logger.error(f"获取联系人列表失败: {exc}")
            return []

    async def get_self_info(self) -> dict:
        """获取自己的微信信息。

        Returns:
            包含 wxid, name 等字段的字典。
        """
        try:
            client = await self._get_client()
            response = await client.post(
                f"{self._base_url}{ENDPOINT_GET_INFO}",
                timeout=httpx.Timeout(5.0),
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("data", {})
            return {}
        except Exception as exc:
            logger.error(f"获取自身信息失败: {exc}")
            return {}

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        if self._client:
            await self._client.aclose()
            self._client = None

    # --- 内部方法 ---

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 httpx 异步客户端。"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                limits=httpx.Limits(max_keepalive_connections=5),
            )
        return self._client

    async def _post_with_retry(
        self, endpoint: str, payload: dict, operation: str
    ) -> bool:
        """带重试的 HTTP POST 请求。

        Args:
            endpoint: API 端点。
            payload: 请求体。
            operation: 操作描述 (用于日志)。

        Returns:
            True 表示请求成功。
        """
        client = await self._get_client()
        url = f"{self._base_url}{endpoint}"

        for attempt in range(1, self._max_retries + 1):
            try:
                logger.debug(
                    f"{operation} (尝试 {attempt}/{self._max_retries}): "
                    f"receiver={payload.get('receiver', '')[:20]}..."
                )

                response = await client.post(url, json=payload)

                if response.status_code == 200:
                    data = response.json()
                    status = data.get("status", -1)
                    if status == 0 or status == 1:
                        logger.info(f"{operation} 成功")
                        return True
                    else:
                        logger.warning(
                            f"{operation} 返回非成功状态: status={status}, "
                            f"message={data.get('message', '')}"
                        )
                        # 某些错误不需要重试
                        if status in (-1, -2):  # 参数错误
                            return False
                else:
                    logger.warning(
                        f"{operation} HTTP {response.status_code}: "
                        f"{response.text[:200]}"
                    )

            except httpx.TimeoutException:
                logger.warning(
                    f"{operation} 超时 (尝试 {attempt}/{self._max_retries})"
                )
            except httpx.ConnectError:
                logger.warning(
                    f"{operation} 连接失败 - WCF 服务可能未启动"
                )
            except Exception as exc:
                logger.error(f"{operation} 异常: {exc}")

            if attempt < self._max_retries:
                wait_time = 2 ** attempt  # 指数退避: 2s, 4s, 8s
                logger.debug(f"等待 {wait_time}s 后重试...")
                await asyncio.sleep(wait_time)

        logger.error(f"{operation} 失败，已达最大重试次数")
        return False
