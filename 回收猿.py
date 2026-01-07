# -*- coding: utf-8 -*-
"""
回收猿（修改版V3）
变量: hsy_gpt ，username=xxx;NAME=xxx多账号@分割
自动签到 + 抽奖 + 查询 ＋今日/七日累计奖励统计
支持企业微信机器人推送
支持备注名、多账号、彩色日志、美化推送
"""

import os, time, random, hashlib, requests, logging
from urllib.parse import urlencode
from datetime import datetime, timedelta

# ========= 推送模块（青龙企业微信机器人）=========
try:
    from notify import send as ql_send
except Exception:
    def ql_send(title, content):
        print(f"\n🔔 {title}\n{content}\n")

# ========= 彩色输出（可选）=========
try:
    from colorama import init, Fore, Style
    init(autoreset=True)
    def line(char="━", length=55): print(Fore.CYAN + char * length + Style.RESET_ALL)
    def color(text, c): return getattr(Fore, c.upper()) + str(text) + Style.RESET_ALL
except ImportError:
    def line(char="━", length=55): print(char * length)
    def color(text, c): return str(text)

# ========= 配置 =========
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_7_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.52(0x18003426) NetType/WIFI Language/zh_CN"
ENV_VAR = "hsy_gpt"
REQUEST_TIMEOUT = 10

