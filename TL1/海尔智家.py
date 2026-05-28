# -*- coding: utf-8 -*-
"""
=====================================================================
              海尔智家 (Haier) V2.0
=====================================================================
【 使用说明 】海尔智家APP
1. 环境变量名：hr_gpt
2. 变量值格式：备注#accountToken#userId (token请填纯小写 )
3. 多账号支持：使用 & 符号分割
4. 定时 cron 6 9 * * * 每天一次自行修改
=====================================================================
"""

import os
import requests
import time
import json

# ================================================================
# ⚙️ 脚本内置默认参数 (若App强制升级导致失效，在此修改版本号即可)
# ================================================================
DEFAULT_APP_ID = "MB-UZHSH-0001"
DEFAULT_CLIENT_ID = "ADB9BD55-6F77-4128-91F8-F571A81E5153"
DEFAULT_VERSION = "10.20.0"
DEFAULT_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Hainer/Haier Simulation/Nebula App/Uplus Nebula mPaaSClient"

def send_notify(title, content):
    try:
        from notify import send
        send(title, content)
    except Exception:
        pass

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")

def run_task(acc_str):
    try:
        p = acc_str.split('#')
        if len(p) < 3:
            log(f"❌ 账号配置解析失败: '{acc_str}' 格式错误")
            return f"👤 **{p[0]}**\n❌ 变量格式错误 (需 备注#Token#UserId)\n\n"
        remark, token, userid = p[0], p[1], p[2]
    except Exception:
        return ""

    log(f"================== 👤 开始同步账号 [{remark}] ==================")

    headers_form = {
        'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
        'Origin': 'null',
        'Accept': 'application/json, text/plain, */*',
        'User-Agent': DEFAULT_UA,
        'Connection': 'keep-alive',
        'Accept-Language': 'zh-CN,zh-Hans;q=0.9'
    }

    # =================================================================
    # 📑 1. 海贝签到逻辑
    # =================================================================
    sign_msg = ""
    try:
        sign_url = "https://zj.haier.net/api-gw/zjBaseServer/daily/sign"
        h_sign = {
            'accountToken': token,
            'appId': DEFAULT_APP_ID,
            'appVersion': DEFAULT_VERSION,
            'clientId': DEFAULT_CLIENT_ID,
            'Content-Type': 'application/json',
            'User-Agent': DEFAULT_UA
        }
        res_sign = requests.post(sign_url, headers=h_sign, json={}, timeout=15).json()
        if res_sign.get("retCode") == "00000":
            hb = res_sign.get("data", {}).get("haibeiCount", 0)
            sign_msg = f"+{hb} 贝"
            log(f"🎁 海贝签到成功: {sign_msg}")
        else:
            sign_msg = res_sign.get("retInfo") or "已签到"
            log(f"🎁 海贝签到结果: {sign_msg}")
    except Exception as e:
        sign_msg = "异常"
        log(f"❌ 海贝签到异常: {str(e)}")

    # =================================================================
    # 📑 2. 成长中心来访与签到 (cdpPlatform 网关)
    # =================================================================
    growth_msg = "完成"
    try:
        kafka_url = "https://hzy.haier.com/vipCode/cdpPlatform/sendKafkaByMemberActive"
        base_kafka_payload = {
            'channel': 'ZJ',
            'eventId': 'MB10731',
            'type': '1',
            'vipCode_userId': userid,
            'vipCode_domainName': 'Haier',
            'vipCode_ssotoken': token
        }
        # 每日来访
        res_visit = requests.post(kafka_url, headers=headers_form, data=base_kafka_payload, timeout=15).json()
        log(f"📈 成长中心-每日来访推送结果: {res_visit.get('message') or res_visit}")
        
        # 成长签到
        payload_sign = base_kafka_payload.copy()
        payload_sign['eventId'] = 'MB10732'
        res_kf_sign = requests.post(kafka_url, headers=headers_form, data=payload_sign, timeout=15).json()
        log(f"📈 成长中心-每日签到推送结果: {res_kf_sign.get('message') or res_kf_sign}")
    except Exception as e:
        growth_msg = "异常"
        log(f"❌ 成长中心推送异常: {str(e)}")

    # =================================================================
    # 📑 3. 最新修复：浏览居家服务板块任务 (更换最新 taskId: 90)
    # =================================================================
    furniture_msg = "已提交"
    try:
        task_url = "https://hzy.haier.com/vipCode/cdpPlatform/finishGrowthTask"
        base_task_payload = {
            'channel': 'ZJ',
            'brandName': 'zjst',
            'vipCode_userId': userid,
            'vipCode_domainName': 'Haier',
            'vipCode_ssotoken': token
        }
        enter_90 = base_task_payload.copy()
        enter_90.update({'taskId': '90', 'type': '1'})
        requests.post(task_url, headers=headers_form, data=enter_90, timeout=15)
        log("🛋️ 居家服务任务: 已成功进入场景，开始静默浏览 18 秒...")

        time.sleep(18)

        leave_90 = base_task_payload.copy()
        leave_90.update({'taskId': '90', 'type': '2'})
        res_l90 = requests.post(task_url, headers=headers_form, data=leave_90, timeout=15)
        
        # 增加安全解析防爆盾，防止接口返回空内容报错
        try:
            res_json = res_l90.json()
            log(f"🛋️ 居家服务任务: 浏览结束离开，场景90回复={res_json.get('isSuccess')}")
        except Exception:
            log(f"🛋️ 居家服务任务: 浏览结束离开，场景90回执为空或非JSON格式，已安全放行")
            
    except Exception as e:
        furniture_msg = "异常"
        log(f"❌ 居家服务任务异常: {str(e)}")

    # =================================================================
    # 📑 4. 付费中心成长等级接口
    # =================================================================
    level_msg = "未知"
    try:
        level_url = "https://hzy.haier.com/vipCode/taskCenter_fufei/getGradeByGrowthValue"
        form_payload = {
            'brandName': 'zjst',
            'vipCode_userId': userid,
            'vipCode_domainName': 'Haier',
            'vipCode_ssotoken': token
        }
        res_level = requests.post(level_url, headers=headers_form, data=form_payload, timeout=15).json()
        log(f"👑 成长等级接口返回原始数据: {res_level}")
        if res_level.get("isSuccess"):
            if res_level.get("result"):
                level_msg = str(res_level.get("result"))
            elif res_level.get("data"):
                data_obj = res_level.get("data", {})
                if isinstance(data_obj, str):
                    try:
                        data_obj = json.loads(data_obj)
                    except Exception:
                        pass
                if isinstance(data_obj, dict):
                    level_msg = data_obj.get("gradeName") or f"V{data_obj.get('grade', '0')}"
        log(f"👑 最终解析成长等级: {level_msg}")
    except Exception as e:
        level_msg = "获取失败"
        log(f"❌ 获取成长等级异常: {str(e)}")

    # =================================================================
    # 📑 5. 原始余额查询逻辑
    # =================================================================
    balance = "未知"
    try:
        bal_url = "https://hzy.haier.com/vipCode/integralCenter/getHaiBeiCount"
        payload = {
            'userId': userid,
            'vipCode_userId': userid,
            'vipCode_domainName': 'Haier',
            'vipCode_ssotoken': token
        }
        res_bal = requests.post(bal_url, headers=headers_form, data=payload, timeout=15).json()
        if res_bal.get("isSuccess"):
            balance = f"{res_bal.get('data', '0')} 贝"
            log(f"💰 余额查询成功: {balance}")
    except Exception as e:
        balance = "获取失败"
        log(f"❌ 余额查询异常: {str(e)}")

    log(f"🔹 【{remark}】同步完成")
    log(f"================== 👤 账号 [{remark}] 执行完毕 ==================\n")

    res = f"👤 **用户账号**：{remark}\n"
    res += f"👑 **成长等级**：{level_msg}\n"
    res += f"🎁 **海贝签到**：{sign_msg}\n"
    res += f"📈 **成长中心**：{growth_msg}\n"
    res += f"🛋️ **家具任务**：{furniture_msg}\n"
    res += f"💰 **当前余额**：**{balance}**\n\n"
    return res

if __name__ == "__main__":
    env_str = os.getenv("hr_gpt")
    
    if not env_str:
        log("❌ 未检测到环境变量: hr_gpt")
    else:
        accounts = [a.strip() for a in env_str.split('&') if a.strip()]
        log(f"🚀 海尔智家启动 | 账号总数: {len(accounts)}")
        report = ""
        for acc in accounts:
            report += run_task(acc)
            time.sleep(2)
        
        if report:
            send_notify("海尔智家签到🙋‍♀️", f"{report.strip()}\n\n⏰ {time.strftime('%Y-%m-%d %H:%M')}")
