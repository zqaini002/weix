"""实际微信集成测试 - 测试发送到真实微信。

测试目标:
    私聊: 小号
    群聊: 贵州铜仁市129办公室工作群

测试场景:
    1. 诊断 AppleScript 环境 (无障碍权限、text area 可访问性)
    2. 私聊完整搜索流程
    3. 私聊免搜索 (60s 内再次发送)
    4. 群聊完整搜索流程
    5. 群聊免搜索 (60s 内再次发送)
    6. 切换发送: 私聊 → 群聊 → 私聊 (验证竞态修复)
"""

import asyncio
import subprocess
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 清除缓存配置强制重载
import app.config
app.config._config = None

from app.core.sender_macos import MacOSSender

PRIVATE_TARGET = "小号"
GROUP_TARGET = "贵州铜仁市129办公室工作群"


def run_applescript(script: str, description: str) -> tuple[bool, str]:
    """执行 AppleScript 并返回 (成功, 输出)。"""
    print(f"\n{'='*60}")
    print(f"[诊断] {description}")
    print(f"{'='*60}")
    print(f"脚本:\n{script[:300]}...")
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=15
        )
        if proc.returncode == 0:
            output = proc.stdout.strip()
            if output.startswith("error:") or "不允许辅助访问" in output:
                print(f"✗ 失败: {output}")
                return False, output
            print(f"✓ 成功: {output}")
            return True, output
        else:
            print(f"✗ 失败 (code={proc.returncode}): {proc.stderr[:200]}")
            return False, proc.stderr
    except subprocess.TimeoutExpired:
        print("✗ 超时 (15s)")
        return False, "timeout"
    except Exception as e:
        print(f"✗ 异常: {e}")
        return False, str(e)


def diagnostic_1_accessibility():
    """诊断 1: 检查辅助功能权限和 WeChat 的 text area。"""
    script = '''
tell application "System Events"
    tell process "WeChat"
        set frontmost to true
        delay 0.3
        try
            set taList to every text area of window 1
            set taCount to count of taList
            set resultStr to "text_area_count:" & taCount
            repeat with i from 1 to taCount
                set ta to item i of taList
                try
                    set taVal to value of ta
                    set taFocused to focused of ta
                    set resultStr to resultStr & " | [" & i & "] value=" & (characters 1 thru 50 of taVal as string) & " focused=" & taFocused
                on error errMsg
                    set resultStr to resultStr & " | [" & i & "] error=" & errMsg
                end try
            end repeat
            return resultStr
        on error errMsg
            return "error:" & errMsg
        end try
    end tell
end tell
'''
    return run_applescript(script, "检查 WeChat text area 可访问性")


def diagnostic_2_window_info():
    """诊断 2: 获取窗口信息。"""
    script = '''
tell application "System Events"
    tell process "WeChat"
        set frontmost to true
        delay 0.2
        set winPos to position of window 1
        set winSize to size of window 1
        set winTitle to title of window 1
        return "title:" & winTitle & " | pos:" & (item 1 of winPos as string) & "," & (item 2 of winPos as string) & " | size:" & (item 1 of winSize as string) & "x" & (item 2 of winSize as string)
    end tell
end tell
'''
    return run_applescript(script, "获取 WeChat 窗口信息")


def diagnostic_3_search_test():
    """诊断 3: 测试搜索功能 - 搜索 '小号' 然后观察结果。"""
    script = f'''
tell application "WeChat" to activate
delay 0.5

do shell script "printf %s " & quoted form of "{PRIVATE_TARGET}" & " | pbcopy"

tell application "System Events"
    tell process "WeChat"
        key code 53
        delay 0.2
        keystroke "f" using command down
        delay 0.3
        keystroke "v" using command down
        delay 1.5
        -- 不选择，只观察，然后 Escape 关掉
        key code 53
        delay 0.2
        key code 53
    end tell
end tell
return "search_test_done"
'''
    return run_applescript(script, "测试搜索 '小号' (搜索后不选择，仅验证搜索面板可用)")


def diagnostic_4_group_search_test():
    """诊断 4: 测试群聊搜索。"""
    script = f'''
tell application "WeChat" to activate
delay 0.5

do shell script "printf %s " & quoted form of "{GROUP_TARGET}" & " | pbcopy"

tell application "System Events"
    tell process "WeChat"
        key code 53
        delay 0.2
        keystroke "f" using command down
        delay 0.3
        keystroke "v" using command down
        delay 1.5
        key code 53
        delay 0.2
        key code 53
    end tell
end tell
return "group_search_test_done"
'''
    return run_applescript(script, "测试搜索群聊 (搜索后不选择，仅验证搜索面板可用)")


async def test_scenario_1_private_full_search():
    """场景 1: 私聊完整搜索 → 发送测试消息。"""
    print(f"\n{'='*60}")
    print("[场景 1] 私聊完整搜索 → {PRIVATE_TARGET}")
    print(f"{'='*60}")

    sender = MacOSSender()
    test_msg = f"[测试消息] 私聊完整搜索 | time={int(time.time())}"

    print(f"目标: {PRIVATE_TARGET}")
    print(f"消息: {test_msg}")

    script = sender._build_script(test_msg, PRIVATE_TARGET, skip_search=False, is_group=False)
    print(f"\n生成的 AppleScript ({script.count(chr(10))} 行):")
    print(script)
    print()

    # 实际执行
    success = await sender.send_text(test_msg, PRIVATE_TARGET, is_group=False)
    print(f"\n结果: {'✓ 成功' if success else '✗ 失败'}")
    return success


