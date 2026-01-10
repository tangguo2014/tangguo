# -*- coding:utf-8 -*-
"""
=========================================================
  🐜 飞蚂蚁 (FMY) 助手 V5.0

【功能特性】
  1. 自动签到 & 步数兑换 (协议版本 V2.00.01)。
  2. 精算逻辑：目前余额 = 累计获得 - 累计支出。
  3. 账单穿透：强制显示最近 8 条流水，收支一目了然。

【使用说明】
  - 环境变量：fmy_gpt = 备注@Token (多账号用 & 隔开)
  - 变量示例：备注@eyJhbGci...
=========================================================
"""
import requests, os, time, json
from datetime import datetime

# --- 配置区 ---
class Config:
    P_KEY = "F2EE24892FBF66F0AFF8C0EB532A9394"
    UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) AppleWebKit/605.1.15"
    VERSION = "V2.00.01"

# --- 推送模块 ---
def send_qywx(title, content):
    qy_key = os.environ.get("QYWX_KEY")
    if not qy_key: return
    url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={qy_key}"
    try:
        requests.post(url, json={
            "msgtype": "text", 
            "text": {"content": f"🐜 {title}\n{'='*25}\n{content}"}
        }, timeout=10)
    except: pass

# --- 核心运行类 ---
def run_fmy(name, tk):
    print(f"🚀 正在运行账号: {name}") 
    report = [f"👤 账号: {name}"]
    
    auth_token = f"bearer {tk}" if not tk.lower().startswith("bearer") else tk
    headers = {
        "device-model": "iPhone 14 Pro",
        "content-type": "application/json;charset=utf8",
        "Authorization": auth_token,
        "User-Agent": Config.UA,
        "Referer": "https://servicewechat.com/wx501990400906c9ff/483/page-frame.html"
    }

    # 1. 自动执行任务
    try:
        requests.post("https://openapp.fmy90.com/sign/new/do", headers=headers, 
                      json={"platformKey": Config.P_KEY, "version": Config.VERSION}, timeout=5)
        for _ in range(3):
            requests.post("https://openapp.fmy90.com/step/exchange", headers=headers, 
                          json={"platformKey": Config.P_KEY, "mini_scene": 1089, "steps": 50000, "version": Config.VERSION}, timeout=5)
            time.sleep(1)
    except: pass

    # 2. 统计逻辑
    total_get, total_spend, income_today = 0, 0, 0
    today_str = datetime.now().strftime("%Y-%m-%d")
    logs_list = []
    
    try:
        # 获取累计获得
        a_res = requests.get("https://openapp.fmy90.com/user/new/beans/info", headers=headers, 
                             params={"type": "1", "platformKey": Config.P_KEY}, timeout=10).json()
        total_get = int(a_res.get('data', {}).get('totalCount', 0))
        
        # 获取流水(1=收入, 2=支出)
        for t in [1, 2]:
            l_res = requests.get("https://openapp.fmy90.com/user/beans/log", headers=headers, 
                                 params={"pageSize": "20", "type": t, "platformKey": Config.P_KEY}, timeout=10).json()
            actual_logs = l_res.get('data', {}).get('data', [])
            for i in actual_logs:
                val = abs(int(i.get('beanNum', 0)))
                add_time = i.get('addTime', '')
                if t == 1:
                    if today_str in add_time: income_today += val
                else:
                    total_spend += val
                logs_list.append({"time": add_time, "text": f"  {add_time[5:10]} {'➕' if t==1 else '➖'} {val} ({i.get('beanInfo')})"})
    except: pass

    # 3. 数据整合
    current_balance = total_get - total_spend
    logs_list.sort(key=lambda x: x["time"], reverse=True)

    report.append(f"🏦 【目前余额】：{current_balance}")
    report.append(f"💰 【累计获得】：{total_get}")
    report.append(f"📉 【累计支出】：-{total_spend}")
    report.append(f"📈 【今日获得】：+{income_today}")
    report.append("\n📆 账单摘要 (最近8条):")
    for l in logs_list[:8]:
        report.append(l["text"])
    
    # 控制台打印输出
    final_report_str = "\n".join(report)
    print(f"{'-'*35}\n{final_report_str}\n{'-'*35}") 
    return final_report_str

# --- 主函数 ---
def main():
    token_str = os.environ.get("fmy_gpt")
    if not token_str: 
        print("❌ 未找到环境变量 fmy_gpt，请先配置。")
        return
    
    final_msgs = []
    lines = token_str.replace('&', '\n').split('\n')
    for line in lines:
        if '@' in line:
            name, tk = line.split('@', 1)
            final_msgs.append(run_fmy(name.strip(), tk.strip()))
        elif line.strip():
            final_msgs.append(run_fmy("默认账号", line.strip()))
    
    if final_msgs:
        send_qywx("飞蚂蚁运行报告", "\n\n".join(final_msgs))
    print(f"✨ 所有账号执行完毕: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == '__main__':
    main()

