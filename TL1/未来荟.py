# -*- coding: utf-8 -*-
import requests
import os
import time
import json
from datetime import datetime

"""
名称：未来荟（原春茧未来会）签到 V1.2
变量：wlh_gpt （备注#Authorization）多账号 & 分割
推送：自动调取推送每日签到＋积分
定时：cron 5 5 * * *每天一次自行修改
"""

# ================= 推送配置 (自动适配你现有的变量) =================
# 这里自动读取你青龙里已经配好的企业微信机器人 KEY
# 优先读取 QYWX_KEY，如果没有，你可以手动把下面引号里改成你常用的变量名
PUSH_KEY = os.getenv("QYWX_KEY") or os.getenv("QYWX_AM") 

def send_msg(title, content):
    """直接使用企业微信机器人推送"""
    print(f"【通知】{title}\n{content}")
    
    if not PUSH_KEY:
        print("⚠️ 提示：未检测到通用的推送变量，跳过通知发送。")
        return

    # 兼容处理：如果变量里带了 webhook 全地址则直接用，如果只有 key 则拼接
    url = PUSH_KEY if "qyapi.weixin.qq.com" in PUSH_KEY else f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={PUSH_KEY}"
    
    headers = {"Content-Type": "application/json"}
    payload = {
        "msgtype": "text",
        "text": {
            "content": f"🔔 {title}\n{'-'*20}\n{content}\n\n统计时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        }
    }
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=15).json()
        if res.get("errcode") == 0:
            print("🚀 企业微信机器人推送成功！")
        else:
            print(f"❌ 推送失败: {res.get('errmsg')}")
    except Exception as e:
        print(f"💥 推送异常: {str(e)}")

# ================= 核心逻辑 =================

def get_wlh_details(headers, payload):
    record_url = "https://wlhmobile.crland.com.cn/marketing/client/task/sign-in/record"
    try:
        res = requests.post(record_url, headers=headers, json=payload, timeout=10).json()
        if res.get("code") == 200:
            result = res.get("result", {})
            count = result.get("currentCycleSignInCount", 0)
            base = result.get("dailyBaseReward", 10)
            step = result.get("dailyRewardIncrement", 5)
            today_points = base + (max(0, count - 1)) * step
            return count, today_points
    except:
        pass
    return 0, 0

def wlh_sign(headers, payload):
    sign_url = "https://wlhmobile.crland.com.cn/marketing/client/task/daily/sign-in"
    try:
        res = requests.post(sign_url, headers=headers, json=payload, timeout=10).json()
        if res.get("code") == 200:
            return "✅ 签到成功"
        return f"ℹ️ {res.get('text', '今日已打卡')}"
    except:
        return "💥 接口异常"

def main():
    ck_env = os.getenv("wlh_gpt")
    if not ck_env:
        print("❌ 未找到环境变量 wlh_gpt")
        return
    
    accounts = ck_env.split("&") if "&" in ck_env else ck_env.splitlines()
    summary = []
    project_uuid = "3a59e62a07f811f1bec0aeefcf2e061a"
    app_id = "wx020209beec4251e0"

    for acc in accounts:
        acc = acc.strip()
        if "#" not in acc: continue
        name, token = acc.split("#", 1)
        print(f"👤 正在处理: {name}")

        headers = {
            "Host": "wlhmobile.crland.com.cn",
            "appId": app_id,
            "projectUuid": project_uuid,
            "Authorization": token if "Wechat" in token else f"Wechat {token}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.61(0x18003d39) NetType/WIFI Language/zh_CN",
            "Referer": f"https://servicewechat.com/{app_id}/2page-frame.html"
        }
        payload = {"custom": {"catch": True}, "projectUuid": project_uuid}

        status = wlh_sign(headers, payload)
        days, points = get_wlh_details(headers, payload)

        summary.append(
            f"👤 账号：{name}\n"
            f"📝 状态：{status}\n"
            f"📈 进度：连签第 {days} 天\n"
            f"🎁 奖励：+{points} 积分"
        )
        time.sleep(2)

    if summary:
        send_msg("未来荟签到🙋‍♀️", "\n\n".join(summary))

if __name__ == "__main__":
    main()
