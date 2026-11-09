# -*- coding:utf-8 -*-
"""
=========================================================
 vx小程序  🐜 飞蚂蚁 (FMY) 助手  
=========================================================
yyb版本：不需要抓包，没有yyb的用抓包版本

变量：yyb_gpt
      格式： YYB地址#ref
      例：   http://196.25.0.1:8000#1
      多账号：一行一个
流程：YYB 取 wx.login code → /auth/wx/login 换 JWT（15 天）→ 签到 + 步数兑换

注意：YYB 同账号取码有间隔限制，撞到 502/429 就等一会儿再跑，别连续重试
定时：cron 建议：35 8 * * *
=========================================================
"""
import requests, os, time, json, base64
from datetime import datetime

# --- 配置区 ---
class Config:
    P_KEY = "F2EE24892FBF66F0AFF8C0EB532A9394"
    UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) AppleWebKit/605.1.15"
    VERSION = "V2.00.01"
    APP_ID = "wx501990400906c9ff"        # 飞蚂蚁小程序 appid（YYB 取码用）
    LOGIN_URL = "https://openapp.fmy90.com/auth/wx/login"


ENV_NAME = "yyb_gpt"        # 格式： YYB地址#ref，多账号一行一个


# ==================== YYB 取码 → JWT ====================
def parse_accounts(raw):
    """yyb_gpt 格式：
         http://172.17.0.1:18000#1
         http://172.17.0.1:18000#2
       一行一个账号，& 或换行分隔。
    """
    out = []
    for line in (raw or "").replace("\n", "&").split("&"):
        line = line.strip()
        if not line or "#" not in line:
            continue
        url, ref = line.rsplit("#", 1)
        url, ref = url.strip().rstrip("/"), ref.strip()
        if not url or not ref:
            print("⚠️ 跳过格式不正确的行：%s" % line[:40])
            continue
        if not url.startswith(("http://", "https://")):
            url = "http://" + url
        out.append({"name": "账号" + ref, "url": url, "ref": ref})
    return out


def yyb_get_code(yyb_url, ref):
    """调 YYB 取 wx.login code
    - 409 → login_buffer 过期，需要重新扫码（致命错误，不重试）
    - -14 → 上游限流，睡 8 秒重试，最多 3 次
    - 兼容不同版本响应结构：result / data.result / data
    """
    last = ""
    for i in range(3):
        try:
            r = requests.post(
                yyb_url + "/wxapp/getCode",
                headers={"Content-Type": "application/json"},
                json={"ref": str(ref), "app_id": Config.APP_ID},
                timeout=40)

            if r.status_code == 409:
                raise RuntimeError("YYB 账号授权已过期（409），请重新扫码登录")

            j = r.json()
            if j.get("code") not in (0, "0"):
                last = str(j.get("error") or j.get("msg") or j)[:120]
            else:
                inner = j.get("result")
                if inner is None:
                    d = j.get("data") or {}
                    inner = d.get("result", d) if isinstance(d, dict) else d
                code = (inner or {}).get("code")
                if code:
                    return code
                last = "YYB 未返回有效 code"

        except RuntimeError:
            raise                                   # 409 直接抛出，不重试
        except Exception as e:
            last = str(e)[:120]

        if i < 2:
            print(f"   ⏳ YYB 取码失败({last})，8 秒后重试 {i+1}/2")
            time.sleep(8)

    raise RuntimeError("YYB 取码失败: " + last)


def code_to_jwt(code):
    """用 code 换 JWT（返回 token + 用户信息）"""
    r = requests.post(
        Config.LOGIN_URL,
        headers={"content-type": "application/x-www-form-urlencoded",
                 "device-version": "iOS 16.3",
                 "device-model": "iPhone 14 Pro<iPhone15,2>",
                 "User-Agent": Config.UA + " MicroMessenger/8.0.76(0x18004c3a) NetType/WIFI Language/zh_CN",
                 "Referer": "https://servicewechat.com/%s/534/page-frame.html" % Config.APP_ID},
        data={"code": code,
              "platformKey": Config.P_KEY,
              "version": Config.VERSION,
              "vital": "",
              "partner_platform_key": "",
              "mini_scene": "1232"},
        timeout=30)
    j = r.json()
    if j.get("code") != 200:
        raise RuntimeError("换 JWT 失败: %s" % (j.get("message") or j))
    return j["data"]["token"], (j["data"].get("user") or {})


def yyb_refresh_token(yyb_url, ref):
    """YYB 走一整套：取码 → 换 JWT"""
    print(f"   🔄 通过 YYB 续期 token（{yyb_url} ref={ref}）…")
    code = yyb_get_code(yyb_url, ref)
    token, user = code_to_jwt(code)
    print(f"   ✅ 续期成功，账号 {user.get('userName') or ''}，环保豆 {user.get('userBeans')}")
    return token


def jwt_expired(token):
    """本地解析 JWT 的 exp，判断是否已过期/即将过期（提前 1 天）"""
    try:
        t = token.replace("bearer ", "").replace("Bearer ", "").strip()
        payload = t.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        exp = int(data.get("exp") or 0)
        return exp - time.time() < 86400          # 剩不足 1 天也算过期
    except Exception:
        return False


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
    raw = (os.environ.get(ENV_NAME) or "").strip()
    if not raw:
        print(f"❌ 未设置环境变量 {ENV_NAME}，格式：YYB地址#ref（多账号一行一个）")
        return

    accounts = parse_accounts(raw)
    if not accounts:
        print(f"❌ {ENV_NAME} 没有解析出有效账号")
        return

    print(f"🚀 共 {len(accounts)} 个账号")
    blocks, okn = [], 0
    for acc in accounts:
        try:
            tk = yyb_refresh_token(acc["url"], acc["ref"])
        except Exception as e:
            msg = str(e)[:120]
            print(f"❌ [{acc['name']}] YYB 换 token 失败：{msg}")
            blocks.append(f"👤 {acc['name']}\n❌ YYB 换 token 失败：{msg}")
            continue
        blocks.append(run_fmy(acc["name"], tk))
        okn += 1
        time.sleep(3)          # 同账号/同 IP 间隔，降低限流概率

    if blocks:
        send_qywx("飞蚂蚁运行报告", "\n\n".join(blocks))
    print(f"✨ 完成 {okn}/{len(accounts)}，结束于 {datetime.now().strftime('%H:%M:%S')}")


if __name__ == '__main__':
    main()
