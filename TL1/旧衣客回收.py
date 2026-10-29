"""
微信小程序：旧衣客回收 V1.1
功能设定：每日自动签到 + 5次广告任务全自动化
定时建议：cron 30 8 * * * 每天一次自己修改
变量格式：Jyk_gpt = 备注#Authorization 
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

HOME_URL = "https://jyk.scjyx.com/api/checkin/home"
COMMON_SIGN_URL = "https://jyk.scjyx.com/api/checkin/sign"
AD_PREPARE_URL = "https://jyk.scjyx.com/api/checkin/ad/prepare"
AD_SIGN_URL = "https://jyk.scjyx.com/api/checkin/ad/sign"

def get_accounts():
    env_str = os.getenv("Jyk_gpt", "")
    if not env_str:
        print("[!] 未检测到环境变量 Jyk_gpt，请在青龙环境变量中配置！")
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
            token = parts[1].strip()
        else:
            tag = f"账号{idx}"
            token = parts[0].strip()
        accounts.append((tag, token))
    return accounts

def get_headers(token):
    auth_token = token.replace("Bearer ", "").strip()
    return {
        "Host": "jyk.scjyx.com",
        "Connection": "keep-alive",
        "content-type": "application/json",
        "Authorization": f"Bearer {auth_token}",
        "X-Payconfig-Id": "2",
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.76(0x18004c3a) NetType/4G Language/zh_CN",
        "Referer": "https://servicewechat.com/wx3f0209cc35a953a4/104/page-frame.html"
    }

def get_home_info(headers):
    try:
        res = requests.get(HOME_URL, headers=headers, timeout=15)
        res_json = res.json()
        if res_json.get("errno") == 0:
            return res_json.get("data", {})
    except Exception as e:
        print(f"    [×] 获取首页数据异常: {str(e)}")
    return {}

def fetch_ad_token(headers):
    """通过 prepare 接口获取单次广告票据"""
    try:
        prep = requests.post(AD_PREPARE_URL, headers=headers, json={}, timeout=15)
        prep_json = prep.json()
        if prep_json.get("errno") == 0:
            return (prep_json.get("data") or {}).get("ad_token", "")
    except Exception:
        pass
    return ""

def do_common_sign(headers, is_ad_day=False):
    """
    基础签到：
    - 免广告日（第 1、2、4 天）：直接提交 {}
    - 广告日（第 3、5、6、7 天）：自动获取 ad_token，模拟播放 17 秒后提交
    """
    payload = {}
    if is_ad_day:
        print("    [!] 今日为周期广告签到日，准备获取广告票据...")
        ad_token = fetch_ad_token(headers)
        if ad_token:
            print("        -> 模拟广告播放中，等待 17 秒...")
            time.sleep(17)
            payload = {"ad_token": ad_token}

    try:
        res = requests.post(COMMON_SIGN_URL, headers=headers, json=payload, timeout=15)
        res_json = res.json()
        errno = res_json.get("errno")
        msg = res_json.get("message", "")

        if errno == 0:
            data = res_json.get("data", {})
            amount = data.get("reward", {}).get("amount", 0)
            sign_text = data.get("reward", {}).get("sign_type_text", "普通签到")
            return True, f"+{amount} 积分 ({sign_text})"

        # 容错：如果当天被临时标记为广告日，自动补调凭证
        if "播放记录" in str(msg) and not is_ad_day:
            print("    [!] 服务端提示需要广告凭据，正在补领并模拟播放...")
            ad_token = fetch_ad_token(headers)
            if ad_token:
                print("        -> 模拟广告播放中，等待 17 秒...")
                time.sleep(17)
                retry_res = requests.post(COMMON_SIGN_URL, headers=headers, json={"ad_token": ad_token}, timeout=15).json()
                if retry_res.get("errno") == 0:
                    data = retry_res.get("data", {})
                    amount = data.get("reward", {}).get("amount", 0)
                    return True, f"+{amount} 积分 (广告签到)"

        return False, f"未成功 ({msg})"

    except Exception as e:
        return False, f"请求异常 ({str(e)})"

def do_single_ad_sign(headers, ad_token):
    """提交广告签到领取奖励"""
    try:
        res = requests.post(AD_SIGN_URL, headers=headers, json={"ad_token": ad_token}, timeout=15)
        res_json = res.json()
        errno = res_json.get("errno")
        msg = res_json.get("message", "")

        if errno == 0:
            data = res_json.get("data", {})
            amount = data.get("reward", {}).get("amount", 0)
            snapshot = data.get("snapshot", {})
            today_count = snapshot.get("today_count", 0)
            daily_limit = snapshot.get("daily_limit", 5)
            return True, amount, today_count, daily_limit, "成功"
        else:
            return False, 0, 0, 0, msg
    except Exception as e:
        return False, 0, 0, 0, str(e)

def run_ad_tasks(headers, ad_data):
    """循环执行每日 5 次广告任务：每次广告 17 秒，任务间隔 2.5 秒"""
    today_count = ad_data.get("today_count", 0)
    daily_limit = ad_data.get("daily_limit", 5)

    if ad_data.get("enabled") is False:
        print(f"    [i] 广告任务功能未开启")
        return 0, 0, today_count

    if today_count >= daily_limit:
        print(f"    [i] 今日广告任务已全部完成 ({today_count}/{daily_limit})")
        return 0, 0, today_count

    limit = daily_limit if daily_limit else 5
    success_times = 0
    total_gained = 0
    cur_done = today_count

    while cur_done < limit:
        idx = cur_done + 1
        print(f"    [▶] 正在执行第 {idx}/{limit} 轮广告任务...")

        # 1. 获取广告票据
        token = fetch_ad_token(headers)
        if not token:
            print("        × 获取 ad_token 失败，跳出广告任务")
            break

        # 2. 模拟广告播放 17 秒
        print("        -> 模拟广告播放中，等待 17 秒...")
        time.sleep(17)

        # 3. 提交销账领奖
        ok, amount, current_cnt, lim, info = do_single_ad_sign(headers, token)
        if ok:
            success_times += 1
            total_gained += int(float(amount))
            cur_done = current_cnt if current_cnt else idx
            print(f"        √ 第 {idx} 轮领取成功: +{amount} 积分 (今日进度: {cur_done}/{lim})")
            if cur_done >= (lim or limit):
                break
        else:
            print(f"        × 第 {idx} 轮提交失败: {info}")
            break

        # 4. 任务间隔缓冲 2.5 秒
        if cur_done < limit:
            print("        -> 任务冷却间隔 2.5 秒...")
            time.sleep(2.5)

    return success_times, total_gained, cur_done

def process_account(index, total, tag, token):
    headers = get_headers(token)
    print(f"\n==================== [账号 {index}/{total}] {tag} ====================")

    # 1. 查询初始状态
    print("[1] 正在查询账户初始状态...")
    home_data = get_home_info(headers)
    today_signed = home_data.get("today_signed", False)
    day_index = home_data.get("today_cycle_day", 1)
    init_integral = home_data.get("integral", "0")
    print(f"    当前总积分: {init_integral} | 签到周期: 第 {day_index} 天 | 今日基础签到: {'已签' if today_signed else '未签'}")

    # 2. 判断今天基础签到是否为广告日
    days_list = home_data.get("days", [])
    is_ad_day = False
    for d in days_list:
        if d.get("day_index") == day_index:
            is_ad_day = (d.get("ad_enabled") == 1)
            break

    # 3. 执行基础签到
    print("[2] 正在处理基础签到...")
    if not today_signed:
        ok, common_res = do_common_sign(headers, is_ad_day=is_ad_day)
        print(f"    结果: {common_res}")
    else:
        common_res = "今日已签到 (无需重复执行)"
        print(f"    结果: {common_res}")

    # 缓冲 2.5 秒进入广告任务
    time.sleep(2.5)

    # 4. 执行看广告任务（5 次循环）
    print("[3] 正在处理看广告任务...")
    ad_data = home_data.get("ad", {})
    suc_count, gained, final_done = run_ad_tasks(headers, ad_data)

    # 5. 刷新最终积分
    time.sleep(1.5)
    final_home = get_home_info(headers)
    final_integral = final_home.get("integral", init_integral)
    print(f"[4] 执行完毕，最新账户总积分: {final_integral}")

    ad_text = f"已完成 {final_done}/5 次"
    if gained > 0:
        ad_text += f" (+{gained} 积分)"
    elif final_done >= 5 and suc_count == 0:
        ad_text += " (今日已做满)"

    card = (
        f"👤 账号备注: {tag}\n"
        f"📅 连续签到: 周期第 {day_index} 天\n"
        f"📝 基础签到: {common_res}\n"
        f"📺 广告任务: {ad_text}\n"
        f"💰 当前积分: {final_integral} 积分"
    )
    return card

def main():
    accounts = get_accounts()
    total_acc = len(accounts)
    print(f"[*] 成功加载 {total_acc} 个旧衣客账号配置\n")

    cards = []
    for idx, (tag, token) in enumerate(accounts, start=1):
        card = process_account(idx, total_acc, tag, token)
        cards.append(card)

    separator = "\n" + "-" * 26 + "\n"
    content = separator.join(cards)

    if cards:
        send("旧衣客回收♻️", content)

if __name__ == "__main__":
    main()
