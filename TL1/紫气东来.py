# -*- coding: utf-8 -*-
import requests, os, time, json
from datetime import datetime

"""
名称：小紫有约签到 V2.1（小程序搜索小紫有约）
说明：变量值SESSION在sxkyziqidonglai.cn域名下的cookie中
变量：zqdl_gpt (格式：备注#SESSION) 多账号用 & 分割
定时：cron 5 6 * * * 每天一次自行修改
功能：签到 + 奖励变动 + 总积分显示 (纯净版)
备注：活动每月更新，替换下面的siteId和actCode值即可
"""

# --- 🚀 核心推送：直接调用青龙标准 notify ---
try:
    from notify import send
except ImportError:
    def send(t, c): print(f"\n📣 [推送预览]\n{t}\n{c}")

# ================= 🔧 维护开关 (每月改这里) =================
# 每月最新抓包替换
SITE_ID = "SITE_33254242630091515087"
ACT_CODE = "SG2633" 
# =========================================================

def get_real_total(session_val):
    url = "https://sxkyziqidonglai.cn/api/mobile/eShop/eshopVipUser/getUserInfo"
    headers = {"Cookie": f"SESSION={session_val}", "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X)"}
    data = f"siteId={SITE_ID}"
    try:
        res = requests.post(url, headers=headers, data=data, timeout=10).json()
        return res.get("data", {}).get("balance", "0") if res.get("success") else "0"
    except: return "查询失败"

def get_score_flow(session_val):
    url = "https://sxkyziqidonglai.cn/api/mobile/eShop/couponVoucher/queryCouponVoucherFlow"
    headers = {"Cookie": f"SESSION={session_val}", "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X)"}
    data = f"siteId={SITE_ID}&searchType=1&page=1&pageSize=1"
    try:
        res = requests.post(url, headers=headers, data=data, timeout=10).json()
        records = res.get("data", {}).get("data", [])
        if res.get("success") and records:
            latest = records[0]
            # 兼容：如果积分是整数，显示更美观
            amt = latest.get('tradeAmountPoints', '0')
            return f"+{amt} ({latest.get('reason', '签到')})"
    except: pass
    return "同步中"

def zqdl_run(index, name, session_val):
    # 净化 SESSION
    session_val = session_val.split("SESSION=")[1].split(";")[0] if "SESSION=" in session_val else session_val.strip()
    print(f"[{index}] >>> 账号: {name} 正在起速...")
    
    headers = {
        "Content-Type": "application/json", 
        "Cookie": f"SESSION={session_val}", 
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X)",
        "Referer": f"https://sxkyziqidonglai.cn/activity/signIn?siteId={SITE_ID}&actCode={ACT_CODE}"
    }
    # 核心 Payload 使用最新抓包参数
    payload = {"actCode": ACT_CODE, "siteId": SITE_ID}
    
    try:
        res = requests.post("https://sxkyziqidonglai.cn/api/mobile/activity-v2/activity/launchByValidater", headers=headers, json=payload, timeout=10).json()
        status = "✅ 签到成功" if res.get("success") else f"ℹ️ {res.get('msg', '今日已打卡')}"
        
        time.sleep(2)
        reward = get_score_flow(session_val)
        total = get_real_total(session_val)
        
        print(f"   ∟ {status} | 奖励: {reward} | 余额: {total}")
        return f"👤 账户：{name}\n📝 状态：{status}\n🎁 奖励：{reward}\n💰 余额：{total} 元"
    except Exception as e:
        print(f"   ∟ 💥 异常: {e}")
        return f"👤 账户：{name}\n❌ 运行异常"

def main():
    env = os.getenv("zqdl_gpt")
    if not env: return print("❌ 错误：请设置 zqdl_gpt 变量")
    
    print(f"🚀 小紫有约 4月版 启动 | {datetime.now().strftime('%H:%M:%S')}")
    print(f"📌 SITE_ID: {SITE_ID} | ACT_CODE: {ACT_CODE}\n" + "="*45)
    
    summary = []
    for i, acc in enumerate(env.split("&"), 1):
        if not acc.strip(): continue
        name, s_val = acc.split("#", 1) if "#" in acc else (f"账户{i}", acc)
        summary.append(zqdl_run(i, name, s_val))
        time.sleep(2)
        
    if summary:
        send("小紫有约🙋‍♀️签到报告", "\n\n".join(summary))

if __name__ == '__main__':
    main()
