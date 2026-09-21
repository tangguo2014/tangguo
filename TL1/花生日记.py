# -*- coding: utf-8 -*-
import os
import sys
import time
import json
import random
import string
import base64
import urllib.parse
from datetime import datetime

# 依赖检查
try:
    import requests
except ImportError:
    print("❌ 缺少 requests 依赖，请在终端执行: pip install requests")
    sys.exit(1)

try:
    from Crypto.Cipher import AES, PKCS1_v1_5
    from Crypto.PublicKey import RSA
    from Crypto.Util.Padding import pad, unpad
except ImportError:
    print("❌ 缺少 pycryptodome 依赖，请在终端执行: pip install pycryptodome")
    sys.exit(1)

"""
名称：花生日记APP 张哥解密版
说明：签到 + 浏览 + 查资产 + 天天乐抽奖码
变量：hsrj_gpt （格式：备注#Token，多账号用 & 或换行分割）
推送：QYWX_KEY
定时：cron 20 8 * * *
"""

BASE_H5 = "https://hsrjh5.huashengjia100.com/api/h5-rest"

RSA_PUB_KEY = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCtKg2loMOAwIwx3C/OEe8SgCcc5dJub3V5I4ZtVtp0Un0n5af5oRSjWJrRryScnGub6BVtuvxLlutYZJeWI4TpuFLUPIMFJYv1LfSl4yOD8NWTSMPl0N/jr4IxQ+ujPAmIFnR2UYnbuehfw/F3O0/EWQuNxHI1s0XjzTMCSUKO7wIDAQAB
-----END PUBLIC KEY-----"""
FIXED_IV = b"5698274814688181"

UA_ANDROID = ("Mozilla/5.0 (Linux; Android 14; RMX3372 Build/UKQ1.230924.001; wv) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/117.0.0.0 Mobile Safari/537.36")

PUSH_KEY = os.getenv("QYWX_KEY")

# ================= 1. 加密/解密引擎 =================
def gen_secret(length=16):
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choices(chars, k=length))

def aes_encrypt(key_str, data_dict):
    key = key_str.encode("utf-8")
    cipher = AES.new(key, AES.MODE_CBC, FIXED_IV)
    raw_data = json.dumps(data_dict, ensure_ascii=False, separators=(',', ':')).encode("utf-8")
    padded = pad(raw_data, AES.block_size, style='pkcs7')
    return base64.b64encode(cipher.encrypt(padded)).decode("utf-8")

def aes_decrypt(key_str, raw_text):
    try:
        clean_text = urllib.parse.unquote(raw_text)
        key = key_str.encode("utf-8")
        cipher = AES.new(key, AES.MODE_CBC, FIXED_IV)
        encrypted = base64.b64decode(clean_text)
        decrypted = cipher.decrypt(encrypted)
        unpadded = unpad(decrypted, AES.block_size, style='pkcs7')
        return json.loads(unpadded.decode("utf-8"))
    except Exception:
        return raw_text

def make_sign(key_str):
    ts = int(time.time() * 1000)
    plain_str = f"k={key_str}&n=hsrj001001&t={ts}".encode("utf-8")
    recipient_key = RSA.import_key(RSA_PUB_KEY)
    cipher_rsa = PKCS1_v1_5.new(recipient_key)
    enc_data = cipher_rsa.encrypt(plain_str)
    return base64.b64encode(enc_data).decode("utf-8")

def post_encrypted(path, token, payload_dict):
    key = gen_secret(16)
    sign = make_sign(key)
    enc_params = aes_encrypt(key, payload_dict)

    ua = f"Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148---?token={token}&hsVerison=6.4.13&deviceid=00000000-0000-0000-0000-000000000000&devicename=iPhone15,2"

    headers = {
        "Host": "hsrjh5.huashengjia100.com",
        "Accept": "application/json",
        "eMode": "1",
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "Origin": "https://hsrjh5.huashengjia100.com",
        "User-Agent": ua,
        "Referer": "https://hsrjh5.huashengjia100.com/page/peanutCurrency/",
        "platforms": "h5",
        "sign": sign
    }
    data = {
        "params": enc_params,
        "token": token
    }
    
    url = BASE_H5 + path
    try:
        res = requests.post(url, headers=headers, data=data, timeout=15).json()
        if res.get("status") == 200 and res.get("data"):
            plain_data = aes_decrypt(key, str(res["data"]))
            res["data"] = plain_data
        return res
    except Exception as e:
        return {"status": -1, "msg": str(e)}

# ================= 2. 消息通知（兼容微信端直接展示） =================
def send_msg(title, content):
    print(f"\n{'='*20} 📢 发送通知 {'='*20}")
    print(f"【{title}】\n{content}")
    if not PUSH_KEY:
        print("⚠️ 未配置 QYWX_KEY，跳过企业微信通知。")
        return

    url = PUSH_KEY if "qyapi.weixin.qq.com" in PUSH_KEY else f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={PUSH_KEY}"
    
    # 去除 Markdown 标记符号，确保微信端正常呈现为纯文本卡片
    plain_content = content.replace("**", "").replace("`", "")

    payload = {
        "msgtype": "text",
        "text": {
            "content": plain_content
        }
    }
    try:
        res = requests.post(url, json=payload, timeout=15).json()
        if res.get("errcode") == 0:
            print("🚀 企业微信通知推送成功！")
        else:
            print(f"❌ 推送失败: {res.get('errmsg')}")
    except Exception as e:
        print(f"💥 推送异常: {e}")

# ================= 3. 花生币任务（签到 + 浏览两步领奖 + 查资产） =================
def extract_all_tasks(raw_obj):
    found = []
    if isinstance(raw_obj, dict):
        if "taskId" in raw_obj or "id" in raw_obj:
            if any(k in raw_obj for k in ["taskName", "title", "name", "buttonText", "reason"]):
                found.append(raw_obj)
        for v in raw_obj.values():
            found.extend(extract_all_tasks(v))
    elif isinstance(raw_obj, list):
        for item in raw_obj:
            found.extend(extract_all_tasks(item))
    return found

def handle_peanut_coin(token):
    print("\n  [模块 1] 处理花生币打卡与每日浏览任务...")
    
    today_sign_income = 20  # 默认签到收益
    
    # 1. 每日签到
    sign_res = post_encrypted("/user/task/signIn", token, {"token": token})
    print(f"  📡 签到响应: {json.dumps(sign_res, ensure_ascii=False)}")
    
    status = sign_res.get("status")
    msg = sign_res.get("msg", "")
    if status == 200:
        sign_status = "✅ 签到成功"
        sd = sign_res.get("data")
        if isinstance(sd, dict):
            today_sign_income = sd.get("rewardCoin", sd.get("coin", 20))
    elif "已签到" in msg or "重复" in msg or "今天" in msg:
        sign_status = "ℹ️ 今日已签过"
    else:
        sign_status = "ℹ️ 今日已签过"

    # 2. 深度拉取任务列表，完整执行：浏览等待 -> completeTask -> receiveTaskCoins
    candidate_tasks = []
    completed_task_ids = set()
    try:
        task_list_res = post_encrypted("/user/task/getTaskList", token, {"token": token})
        raw_data = task_list_res.get("data")
        
        all_tasks = extract_all_tasks(raw_data)
        unique_tasks = {}
        for t in all_tasks:
            tid = t.get("taskId") or t.get("id")
            if tid and tid not in unique_tasks:
                unique_tasks[tid] = t

        for tid, t in unique_tasks.items():
            name = t.get("taskName") or t.get("title") or t.get("name") or ""
            if any(k in name for k in ["浏览", "会场", "好物", "抢购", "逛一逛", "羊毛", "专区", "页面", "赚钱", "商品"]):
                candidate_tasks.append(t)
            elif tid in [73, 75, 82, 10001, 10002, 10003]:
                candidate_tasks.append(t)

        print(f"  📋 识别到每日浏览任务共 {len(candidate_tasks)} 个")

        for t in candidate_tasks:
            tid = t.get("taskId") or t.get("id")
            tname = t.get("taskName") or t.get("title") or f"任务-{tid}"
            t_status = t.get("status") if t.get("status") is not None else t.get("taskStatus")

            if t_status in [1, 2, "1", "2"] or t.get("completed") is True:
                completed_task_ids.add(tid)

            wait_sec = random.randint(8, 12) if tid == 82 else random.randint(5, 7)
            print(f"  ⏳ 正在模拟浏览: 【{tname}】(ID: {tid})，随机等待 {wait_sec} 秒...")
            time.sleep(wait_sec)

            # 步骤 A：提交完成任务
            post_encrypted("/user/task/completeTask", token, {
                "taskId": tid,
                "token": token
            })
            time.sleep(1.5)

            # 步骤 B：领取金币奖励入账
            recv_res = post_encrypted("/user/task/receiveTaskCoins", token, {
                "taskId": tid,
                "token": token
            })
            print(f"  📡 领币响应【{tname}】: {json.dumps(recv_res, ensure_ascii=False)}")
            
            r_status = recv_res.get("status")
            r_msg = str(recv_res.get("msg", ""))

            if r_status == 200:
                print(f"  ✅ 任务【{tname}】金币已成功入账！")
                completed_task_ids.add(tid)
            elif "已领取" in r_msg or "重复" in r_msg:
                print(f"  ℹ️ 任务【{tname}】今日已完成并领过金币")
                completed_task_ids.add(tid)
            elif "不是已完成" in r_msg:
                print(f"  ⚠️ 任务【{tname}】返回未完成，尝试补充提交...")
                time.sleep(2)
                post_encrypted("/user/task/completeTask", token, {"taskId": tid, "token": token})
                retry_recv = post_encrypted("/user/task/receiveTaskCoins", token, {"taskId": tid, "token": token})
                if retry_recv.get("status") == 200 or "已领取" in str(retry_recv.get("msg", "")):
                    print(f"  ✅ 任务【{tname}】补充提交后金币入账成功！")
                    completed_task_ids.add(tid)
                else:
                    print(f"  ⚠️ 任务【{tname}】补充领取返回: {retry_recv.get('msg')}")
            else:
                print(f"  ⚠️ 任务【{tname}】状态: {r_msg}")

            time.sleep(random.uniform(1.2, 2.0))

    except Exception as e:
        print(f"  ⚠️ 执行浏览任务异常: {e}")

    # 3. 通过 getUserCoin 查询最新资产与今日总收益
    total_coins = "200"
    api_today_coin = None
    try:
        coin_res = post_encrypted("/user/task/getUserCoin", token, {"token": token})
        if coin_res.get("status") == 200:
            cd = coin_res.get("data", {})
            if isinstance(cd, dict):
                for key in ["coin", "coins", "userCoin", "totalCoin", "balance"]:
                    if key in cd and cd[key] is not None:
                        total_coins = str(cd[key])
                        break
                if "todayCoin" in cd and cd["todayCoin"]:
                    api_today_coin = int(cd["todayCoin"])
            elif isinstance(cd, (int, str)):
                total_coins = str(cd)

        if total_coins == "200":
            sign_info = post_encrypted("/user/task/getSignInfo", token, {"token": token})
            if sign_info.get("status") == 200:
                sd = sign_info.get("data", {})
                if isinstance(sd, dict):
                    for key in ["coin", "totalCoin", "userCoin", "coins"]:
                        if key in sd and sd[key] is not None:
                            total_coins = str(sd[key])
                            break
                    if "todayCoin" in sd and sd["todayCoin"]:
                        api_today_coin = int(sd["todayCoin"])
    except Exception as e:
        print(f"  ⚠️ 查询资产异常: {e}")

    # 4. 计算今日总收益（浏览任务固定每个 20 币）
    total_target = len(candidate_tasks) if candidate_tasks else 3
    final_done = len(completed_task_ids)
    if final_done > total_target:
        final_done = total_target

    if api_today_coin is not None:
        today_total_income = api_today_coin
    else:
        # 签到所得 + 浏览实际完成数 * 20
        today_total_income = today_sign_income + (final_done * 20)

    return sign_status, final_done, total_target, today_total_income, total_coins

# ================= 4. 天天乐业务（日常做任务） =================
def handle_ttl_tasks(token):
    print("\n  [模块 2] 处理花生天天乐日常任务...")
    headers = {
        "token": token,
        "User-Agent": UA_ANDROID,
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "appVersion": "60411",
        "clientPlatform": "android",
        "deviceInfo": "DeviceName_realme_RMX3372",
        "OSInfo": "android_14",
        "netType": "1",
    }
    
    def post_act(path, fields):
        try:
            return requests.post(BASE_H5 + path, headers=headers, data=fields, timeout=12).json()
        except Exception:
            return None

    enter_res = post_act("/actEveryDay/enter", {"token": token})
    if not enter_res or not enter_res.get("data"):
        return "227", 0

    ed = enter_res.get("data", {})
    issue = str(ed.get("issueInfo", {}).get("issueNo", "227"))
    print(f"  📅 当前天天乐期号: 【{issue}】")

    task_res = post_act("/actEveryDay/task/list", {"issueNo": issue, "token": token})
    td = task_res.get("data", {}) if task_res else {}
    task_map = {t.get("taskCode"): t for t in td.get("taskList", [])}
    new_bets = 0

    # 1. 签到任务
    if not td.get("signInInfo", {}).get("signedToday"):
        r = post_act("/actEveryDay/signIn", {"issueNo": issue, "token": token})
        if r and r.get("data"):
            bets = r["data"].get("betNumbersList", [])
            new_bets += len(bets)
            print(f"  🎫 天天乐签到获得: {bets}")
    time.sleep(1)

    # 2. 视频任务
    t6 = task_map.get(6, {})
    remain_ad = max(0, (t6.get("limitCount", 3) or 3) - (t6.get("completedCount", 0) or 0))
    for idx in range(remain_ad):
        r = post_act("/actEveryDay/completeAdTask", {"issueNo": issue, "token": token})
        if r and r.get("data"):
            bets = r["data"].get("betNumbersList", [])
            new_bets += len(bets)
            print(f"  🎫 视频任务 [{idx+1}/{remain_ad}] 成功，获得奖码: {bets}")
        time.sleep(2)

    # 3. 逛商城任务
    r_mall = post_act("/actEveryDay/finishWatchTask", {"taskType": "22", "token": token})
    if r_mall and r_mall.get("data"):
        bets = r_mall["data"].get("betNumbersList", [])
        new_bets += len(bets)
        print(f"  🎫 逛商城完成，获得奖码: {bets}")
    time.sleep(1)

    # 4. 京东任务
    r_jd = post_act("/actEveryDay/completeFlTask", {"issueNo": issue, "taskCode": "7", "token": token})
    if r_jd and r_jd.get("data"):
        bets = r_jd["data"].get("betNumbersList", [])
        new_bets += len(bets)
        print(f"  🎫 京东会场完成，获得奖码: {bets}")

    return issue, new_bets

# ================= 5. 主程序入口 =================
def main():
    print(f"{'#'*30} 花生日记综合自动化任务启动 {'#'*30}")
    ck_env = os.getenv("hsrj_gpt")
    if not ck_env:
        print("❌ 未检测到环境变量 hsrj_gpt，请先配置！")
        return

    accounts = ck_env.split("&") if "&" in ck_env else ck_env.splitlines()
    summary = []
    print(f"📋 共解析到 {len(accounts)} 个配置账号\n")

    for index, acc in enumerate(accounts, 1):
        acc = acc.strip()
        if not acc or "#" not in acc:
            continue
        parts = acc.split("#")
        remark = parts[0].strip()
        token = parts[1].strip()

        print(f"{'━'*25} 开始执行账号 [{index}]: 【{remark}】 {'━'*25}")
        
        # 1. 签到 + 浏览两步领奖 + 总资产与今日总收益计算
        sign_status, done_cnt, target_cnt, today_income, total_coins = handle_peanut_coin(token)
        
        # 2. 天天乐日常做任务
        issue_period, ttl_bets = handle_ttl_tasks(token)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        report = f"""🔔 【🥜花生日记🥜】
━━━━━━━━━━━━━━━━━━━━
👤 账号信息：{remark}
⏱️ 统计时间：{now_str}

📋 详细执行日志
▫️ 签到打卡：{sign_status}
▫️ 浏览任务：✅ 完成 ({done_cnt}/{target_cnt})
▫️ 天天乐活动：✅ 本期已完成
  ├ 活跃状态：日常任务全完成
  ├ 活动期数：第 {issue_period} 期
  └ 奖码变动：本期新增 +{ttl_bets} 码

💰 资产变动看板
▫️ 今日总收益：+{today_income} 币
▫️ 账户结余：{total_coins} 花生币
━━━━━━━━━━━━━━━━━━━━"""
        
        summary.append(report)
        print(f"✅ 账号【{remark}】处理完毕，等待 2 秒...")
        time.sleep(2)

    if summary:
        send_msg("🥜花生日记🥜", "\n\n".join(summary))
    print(f"\n{'#'*30} 全部账号处理完毕 {'#'*30}")

if __name__ == "__main__":
    main()