# ========= 解析环境变量 =========
def parse_env_var(env_value):
    """
    支持格式：
    username=abc;NAME=张三@username=def;NAME=李四
    或简写：abc@def
    """
    accounts = []
    for part in env_value.split("@"):
        part = part.strip()
        if not part:
            continue
        conf = {}
        for kv in part.split(";"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                conf[k.strip().upper()] = v.strip()
        if not conf.get("USERNAME"):
            conf["USERNAME"] = part
        accounts.append(conf)
    return accounts

# ========= 核心类 =========
class Hsy:
    def __init__(self, username, name=None):
        self.key = "1079fb245839e765"
        self.scret = "UppwYkfBlk"
        self.username = username
        self.name = name
        self.session = requests.Session()
        self.headers = {
            'User-Agent': UA,
            'xweb_xhr': "1",
            'content-type': "application/json",
            'referer': "https://servicewechat.com/wxadd84841bd31a665/84/page-frame.html",
        }

    def md5_sign(self, params: dict) -> str:
        s = urlencode(sorted(params.items())) + self.scret
        m = hashlib.md5(); m.update(s.encode('utf-8'))
        return m.hexdigest()

    def req(self, url, params):
        try:
            r = self.session.get(url, params=params, headers=self.headers, timeout=REQUEST_TIMEOUT)
            return r.json()
        except Exception as e:
            logging.warning("请求错误: %s", e)
            return {}

    # --- 签到 ---
    def signin(self):
        url = "https://www.52bjy.com/api/app/hsy.php"
        params = {
            'action': "user", 'app': "hsywx",
            'appkey': self.key, 'merchant_id': "2",
            'method': "qiandao", 'username': self.username, 'version': "2"
        }
        params['sign'] = self.md5_sign(params)
        r = self.req(url, params)
        return r.get('code') == 200, r.get('message', '')

    # --- 抽奖 ---
    def draw(self):
        url = "https://www.52bjy.com/api/app/promotionjgg.php"
        params = {
            'action': "addorder", 'appkey': self.key,
            'mallid': "2580", 'merchant_id': "2",
            'order_status': "3", 'promotion_id': "258",
            'username': self.username,
        }
        params['sign'] = self.md5_sign(params)
        r = self.req(url, params)
        return r.get('is_success', False), r.get('message', '')

    # --- 查询奖励记录（含今日 & 七日累计统计）---
    def awardinfo(self):
        url = "https://www.52bjy.com/api/app/envcash.php"
        params = {
            'action': "awardlist", 'appkey': self.key,
            'genre': "0", 'merchant_id': "2",
            'page': "1", 'type': "award",
            'username': self.username,
        }
        params['sign'] = self.md5_sign(params)
        r = self.req(url, params)
        records = r.get('data', {}).get('record', [])

        today_str = datetime.now().strftime("%Y-%m-%d")
        now = datetime.now()
        seven_days_ago = now - timedelta(days=7)

        today_total = 0.0
        week_total = 0.0
        for rec in records:
            amt = float(rec.get("amount", "+0").replace("+", ""))
            addtime = rec.get("addtime", "")
            try:
                record_date = datetime.strptime(addtime, "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            # 今日奖励
            if addtime.startswith(today_str):
                today_total += amt
            # 七日累计奖励
            if record_date >= seven_days_ago:
                week_total += amt

        return records, today_total, week_total

    # --- 主任务 ---
    def task(self):
        name_display = self.name or self.username
        result_lines, markdown_msg = [], []

        line()
        print(f"💎 账号：{color(name_display, 'GREEN')}（{self.username}）")
        t = random.randint(1, 3)
        print(f"🕐 随机等待：{t} 秒...\n")
        time.sleep(t)

        # 签到
        ok, msg = self.signin()
        print("📋 签到：", color("成功" if ok else "失败", "GREEN" if ok else "RED"), msg)
        result_lines.append(f"📋 签到：{'✅ 成功' if ok else '❌ 失败'}（{msg}）")
        markdown_msg.append(f"> 📋 签到：{'✅ 成功' if ok else '❌ 失败'}（{msg}）")

        # 抽奖
        ok, msg = self.draw()
        print("🎯 抽奖：", color("成功" if ok else "失败", "GREEN" if ok else "YELLOW"), msg)
        result_lines.append(f"🎯 抽奖：{'✅ 成功' if ok else '⚠️ '+msg}")
        markdown_msg.append(f"> 🎯 抽奖：{'✅ 成功' if ok else '⚠️ '+msg}")

        # 查询奖励
        rec, today_total, week_total = self.awardinfo()
        print(color("\n📊 最近奖励记录（前5条）", "CYAN"))
        result_lines.append("📊 奖励记录：")
        markdown_msg.append("> 📊 奖励记录：")
        for i, item in enumerate(rec[:5], 1):
            line_text = f"  {i}. {item['addtime']}｜{item['amount']}｜{item['reason']}"
            print(line_text)
            result_lines.append(line_text)
            markdown_msg.append(f"> {i}. {item['addtime']}｜{item['amount']}｜{item['reason']}")

        # 今日 & 七日累计输出
        print(color(f"\n💰 今日奖励金：+{today_total:.2f} 元", "GREEN"))
        print(color(f"💎 七日累计奖励金：+{week_total:.2f} 元", "YELLOW"))

        result_lines.append(f"\n💰 今日奖励金：+{today_total:.2f} 元")
        result_lines.append(f"💎 七日累计奖励金：+{week_total:.2f} 元")
        markdown_msg.append(f"> 💰 今日奖励金：+{today_total:.2f} 元")
        markdown_msg.append(f"> 💎 七日累计奖励金：+{week_total:.2f} 元")

        line()

        title = f"🎯 {name_display}｜签到结果"
        text = "\n".join(result_lines)
        markdown = f"**{title}**\n" + "\n".join(markdown_msg)
        return title, text, markdown

# ========= 主入口 =========
def main():
    raw = os.getenv(ENV_VAR, "").strip()
    if not raw:
        print(f"❌ 未检测到环境变量 {ENV_VAR}")
        return
    accs = parse_env_var(raw)
    print(f"🚀 共检测到 {len(accs)} 个账号\n")

    all_results, all_markdown = [], []
    for i, conf in enumerate(accs, 1):
        u = conf.get("USERNAME")
        n = conf.get("NAME", f"账号{i}")
        client = Hsy(u, n)
        title, text, markdown = client.task()
        all_results.append(f"{title}\n{text}")
        all_markdown.append(markdown)
        time.sleep(1)

    summary_md = "\n\n".join(all_markdown)
    signature = "\n\n> 我喜欢的人…也能喜欢上自己，我认为这就是奇迹。——月色真美 🌙"
    ql_send("📬 回收猿 签到汇总", summary_md + signature)
    print("\n✅ 推送已发送至企业微信机器人（青龙 notify）")

if __name__ == "__main__":
    main()
