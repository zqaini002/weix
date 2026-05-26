"""Agent 工具集。

使用 @tool 装饰器定义 LangChain 工具，供 Agent 调用。
"""

from __future__ import annotations

import ast
import hashlib
import logging
import operator
from datetime import datetime
from typing import Callable

import httpx
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.tools import tool

from app.config import get_config
from app.utils.paths import get_base_dir

logger = logging.getLogger(__name__)

# 安全的数学运算白名单
_SAFE_OPERATORS: dict = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_SAFE_FUNCTIONS: dict[str, Callable] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "int": int,
    "float": float,
    "sqrt": lambda x: x ** 0.5,
}


def _safe_eval(expression: str) -> float:
    """安全地计算数学表达式。

    使用 AST 白名单解析，只允许安全的运算符和函数，
    防止代码注入攻击。

    Args:
        expression: 数学表达式字符串。

    Returns:
        计算结果。

    Raises:
        ValueError: 表达式包含不允许的操作。
    """
    # 清理输入
    expression = expression.strip()
    if not expression:
        raise ValueError("表达式不能为空")

    def _eval_node(node: ast.AST) -> float:
        if isinstance(node, ast.Constant):  # Python 3.8+ 数字常量
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError(f"不支持的常量类型: {type(node.value)}")
        elif isinstance(node, ast.Num):  # Python 3.7 兼容
            return node.n
        elif isinstance(node, ast.BinOp):
            op_type = type(node.op)
            if op_type not in _SAFE_OPERATORS:
                raise ValueError(f"不支持的运算符: {op_type.__name__}")
            left = _eval_node(node.left)
            right = _eval_node(node.right)
            if op_type is ast.Div and right == 0:
                raise ValueError("除数不能为零")
            return _SAFE_OPERATORS[op_type](left, right)
        elif isinstance(node, ast.UnaryOp):
            op_type = type(node.op)
            if op_type not in _SAFE_OPERATORS:
                raise ValueError(f"不支持的一元运算符: {op_type.__name__}")
            return _SAFE_OPERATORS[op_type](_eval_node(node.operand))
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError("不支持嵌套函数调用")
            func_name = node.func.id
            if func_name not in _SAFE_FUNCTIONS:
                raise ValueError(f"不支持的函数: {func_name}")
            args = [_eval_node(arg) for arg in node.args]
            return _SAFE_FUNCTIONS[func_name](*args)
        else:
            raise ValueError(f"不支持的语法: {type(node).__name__}")

    try:
        tree = ast.parse(expression, mode="eval")
        return _eval_node(tree.body)
    except (SyntaxError, ValueError) as e:
        raise ValueError(f"表达式计算失败: {e}") from e


@tool
def search_web(query: str) -> str:
    """在网络上搜索信息。当需要查找实时信息、新闻、事实数据时使用此工具。

    Args:
        query: 搜索关键词或问题。
    """
    logger.info(f"Tool search_web called with query: {query}")
    try:
        search_tool = DuckDuckGoSearchRun()
        result = search_tool.invoke(query)
        logger.info(f"search_web result length: {len(result)} chars")
        return result
    except Exception as e:
        logger.error(f"search_web failed: {e}")
        return f"搜索工具暂时不可用: {e}"


