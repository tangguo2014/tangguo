# -*- coding: utf-8 -*-
import os
import json
import requests
from datetime import datetime

# =============================
# 同城旅行最终修改V6
# 智能加载青龙 notify.py 的 send 函数
# 从环境变量加载配置（tc_gpt 风格）
# 格式：PHONE#APPTOKEN#DEVICE
# 多账号用 &分隔
# 账号自动脱敏
# =============================
push_func = None
try:
    import sys
    sys.path.append('/ql/scripts')
    from notify import send
    push_func = send
    print("✅ 成功加载 notify.py 的 send 推送函数")
except Exception as e:
    print(f"❌ 加载 send 函数失败: {e}")

# =============================
# 1. 环境变量解析（tc_gpt）
# =============================
TC_GPT = os.getenv("tc_gpt")
if not TC_GPT:
    print("❌ 未检测到 tc_gpt 环境变量")
    exit()

def parse_accounts(env_str):
    accounts = []
    for item in env_str.split("&&"):
        item = item.strip()
        if not item:
            continue
        parts = item.split("#")
        if len(parts) != 3:
            print(f"⚠️ 账号格式错误: {item}")
            continue
        accounts.append({
            "phone": parts[0],
            "apptoken": parts[1],
            "device": parts[2]
        })
    return accounts

accounts = parse_accounts(TC_GPT)

# =============================
# 2. 脱敏函数
# =============================
def mask_phone(phone):
    return phone[:3] + "****" + phone[-4:]

# =============================
# 3. 核心函数
# =============================
def get_headers(phone, apptoken, device):
    return {
        'content-type': 'application/json',
        'accept': 'application/json, text/plain, */*',
        'phone': phone,
        'channel': '1',
        'apptoken': apptoken,
        'sec-fetch-site': 'same-site',
        'accept-language': 'zh-CN,zh-Hans;q=0.9',
        'accept-encoding': 'gzip, deflate, br',
        'sec-fetch-mode': 'cors',
        'origin': 'https://m.17u.cn',
        'user-agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_7_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 TcTravel/11.0.1 tctype/wk',
        'referer': 'https://m.17u.cn/',
        'device': device,
        'sec-fetch-dest': 'empty'
    }

def get_today_date():
    return datetime.now().strftime('%Y-%m-%d')

def sign_in(phone, apptoken, device):
    url = "https://app.17u.cn/welfarecenter/index/signIndex"
    headers = get_headers(phone, apptoken, device)
    try:
        response = requests.post(url, json={}, headers=headers, timeout=10)
        data = response.json()
        if data['code'] != 2200:
            return None, None, None, None
        d = data['data']
        return (
            d['todaySign'],
            d['mileageBalance']['mileage'],
            d['cycleSighNum'],
            d['mileageBalance']['todayMileage']
        )
    except Exception as e:
        print(f"⚠️ sign_in 异常: {e}")
        return None, None, None, None

def do_sign_in(phone, apptoken, device):
    url = "https://app.17u.cn/welfarecenter/index/sign"
    payload = {"type": 1, "day": get_today_date()}
    headers = get_headers(phone, apptoken, device)
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        return response.json()['code'] == 2200
    except:
        return False

def get_task_list(phone, apptoken, device):
    url = "https://app.17u.cn/welfarecenter/task/taskList?version=11.0.0.0"
    headers = get_headers(phone, apptoken, device)
    try:
        response = requests.post(url, json={}, headers=headers, timeout=10)
        data = response.json()
        if data['code'] != 2200:
            return []
        return [
            {'taskCode': t['taskCode'], 'title': t['title'], 'browserTime': t['browserTime']}
            for t in data['data'] if t['state'] == 1 and t['browserTime'] > 0
        ]
    except:
        return []

def start_task(phone, apptoken, device, task_code):
    url = "https://app.17u.cn/welfarecenter/task/start"
    payload = {"taskCode": task_code}
    headers = get_headers(phone, apptoken, device)
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        data = response.json()
        return data['data'] if data['code'] == 2200 else None
    except:
        return None

def finish_task(phone, apptoken, device, task_id):
    url = "https://app.17u.cn/welfarecenter/task/finish"
    payload = {"id": task_id}
    headers = get_headers(phone, apptoken, device)
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.json()['code'] == 2200:
            return True
        # 重试一次
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        return response.json()['code'] == 2200
    except:
        return False

def receive_reward(phone, apptoken, device, task_id):
    url = "https://app.17u.cn/welfarecenter/task/receive"
    payload = {"id": task_id}
    headers = get_headers(phone, apptoken, device)
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        return response.json()['code'] == 2200
    except:
        return False

# =============================
# 4. 主流程
# =============================
def main():
    log_lines = []
    summary_lines = []

    for account in accounts:
        phone = account['phone']
        apptoken = account['apptoken']
        device = account['device']
        masked_phone = mask_phone(phone)

        print(f"\n🔐 账号: {masked_phone}")
        log_lines.append(f"🔐 账号: {masked_phone}")

        try:
            today_sign, mileage, cycle_sign_num, today_mileage = sign_in(phone, apptoken, device)
            if today_sign is None:
                msg = f"❌ {masked_phone} 登录失效"
                print(msg)
                log_lines.append(msg)
                continue

            if today_sign:
                msg = f"✅ {masked_phone} 已签到"
                print(msg)
                log_lines.append(msg)
            else:
                if do_sign_in(phone, apptoken, device):
                    msg = f"🎉 {masked_phone} 签到成功"
                    print(msg)
                    log_lines.append(msg)
                else:
                    msg = f"❌ {masked_phone} 签到失败"
                    print(msg)
                    log_lines.append(msg)

            tasks = get_task_list(phone, apptoken, device)
            for task in tasks:
                task_code = task['taskCode']
                title = task['title']
                browser_time = task['browserTime']
                print(f"📺 {masked_phone} 任务: {title}")
                log_lines.append(f"📺 {masked_phone} 任务: {title}")
                task_id = start_task(phone, apptoken, device, task_code)
                if task_id:
                    # 模拟浏览时长
                    import time
                    time.sleep(browser_time)
                    if finish_task(phone, apptoken, device, task_id):
                        receive_reward(phone, apptoken, device, task_id)
                        log_lines.append(f"✅ {masked_phone} 完成: {title}")
                    else:
                        log_lines.append(f"❌ {masked_phone} 失败: {title}")

            summary = f"📊 {masked_phone} 本月签到: {cycle_sign_num}天 | 今日里程: {today_mileage} | 剩余: {mileage}"
            print(summary)
            summary_lines.append(summary)

        except Exception as e:
            err = f"💥 {masked_phone} 异常: {str(e)}"
            print(err)
            log_lines.append(err)

    # === 推送处理 ===
    title = "🚀 同程旅行自动任务日报"
    content = "\n".join(log_lines + summary_lines)
    
    # 输出日志（青龙会自动捕获）
    print("\n" + "="*40)
    print(content)
    
    # 尝试调用 send 推送
    if push_func:
        try:
            push_func(title, content)
            print("✅ 推送已发送")
        except Exception as e:
            print(f"❌ 推送调用异常: {e}")
    else:
        print("ℹ️ 推送函数不可用，仅输出日志")

if __name__ == "__main__":
    main()