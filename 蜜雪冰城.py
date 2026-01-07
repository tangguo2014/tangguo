"""
蜜雪冰城自动签到脚本
功能说明：
- 自动每日签到获取雪王币
- 支持多账号签到
- 签到结果通过 PushPlus 推送通知
环境变量配置：
1. mxbc_gpt：账号 Token 和昵称，格式为 "Token#昵称"，多个账号用 @ 分隔
2. PushPlus 消息推送
3. 私钥已配置
"""

import requests
import time
import os
import sys
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
import base64

# === 配置区（填自己的token） ===
PUSHPLUS_TOKEN = "8e775524b4894f67a0ccd98d64afddee"

# === 私钥配置 （secret_key解密勿动）===
PRIVATE_KEY_PEM = (
    "-----BEGIN PRIVATE KEY-----\n"
    "MIIEvwIBADANBgkqhkiG9w0BAQEFAASCBKkwggSlAgEAAoIBAQCtypUdHZJKlQ9L\n"
    "L6lIJSphnhqjke7HclgWuWDRWvzov30du235cCm13mqJ3zziqLCwstdQkuXo9sOP\n"
    "Ih94t6nzBHTuqYA1whrUnQrKfv9X4/h3QVkzwT+xWflE+KubJZoe+daLKkDeZjVW\n"
    "nUku8ov0E5vwADACfntEhAwiSZUALX9UgNDTPbj5ESeII+VztZ/KOFsRHMTfDb1G\n"
    "IR/dAc1mL5uYbh0h2Fa/fxRPgf7eJOeWGiygesl3CWj0Ue13qwX9PcG7klJXfToI\n"
    "576MY+A7027a0aZ49QhKnysMGhTdtFCksYG0lwPz3bIR16NvlxNLKanc2h+ILTFQ\n"
    "bMW/Y3DRAgMBAAECggEBAJGTfX6rE6zX2bzASsu9HhgxKN1VU6/L70/xrtEPp4SL\n"
    "SpHKO9/S/Y1zpsigr86pQYBx/nxm4KFZewx9p+El7/06AX0djOD7HCB2/+AJq3iC\n"
    "5NF4cvEwclrsJCqLJqxKPiSuYPGnzji9YvaPwArMb0Ff36KVdaHRMw58kfFys5Y2\n"
    "HvDqh4x+sgMUS7kSEQT4YDzCDPlAoEFgF9rlXnh0UVS6pZtvq3cR7pR4A9hvDgX9\n"
    "wU6zn1dGdy4MEXIpckuZkhwbqDLmfoHHeJc5RIjRP7WIRh2CodjetgPFE+SV7Sdj\n"
    "ECmvYJbet4YLg+Qil0OKR9s9S1BbObgcbC9WxUcrTgECgYEA/Yj8BDfxcsPK5ebE\n"
    "9N2teBFUJuDcHEuM1xp4/tFisoFH90JZJMkVbO19rddAMmdYLTGivWTyPVsM1+9s\n"
    "tq/NwsFJWHRUiMK7dttGiXuZry+xvq/SAZoitgI8tXdDXMw7368vatr0g6m7ucBK\n"
    "jZWxSHjK9/KVquVr7BoXFm+YxaECgYEAr3sgVNbr5ovx17YriTqe1FLTLMD5gPrz\n"
    "ugJj7nypDYY59hLlkrA/TtWbfzE+vfrN3oRIz5OMi9iFk3KXFVJMjGg+M5eO9Y8m\n"
    "14e791/q1jUuuUH4mc6HttNRNh7TdLg/OGKivE+56LEyFPir45zw/dqwQM3jiwIz\n"
    "yPz/+bzmfTECgYATxrOhwJtc0FjrReznDMOTMgbWYYPJ0TrTLIVzmvGP6vWqG8rI\n"
    "S8cYEA5VmQyw4c7G97AyBcW/c3K1BT/9oAj0wA7wj2JoqIfm5YPDBZkfSSEcNqqy\n"
    "5Ur/13zUytC+VE/3SrrwItQf0QWLn6wxDxQdCw8J+CokgnDAoehbH6lTAQKBgQCE\n"
    "67T/zpR9279i8CBmIDszBVHkcoALzQtU+H6NpWvATM4WsRWoWUx7AJ56Z+joqtPK\n"
    "G1WztkYdn/L+TyxWADLvn/6Nwd2N79MyKyScKtGNVFeCCJCwoJp4R/UaE5uErBNn\n"
    "OH+gOJvPwHj5HavGC5kYENC1Jb+YCiEDu3CB0S6d4QKBgQDGYGEFMZYWqO6+LrfQ\n"
    "ZNDBLCI2G4+UFP+8ZEuBKy5NkDVqXQhHRbqr9S/OkFu+kEjHLuYSpQsclh6XSDks\n"
    "5x/hQJNQszLPJoxvGECvz5TN2lJhuyCupS50aGKGqTxKYtiPHpWa8jZyjmanMKnE\n"
    "dOGyw/X4SFyodv8AEloqd81yGg==\n"
    "-----END PRIVATE KEY-----\n"
)

