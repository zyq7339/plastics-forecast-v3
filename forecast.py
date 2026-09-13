# -*- coding: utf-8 -*-
"""
塑料颗粒AI预测 - 全自动工作流（Tavily优先版）
支持 daily / weekly / monthly 三种模式
优先使用 Tavily 搜索实时数据，再由 DeepSeek 分析生成报告。
所有敏感信息从环境变量读取。
"""

import requests
import json
import re
import time
import sys
import os
from datetime import datetime, timedelta

# ================================================
# ✅ 配置区：全部从环境变量读取
# ================================================
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
FEISHU_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")   # 必填，去 tavily.com 免费注册

TABLE_ID_DAILY = None
TABLE_ID_WEEKLY = None
TABLE_ID_MONTHLY = None
# ================================================

DEEPSEEK_API_URL = "https://api.deepseek.com/responses"
DEEPSEEK_MODEL = "deepseek-v4-flash"


# ================================================
# 工具函数
# ================================================
def get_target_date(mode="daily"):
    today = datetime.now()
    if mode == "daily":
        weekday = today.weekday()
        delta = 3 if weekday == 4 else 1
        return today + timedelta(days=delta)
    return today


def extract_output_text(result):
    try:
        output = result.get("output", [])
        text_parts = []
        for item in output:
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        text_parts.append(content.get("text", ""))
        return "\n".join(text_parts) if text_parts else None
    except Exception as e:
        print(f"⚠️ 解析异常: {e}")
        return None


def tavily_search(query, max_results=8):
    """使用 Tavily 搜索"""
    if not TAVILY_API_KEY:
        print("⚠️ TAVILY_API_KEY 未配置，跳过 Tavily 搜索")
        return None
    try:
        print(f"🔍 Tavily 搜索: {query[:60]}...")
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "search_depth": "advanced",
                "max_results": max_results,
                "include_answer": False
            },
            timeout=30
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                text = "\n".join([f"- {r['title']}: {r['content']}" for r in results])
                print(f"✅ Tavily 返回 {len(results)} 条结果")
                return text
        else:
            print(f"⚠️ Tavily HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"⚠️ Tavily 异常: {e}")
    return None


def call_deepseek_with_search(prompt, max_retries=3):
    """
    优先用 Tavily 搜索，将结果拼入 prompt，再调用 DeepSeek 分析。
    如果 Tavily 未配置，则尝试 DeepSeek 自带的 web_search。
    """
    # ---------- 优先 Tavily ----------
    if TAVILY_API_KEY:
        # 构造搜索查询：覆盖主要品种和关键指标
        queries = [
            "华东 中安7042 价格 最近交易日 隆众资讯",
            "华东 中安T03S 价格 最近交易日 隆众资讯",
            "华东 中安7050H 价格 最近交易日 隆众资讯",
            "华东 中安K8003 价格 最近交易日 隆众资讯",
            "华东 中韩35H 价格 最近交易日 隆众资讯",
            "布伦特原油期货 最近交易日 收盘价",
            "PP期货主力合约 最近交易日 收盘价",
            "PE期货主力合约 最近交易日 收盘价",
            "宝丰7042 华东 价格 最近交易日",
            "华东 塑料 库存 最近交易日"
        ]
        search_texts = []
        for q in queries:
            t = tavily_search(q, max_results=3)
            if t:
                search_texts.append(f"【{q}】\n{t}")
        
        if search_texts:
            combined_search = "\n\n".join(search_texts)
            # 将搜索结果作为上下文，拼接到 prompt
            augmented_prompt = (
                f"{prompt}\n\n"
                f"【以下为 Tavily 搜索到的最近交易日实时数据，请基于这些数据进行分析】\n"
                f"{combined_search}\n\n"
                f"请根据以上数据，按原格式生成预测报告。"
            )
            print(f"✅ 已将 Tavily 搜索结果拼入 prompt（共 {len(combined_search)} 字符）")
            return call_deepseek_plain(augmented_prompt, max_retries)
        else:
            print("⚠️ Tavily 未返回有效结果，尝试 DeepSeek web_search")

    # ---------- 备选：DeepSeek web_search ----------
    return call_deepseek_websearch(prompt, max_retries)


def call_deepseek_plain(prompt, max_retries=3):
    """普通 DeepSeek 调用（不带 web_search）"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "input": prompt,
        "temperature": 0.3,
        "stream": False
    }
    for attempt in range(max_retries):
        try:
            print(f"🔄 调用 DeepSeek（无搜索，尝试 {attempt+1}/{max_retries}）...")
            resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=180)
            if resp.status_code == 200:
                text = extract_output_text(resp.json())
                if text:
                    print(f"✅ 成功，报告长度: {len(text)} 字符")
                    return text
            else:
                print(f"⚠️ HTTP {resp.status_code}: {resp.text[:300]}")
        except Exception as e:
            print(f"⚠️ 异常: {e}")
        if attempt < max_retries - 1:
            time.sleep(5 * (attempt + 1))
    return None


def call_deepseek_websearch(prompt, max_retries=3):
    """DeepSeek 自带 web_search（备选）"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "input": prompt,
        "tools": [{"type": "web_search"}],
        "tool_choice": {"type": "web_search"},
        "temperature": 0.3,
        "stream": False
    }
    for attempt in range(max_retries):
        try:
            print(f"🔄 调用 DeepSeek web_search（尝试 {attempt+1}/{max_retries}）...")
            resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=180)
            if resp.status_code == 200:
                result = resp.json()
                has_search = any(item.get("type") == "web_search_call" for item in result.get("output", []))
                print(f"🔍 搜索调用检测: {'✅ 已调用' if has_search else '❌ 未调用'}")
                text = extract_output_text(result)
                if text:
                    print(f"✅ 成功，报告长度: {len(text)} 字符")
                    return text
            else:
                print(f"⚠️ HTTP {resp.status_code}: {resp.text[:300]}")
        except Exception as e:
            print(f"⚠️ 异常: {e}")
        if attempt < max_retries - 1:
            time.sleep(5 * (attempt + 1))
    return None


