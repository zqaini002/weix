r"""Diagnose Windows Weixin database key extraction.

Run from the project root:

    .\venv\Scripts\python.exe backend\diagnose_weixin_windows.py

The script does not print full database keys. It reports path/process status
and validates any extracted key against message_0.db.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.db_reader_windows import WindowsDBReader
from app.core.key_extractor_windows import WindowsKeyExtractor


def _extract_out_arg() -> Path | None:
    for i, arg in enumerate(list(sys.argv)):
        if arg == "--out" and i + 1 < len(sys.argv):
            path = Path(sys.argv[i + 1])
            del sys.argv[i:i + 2]
            return path
        if arg.startswith("--out="):
            sys.argv.remove(arg)
            return Path(arg.split("=", 1)[1])
    return None


def _mask(hex_value: str) -> str:
    if len(hex_value) <= 16:
        return hex_value
    return f"{hex_value[:8]}...{hex_value[-8:]}"


def _print_key_info_files() -> None:
    appdata = os.getenv("APPDATA", "")
    login_dir = Path(appdata) / "Tencent" / "xwechat" / "login" if appdata else None
    if not login_dir or not login_dir.is_dir():
        print("key_info.dat: not_found")
        return
    files = sorted(login_dir.glob("*/key_info.dat"))
    if not files:
        print("key_info.dat: not_found")
        return
    for path in files:
        data = path.read_bytes()
        print(
            "key_info.dat:",
            path,
            "size=", len(data),
            "md5=", hashlib.md5(data).hexdigest(),
            "sha256=", hashlib.sha256(data).hexdigest(),
        )


def _print_message_read_stats(reader: WindowsDBReader) -> None:
    """Print message-read proof without exposing message contents."""
    conn = getattr(reader, "_sqlite_conn", None)
    if conn is None:
        print("message_read: no_connection")
        return

    try:
        if reader._has_msg_shard_tables():
            tables = reader._get_v4_msg_tables()
            total_rows = 0
            text_rows = 0
            latest_ts = 0
            for table, _username in tables:
                row = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
                total_rows += int(row[0] or 0)
                row = conn.execute(
                    f'SELECT COUNT(*), MAX(create_time) FROM "{table}" '
                    f'WHERE local_type = 1'
                ).fetchone()
                text_rows += int(row[0] or 0)
                latest_ts = max(latest_ts, int(row[1] or 0))
            sample = reader.get_my_messages(limit=5, since_days=3650)
            print("message_schema: windows_4x")
            print("message_tables:", len(tables))
            print("message_rows:", total_rows)
            print("text_message_rows:", text_rows)
            print("my_text_sample_count:", len(sample))
            print("latest_message_time:", latest_ts)
            return

        row = conn.execute("SELECT COUNT(*), MAX(msg_create_time) FROM MSG").fetchone()
        total_rows = int(row[0] or 0)
        latest_ts = int(row[1] or 0)
        row = conn.execute(
            "SELECT COUNT(*) FROM MSG WHERE msg_type = 1"
        ).fetchone()
        text_rows = int(row[0] or 0)
        sample = reader.get_my_messages(limit=5, since_days=3650)
        print("message_schema: legacy")
        print("message_rows:", total_rows)
        print("text_message_rows:", text_rows)
        print("my_text_sample_count:", len(sample))
        print("latest_message_time:", latest_ts)
    except Exception as exc:
        print("message_read_error:", exc)


def _run_diagnosis() -> int:
    print("platform:", sys.platform)
    print("started_at:", int(time.time()))

    extractor = WindowsKeyExtractor()
    pids = extractor.find_wechat_processes()
    print("pids:", pids[:10])
    print("data_dirs:", extractor._find_wechat_data_dirs())
    _print_key_info_files()

    db_infos = extractor._collect_validation_dbs()
    print("encrypted_db_count:", len(db_infos))
    for info in db_infos:
        rel = str(info["rel_path"]).replace("\\", "/")
        if rel.endswith("message/message_0.db") or os.path.basename(str(info["path"])) in {
            "message_0.db",
            "contact.db",
        }:
            print("db:", rel, "salt=", info["salt"], "path=", info["path"])

    message_db = next(
        (
            info
            for info in db_infos
            if str(info["rel_path"]).replace("\\", "/").lower()
            == "message/message_0.db"
        ),
        None,
    )
    if message_db is None:
        print("result: no_message_db")
        return 2
    if not pids:
        print("result: no_weixin_process")
        return 3

    print("scan_status: starting (pure memory scan)")
    keys = extractor.scan_memory_for_keys(pids[0])
    print("scan_key_count:", len(keys))
    print("scan_key_paths:", sorted(keys))

    result_key = keys.get("message/message_0.db")
    if not result_key:
        print("scan_message_key: False")
        print("result: no_message_key")
        return 4

    print("scan_message_key: True")
    reader = WindowsDBReader()
    ok = reader.open_db(str(message_db["path"]), bytes.fromhex(result_key))
    print("open_message_db:", ok)
    if not ok:
        print("result: scan_key_did_not_open_message_db")
        return 7

    is_message = reader.is_message_db()
    print("is_message_db:", is_message)
    if is_message:
        _print_message_read_stats(reader)
    reader.close()
    print("result:", "ok" if is_message else "opened_but_not_message_db")
    return 0 if is_message else 6


def main() -> int:
    out_path = _extract_out_arg()
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            with redirect_stdout(f), redirect_stderr(f):
                rc = _run_diagnosis()
                print("exit_code:", rc)
                return rc

    return _run_diagnosis()


if __name__ == "__main__":
    raise SystemExit(main())
