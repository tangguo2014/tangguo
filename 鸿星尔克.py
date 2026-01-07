#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
鸿星尔克自动签到（青龙终极版 v7）

更新：
✅ 变量：hxek_gpt，支持备注名显示
（格式 memberId@enterpriseId@备注名）
多账号#隔开
✅ sign签名解密
✅ 缺失数据自动补查 get-member-sign-info.json
✅ 显示连续天数、累计天数、今日奖励、当前总积分
✅ 美化推送（纯文本+表情）
✅ PushPlus Token 固定写入
"""

import os
import time
import random
import hashlib
import requests

# ===== PushPlus Token 固定写入 =====
PUSHPLUS_TOKEN = "f66714be821c474c93dbae7dc0cdeefa"

# ===== 基础参数 =====
BASE_URL = "https://hope.demogic.com/gic-wx-app/"
APPID = "wxa1f1fa3785a47c7d"
VERSION = "3.9.54"
SECRET = "damogic8888"

hxek_gpt = os.getenv("hxek_gpt", "").strip()


# ===== 签名算法 =====
def hxek_sign(memberId):
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    rand = random.randint(1000000, 9999999)
    raw = f"timestamp={ts}transId={APPID}{ts}secret={SECRET}random={rand}memberId={memberId}"
    sign = hashlib.md5(raw.encode()).hexdigest()
    transId = f"{APPID}{ts}"
    return sign, rand, ts, transId


# ===== 请求头 =====
def build_headers(memberId, enterpriseId):
    return {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X)",
        "channelEntrance": "wx_app",
        "memberId": memberId,
        "enterpriseId": enterpriseId,
    }


# ===== 补查签到信息接口 =====
def get_sign_info(memberId, enterpriseId):
    headers = build_headers(memberId, enterpriseId)
    sign, rand, ts, transId = hxek_sign(memberId)
    params = {
        "memberId": memberId,
        "enterpriseId": enterpriseId,
        "appid": APPID,
        "gicWxaVersion": VERSION,
        "timestamp": ts,
        "random": rand,
        "sign": sign,
        "transId": transId
    }

    try:
        url = f"{BASE_URL}sign/get-member-sign-info.json"
        r = requests.get(url, headers=headers, params=params, timeout=10)
        data = r.json()
        res = data.get("result", {}) or data.get("response", {})

        continuous = res.get("continuousSign", 0)
        cumulative = res.get("cumulativeSign", 0)
        points = res.get("points", res.get("memberSignIntegral", 0))
        today_reward = 0

        for d in res.get("memberSignCalendar", []):
            if d.get("currentDayFlag") == 1:
                for a in d.get("memberSignAwards", []):
                    if a.get("type") == "integral":
                        today_reward = a.get("count", 0)
        return int(continuous), int(cumulative), int(today_reward), int(points)
    except Exception as e:
        print(f"❌ 补查签到信息失败：{e}")
        return 0, 0, 0, 0


# ===== 资产补查接口 =====
def get_member_asset(memberId, enterpriseId):
    headers = build_headers(memberId, enterpriseId)
    sign, rand, ts, transId = hxek_sign(memberId)
    params = {
        "memberId": memberId,
        "enterpriseId": enterpriseId,
        "appid": APPID,
        "gicWxaVersion": VERSION,
        "timestamp": ts,
        "random": rand,
        "sign": sign,
        "transId": transId,
        "dataIconKeyList": "D007",
    }
    try:
        url = f"{BASE_URL}get-member-asset.json"
        r = requests.get(url, headers=headers, params=params, timeout=10)
        data = r.json()
        if str(data.get("code")) == "0":
            return int(data.get("result", {}).get("D007", 0))
    except Exception:
        pass
    return 0


# ===== 签到主请求 =====
def sign_once(memberId, enterpriseId):
    headers = build_headers(memberId, enterpriseId)
    sign, rand, ts, transId = hxek_sign(memberId)
    payload = {
        "memberId": memberId,
        "enterpriseId": enterpriseId,
        "appid": APPID,
        "gicWxaVersion": VERSION,
        "timestamp": ts,
        "random": rand,
        "sign": sign,
        "transId": transId,
        "source": "wxapp",
        "useClique": 0,
        "cliqueId": "-1",
        "cliqueMemberId": "-1",
        "launchOptions": "{\"path\":\"pages/member-center/member-sign/index/index\"}"
    }

    try:
        url = f"{BASE_URL}sign/member_sign.json"
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        data = r.json()
        errcode = str(data.get("errcode", ""))
        errmsg = data.get("errmsg", data.get("message", ""))

        resp = data.get("response", {}) or {}
        memberSign = resp.get("memberSign", {}) or {}

        cont = memberSign.get("continuousCount", 0)
        cumul = resp.get("cumulativeSign", 0)
        reward = memberSign.get("integralCount", 0)
        total = resp.get("points", 0)

        # ==== 仅为“第一次签到日志显示不对”做的最小修改：放宽成功与已签判断 ====
        if errcode == "0" or "成功" in errmsg or "操作成功" in errmsg:
            return "success", cont, cumul, reward, total, "签到成功"
        elif errcode == "900001" or "已经签到" in errmsg or "今日已签到" in errmsg or "已签到" in errmsg:
            return "already", cont, cumul, reward, total, "今日已签到"
        else:
            return "fail", cont, cumul, reward, total, errmsg or "未知错误"
        # ==== 以上之外的任何内容均未改动 ====
    except Exception as e:
        return "error", 0, 0, 0, 0, f"请求异常：{e}"


# ===== 推送 =====
def pushplus_notify(title, content):
    try:
        requests.post(
            "https://www.pushplus.plus/send",
            json={"token": PUSHPLUS_TOKEN, "title": title, "content": content},
            timeout=10
        )
        print("📤 PushPlus 推送成功")
    except Exception as e:
        print(f"❌ 推送异常：{e}")


# ===== 主程序 =====
def main():
    print(f"## 开始执行... {time.strftime('%Y-%m-%d %H:%M:%S')}")
    start = time.time()

    if not hxek_gpt:
        msg = "❌ 未检测到 hxek_gpt 环境变量，请配置 memberId@enterpriseId@备注名"
        print(msg)
        pushplus_notify("鸿星尔克自动签到报告", msg)
        return

    accounts = [x.strip() for x in hxek_gpt.split("#") if x.strip()]
    print(f"检测到 {len(accounts)} 个账号")

    lines = []
    succ = already = fail = 0

    for i, acc in enumerate(accounts, start=1):
        parts = acc.split("@")
        if len(parts) < 2:
            fail += 1
            lines.extend([
                "———————————————————",
                f"账号{i}：",
                "签到状态：❌ 签到失败",
                "失败原因：变量格式错误，应为 memberId@enterpriseId@备注名",
            ])
            continue

        memberId, enterpriseId = parts[0], parts[1]
        remark = parts[2] if len(parts) > 2 else f"账号{i}"
        print(f"开始处理：{remark}")

        status, cont, cumul, reward, total, reason = sign_once(memberId, enterpriseId)

        # 缺失信息补查
        if cont == 0 and cumul == 0:
            cont, cumul, reward2, total2 = get_sign_info(memberId, enterpriseId)
            if reward == 0:
                reward = reward2
            if total == 0:
                total = total2
        if total == 0:
            total = get_member_asset(memberId, enterpriseId)

        block = ["———————————————————", f"{remark}："]
        if status == "success":
            succ += 1
            block.extend([
                "签到状态：✅ 签到成功",
                f"📅 连续签到：{cont} 天 | 累计签到：{cumul} 天",
                f"🎁 今日奖励：+{reward} 积分",
                f"💎 当前总积分：{total}",
            ])
        elif status == "already":
            already += 1
            block.extend([
                "签到状态：⚠️ 今日已签到",
                f"📅 连续签到：{cont} 天 | 累计签到：{cumul} 天",
                f"🎁 今日奖励：+{reward} 积分",
                f"💎 当前总积分：{total}",
            ])
        else:
            fail += 1
            block.extend([
                "签到状态：❌ 签到失败",
                f"失败原因：{reason}",
                f"📅 连续签到：{cont} 天 | 累计签到：{cumul} 天",
                f"🎁 今日奖励：+{reward} 积分",
                f"💎 当前总积分：{total}",
            ])
        lines.extend(block)
        time.sleep(random.uniform(1.5, 3.0))

    elapsed = round(time.time() - start, 1)
    summary = [
        "🏅 鸿星尔克自动签到报告",
        f"🕒 执行时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        *lines,
        "———————————————————",
        f"统计：✅ 成功 {succ} 个 | ⚠️ 已签 {already} 个 | ❌ 失败 {fail} 个",
        f"耗时：{elapsed} 秒"
    ]
    content = "\n".join(summary)

    print(content)
    pushplus_notify("鸿星尔克自动签到报告", content)
    print(f"\n## 执行结束... {time.strftime('%Y-%m-%d %H:%M:%S')}  耗时 {elapsed} 秒")


if __name__ == "__main__":
    main()
