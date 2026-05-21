"""测试 OCR helper 的关键匹配策略。"""

from pathlib import Path


def test_ocr_exact_match_uses_normalized_text():
    """精确匹配应忽略 OCR 插入的空格，避免群标题带成员数时误判。"""
    source = Path("backend/app/core/ocr_helper.swift").read_text(encoding="utf-8")

    assert "let cleanText = normalized(text)" in source
    assert "let exactMatch = cleanText.contains(cleanTarget)" in source
    assert "exact: exactMatch" in source


def test_chat_title_search_page_markers_do_not_use_broad_news_word():
    """标题校验不能用“新闻”这类泛词判断搜索页，聊天列表里也可能出现新闻。"""
    source = Path("backend/app/core/ocr_helper.swift").read_text(encoding="utf-8")

    assert '"新闻"' not in source


def test_ocr_reports_no_text_when_screenshot_has_no_observations():
    """截图没有 OCR 文本时应输出专门状态，便于区分权限/会话问题和搜索无结果。"""
    source = Path("backend/app/core/ocr_helper.swift").read_text(encoding="utf-8")

    assert 'if observations.isEmpty {' in source
    assert 'print("no_text_found")' in source
