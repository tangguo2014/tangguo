# ==========================================
# 【爱玛会员自动签到脚本 V3.2】
# 【微信小程序抓包】
# 1. 变量名称：aima_gpt
# 2. 变量格式：备注#Access-Token
# 3. 多 账 号：使用 & 或 换行 分隔
#    例如：张三#token1&李四#token2
# 4. 定时 cron 26 14 * * * 每天一次自行修改
#
# [核心修复说明]
# - 自动修复：针对云服务器/本地NAS环境，强制改DNS为223.5.5.5
# - 参数修复：解决 RPC_PARAM_ILLEGAL 导致的连签0天问题
# - 环境适配：专为青龙面板/Docker环境优化
# ==========================================

import os, requests, time, urllib3

# 彻底禁用 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- 🚀 自动修复青龙/Docker DNS 环境 ---
def fix_dns():
    try:
        with open('/etc/resolv.conf', 'w') as f:
            f.write("nameserver 223.5.5.5\nnameserver 119.29.29.29\n")
        print(f"[{time.strftime('%H:%M:%S')}] ✅ 已针对本地网络环境完成解析优化")
    except: pass

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")

def push_msg(title, content):
    for module_name in ["sendNotify", "notify"]:
        try:
            m = __import__(module_name)
            if hasattr(m, "send"):
                m.send(title, content)
                return
        except: continue

def start():
    fix_dns()
    env = os.getenv("aima_gpt")
    if not env:
        log("❌ 未找到变量: aima_gpt")
        return

    session = requests.Session()
    session.trust_env = False 

    summary = []
    accounts = env.replace('\n', '&').split("&")
    
    for account in accounts:
        if "#" not in account: continue
        name, token = account.split("#")[:2]
        
        headers = {
            "Host": "scrm.aimatech.com",
            "Access-Token": token,
            "App-Id": "scrm",
            "content-type": "application/json",
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
            "Referer": "https://servicewechat.com/wx2dcfb409fd5ddfb4/216/page-frame.html"
        }

        try:
            base_url = "https://scrm.aimatech.com"
            # 这里的 ID 是之前报错 RPC 参数不合法的核心修复点
            current_id = "100001191" 
            
            # 1. 执行签到 (Join)
            join_res = session.post(f"{base_url}/aima/wxclient/mkt/activities/sign:join", 
                                   headers=headers, json={"activityId": current_id}, timeout=20, verify=False).json()
            
            code = join_res.get("code")
            if code == 200:
                res_msg = f"✅ 签到成功(+{join_res.get('content', {}).get('point', 10)})"
            elif code == 920101:
                res_msg = "⚠️ 今日已签到"
            else:
                res_msg = f"❌ {join_res.get('chnDesc', '签到失败')}"

            # 2. 获取连签天数 (Search)
            search_res = session.post(f"{base_url}/aima/wxclient/mkt/activities/sign:search", 
                                    headers=headers, json={"activityId": current_id}, timeout=20, verify=False).json()
            content = search_res.get("content") or {}
            signed_days = content.get("signed", "0")

            # 3. 查资产 (IndexInfo)
            info_res = session.get(f"{base_url}/aima/wxclient/member/IndexInfo", headers=headers, timeout=20, verify=False).json()
            user_data = info_res.get("content") or {}
            nickname = user_data.get("nickname", name)
            level = user_data.get("memberLevelName", "LV.1")
            points = (user_data.get("vipMemberPointDTO") or {}).get("pointValue", "0")

            info = (
                f"👤 {nickname} ({level})\n"
                f"{res_msg}\n"
                f"📅 连签: {signed_days}天\n"
                f"💰 积分: {points}"
            )
            summary.append(info)
            log(f"✅ {nickname} 处理完毕")
            
        except Exception as e:
            log(f"❌ {name} 异常: {e}")
            summary.append(f"👤 {name}\n💥 运行异常 (网络超时或Token过期)")

    if summary:
        push_msg("爱玛🛵签到🙋‍♀️报告", "\n---\n".join(summary))

if __name__ == "__main__":
    start()

    start()