def send_to_feishu(content):
    if not FEISHU_WEBHOOK_URL:
        print("❌ FEISHU_WEBHOOK_URL 未配置")
        return False
    if len(content) > 4000:
        content = content[:3900] + "\n\n... (截断)"
    try:
        r = requests.post(FEISHU_WEBHOOK_URL, json={"msg_type": "text", "content": {"text": content}}, timeout=30)
        if r.status_code == 200 and r.json().get("code") == 0:
            print("✅ 飞书推送成功")
            return True
    except Exception as e:
        print(f"❌ 飞书推送异常: {e}")
    return False


# ================================================
# 每日预测
# ================================================
def get_daily_prompt():
    today = datetime.now()
    today_str = today.strftime('%Y年%m月%d日')
    target = get_target_date("daily")
    target_str = target.strftime('%Y年%m月%d日')
    weekday_note = "（周五触发，自动跳过周末，预测下周一）" if today.weekday() == 4 else ""

    return f"""请根据今日最新数据，生成{target_str}华东市场塑料颗粒价格预测报告。

【日期说明】
今日日期：{today_str}
预测日期：{target_str} {weekday_note}

【⚠️ 数据时效性强制要求】
1. 所有价格必须使用最近一个交易日（周末/节假日则用前一交易日）的收盘价。
2. 布伦特原油合理区间 95-100 美元/桶。
3. 期货必须使用当前主力合约的最近交易日收盘价。

【⚠️ 强制推算规则】
第一步：只预测中安7042（基准）
第二步：其他品种按公式推算，严禁独立预测！
中安T03S = 中安7042 + 300~400
中安K8003 = 中安7042 + 550~750
中安7050H = 中安7042 + 100~250
中韩35H = 中安7050H - 50~100

【输出格式】
========================================
📊 {target_str}华东市场塑料颗粒价格预测
========================================

预测日期：{target_str}

【核心品种预测】
品种：中安7042（线性PE）
预测价格区间：X - Y 元/吨
涨跌方向：大涨/小涨/持平/小跌/大跌
置信度：高/中/低

品种：中安T03S（拉丝PP）
预测价格区间：X - Y 元/吨
涨跌方向：大涨/小涨/持平/小跌/大跌
置信度：高/中/低

品种：中安7050H（线性PE高透明吹塑）
预测价格区间：X - Y 元/吨
涨跌方向：大涨/小涨/持平/小跌/大跌
置信度：高/中/低

品种：中安K8003（低熔共聚PP）
预测价格区间：X - Y 元/吨
涨跌方向：大涨/小涨/持平/小跌/大跌
置信度：高/中/低

品种：中韩35H（LLDPE普通膜料）
预测价格区间：X - Y 元/吨
涨跌方向：大涨/小涨/持平/小跌/大跌
置信度：高/中/低

【主要依据】
原油价格：XX美元/桶（布伦特期货结算价）
PP期货（主力）：XX元/吨
PE期货（主力）：XX元/吨
华东现货参考价：XX元/吨（中安7042）
竞品参考价（宝丰7042）：XX元/吨
华东库存状态：高/中/低

【竞品价差分析】
宝丰7042 vs 中安7042：价差XX元/吨
建议：降价/提价/维持

【{target_str}关键影响因素】
1. 因素（利多/利空 ↑↓）：说明
2. 因素（利多/利空 ↑↓）：说明
3. 因素（利多/利空 ↑↓）：说明

【{target_str}操作建议】
建议类型：正常采购/观望/加快补库/推迟采购
理由：简要说明

【风险预警】
上行风险：描述
下行风险：描述

【预测可信度提示】
中安7042 → ★★★★
中安T03S → ★★★☆
中安7050H → ★★★
中安K8003 → ★★
中韩35H → ★★★

========================================
数据来源：Tavily 联网搜索最近交易日数据
免责声明：仅供参考，实际交易请结合自身情况决策
========================================
"""


