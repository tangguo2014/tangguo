# -*- coding: utf-8 -*-
import requests
import os
import time
import hashlib
import random
import string

"""
小程序：益好定制 签到V1.3
变量名：yh_gpt (格式：备注1#Authorization1&备注2#Authorization2)
功能：自动签到 + 任务中心3任务 + 实时积分查询（动态签名）
定时：cron 5 6 * * * 一天一次自行修改
"""

BASE_URL = "https://wmall.36588.com.cn/shopex-api"
CHARS = string.ascii_uppercase + string.ascii_lowercase + string.digits


def log(msg):
    print(f"{msg}")


def send_notify(title: str, content: str):
    """青龙面板内置推送（增强版）"""
    candidates = [
        ("/ql/config/notify.py", "notify"),
        ("/ql/config/sendNotify.py", "sendNotify"),
        ("/Scripts/notify.py", "notify"),
        ("/Scripts/sendNotify.py", "sendNotify"),
        ("/jd/config/notify.py", "notify"),
        ("/jd/config/sendNotify.py", "sendNotify"),
    ]
    
    for path, module_name in candidates:
        if os.path.exists(path):
            try:
                import sys
                sys.path.insert(0, os.path.dirname(path))
                module = __import__(module_name)
                if hasattr(module, 'send'):
                    module.send(title, content)
                    log(f"推送成功 (使用 {path})")
                elif hasattr(module, 'sendNotify'):
                    module.sendNotify(title, content)
                    log(f"推送成功 (使用 {path} sendNotify)")
                return
            except Exception as e:
                continue
    
    try:
        from notify import send
        send(title, content)
        log("推送成功 (直接导入 notify)")
        return
    except ImportError:
        pass
    
    try:
        from sendNotify import send
        send(title, content)
        log("推送成功 (直接导入 sendNotify)")
        return
    except ImportError:
        pass
    
    log("⚠️ 未找到青龙推送模块，消息未发送")
    print(f"\n【推送通知】{title}\n{content}\n")


def md5(data: str) -> str:
    """标准 MD5 实现（源码还原）"""
    return hashlib.md5(data.encode()).hexdigest()


def random_string(length: int = 6) -> str:
    """6位随机字符串（源码还原 randomString）"""
    return ''.join(random.choice(CHARS) for _ in range(length))


def get_headers(token: str) -> dict:
    """构造基础请求头"""
    return {
        "Host": "wmall.36588.com.cn",
        "Authorization": token if "Bearer" in token else f"Bearer {token}",
        "terminal": "client",
        "uuid": "72526820-6794-11f1-a00f-e3ec43c2ee27",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) AppleWebKit/605.1.15",
        "Referer": "https://servicewechat.com/wxee2de3fd541cc3b1/121/page-frame.html"
    }


def get_uuid(token: str) -> str:
    """从 token 中提取 uuid（user_id_buyer 的最后一段）"""
    # token 格式: eyJhbGciOiJIUzUxMiJ9.xxx.xxx
    # payload 中含 user_id_buyer
    import base64
    import json
    try:
        payload_b64 = token.split('.')[1]
        # 补全 base64 padding
        padding = 4 - len(payload_b64) % 4
        if padding < 4:
            payload_b64 += '=' * padding
        decoded = base64.b64decode(payload_b64).decode()
        data = json.loads(decoded)
        uid = data.get("user_id_buyer", "")
        return str(uid)
    except:
        return "b484e2f0-00be-11f1-9fd3-4bb525caa662"


def sign_url(url: str, token: str) -> str:
    """
    动态生成带签名的 URL
    sign = MD5(nonce + timestamp + token)
    """
    nonce = random_string(6)
    timestamp = str(int(time.time()))
    token_raw = token.replace("Bearer ", "").strip()
    sign = md5(nonce + timestamp + token_raw)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}nonce={nonce}&timestamp={timestamp}&sign={sign}"


