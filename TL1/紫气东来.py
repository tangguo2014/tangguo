# -*- coding: utf-8 -*-
import requests, os, time, json
from datetime import datetime

"""
名称：小紫有约签到 V2.1（小程序搜索小紫有约）
说明：变量值SESSION在sxkyziqidonglai.cn域名下的cookie中
变量：zqdl_gpt (格式：备注#SESSION) 多账号用 & 分割
定时：cron 5 6 * * * 每天一次自行修改
功能：签到 + 奖励变动 + 总积分显示 (纯净版)
"""

PUSH_KEY = os.getenv("QYWX_KEY") or os.getenv("QYWX_AM")

def send_msg(title, content):
    if not PUSH_KEY: return
    url = PUSH_KEY if "qyapi.weixin.qq.com" in PUSH_KEY else f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={PUSH_KEY}"
    payload = {"msgtype": "text", "text": {"content": f"🔔 {title}\n{'-'*20}\n{content}\n\n时间：{datetime.now().strftime('%H:%M:%S')}"}}
    requests.post(url, json=payload, timeout=15)

def get_real_total(session_val):
    """提取 getUserInfo 接口中的 balance 字段"""
    url = "https://sxkyziqidonglai.cn/api/mobile/eShop/eshopVipUser/getUserInfo"
    headers = {
        "Cookie": f"SESSION={session_val}",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X)"
    }
    data = "siteId=SITE_33254242630091515087"
    try:
        res = requests.post(url, headers=headers, data=data, timeout=10).json()
        if res.get("success"):
            # 根据最新抓包，总积分存放在 data 下的 balance 字段
            total = res.get("data", {}).get("balance", "0")
            return f"{total}"
    except: pass
    return "查询失败"

def get_score_flow(session_val):
    """获取流水奖励"""
    url = "https://sxkyziqidonglai.cn/api/mobile/eShop/couponVoucher/queryCouponVoucherFlow"
    headers = {
        "Cookie": f"SESSION={session_val}",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X)"
    }
    data = "siteId=SITE_33254242630091515087&searchType=1&page=1&pageSize=1"
    try:
        res = requests.post(url, headers=headers, data=data, timeout=10).json()
        if res.get("success"):
            records = res.get("data", {}).get("data", [])
            if records:
                latest = records[0]
                amount = latest.get("tradeAmountPoints", "0")
                reason = latest.get("reason", "签到奖励")
                return f"+{amount} ({reason})"
    except: pass
    return "同步中"

def zqdl_run(name, session_val):
    # 净化 SESSION
    if "SESSION=" in session_val:
        session_val = session_val.split("SESSION=")[1].split(";")[0]
    elif ";" in session_val:
        session_val = session_val.split(";")[0]
    session_val = session_val.strip()

    print(f"== 账号: {name} 执行中 ==")
    sign_url = "https://sxkyziqidonglai.cn/api/mobile/activity-v2/activity/launchByValidater"
    headers = {"Content-Type": "application/json", "Cookie": f"SESSION={session_val}"}
    payload = {"actCode": "SIGNIN202602271421193146", "siteId": "SITE_33254242630091515087"}
    
    try:
        res = requests.post(sign_url, headers=headers, json=payload, timeout=10).json()
        msg = res.get("msg", "请求失败")
        status = "✅ 签到成功" if res.get("success") else f"ℹ️ {msg}"

        time.sleep(2.5)
        reward = get_score_flow(session_val)
        total = get_real_total(session_val)
        
        log = (
            f"👤 账号：{name}\n"
            f"📝 状态：{status}\n"
            f"🎁 奖励：{reward}\n"
            f"💰 总计：{total} 积分"
        )
        print(f"   ∟ {status} | 奖励: {reward} | 总计: {total}")
        return log
    except Exception as e:
        print(f"   ∟ 💥 异常: {e}")
        return f"👤 账号：{name}\n❌ 接口报错"

def main():
    ck_env = os.getenv("zqdl_gpt")
    if not ck_env:
        print("错误：请设置 zqdl_gpt 变量")
        return
    
    accounts = ck_env.split("&")
    summary = []
    print("开始执行紫气东来任务...\n")

    for acc in accounts:
        if not acc.strip(): continue
        name, s_val = acc.split("#", 1) if "#" in acc else ("默认账号", acc)
        summary.append(zqdl_run(name, s_val))
        print("-" * 25)
        time.sleep(1.5)

    if summary:
        send_msg("小紫有约🙋‍♀️签到", "\n\n".join(summary))

if __name__ == '__main__':
    main()