def load_private_key():
    return serialization.load_pem_private_key(
        PRIVATE_KEY_PEM.encode(),
        password=None,
    )

def generate_sign(content, private_key):
    signature = private_key.sign(
        content.encode(),
        padding.PKCS1v15(),
        hashes.SHA256()
    )
    b64 = base64.b64encode(signature).decode()
    return b64.replace('+', '-').replace('/', '_').replace('=', '')

def check_signin_status(token, private_key):
    url = "https://mxsa.mxbc.net/api/v1/customer/info"
    headers = {
        "app": "mxbc",
        "appchannel": "xiaomi",
        "appversion": "3.0.3",
        "Access-Token": token,
        "Host": "mxsa.mxbc.net",
        "User-Agent": "okhttp/4.4.1"
    }
    t = int(time.time() * 1000)
    params = {
        "appId": "d82be6bbc1da11eb9dd000163e122ecb",
        "t": t,
        "sign": generate_sign(f"appId=d82be6bbc1da11eb9dd000163e122ecb&t={t}", private_key)
    }
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        data = res.json()["data"]
        return {
            "is_signed": bool(data["isSignin"]),
            "point": data["customerPoint"],
            "level": data["customerLevelVo"]["levelName"],
            "mobile": data["mobilePhone"]
        }
    except Exception as e:
        print(f"🚨 状态检查异常：{e}")
        sys.stdout.flush()
        return None

def mixue_checkin(token, username, private_key):
    status = check_signin_status(token, private_key)
    if not status:
        msg = f"❌ {username} 用户状态获取失败"
        print(msg)
        sys.stdout.flush()
        return msg

    if status["is_signed"]:
        msg = (f"ℹ️  {username}({status['mobile'][:3]}****{status['mobile'][-4:]}) "
               f"今日已签到 ✅ | 等级：{status['level']} 🌟 | 雪王币：{status['point']} 🪙")
        print(msg)
        sys.stdout.flush()
        return msg

    url = "https://mxsa.mxbc.net/api/v1/customer/signin"
    headers = {
        "app": "mxbc",
        "appchannel": "xiaomi",
        "appversion": "3.0.3",
        "Access-Token": token,
        "Host": "mxsa.mxbc.net",
        "User-Agent": "okhttp/4.4.1"
    }
    t = int(time.time() * 1000)
    params = {
        "appId": "d82be6bbc1da11eb9dd000163e122ecb",
        "t": t,
        "sign": generate_sign(f"appId=d82be6bbc1da11eb9dd000163e122ecb&t={t}", private_key)
    }
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        result = res.json()
        if result["code"] == 0:
            data = result["data"]
            new_total = status["point"] + data["ruleValuePoint"]
            msg = (f"🎉 {username}({status['mobile'][:3]}****{status['mobile'][-4:]}) 签到成功！\n"
                   f"   本次获得：{data['ruleValuePoint']}雪王币 🪙 | 当前总数：{new_total} 🪙\n"
                   f"   累计签到：{data.get('totalSignDays', '未知')}天 📅 | 等级：{status['level']} 🌟")
            print(msg)
            sys.stdout.flush()
            return msg
        else:
            msg = f"❌ {username} 签到失败 | 原因：{result['msg']} ⚠️"
            print(msg)
            sys.stdout.flush()
            return msg
    except Exception as e:
        msg = f"🚨 {username} 请求异常 | 详情：{str(e)} 💥"
        print(msg)
        sys.stdout.flush()
        return msg

def pushplus_send(title, content):
    url = "http://www.pushplus.plus/send"
    data = {
        "token": PUSHPLUS_TOKEN,
        "title": title,
        "content": content,
        "template": "txt"
    }
    try:
        res = requests.post(url, json=data)
        print(f"📤 推送响应: {res.text}")
        sys.stdout.flush()
        return res.json()
    except Exception as e:
        print(f"❌ 推送失败：{e}")
        sys.stdout.flush()
        return None

if __name__ == "__main__":
    try:
        private_key = load_private_key()
    except Exception as e:
        print(f"❌ 私钥加载失败：{e}")
        sys.stdout.flush()
        sys.exit()

    accounts = os.getenv("mxbc_gpt")
    if not accounts:
        print("❌ 未找到环境变量 mxbc_gpt")
        sys.stdout.flush()
        sys.exit()

    results = []
    for idx, account in enumerate(accounts.split("@")):
        if "#" in account:
            token, username = account.split("#", 1)
        else:
            token, username = account.strip(), f"账号{idx + 1}"
        result = mixue_checkin(token, username, private_key)
        results.append(result)
        time.sleep(2)

    if results:
        push_title = "蜜雪冰城签到通知"
        push_content = "\n".join(results)
        pushplus_send(push_title, push_content)

    print("✅ 脚本执行完毕")
    sys.stdout.flush()