class YiHaoSign:
    def __init__(self, name, auth):
        self.name = name
        self.auth = auth if "Bearer" in auth else f"Bearer {auth}"
        self.token = self.auth.replace("Bearer ", "").strip()
        self.base_headers = get_headers(self.auth)

    def api_get(self, path: str, params: dict = None) -> dict:
        """GET 请求，自动签名"""
        url = f"{BASE_URL}{path}"
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{qs}"
        signed_url = sign_url(url, self.token)
        try:
            return requests.get(signed_url, headers=self.base_headers, timeout=10).json()
        except Exception as e:
            return {"success": False, "message": str(e)}

    def api_post(self, path: str, params: dict = None) -> dict:
        """POST 请求，自动签名"""
        url = f"{BASE_URL}{path}"
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{qs}"
        signed_url = sign_url(url, self.token)
        try:
            return requests.post(signed_url, headers=self.base_headers, json={}, timeout=10).json()
        except Exception as e:
            return {"success": False, "message": str(e)}

    def do_sign(self) -> str:
        """执行签到"""
        # 签到接口已有自己的签名，用固定参数
        sign_url = f"{BASE_URL}/user/buyer/members/sign?nonce=fHIJpL&timestamp=1770095890&sign=a9caf743caa89aa50aea68f90659545f"
        try:
            res = requests.post(sign_url, headers=self.base_headers, json={}, timeout=10).json()
            msg = res.get("message") or res.get("msg") or ""
            if res.get("success") == True or res.get("code") == 200:
                return "✅ 签到成功"
            elif "重复" in msg or "已签到" in msg:
                return "💡 今日已签"
            elif "失效" in msg or "过期" in msg:
                return "❌ Token失效"
            else:
                return f"❓ {msg}"
        except:
            return "❌ 签到异常"

    def do_task(self, task_type: str, task_name: str) -> str:
        """执行单个任务：检查 -> 完成 -> 领取"""
        # 1. 检查任务状态（GET）
        check = self.api_get("/promotion/buyer/pointsLand/checkMemberTask", {"taskType": task_type})
        result = check.get("result") or {}
        status = result.get("taskStatus")
        points = result.get("points", 5)

        if status == "finished":
            return f"📌 {task_name}：✅ 今日已完成"

        # 2. 开始执行任务，等待15s模拟浏览
        log(f"📌 {task_name}：⏳ 开始执行，等待15s...")
        time.sleep(15)

        # 3. 提交任务完成
        time.sleep(1)
        finish = self.api_post("/promotion/buyer/pointsLand/finishMemberTask", {"taskType": task_type})
        if finish.get("success") != True:
            msg = finish.get("message") or finish.get("msg") or "完成任务失败"
            return f"📌 {task_name}：❌ {msg}"
        log(f"📌 {task_name}：✅ 任务提交成功")
        time.sleep(1)

        # 4. 检查任务状态
        check2 = self.api_get("/promotion/buyer/pointsLand/checkMemberTask", {"taskType": task_type})
        result2 = check2.get("result") or {}
        status2 = result2.get("taskStatus")
        points2 = result2.get("points") or points

        if status2 == "finished":
            return f"📌 {task_name}：✅ 今日已完成"

        if status2 != "success":
            return f"📌 {task_name}：{check2.get('message', '未知')}"

        # 5. 领取积分
        time.sleep(1)
        receive = self.api_post("/promotion/buyer/pointsLand/receiveTaskPoint", {"taskType": task_type})
        if receive.get("success") == True:
            res = receive.get("result")
            if isinstance(res, dict):
                earned = res.get("points") or points2
            else:
                earned = points2
            return f"📌 {task_name}：✅ 领取{earned}积分"
        msg = receive.get("message") or receive.get("msg") or "领取失败"
        return f"📌 {task_name}：❌ {msg}"

    def run_tasks(self) -> str:
        """执行3个任务"""
        tasks = [
            ("VIEW_PAGE", "浏览任意商品页大于15s"),
            ("VIEW_POINT_LIST", "浏览积分商城页大于15s"),
            ("SHARE_MINI_APP", "分享小程序到微信"),
        ]
        results = []
        for task_type, task_name in tasks:
            results.append(self.do_task(task_type, task_name))
            time.sleep(1)
        return "\n".join(results)

    def get_points(self) -> str:
        """获取实时积分"""
        time.sleep(1)
        try:
            info_res = self.api_get("/user/buyer/member")
            if info_res.get("success") == True:
                return info_res.get("result", {}).get("point", 0)
        except:
            pass
        return "获取失败"

    def run(self):
        log(f"🚀 开始执行账号：{self.name}")
        sign_status = self.do_sign()
        task_report = self.run_tasks()
        point_val = self.get_points()
        return f"👤 账号：{self.name}\n📢 签到：{sign_status}\n{task_report}\n💰 积分：{point_val}\n"


def main():
    yh_env = os.getenv("yh_gpt")
    if not yh_env:
        log("❌ 找不到变量 yh_gpt，请检查青龙环境变量设置！")
        return

    accounts = yh_env.split("&")
    log(f"🚀 开始执行益好签到，共 {len(accounts)} 个账号...")
    results = []

    for acc in accounts:
        if "#" not in acc:
            continue
        name, token = acc.split("#", 1)
        bot = YiHaoSign(name.strip(), token.strip())
        res_text = bot.run()
        log(res_text)
        results.append(res_text)
        time.sleep(3)

    if results:
        final_report = "--------------------\n".join(results)
        send_notify("益好签到🙋‍♀️", final_report)


if __name__ == "__main__":
    main()
