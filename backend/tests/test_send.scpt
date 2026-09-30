-- 微信消息发送测试脚本
-- 用法: osascript tests/test_send.scpt <联系人名> <消息内容>
-- 例: osascript tests/test_send.scpt "张三" "测试消息123"

on run argv
	if (count of argv) < 3 then
		display dialog "用法: osascript test_send.scpt <方法1|2> <联系人名> <消息>"
		return
	end if

	set testMethod to item 1 of argv
	set contactName to item 2 of argv
	set messageText to item 3 of argv

	log "=== 开始发送 (方法" & testMethod & ") ==="
	log "接收人: " & contactName
	log "消息: " & messageText

	if testMethod = "2" then
		doTest2(contactName, messageText)
	else
		doTest1(contactName, messageText)
	end if
end run

-- 方法 1: 搜索 → Tab → Enter 打开 → Escape 关搜索 → 点击输入框 → Cmd+V → Enter
on doTest1(contactName, messageText)
	log "--- 方法1: 搜索+点击输入框 ---"

	tell application "WeChat" to activate
	delay 0.8

	-- 复制联系人名到剪贴板
	do shell script "printf %s " & quoted form of contactName & " | pbcopy"

	tell application "System Events"
		tell process "WeChat"
			-- Escape 清理 + Cmd+F 搜索
			key code 53
			delay 0.2
			keystroke "f" using command down
			delay 0.3

			-- 粘贴联系人名
			keystroke "v" using command down
			delay 1.2

			-- Tab 切到结果
			keystroke tab
			delay 0.3

			-- Enter 打开第一条结果 (私聊)
			keystroke return
			delay 2.5

			-- Escape 关搜索面板
			key code 53
			delay 0.5

			-- 获取窗口信息
			set winPos to position of window 1
			set winSize to size of window 1
			log "窗口位置: " & (item 1 of winPos) & ", " & (item 2 of winPos)
			log "窗口尺寸: " & (item 1 of winSize) & ", " & (item 2 of winSize)

			-- 复制消息
			do shell script "printf %s " & quoted form of messageText & " | pbcopy"
			delay 0.2

			-- 点击输入框 (底部偏右)
			set clickX to item 1 of winPos + (item 1 of winSize) * 0.6
			set clickY to item 2 of winPos + (item 2 of winSize) - 35
			log "点击坐标: " & clickX & ", " & clickY
			click at {clickX, clickY}
			delay 0.3

			-- 粘贴
			keystroke "v" using command down
			delay 0.3

			-- 发送
			keystroke return
			delay 0.3
		end tell
	end tell

	log "--- 方法1 完成 ---"
end doTest1

-- 方法 2: 免搜索 (假设已在聊天窗口中) → Escape → 点击输入框 → Cmd+V → Enter
on doTest2(contactName, messageText)
	log "--- 方法2: 免搜索(已在聊天) ---"

	tell application "WeChat" to activate
	delay 0.8

	tell application "System Events"
		tell process "WeChat"
			-- Escape 清理残留面板
			key code 53
			delay 0.2

			-- 获取窗口信息
			set winPos to position of window 1
			set winSize to size of window 1
			log "窗口位置: " & (item 1 of winPos) & ", " & (item 2 of winPos)
			log "窗口尺寸: " & (item 1 of winSize) & ", " & (item 2 of winSize)

			-- 复制消息
			do shell script "printf %s " & quoted form of messageText & " | pbcopy"
			delay 0.2

			-- 点击输入框
			set clickX to item 1 of winPos + (item 1 of winSize) * 0.6
			set clickY to item 2 of winPos + (item 2 of winSize) - 35
			log "点击坐标: " & clickX & ", " & clickY
			click at {clickX, clickY}
			delay 0.3

			-- 粘贴 + 发送
			keystroke "v" using command down
			delay 0.3
			keystroke return
			delay 0.3
		end tell
	end tell

	log "--- 方法2 完成 ---"
end doTest2