@tool
def get_weather(city: str) -> str:
    """查询指定城市的实时天气。只有用户明确给出城市时才调用；如果用户只问天气但没有城市，先追问“你想查哪个城市？”。

    Args:
        city: 城市名称，例如“贵阳”“北京”“上海”。
    """
    city = str(city or "").strip()
    if not city:
        return "你想查哪个城市的天气？"

    ai_cfg = get_config().ai if isinstance(get_config().ai, dict) else {}
    amap_key = str(ai_cfg.get("amap_key") or "").strip()
    amap_security_key = str(ai_cfg.get("amap_security_key") or "").strip()
    if not amap_key:
        return "天气工具未配置高德地图 key，请先在配置里设置 ai.amap_key。"

    params = {
        "key": amap_key,
        "city": city,
        "extensions": "base",
        "output": "JSON",
    }
    if amap_security_key:
        sign_base = "&".join(f"{k}={params[k]}" for k in sorted(params)) + amap_security_key
        params["sig"] = hashlib.md5(sign_base.encode("utf-8")).hexdigest()

    try:
        response = httpx.get(
            "https://restapi.amap.com/v3/weather/weatherInfo",
            params=params,
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.error("get_weather failed: %s", exc)
        return f"天气查询失败: {exc}"

    if payload.get("status") != "1":
        return f"天气查询失败: {payload.get('info', '未知错误')}（infocode={payload.get('infocode', '')}）"

    lives = payload.get("lives") or []
    if not lives:
        return f"没有查到{city}的实时天气。"

    live = lives[0]
    city_name = live.get("city") or city
    weather = live.get("weather", "未知")
    temp = live.get("temperature", "未知")
    wind = live.get("winddirection", "未知")
    wind_power = live.get("windpower", "未知")
    humidity = live.get("humidity", "未知")
    report_time = live.get("reporttime", "")
    result = (
        f"{city_name}天气：{weather}，{temp}°C，"
        f"{wind}风 {wind_power}级，湿度 {humidity}%"
    )
    if report_time:
        result += f"。更新时间：{report_time}"
    logger.info("Tool get_weather: city=%s result=%s", city, result)
    return result


@tool
def get_current_time() -> str:
    """获取当前的日期和时间。当需要知道现在是什么时间、日期、星期几时使用此工具。"""
    now = datetime.now()
    result = now.strftime("%Y年%m月%d日 %H:%M:%S 星期%w")
    # 将数字星期转换为中文
    weekday_map = {"0": "日", "1": "一", "2": "二", "3": "三", "4": "四", "5": "五", "6": "六"}
    weekday_cn = weekday_map.get(str(now.weekday() + 1), str(now.weekday() + 1))
    result = result.replace(f"星期{now.strftime('%w')}", f"星期{weekday_cn}")
    logger.info(f"Tool get_current_time: {result}")
    return result


@tool
def calculate(expression: str) -> str:
    """执行简单的数学计算。支持加减乘除、幂运算和常用函数（abs、round、min、max、sqrt）。
    当需要进行数值计算、数学表达式求值时使用此工具。

    Args:
        expression: 数学表达式字符串，例如 "2 + 3 * 4" 或 "sqrt(16) + 5"。
    """
    logger.info(f"Tool calculate called with expression: {expression}")
    try:
        result = _safe_eval(expression)
        # 智能格式化结果：整数不显示小数点，浮点数保留合理精度
        if isinstance(result, float) and result == int(result):
            result_display = int(result)
        elif isinstance(result, float):
            result_display = round(result, 10)
        else:
            result_display = result
        logger.info(f"calculate result: {expression} = {result_display}")
        return f"计算结果：{expression} = {result_display}"
    except ValueError as e:
        logger.warning(f"calculate failed: {e}")
        return f"计算失败：{e}"


def _make_query_statistics(db_url: str = "") -> Callable:
    """创建 query_statistics 工具（闭包捕获数据库配置）。

    Args:
        db_url: 数据库连接 URL，默认为 SQLite。

    Returns:
        query_statistics 工具函数。
    """
    @tool
    def query_statistics(room_id: str, stat_type: str = "daily") -> str:
        """查询聊天统计数据。可以查询指定群的每日、每周或每月活跃度排行和消息统计。

        Args:
            room_id: 群聊 ID，例如 "123456789@chatroom"。
            stat_type: 统计类型，可选 "daily"（每日）/ "weekly"（每周）/ "monthly"（每月）。
        """
        logger.info(f"Tool query_statistics called: room_id={room_id}, stat_type={stat_type}")

        if stat_type not in ("daily", "weekly", "monthly"):
            return f"不支持的统计类型: {stat_type}，可选值：daily / weekly / monthly"

        # 使用同步 SQLite 连接查询
        try:
            import sqlite3
            from pathlib import Path

            # 从 URL 提取数据库路径
            if not db_url:
                from app.config import get_config
                cfg = get_config()
                raw_url = cfg.database.get("url", "sqlite+aiosqlite:///data/weix.db")
            else:
                raw_url = db_url

            db_path = raw_url.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
            if not Path(db_path).is_absolute():
                db_path = str(get_base_dir() / db_path)

            if not Path(db_path).exists():
                return f"暂无统计数据（数据库文件不存在）"

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute(
                """SELECT user_wxid, user_name, SUM(message_count) as total
                   FROM chat_statistics
                   WHERE room_id = ? AND stat_type = ?
                   GROUP BY user_wxid
                   ORDER BY total DESC
                   LIMIT 10""",
                (room_id, stat_type),
            )
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                return f"群 {room_id} 暂无 {stat_type} 统计数据。"

            lines = [f"📊 {room_id} {stat_type} 活跃排行："]
            for i, row in enumerate(rows, 1):
                name = row["user_name"] or row["user_wxid"]
                lines.append(f"  {i}. {name}: {row['total']} 条消息")
            return "\n".join(lines)

        except Exception as e:
            logger.error(f"query_statistics failed: {e}")
            return f"查询统计数据失败: {e}"

    return query_statistics


# 模块级工具实例（使用默认数据库配置）
query_statistics = _make_query_statistics()


def create_tools(
    include_search: bool = True,
    include_time: bool = True,
    include_calculator: bool = True,
    include_statistics: bool = True,
    db_url: str = "",
) -> list:
    """创建 Agent 工具集列表。

    可根据需要选择性包含某些工具。

    Args:
        include_search: 是否包含网页搜索工具。
        include_time: 是否包含时间查询工具。
        include_calculator: 是否包含计算器工具。
        include_statistics: 是否包含统计查询工具。
        db_url: 数据库 URL（用于统计工具）。

    Returns:
        LangChain Tool 列表。
    """
    tools: list = []
    tools.append(get_weather)
    if include_search:
        tools.append(search_web)
    if include_time:
        tools.append(get_current_time)
    if include_calculator:
        tools.append(calculate)
    if include_statistics:
        tools.append(_make_query_statistics(db_url) if db_url else query_statistics)
    return tools
