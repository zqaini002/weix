"""Explicit Windows live probe; never sends unless --send is supplied.

Uses the production search/title/compose/send operations. Incoming and outgoing
message content is verified locally from the database, without uploading chat
history to the vision service. Stop on the first failure; never resend an
unconfirmed message automatically.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path


def resolve_contact(contacts: list[dict], requested: str) -> tuple[str, str]:
    matches = {}
    for contact in contacts:
        wxid = str(contact.get("wxid", ""))
        if not wxid or wxid.endswith("@chatroom"):
            continue
        if requested not in [contact.get(field) for field in ("wxid", "nickname", "remark", "alias")]:
            continue
        title = contact.get("remark") or contact.get("nickname") or contact.get("alias") or wxid
        matches[wxid] = str(title)
    if len(matches) != 1:
        raise ValueError(f"联系人必须唯一，当前匹配 {len(matches)} 个；已停止测试")
    return next(iter(matches.items()))


def make_probe_sender(vision):
    from app.core.sender_windows import WindowsSender

    sender = WindowsSender(vision)
    sender._verify_after_send = True
    return sender


async def run(args):
    import yaml
    from dotenv import dotenv_values
    from app import config as config_module
    from app.config import Config
    from app.core.platform import Platform
    from app.core.sender_windows import WindowsSender
    from app.core.vision_client import VisionClient

    package = args.package_dir.resolve()
    raw = yaml.safe_load((package / "config/config.yaml").read_text(encoding="utf-8"))
    env = {**dotenv_values(package / ".env"), **os.environ}
    config_module._config = Config(**Config._resolve_env(raw, env))
    Platform._instance = None
    platform = Platform.get()
    if not platform.is_windows:
        raise RuntimeError("这个实机测试脚本只用于 Windows")
    keys = platform.key_extractor.validate_cached_keys(json.loads(args.keys.read_text(encoding="utf-8")))
    contacts = []
    reader = platform.db_reader.__class__()
    for db_path in reader.find_database_files():
        if not db_path.endswith("contact.db"):
            continue
        for key_path, key in keys.items():
            if WindowsSender._key_matches_db_path(key_path, db_path) and reader.open_db(db_path, bytes.fromhex(key)):
                contacts.extend(reader.get_contacts())
                reader.close()
    requested_names = [args.receiver]
    if args.alternate_with and args.alternate_with != args.receiver:
        requested_names.append(args.alternate_with)
    targets = []
    for requested in requested_names:
        target_id, title = resolve_contact(contacts, requested)
        targets.append({"requested": requested, "search_title": title, "target_id": target_id})
    if len({t["target_id"] for t in targets}) != len(targets):
        raise ValueError("往返测试必须是两个不同的联系人")
    report = {"targets": targets, "send_enabled": args.send, "attempts": []}
    args.report.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    save()
    print(json.dumps(report, ensure_ascii=False), flush=True)
    if not args.allow_vision:
        print("仅核对数据库联系人；未截图、未发送。", flush=True)
        return
    api_key = config_module.get_config().ai.get("api_key", "")
    if not api_key:
        raise RuntimeError("未配置视觉模型密钥")
    sender = make_probe_sender(VisionClient(api_key))
    # The independent probe uses the provided cache, rather than another
    # package's cache, for the production database verification routine.
    original_find_key = sender._find_message_db_key

    def find_message_key():
        for db_path in reader.find_database_files():
            if os.path.basename(db_path) == "message_0.db":
                for key_path, key in keys.items():
                    if WindowsSender._key_matches_db_path(key_path, db_path):
                        return db_path, key
        return "", ""

    WindowsSender._find_message_db_key = staticmethod(find_message_key)
    try:
        if not args.send:
            verified = []
            for target in targets:
                verified.append(bool(await sender.inspect_chat(target["search_title"], [target["search_title"]])))
            report["location_verified"] = verified
            save()
            if not all(verified):
                raise RuntimeError("定位或聊天标题确认失败")
            return
        for round_number in range(1, 4):
            for target in targets:
                text = (f"【Weix切换测试】{target['requested']} {round_number}" if len(targets) > 1
                        else f"【Weix定位测试】{round_number}")
                confirmed = await sender.send_text(text, target["search_title"], target_id=target["target_id"])
                report["attempts"].append({"text": text, "target_id": target["target_id"],
                                           "sent_and_db_confirmed": confirmed})
                save()
                print(json.dumps(report["attempts"][-1], ensure_ascii=False), flush=True)
                if not confirmed:
                    raise RuntimeError("测试未通过，已停止后续发送；不会自动重发")
    finally:
        WindowsSender._find_message_db_key = staticmethod(original_find_key)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parent.parent
    parser.add_argument("--receiver", required=True)
    parser.add_argument("--alternate-with")
    parser.add_argument("--package-dir", type=Path, default=root)
    parser.add_argument("--keys", type=Path, default=root / "data/all_keys.json")
    parser.add_argument("--report", type=Path, default=root / "data/live-reply-probe.json")
    parser.add_argument("--allow-vision", action="store_true")
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()
    if args.send and not args.allow_vision:
        parser.error("发送测试必须同时启用 --allow-vision")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
