# -*- coding: utf-8 -*-
import requests
import os
import time
import json
from datetime import datetime

"""
名称：小程序  天牛旧衣服回收 V1.3 (精准积分版)
变量：tn_gpt （备注#token）多账号 & 分割
定时：cron 15 7 * * * 每天一次自行修改
功能：隐私脱敏 + 强制日志显示 + 1:10汇率对齐
"""

# ================= 配置区 =================
PUSH_KEY = os.getenv("QYWX_KEY") or os.getenv("QYWX_AM")

def send_msg(title, content):
    """汇总推送"""
    if not PUSH_KEY:
        print("⚠️ 未配置推送变量，跳过推送")
        return
    url = PUSH_KEY if "qyapi.weixin.qq.com" in PUSH_KEY else f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={PUSH_KEY}"
    payload = {"msgtype": "text", "text": {"content": f"🔔 {title}\n{'-'*20}\n{content}\n\n时间：{datetime.now().strftime('%H:%M:%S')}"}}
    try:
        requests.post(url, json=payload, timeout=15)
        print("🚀 推送已送达机器人")
    except:
        print("❌ 推送发送失败")

def mask_mobile(mobile):
    """手机号脱敏处理"""
    if len(str(mobile)) == 11:
        return f"{mobile[:3]}****{mobile[7:]}"
    return "未知用户"

# ================= 核心逻辑 =================

def get_info(token):
    """获取用户信息接口"""
    url = 'https://tianniu.fzjingzhou.com/api/Person/index'
    headers = {"content-type": "application/x-www-form-urlencoded", "platform": "MP-WEIXIN"}
    try:
        res = requests.post(url, headers=headers, data=f'token={token}', timeout=10).json()
        if res.get("code") == 1000:
            return res.get("data", {})
    except Exception as e:
        print(f"   ∟ ❌ 获取信息异常: {e}")
    return None

def tn_run(name, token):
    headers = {"content-type": "application/x-www-form-urlencoded", "platform": "MP-WEIXIN"}
    
    print(f"【{name}】1. 同步初始状态...")
    old_data = get_info(token)
    if not old_data:
        return f"👤 账号：{name}\n❌ 状态：Token失效或连接超时"

    old_money = float(old_data.get("exchange", 0))
    mobile = mask_mobile(old_data.get("mobile", ""))
    
    print(f"【{name}】2. 发起签到请求...")
    try:
        sign_res = requests.post('https://tianniu.fzjingzhou.com/api/Person/sign', headers=headers, data=f'token={token}', timeout=10).json()
        sign_msg = sign_res.get("msg", "未知")
        print(f"   ∟ 结果: {sign_msg}")
    except:
        sign_msg = "请求失败"

    print(f"【{name}】3. 校验余额变动...")
    new_data = get_info(token)
    new_money = float(new_data.get("exchange", 0))
    sign_num = int(new_data.get("sign_in_num", 0))
    
    # 计算变动
    change = round(new_money - old_money, 2)
    reward_status = f"🎁 获得：+{change} 元" if change > 0 else "ℹ️ 状态：非奖励日(累计中)"
    
    # 强制控制台实时输出
    log_content = (
        f"👤 账号：{name} ({mobile})\n"
        f"📝 签到：{sign_msg} (连签{sign_num}天)\n"
        f"{reward_status}\n"
        f"💰 余额：{new_money} 元"
    )
    print(log_content + "\n" + "-"*30)
    return log_content

def main():
    # 兼容两种变量名
    ck_env = os.getenv("tn_gpt") or os.getenv("tnhs")
    if not ck_env:
        print("❌ 错误：未找到环境变量 tn_gpt 或 tnhs")
        return
    
    # 兼容 & 分割
    accounts = ck_env.split("&")
    summary = []
    print(f"天牛回收 V1.3\n")

    for acc in accounts:
        acc = acc.strip()
        if not acc: continue
        if "#" in acc:
            name, token = acc.split("#", 1)
        else:
            name, token = "默认账号", acc
            
        report = tn_run(name, token)
        summary.append(report)
        time.sleep(1.5)

    if summary:
        send_msg("天牛旧衣服回收♻️", "\n\n".join(summary))

if __name__ == '__main__':
    main()