def run_daily():
    target = get_target_date("daily")
    print(f"\n📅 预测目标: {target.strftime('%Y年%m月%d日')}")
    report = call_deepseek_with_search(get_daily_prompt())
    if report:
        send_to_feishu(report)


# ================================================
# 每周预测
# ================================================
def get_weekly_prompt():
    today = datetime.now()
    ws = (today - timedelta(days=today.weekday())).strftime('%Y年%m月%d日')
    we = (today + timedelta(days=6 - today.weekday())).strftime('%Y年%m月%d日')
    return f"""请根据本周最新数据，生成{ws}至{we}华东市场塑料颗粒行情预测报告。

【输出格式】
========================================
📈 本周华东市场塑料颗粒行情预测
========================================
预测周次：{ws} - {we}

【本周走势预判】
中安7042：上涨/震荡/下跌，区间 X-Y 元/吨
中安T03S：上涨/震荡/下跌，区间 X-Y 元/吨
中安7050H：上涨/震荡/下跌，区间 X-Y 元/吨
中安K8003：上涨/震荡/下跌，区间 X-Y 元/吨
中韩35H：上涨/震荡/下跌，区间 X-Y 元/吨

【本周核心驱动因素】
1. ...
2. ...
3. ...

【本周采购策略建议】
策略方向：积极补库/按需采购/观望等待
理由：...

【风险预警】
上行风险：...
下行风险：...
========================================
"""


def run_weekly():
    print("\n📅 预测目标: 本周")
    report = call_deepseek_with_search(get_weekly_prompt())
    if report:
        send_to_feishu(report)


# ================================================
# 每月预测
# ================================================
def get_monthly_prompt():
    today = datetime.now()
    month = today.strftime('%Y年%m月')
    return f"""请根据本月最新数据，生成{month}华东市场塑料颗粒价格中枢预测报告。

【输出格式】
========================================
📅 {month}华东市场塑料颗粒价格预测
========================================
预测月份：{month}

【本月价格中枢预测】
中安7042：上涨/震荡/下跌，均价区间 X-Y 元/吨
中安T03S：上涨/震荡/下跌，均价区间 X-Y 元/吨
中安7050H：上涨/震荡/下跌，均价区间 X-Y 元/吨
中安K8003：上涨/震荡/下跌，均价区间 X-Y 元/吨
中韩35H：上涨/震荡/下跌，均价区间 X-Y 元/吨

【本月核心驱动逻辑】
1. ...
2. ...
3. ...

【本月采购策略建议】
月度策略：积极建仓/按需采购/去库存为主
最佳采购窗口：上旬/中旬/下旬
理由：...

【风险预警】
上行风险：...
下行风险：...
========================================
"""


def run_monthly():
    print("\n📅 预测目标: 本月")
    report = call_deepseek_with_search(get_monthly_prompt())
    if report:
        send_to_feishu(report)


# ================================================
# 主入口
# ================================================
def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    print("=" * 50)
    print(f"📊 塑料颗粒AI预测系统（Tavily优先版）")
    print(f"⏰ 启动: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📌 模式: {mode}")
    print("=" * 50)

    if mode == "daily":
        run_daily()
    elif mode == "weekly":
        run_weekly()
    elif mode == "monthly":
        run_monthly()
    else:
        print(f"❌ 未知模式: {mode}")
        sys.exit(1)

    print("\n✅ 完成")


if __name__ == "__main__":
    main()
