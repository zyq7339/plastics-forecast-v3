# -*- coding: utf-8 -*-
"""
塑料颗粒AI预测 - 全自动工作流（博查搜索版·综合优化版）
博查负责搜索实时数据，DeepSeek负责分析生成报告。
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
BOCHA_API_KEY = os.environ.get("BOCHA_API_KEY", "")

TABLE_ID_DAILY = None
TABLE_ID_WEEKLY = None
TABLE_ID_MONTHLY = None
# ================================================

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-flash"
BOCHA_API_URL = "https://api.bocha.cn/v1/web-search"


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


def bocha_search(query, count=5, freshness="oneWeek"):
    """用博查Web Search API搜索，返回清洗后的文本"""
    if not BOCHA_API_KEY:
        print("⚠️ BOCHA_API_KEY 未配置")
        return None
    try:
        resp = requests.post(
            BOCHA_API_URL,
            headers={
                "Authorization": f"Bearer {BOCHA_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "query": query,
                "count": count,
                "freshness": freshness,
                "summary": True
            },
            timeout=30
        )
        if resp.status_code == 200:
            data = resp.json()
            pages = data.get("data", {}).get("webPages", {}).get("value", [])
            if pages:
                results = []
                for p in pages:
                    title = p.get("name", "")
                    snippet = p.get("snippet", "")
                    results.append(f"- {title}: {snippet}")
                return "\n".join(results)
    except Exception as e:
        print(f"⚠️ 博查搜索异常: {e}")
    return None


def call_deepseek(prompt, max_retries=3):
    """调用 DeepSeek 分析"""
    if not DEEPSEEK_API_KEY:
        print("❌ DEEPSEEK_API_KEY 未配置")
        return None

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": "你是塑料颗粒市场分析专家，必须严格按照用户指定的规则输出。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "stream": False
    }

    for attempt in range(max_retries):
        try:
            print(f"🔄 调用 DeepSeek（尝试 {attempt+1}/{max_retries}）...")
            resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=180)
            if resp.status_code == 200:
                content = resp.json()["choices"][0]["message"]["content"]
                print(f"✅ 成功，报告长度: {len(content)} 字符")
                return content
            else:
                print(f"⚠️ HTTP {resp.status_code}: {resp.text[:300]}")
        except Exception as e:
            print(f"⚠️ 异常: {e}")
        if attempt < max_retries - 1:
            time.sleep(5 * (attempt + 1))
    return None


def search_and_analyze(prompt, search_queries):
    """先搜索，将搜索结果拼入prompt，再让DeepSeek分析"""
    search_texts = []
    for q in search_queries:
        print(f"🔍 博查搜索: {q[:50]}...")
        t = bocha_search(q)
        if t:
            search_texts.append(f"【{q}】\n{t}")

    if search_texts:
        combined = "\n\n".join(search_texts)
        augmented = (
            f"{prompt}\n\n"
            f"【以下为博查搜索到的最近交易日实时数据，请基于这些数据进行分析】\n"
            f"{combined}\n\n"
            f"请根据以上数据，按原格式生成预测报告。"
        )
        print(f"✅ 已拼入 {len(search_texts)} 组搜索结果（共{len(combined)}字符）")
        return call_deepseek(augmented)
    else:
        print("❌ 博查未返回有效结果")
        return call_deepseek(prompt)


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

    return f"""请根据以下数据，生成{target_str}华东市场塑料颗粒价格预测报告。

【日期说明】
今日日期：{today_str}
预测日期：{target_str} {weekday_note}

【⚠️ 强制推算规则】
第一步：只预测中安7042（基准）
第二步：其他品种按公式推算，严禁独立预测！
中安T03S = 中安7042 + 400~600（已根据当前市场价差调整）
中安K8003 = 中安7042 + 550~750
中安7050H = 中安7042 + 100~250
中韩35H = 中安7050H - 50~100

【⚠️ T03S 现货校验】
中安T03S的预测必须以华东市场拉丝PP的最近交易日实际成交价为基准。
若根据公式推算的价格与现货市场价偏差超过200元/吨，以现货市场价为准。
务必搜索“华东 拉丝PP 市场价 最近交易日”并参考。

【⚠️ 期货数据校验】
PP/PE期货价格必须来自大连商品交易所主力合约的最近交易日收盘价。
若搜索到的期货价格与现货价格价差超过±1500元/吨，请重新搜索确认。

【⚠️ 原油数据校验】
布伦特原油价格必须使用最近交易日ICE布伦特原油期货结算价。
近期合理区间为95-110美元/桶，若搜索结果超出该区间，请重新搜索确认。

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
数据来源：博查联网搜索最近交易日数据
免责声明：仅供参考，实际交易请结合自身情况决策
========================================
"""


def run_daily():
    target = get_target_date("daily")
    print(f"\n📅 预测目标: {target.strftime('%Y年%m月%d日')}")
    queries = [
        "华东 中安7042 价格 最近交易日",
        "华东 中安T03S 市场价 最近交易日",
        "华东 拉丝PP 市场价 最近交易日",
        "华东 中安7050H 价格 最近交易日",
        "华东 中安K8003 价格 最近交易日",
        "华东 中韩35H 价格 最近交易日",
        "布伦特原油期货 最近交易日 结算价 隆众资讯",   # 原油搜索词优化
        "PP期货主力 最近交易日 收盘价 大连商品交易所",
        "PE期货主力 最近交易日 收盘价 大连商品交易所",
        "宝丰7042 华东 价格 最近交易日",
        "华东 塑料 库存 最近交易日"
    ]
    report = search_and_analyze(get_daily_prompt(), queries)
    if report:
        send_to_feishu(report)


# ================================================
# 每周预测
# ================================================
def get_weekly_prompt():
    today = datetime.now()
    ws = (today - timedelta(days=today.weekday())).strftime('%Y年%m月%d日')
    we = (today + timedelta(days=6 - today.weekday())).strftime('%Y年%m月%d日')
    return f"""请根据本周数据，生成{ws}至{we}华东市场塑料颗粒行情预测报告。

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
    queries = [
        "华东 PP拉丝 本周价格走势",
        "华东 PE7042 本周价格走势",
        "布伦特原油 本周走势",
        "PP期货 本周走势",
        "PE期货 本周走势"
    ]
    report = search_and_analyze(get_weekly_prompt(), queries)
    if report:
        send_to_feishu(report)


# ================================================
# 每月预测
# ================================================
def get_monthly_prompt():
    today = datetime.now()
    month = today.strftime('%Y年%m月')
    return f"""请根据本月数据，生成{month}华东市场塑料颗粒价格中枢预测报告。

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
    queries = [
        "华东 PP拉丝 本月均价",
        "华东 PE7042 本月均价",
        "布伦特原油 本月均价",
        "PP新增产能 投产 计划",
        "PE新增产能 投产 计划"
    ]
    report = search_and_analyze(get_monthly_prompt(), queries)
    if report:
        send_to_feishu(report)


# ================================================
# 主入口
# ================================================
def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    print("=" * 50)
    print(f"📊 塑料颗粒AI预测系统（博查搜索版·综合优化版）")
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
