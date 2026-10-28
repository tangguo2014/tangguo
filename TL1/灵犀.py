"""
APP灵犀 （灵犀晓伴AI）- 福利打卡7/14日领token活动
定时建议：cron 40 8 * * * (每天一次)
变量格式：Lx_gpt = 备注#Authorization
多账号支持：& 或换行分割
"""

import os
import sys
import time
import requests

try:
    from notify import send
except ImportError:
    def send(title, content):
        pass

CHECKIN_URL = "https://ai.suxiaoban.com:18980/api/aifactory/giftActivity/checkin"

def get_accounts():
    env_str = os.getenv("Lx_gpt", "")
    if not env_str:
        print("[!] 未检测到环境变量 Lx_gpt，请在青龙环境变量中配置！")
        sys.exit(1)

    raw_items = []
    for line in env_str.split("\n"):
        for item in line.split("&"):
            if item.strip():
                raw_items.append(item.strip())

    accounts = []
    for idx, item in enumerate(raw_items, start=1):
        parts = item.split("#")
        if len(parts) >= 2:
            tag = parts[0].strip()
            ck = parts[1].strip()
        else:
            tag = f"账号{idx}"
            ck = parts[0].strip()
        accounts.append((tag, ck))
    return accounts

def get_headers(ck):
    auth_token = ck.replace("Bearer ", "").strip()
    return {
        "Host": "ai.suxiaoban.com:18980",
        "Connection": "keep-alive",
        "Content-Type": "application/json",
        "Authorization": auth_token,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh-Hans;q=0.9",
        "X-SXB-Platform": "ios",
        "X-SXB-App-Version-Name": "1.3.1",
        "X-SXB-App-Version-Code": "32",
        "X-SXB-Device-Model": "iPhone15,2",
        "X-SXB-OS-Version": "16.3.0",
        "X-SXB-Longitude": "117.27406103043477",
        "X-SXB-Latitude": "39.22072770385467",
        "X-SXB-Adcode": "120000",
        "User-Agent": "mainProject/32 CFNetwork/1404.0.5 Darwin/22.3.0"
    }

def do_checkin(headers):
    """
    执行签到请求
    返回: (状态文本, 连续天数)
    """
    try:
        res = requests.post(CHECKIN_URL, headers=headers, json={}, timeout=15)
        res_json = res.json()
        code = res_json.get("code")
        msg = res_json.get("msg", "")
        data = res_json.get("data") or {}

        streak = data.get("streakDays", 0)

        # 1. 成功签到
        if code == 0:
            holiday = data.get("holidayAutoCheckedIn", False)
            tip = "签到成功"
            if holiday:
                tip += " (假期自动打卡)"
            return tip, streak

        # 2. 今日已签到（正常处理，不报错）
        if "已签到" in str(msg) or "repeat" in str(msg).lower():
            tip = "今日已签到 (无需重复执行)"
            return tip, streak

        # 3. 其它异常提示
        return f"未成功 ({msg})", streak

    except Exception as e:
        return f"请求异常 ({str(e)})", 0

def process_account(index, total, tag, ck):
    headers = get_headers(ck)
    print(f"\n==================== [账号 {index}/{total}] {tag} ====================")
    print("[1] 正在执行每日签到...")

    tip, streak = do_checkin(headers)
    streak_str = f"已连签 {streak} 天" if streak > 0 else "已打卡"
    print(f"    签到结果: {tip} | 连续天数: {streak_str}")

    card = (
        f"👤 账号备注: {tag}\n"
        f"📅 连续签到: {streak_str}\n"
        f"📝 签到状态: {tip}"
    )
    return card

def main():
    accounts = get_accounts()
    total_acc = len(accounts)
    print(f"[*] 成功加载 {total_acc} 个灵犀账号配置\n")

    cards = []
    for idx, (tag, ck) in enumerate(accounts, start=1):
        card = process_account(idx, total_acc, tag, ck)
        cards.append(card)
        if idx < total_acc:
            time.sleep(2)

    separator = "\n" + "-" * 26 + "\n"
    content = separator.join(cards)

    if cards:
        send("灵犀AI签到🙋‍♀️", content)

if __name__ == "__main__":
    main()
