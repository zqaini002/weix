"""平台相关 API：联系人列表、群聊列表、数据库状态。"""

import os

from fastapi import APIRouter, Depends, Query

from app.api.auth import verify_token
from app.core.platform import Platform
from app.utils.paths import get_data_dir

router = APIRouter(
    prefix="/api/platform",
    tags=["platform"],
    dependencies=[Depends(verify_token)],
)


def _normalize_db_key_path(path: str) -> str:
    return path.replace("\\", "/").lower()


def _key_matches_db_path(key_path: str, full_path: str) -> bool:
    normalized_key = _normalize_db_key_path(key_path)
    normalized_full = _normalize_db_key_path(full_path)
    basename = os.path.basename(full_path)
    if "/" in normalized_key:
        return normalized_full.endswith(normalized_key)
    return os.path.normcase(key_path) == os.path.normcase(basename)


@router.get("/contacts")
async def list_contacts(
    type: str = Query("all", pattern="^(all|contacts|chatrooms)$"),
    search: str = Query("", description="搜索关键词，为空则返回全部"),
):
    """获取微信联系人/群聊列表。

    从微信本地数据库读取，如果数据库不可用则返回空列表。

    Args:
        type: 返回类型。all=全部, contacts=联系人, chatrooms=仅群聊
        search: 模糊搜索昵称、备注、wxid。为空返回全部。
    """
    platform = Platform.get()
    contacts: list[dict] = []
    chatrooms: list[dict] = []
    error = ""

    try:
        extractor = platform.key_extractor
        # macOS 加载缓存密钥，Windows 加载缓存或重新提取
        if hasattr(extractor, "load_keys"):
            keys = extractor.load_keys()
        else:
            keys = getattr(extractor, "_keys", {})

        if not keys:
            # 尝试从 all_keys.json 手动加载
            import json
            cache = get_data_dir() / "all_keys.json"
            if cache.exists():
                with open(cache) as f:
                    keys = json.load(f)

        if not keys:
            error = "尚未提取数据库密钥。请以 sudo 启动后端进行密钥提取"
        else:
            from app.core.db_reader_macos import MacOSDBReader
            from app.core.db_reader_windows import WindowsDBReader

            if platform.is_macos:
                reader = MacOSDBReader()
            else:
                reader = WindowsDBReader()

            # 查找并匹配数据库
            def _find_db_key(target: str) -> tuple[str | None, str | None]:
                """在 all_dbs 中查找匹配 target 的数据库路径和密钥。

                target 可以是完整路径后缀 (如 'contact/contact.db')
                或文件名 (如 'message_0.db')。
                """
                if hasattr(reader, "find_database_files"):
                    for full_path in all_dbs:
                        for key_path, hex_key in keys.items():
                            if _key_matches_db_path(key_path, full_path):
                                if target in key_path or target in os.path.basename(full_path):
                                    return full_path, hex_key
                return None, None

            # 收集所有 DB 文件
            all_dbs: list[str] = []
            if hasattr(reader, "find_database_files"):
                all_dbs = reader.find_database_files()

            # 联系人和群聊从 contact.db 读取
            contact_db_path, contact_key = _find_db_key("contact.db")
            if not contact_db_path:
                # legacy fallback
                contact_db_path = extractor._find_msg_db() if hasattr(extractor, "_find_msg_db") else None
                contact_key = keys.get("MSG", "")

            result = _find_db_key("message_0.db")
            if not result[0]:
                result = _find_db_key("MSG.db")
            msg_db_path, msg_key = result
            if not msg_db_path:
                # legacy fallback (Windows)
                msg_db_path = extractor._find_msg_db() if hasattr(extractor, "_find_msg_db") else None
                msg_key = keys.get("MSG", list(keys.values())[0] if keys else "")

            if not msg_key:
                msg_key = list(keys.values())[0] if keys else ""
            if not contact_key:
                contact_key = list(keys.values())[0] if keys else ""

            if contact_key and contact_db_path:
                try:
                    reader.open_db(contact_db_path, bytes.fromhex(contact_key))
                    if type in ("all", "contacts"):
                        contacts = reader.get_contacts()
                    if type in ("all", "chatrooms"):
                        chatrooms = reader.get_chatrooms()
                except Exception as e:
                    error = f"联系人数据库解密失败: {e}"
            elif not contact_db_path:
                error = "未找到联系人数据库 (contact.db)"
    except Exception as e:
        error = str(e)

    # 服务端模糊搜索过滤
    s = search.strip().lower()
    if s:
        contacts = [
            c for c in contacts
            if s in (c.get("nickname", "") or "").lower()
            or s in (c.get("remark", "") or "").lower()
            or s in (c.get("alias", "") or "").lower()
            or s in (c.get("wxid", "") or "").lower()
        ]
        chatrooms = [
            r for r in chatrooms
            if s in (r.get("name", "") or "").lower()
            or s in (r.get("room_id", "") or "").lower()
        ]

    return {
        "contacts": contacts,
        "chatrooms": chatrooms,
        "total_contacts": len(contacts),
        "total_chatrooms": len(chatrooms),
        "ready": bool(not error),
        "error": error,
    }


@router.get("/status")
async def platform_status():
    """获取平台状态：微信进程、数据库、密钥提取。"""
    platform = Platform.get()
    import json

    wechat_running = await platform.sender.is_wechat_running()
    key_ready = (get_data_dir() / "all_keys.json").exists()

    return {
        "platform": platform.name,
        "wechat_running": wechat_running,
        "key_ready": key_ready,
        "db_ready": False,  # 需要实际尝试打开才能确认
    }