async def test_scenario_2_private_skip_search():
    """场景 2: 私聊免搜索 (60s 内再次发送)。"""
    print(f"\n{'='*60}")
    print("[场景 2] 私聊免搜索 → {PRIVATE_TARGET}")
    print(f"{'='*60}")

    sender = MacOSSender()
    # 先手动设置 last_receiver 模拟 60s 内状态
    sender._last_receiver = PRIVATE_TARGET
    sender._last_send_time = time.monotonic() - 10  # 10s 前

    test_msg = f"[测试消息] 私聊免搜索 | time={int(time.time())}"

    script = sender._build_script(test_msg, PRIVATE_TARGET, skip_search=True, is_group=False)
    print(f"\n生成的 AppleScript ({script.count(chr(10))} 行):")
    print(script)
    print()

    success = await sender.send_text(test_msg, PRIVATE_TARGET, is_group=False)
    print(f"\n结果: {'✓ 成功' if success else '✗ 失败'}")
    return success


async def test_scenario_3_group_full_search():
    """场景 3: 群聊完整搜索 → 发送测试消息。"""
    print(f"\n{'='*60}")
    print("[场景 3] 群聊完整搜索 → {GROUP_TARGET}")
    print(f"{'='*60}")

    sender = MacOSSender()
    test_msg = f"[测试消息] 群聊完整搜索 | time={int(time.time())}"

    print(f"目标: {GROUP_TARGET}")
    print(f"消息: {test_msg}")

    script = sender._build_script(test_msg, GROUP_TARGET, skip_search=False, is_group=True)
    print(f"\n生成的 AppleScript ({script.count(chr(10))} 行):")
    print(script)

    # 验证关键元素
    assert "verifyCurrentChatTitle" not in script, "群聊发送不应依赖标题截图校验"
    assert "screenshot_helper.py" not in script, "群聊发送不应生成截图"
    assert "weix_ocr_helper" not in script, "群聊发送不应依赖 OCR"
    assert "pyautogui.click" not in script, "群聊搜索结果不应依赖坐标点击"
    assert "key code 125" not in script, "不应再用下箭头，避免触发搜一搜"
    assert "key code 36" in script, "应使用回车确认第一条群聊结果"
    assert GROUP_TARGET in script, "缺少群名"
    print("\n✓ 脚本结构验证通过: 无截图/OCR + 回车确认 + 群名")

    print()
    success = await sender.send_text(test_msg, GROUP_TARGET, is_group=True)
    print(f"\n结果: {'✓ 成功' if success else '✗ 失败'}")
    return success


async def test_scenario_4_switch_private_group():
    """场景 4: 连续切换发送: 私聊 → 群聊 → 私聊 (验证竞态修复)。"""
    print(f"\n{'='*60}")
    print("[场景 4] 连续切换: 私聊 → 群聊 → 私聊")
    print(f"{'='*60}")

    sender = MacOSSender()
    test_msg_private = f"[测试] 切换-私聊 | {int(time.time())}"
    test_msg_group = f"[测试] 切换-群聊 | {int(time.time())}"

    # 私聊
    print("\n-- 4a: 私聊发送 --")
    s1 = await sender.send_text(test_msg_private, PRIVATE_TARGET, is_group=False)
    print(f"私聊: {'✓' if s1 else '✗'}")

    await asyncio.sleep(1)

    # 群聊 (验证 is_group 参数正确传递)
    print("\n-- 4b: 群聊发送 --")
    s2 = await sender.send_text(test_msg_group, GROUP_TARGET, is_group=True)
    print(f"群聊: {'✓' if s2 else '✗'}")

    await asyncio.sleep(1)

    # 再次私聊
    print("\n-- 4c: 私聊再次发送 --")
    s3 = await sender.send_text(test_msg_private + "_2", PRIVATE_TARGET, is_group=False)
    print(f"私聊: {'✓' if s3 else '✗'}")

    all_pass = s1 and s2 and s3
    print(f"\n切换测试: {'✓ 全部通过' if all_pass else '✗ 存在失败'}")
    return all_pass


async def main():
    print("=" * 60)
    print("  微信发送集成测试")
    print(f"  私聊目标: {PRIVATE_TARGET}")
    print(f"  群聊目标: {GROUP_TARGET}")
    print("=" * 60)

    # ---- 诊断阶段 ----
    print("\n\n### 诊断阶段 ###")
    diagnostic_1_accessibility()
    diagnostic_2_window_info()

    # 询问是否继续实际发送测试
    print("\n\n### 实际发送测试 ###")
    print(f"将向以下目标发送测试消息:")
    print(f"  1. 私聊: {PRIVATE_TARGET}")
    print(f"  2. 群聊: {GROUP_TARGET}")
    print(f"\n消息内容均以 '[测试消息]' 开头")

    # 场景 1: 私聊完整搜索
    r1 = await test_scenario_1_private_full_search()
    await asyncio.sleep(3)

    # 场景 2: 私聊免搜索
    r2 = await test_scenario_2_private_skip_search()
    await asyncio.sleep(3)

    # 场景 3: 群聊完整搜索
    r3 = await test_scenario_3_group_full_search()
    await asyncio.sleep(3)

    # 场景 4: 连续切换
    r4 = await test_scenario_4_switch_private_group()

    # ---- 结果汇总 ----
    print(f"\n\n{'='*60}")
    print("  测试结果汇总")
    print(f"{'='*60}")
    print(f"  场景 1 (私聊完整搜索): {'✓' if r1 else '✗'}")
    print(f"  场景 2 (私聊免搜索):   {'✓' if r2 else '✗'}")
    print(f"  场景 3 (群聊完整搜索): {'✓' if r3 else '✗'}")
    print(f"  场景 4 (切换发送):     {'✓' if r4 else '✗'}")
    print(f"  通过: {sum([r1, r2, r3, r4])}/4")


if __name__ == "__main__":
    asyncio.run(main())
