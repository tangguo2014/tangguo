# -*- coding: utf-8 -*-
"""
================================================================================
网上国网 每日签到（登录成功缓存15天后续静默账密登录）
================================================================================
依赖安装
      pip3 install requests gmssl pycryptodome

环境变量 
      wsgw_gpt   手机号#密码
      带备注：张三#13800138000#密码
      带IP 13800138000#密码#26.162.201.58
      变量值多段不影响运行，本子会自动识别
      多账号&分割或者换行

可选变量
     wsgw_IP 服务器公网出口IP 例111.119.253.202
     IP偏差脚本容易触发风控/要求短信验证。
     选填，不填不影响运行
     查IP：curl -s ifconfig.me


短信验证码步骤
       ① python3 网上国网.py --send-sms 
       ② 手机收到 6 位验证码（5 分钟内有效）
       ③ python3 网上国网.py --code 123456       
       ④ 成功会打印 ✅ 新设备校验通过，令牌已缓存

注意事项
      · 验证码 **只有 5 分钟有效**，超时要重新 --send-sms
      · 两条命令必须用**同一个账号**（codeKey 按账号缓存在
        ~/.sgcc_checkin/sms_device.json，换账号会读不到）
        兜底方案（密码走不通时）：免密短信登录
        python3 网上国网.py --sms-send
        python3 网上国网.py --sms-login 123456


参考命令
    --login          只做密码登录，验证账号密码
    --send-sms       触发新设备校验验证码
    --code 123456    密码 + 验证码，完成新设备校验
    --sms-login 123456  兜底：免密短信登录
    --status         查看缓存令牌还剩多久
    --query          只查状态不签到
    --smoke          用假令牌打一次请求，验证加密链路
    --no-push        本次不推送

================================================================================
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import base64  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import secrets  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402
from datetime import datetime  # noqa: E402

import requests  # noqa: E402
import threading  # noqa: E402
from Crypto.Cipher import AES  # noqa: E402
from Crypto.Util.Padding import pad  # noqa: E402
from gmssl import sm2, sm3, sm4  # noqa: E402
import urllib3  # noqa: E402

urllib3.disable_warnings()

BASE_URL = "https://csc-service.sgcc.com.cn:28630"
SERVER_PUBKEY = ("0409C10D38CF7B4E28097EAAA519E3157C9B4E72194CD13BD11932CE40ED5624A"
                 "FEFF27F78893E4C4FC2029DA147AB6CEAE0CDC8E3A547EFCDC5AB91757FE1CA60")
CLIENT_PRIVKEY = "50C4AF48DF75808050729977AA6BC127DC21A404755C660B94222F9D50FA4A75"
CHECK_CODE_AES_KEY = b"LNPO+ISJ+QeqNemFK+3OMUdexSn2i6pB"

LOGIN_PATH = "emss-uia-center-front/member/c2/f01"
DEVICE_SMS_PATH = "emss-uia-center-front/member/c1/f01"
SMS_LOGIN_PATH = "emss-uia-center-front/member/c2/f02"

DEVICE_ID = "000000"
DEVICE_MODEL = "sdk_gphone64_arm64"
ANDROID_RELEASE = "13"
PUSH_ID = "000000,000000"
TIMEOUT = 20
# 参考仓库：登录令牌约 15 天，剩余不足两天就主动重登，避免服务端提前作废
RENEW_MARGIN_S = 2 * 86400
# 服务端要求新设备安全校验时的业务码
DEVICE_VERIFY_CODE = "4006"

STATE_DIR = os.environ.get("SGCC_STATE_DIR") or os.path.join(
    os.path.expanduser("~"), ".sgcc_checkin")
MODE = (os.environ.get("SGCC_MODE") or "all").strip().lower()
# App 会把自己的网络出口 IP 报给服务端做风控比对，伪造 127.0.0.1 容易被判 RK 错误
DEVICE_IP = (os.environ.get("wsgw_IP") or os.environ.get("SGCC_DEVICE_IP") or "127.0.0.1").strip()

# 登录成功后从 userInfo 取到的省份，鉴权接口的 province 头要用它
PROVINCE = ""


def mask_account(value):
    """日志里不打印完整手机号，中间四位打码（脱敏版新增）。"""
    s = str(value or "")
    if len(s) >= 11:
        return s[:3] + "****" + s[-4:]
    if len(s) > 4:
        return s[:1] + "*" * (len(s) - 2) + s[-1]
    return "*" * len(s)


def app_guid():
    """60 位设备级 AppGuid（40 随机 + 17 位时间 + 3 位随机），与图灵盾令牌同机绑定。

    参考仓库要求 AppGuid 是「同一台 App 设备导出的 60 位值」，全 0 的 40 位假值
    会被风控直接判死（RK001 网络连接超时），所以这里生成一次后持久化复用。
    """
    path = os.path.join(STATE_DIR, "app_guid.txt")
    try:
        with open(path, encoding="utf-8") as f:
            value = f.read().strip()
        if len(value) == 60 and value.isalnum():
            return value
    except Exception:
        pass
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    value = ("".join(secrets.choice(alphabet) for _ in range(40))
             + datetime.now().strftime("%Y%m%d%H%M%S%f")[:17]
             + str(secrets.randbelow(900) + 100))
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(value)
    except Exception:
        pass
    return value


# ────────────────────────────── 加密信封 ──────────────────────────────
def _compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _sm4_key(key_text):
    return key_text[:16].encode("ascii")


def sm4_encrypt_hex(text, key_text):
    cipher = sm4.CryptSM4()
    cipher.set_key(_sm4_key(key_text), sm4.SM4_ENCRYPT)
    return cipher.crypt_ecb(text.encode("utf-8")).hex().upper()


def sm4_decrypt_hex(cipher_hex, key_text):
    cipher = sm4.CryptSM4()
    cipher.set_key(_sm4_key(key_text), sm4.SM4_DECRYPT)
    return cipher.crypt_ecb(bytes.fromhex(cipher_hex))


def _pubkey_body(pub_hex):
    return pub_hex[2:] if pub_hex.lower().startswith("04") else pub_hex


def sm2_encrypt_key(key_text, pub_hex):
    crypt = sm2.CryptSM2(private_key="", public_key=_pubkey_body(pub_hex), mode=1)
    return "04" + crypt.encrypt(key_text.encode("ascii")).hex().lower()


def _derive_pubkey(priv_hex):
    probe = sm2.CryptSM2(private_key=priv_hex, public_key="", mode=1)
    return probe._kg(int(priv_hex, 16), probe.ecc_table["g"])


def sm2_decrypt_key(wrapped_hex, priv_hex):
    wire = wrapped_hex[2:] if wrapped_hex.lower().startswith("04") else wrapped_hex
    crypt = sm2.CryptSM2(private_key=priv_hex, public_key=_derive_pubkey(priv_hex), mode=1)
    return crypt.decrypt(bytes.fromhex(wire)).decode("ascii")


def envelope(payload, timestamp=None, key_text=None):
    """构造 App 请求信封：data=SM4(json)，skey=SM2(SM4密钥)，sign=SM3(skey+data+ts)"""
    key_text = key_text or uuid.uuid4().hex
    timestamp = timestamp or str(int(time.time() * 1000))
    data = sm4_encrypt_hex(_compact(payload), key_text)
    skey = sm2_encrypt_key(key_text, SERVER_PUBKEY)
    sign = sm3.sm3_hash(list((skey + data + timestamp).encode("utf-8"))).lower()
    return {"data": data, "sign": sign, "skey": skey, "timestamp": timestamp}


def decrypt_response(outer):
    key_text = sm2_decrypt_key(str(outer["respKey"]), CLIENT_PRIVKEY)
    plain = sm4_decrypt_hex(str(outer["encryptData"]), key_text).rstrip(b"\x00")
    return json.loads(plain.decode("utf-8"))


# ────────────────────────────── 登录报文 ──────────────────────────────
def _java_hash(value):
    result = 0
    for char in value:
        result = (31 * result + ord(char)) & 0xFFFFFFFF
    return result


def _hashmap_order(value):
    """Gson 序列化 Map 时按 Java HashMap 桶序输出，md5 头必须跟着这个顺序算。"""
    if isinstance(value, dict):
        buckets = {}
        for key in value:
            hashed = _java_hash(str(key))
            hashed ^= hashed >> 16
            buckets.setdefault(hashed & 15, []).append(str(key))
        return {k: _hashmap_order(value[k])
                for bucket in sorted(buckets)
                for k in buckets[bucket]}
    if isinstance(value, list):
        return [_hashmap_order(item) for item in value]
    return value


def login_header_md5(params):
    material = "".join(
        key + json.dumps(_hashmap_order(params[key]), ensure_ascii=False, separators=(",", ":"))
        for key in sorted(params)
    )
    return hashlib.md5(material.encode("utf-8")).hexdigest()


def gen_check_code(account):
    stamp = int(time.time() * 1000)
    random_24 = "".join(str(secrets.randbelow(10)) for _ in range(24))
    raw = ("%s%s" % (account, stamp)).encode()
    enc = AES.new(CHECK_CODE_AES_KEY, AES.MODE_ECB).encrypt(pad(raw, 16))
    return random_24 + enc.hex()


def _usc_info():
    return {
        "isEncrypt": "false",
        "devciceIp": DEVICE_IP,
        "devciceId": DEVICE_ID,
        "tenant": "state_grid",
        "member": "2202",
        "optSys": "Android %s" % ANDROID_RELEASE,
        "operatorType": "",
        "devciceName": DEVICE_MODEL,
        "pushId": PUSH_ID,
    }


def password_login_payload(account, password, code="", code_key=""):
    return {
        "quInfo": {
            "code": code,
            "codeKey": code_key,
            "account": account,
            "password": hashlib.md5(password.encode("utf-8")).hexdigest(),
            "optSys": "Android",
            "pushId": PUSH_ID,
            "addressCity": "",
            "addressProvince": "",
            "addressRegion": "",
        },
        "uscInfo": _usc_info(),
        "checkCode": gen_check_code(account),
        "avalonValidCode": "",
    }


def sms_payload(account, business="logindevice"):
    """发短信：logindevice=新设备安全校验（配合密码登录），login=免密登录"""
    return {
        "uscInfo": {
            "tenant": "state_grid",
            "member": "2202",
            "devciceId": DEVICE_ID,
            "devciceName": DEVICE_MODEL,
            "devciceIp": DEVICE_IP,
        },
        "quInfo": {
            "voiceCodeFlag": False,
            "account": account,
            "sendType": 0,
            "businessType": business,
        },
    }


def sms_login_payload(account, code, code_key):
    return {
        "quInfo": {
            "account": account,
            "businessType": "login",
            "code": code,
            "optSys": "Android",
            "pushId": PUSH_ID,
            "codeKey": code_key,
        },
        "uscInfo": _usc_info(),
    }


# ────────────────────────────── 请求 ──────────────────────────────
def _app_guid_new():
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    prefix = "".join(secrets.choice(alphabet) for _ in range(40))
    stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")[:17]
    return "%s%s%d" % (prefix, stamp, secrets.randbelow(900) + 100)


def device_token():
    """图灵盾 deviceTokenTX（App 每 4 小时换一次，这里缓存 3.5 小时）"""
    cache = os.path.join(STATE_DIR, "device_token.json")
    try:
        with open(cache, encoding="utf-8") as f:
            d = json.load(f)
        if float(d.get("expires_at", 0)) > time.time():
            return d["token"], d["token_time"]
    except Exception:
        pass
    try:
        from pathlib import Path

        # 内联模块定义在文件末尾，用 sys.modules 取本模块，避免 exec/globals 差异
        _mod = sys.modules.get(__name__) or globals()
        _gt = getattr(_mod, "generate_device_token", None)
        _lp = getattr(_mod, "load_or_create_profile", None)
        if not callable(_gt) or not callable(_lp):
            _g2 = globals()
            _gt = _g2.get("generate_device_token")
            _lp = _g2.get("load_or_create_profile")
        if not callable(_gt) or not callable(_lp):
            raise RuntimeError("图灵盾模块未加载")

        profile = _lp(Path(STATE_DIR) / "device_profile.json")
        gen = _gt(profile)
    except Exception as exc:  # 生成失败不阻塞，退回不带该头
        print("  ⚠️ 图灵盾令牌生成失败（%s），本次不带 deviceTokenTX" % str(exc)[:60])
        return "", ""
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(cache, "w", encoding="utf-8") as f:
        json.dump({"token": gen.token, "token_time": gen.token_time,
                   "expires_at": time.time() + 3.5 * 3600}, f)
    return gen.token, gen.token_time


def _request_ts():
    stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")[:17]
    return stamp + "".join(str(secrets.randbelow(10)) for _ in range(6))


def headers(token="", userid="0", login_params=None, with_device=True):
    if os.environ.get("SGCC_NO_TURING"):
        with_device = False
    tok_tx, tok_time = device_token() if with_device else ("", "")
    h = {
        "Content-Type": "application/json; charset=UTF-8",
        "timeStamp": (datetime.now().strftime("%Y%m%d%H%M%S")
                      if login_params is not None else _request_ts()),
        "t": token,
        "userid": userid,
        "AppGuid": app_guid(),
        "AppGuidNew": _app_guid_new(),
        "security": "android",
        "appcode": "WSGW-SG1001-APP",
        "datacenter": "99",
        "AccessMethod": "App",
        "deviceTokenTX": tok_tx,
        "deviceTokenTXTime": tok_time,
        "province": PROVINCE if token else "",
        "version": "3.2.3",
        "wsgwType": "android",
        "ip": DEVICE_IP,
        "os": "android",
        "User-Agent": "okhttp/3.14.9",
    }
    if login_params is not None:
        h["md5"] = login_header_md5(login_params)
    return h


def post(path, payload, token="", userid="0", login_params=None, raw_envelope=None):
    env = raw_envelope or envelope(payload)
    url = BASE_URL.rstrip("/") + "/" + path.lstrip("/")
    resp = requests.post(url, data=_compact(env),
                         headers=headers(token, userid, login_params),
                         timeout=TIMEOUT, verify=False)
    text = resp.text
    if resp.status_code >= 400:
        raise RuntimeError("网关 HTTP %s：%s" % (resp.status_code, text[:150]))
    try:
        outer = json.loads(text)
    except Exception:
        raise RuntimeError("网关返回非 JSON：%s" % text[:150])
    if "respKey" not in outer or "encryptData" not in outer:
        return outer
    return decrypt_response(outer)


def split_result(resp):
    """拆出 srvrt 业务码 / 消息 / bizrt 数据"""
    data = resp.get("data") if isinstance(resp, dict) else None
    top = str(resp.get("code", "")) if isinstance(resp, dict) else ""
    if not isinstance(data, dict):
        return top, str(resp.get("message", "")) if isinstance(resp, dict) else "", {}
    server = data.get("srvrt") or {}
    code = str(server.get("resultCode") or "") or top
    msg = str(server.get("resultMessage") or resp.get("message", ""))
    return code, msg, data


# ────────────────────────────── 令牌缓存 ──────────────────────────────
def _state_file(account):
    os.makedirs(STATE_DIR, exist_ok=True)
    return os.path.join(STATE_DIR, "token_%s.json" % account)


def load_token(account):
    global PROVINCE
    try:
        with open(_state_file(account), encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    # 剩余不足两天就当作过期：服务端可能提前作废令牌（参考仓库 issue #2）
    if not d.get("token") or float(d.get("expires_at", 0)) < time.time() + RENEW_MARGIN_S:
        return None
    if d.get("province"):
        PROVINCE = str(d["province"])
    return d


def save_token(account, token, userid, lifetime):
    d = {"account": account, "token": token, "userid": str(userid),
         "province": PROVINCE,
         "expires_at": time.time() + max(3600, lifetime),
         "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(_state_file(account), "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    return d


# ────────────────────────────── 登录流程 ──────────────────────────────
class LoginError(Exception):
    def __init__(self, code, message):
        self.code = str(code)
        super().__init__("%s %s" % (code, message))


def _login_result(data, label="登录"):
    """从 bizrt 里取 token / userId / 有效期 / 省份"""
    global PROVINCE
    bizrt = data.get("bizrt") or {}
    token = str(bizrt.get("token") or "")
    if not token:
        raise LoginError("no_token", "%s返回里没有 token" % label)
    info = bizrt.get("userInfo")
    if isinstance(info, list):
        info = next((x for x in info if isinstance(x, dict)), {}) or {}
    elif not isinstance(info, dict):
        info = {}
    userid = str(info.get("userId") or bizrt.get("userId") or "0")
    province = str(info.get("addressProvince") or "")
    if province:
        PROVINCE = province
    try:
        lifetime = int(bizrt.get("tokenExpireTime") or 1296000)
    except Exception:
        lifetime = 1296000
    return token, userid, lifetime


def do_login(account, password, code="", code_key=""):
    """密码登录（c2/f01）。code/code_key 只在「新设备校验」后重试时携带。"""
    if not password:
        raise LoginError("no_password", "wsgw_gpt 缺少密码")
    if bool(code) != bool(code_key):
        raise LoginError("bad_code", "验证码和 codeKey 必须一起提供")
    payload = password_login_payload(account, password, code, code_key)
    resp = post(LOGIN_PATH, payload, login_params=payload)
    rcode, rmsg, data = split_result(resp)
    if rcode != "0000":
        raise LoginError(rcode, rmsg or "登录失败")
    return _login_result(data, "密码登录")


def send_sms(account, business="logindevice"):
    """发短信验证码，返回 codeKey。

    新设备校验用 logindevice：服务端判定「这台设备没见过」时才会接受，
    它是密码登录的补充，不是独立登录方式。
    """
    resp = post(DEVICE_SMS_PATH, sms_payload(account, business))
    rcode, rmsg, data = split_result(resp)
    if rcode != "0000":
        raise LoginError(rcode, rmsg or "短信发送失败")
    key = str((data.get("bizrt") or {}).get("codeKey") or "")
    if not key:
        raise LoginError("no_code_key", "响应里没有 codeKey")
    return key


def sms_login(account, code, code_key):
    """兜底：免密短信登录（c2/f02）。参考仓库未使用，仅在没有密码时保底。"""
    payload = sms_login_payload(account, code, code_key)
    resp = post(SMS_LOGIN_PATH, payload, login_params=payload)
    rcode, rmsg, data = split_result(resp)
    if rcode != "0000":
        raise LoginError(rcode, rmsg or "短信登录失败")
    return _login_result(data, "短信登录")


def ensure_token(account, password=""):
    """优先级：环境变量手抓令牌 > 本地缓存 > 密码登录"""
    env_t = (os.environ.get("SGCC_TOKEN") or "").strip()
    env_u = (os.environ.get("SGCC_USERID") or "").strip()
    if env_t:
        return env_t, env_u or "0", "环境变量"
    cached = load_token(account)
    if cached:
        left = int((cached["expires_at"] - time.time()) / 3600)
        return cached["token"], cached["userid"], "本地缓存（剩余约 %d 小时）" % left
    token, userid, lifetime = do_login(account, password)
    save_token(account, token, userid, lifetime)
    return token, userid, "密码登录"


# ────────────────────────────── 签到报文 ──────────────────────────────
SKEYS = {
    "query_sign":   {"api": "/osg-omgmt1042/member/c1/q104051",
                     "skey": "0426710b7ea19a1aed28451ae6c707adfcccc0c88873bb1d1e99aa8e1d649aa5d04cff486e45658dfe315797293bbcbc9c3af984174ea3845495fdca97af287d42751d88c8bac339fc83a5fa200d502a79d1808cc9b22514f3d2b7f1a3f36397b1c2ed572b03b42a84489d915562a867696db358a03db6bc90ad0a798b385bf3da",
                      "data": "91E0D8E458CB8A0D5994034BDCA3B4F5D8AD8C68B49854718FD6637927CEB417"},
    "query_detail": {"api": "/osg-omgmt1042/member/c1/q022607",
                     "skey": "04809a31789533cdb4b2884dd68baca625ba2216985566bdf3f0a107dbd1c3ac9c7a29f2e4f994d9ccf4eecd4d0bc2aa3781faf14e24051b03ded62822bc0b0ee7a3f4536d716d3cbbc1d0ca5fa1dcbb59c7751f2ec8b57a7791ed71e7699e9010ff12ceeb11f0db89786d0824157027e0a94f6c8e4951e4b5e0c9d74afca5f861",
                      "data": "5F1005470B2ADD0C1AC712311CD1CEE4CF036E2FA6BB0239B45E912B393ECD39DF82DB04AD39736708C5F1D874870BE731C335B5E9125537203B8AE7E3F3DABBE540311FFDD91E3885B72C2301BEB31E5BD8855F90BEABC491DFE81300018F283B49DD10B5CCFF24131674D6F8742963FD710E7A8C69C31112A35360828EDFDBEBB101DFE495E1008A18766AF087D38CBBF895EB67A651727A681CDA999133D1BCCA0194E17676084423C6DB1315481704EDE22BF66A7FD17D4C5679034690187854BBF2223B2F17F000768A1C7DF217"},
    "query_record":  {"api": "/osg-omgmt1042/member/c1/q048740",
                      "skey": "04db50d17d436cfb8aa96f40afdb2db2b9165915b64cfb304d78ab23faf128ca2095dd39db003646eb540f496d6c18e55b0c0a202fc9907aae216412c3d87566ecf93f4a3ccf2f5420bed38348a0b21b685e232c24fdb1f7075f1a40d0e3f2028c17b7761fa53d7a523403fabb07aadcc25b1c7bf50e00bba217aa88deba8b42dd",
                      "data": "00C227FC6AB44280D1D8F3FA22C15DE13CCC99CDA3A987C60F920FAB345469AE64D2FD40E840731B4441E3347AFC2F7D55B8F1F6223199E2FA10D083236E9BD054AE98E5ED27FA69F27486C139056868"},
    "query_history": {"api": "/osg-omgmt1042/member/c1/q034992",
                      "skey": "04c6b8592f326802c3883755728e4d503abe32bb65274539ff2bb339636c57681dc5bede00432a1834ff2fe84bc27673ab7844630093a5663cb966c50dea8553a7d108d4f3f023480c7438146b2d43123b732d1330486efa77c540a5818deb750c7171569b1057e07eaff4f350a4b0d9a606031913442fca97f8de8f8650901e59",
                      "data": "A17F3C75BE291563A9404436CCE99B8B9A8E5D47F03EB232B4FA0014E0DF12268FA0BB1ED4C63B2920BE1062DB689A6C276F09906ADA9A4C489F9436E8349E629C3D3058FD71D183CF12D543659E90BF7AA4407885027EF8F67D894F3DC943ABBEE68A7568BDEACBAB0F9DF8E065A4F1EB58EF78D2C403126EEFBBCBD2AB26DA"},
    "query_points":  {"api": "/osg-omgmt1042/member/c1/q034990",
                      "skey": "04a3983f92286aa858da4b4a8481902e23524b47bcfacbc07db15258b3193f651708efd6e6652bcd3227f1f5bbb35240ebce1fffffe3ee51ca042e1f23ae11bcea08b34428344d002453790c6415de5e8e37bae096dd8ee1276d841f27f112d0fcbda40e561e48c8f52c440a34c7d3e63acfa1342b77a89fd4ff1a7a425c470ca5",
                      "data": "DB95BBA3019AE0EF248D15AAAFB542DF9180D758DF418E67BD3B68CDAA270193E42E7E09675131AB41B48308AE1AAE756091F50709498E6F3FA86C5B5566F0807403097FEC0A73EE7260E5DAD4DAE7CA"},
    "m1_0584615": {"api": "/osg-omgmt1042/member/m1/0584615",
                 "skey": "0400db06d82e8f4ead991f1cd85cd333d938ad7e5edf42f4db8e169adff58d26574beff73266481b9b59202b14fa13b87065ed0e3a5b5d820cf4e8168a17e3f1b8b5b235097e4414dee1629d5a28b0203552041d0c6fd4b91fffbeb33f27a5dc8dbd08df87ee1a264ba7befab32c15f065c10c1af245594fb0aff79d7b69ca6738",
                  "data": "C68F73A7A7702E4D784EAD70A7AA8AE1B775FF3DC79A63EDC3CE08EBC1AE51A566A74FA379F87F5084C8D0D77E27FF7CE8B42AE360CCC3EF9F3DEBAC0D7453C86E39A763930F138F61A7FED8AAC29FA8C9C22FA4114A49AC8A4A288FB8B74596"},
    "m1_0103514": {"api": "/osg-omgmt1042/member/m1/0103514",
                  "skey": "04c7c381e4a5223d32b5ac048e3ead286178db349dea351d1c24486ffe03c8e8fb2aea413b53d8189ba225737b05f0fd12e4f87f6d4eb07a8b5fc6d56aa1525f512d72856c84e368d8d196d6a27ea202878ce5c62da48911d0b6a1e120169f74f5a70beaf644bf12dac75e08c6be5a1255df2cfc0c1ef4d6615851cf151fa5ad3b",
                  "data": "69CBE9F400071A7F84DCBF7E26DF619FD45A3652A7F21A4A509B5FFDF364B40FB64E91A579517F7F9541AF639429EB853F422AAD5975A35AB190FBF2B99BF1FD344A902194ED78A9A34F81FEC79F12D63FA434AF9A7C043ABC1C74E6C3DD4EFF"},
}

# 实测（2026-11-07，真实账号）：
#   m1/0584615 → -100「无法补签本周以外断签，不满足补签条件！」⇒ **补签**
#   m1/0103514 → 102007「今日参与次数已用完，请明日再来！」     ⇒ **签到**（今天已签过）
# 原始 国网签到10.py 里这两个标签是反的。若哪天服务端反过来，脚本会自动对调并记住。
SIGN_KEYS = {"sign": "m1_0103514", "resign": "m1_0584615"}
SIGN_MAP_FLAG = "sign_map_flipped.flag"


def _load_sign_map():
    if os.path.isfile(os.path.join(STATE_DIR, SIGN_MAP_FLAG)):
        SIGN_KEYS["sign"], SIGN_KEYS["resign"] = SIGN_KEYS["resign"], SIGN_KEYS["sign"]


def _flip_sign_map():
    """签到/补签对调，并把结果持久化，下次启动直接按新映射走"""
    SIGN_KEYS["sign"], SIGN_KEYS["resign"] = SIGN_KEYS["resign"], SIGN_KEYS["sign"]
    flag = os.path.join(STATE_DIR, SIGN_MAP_FLAG)
    try:
        if os.path.isfile(flag):
            os.remove(flag)
        else:
            os.makedirs(STATE_DIR, exist_ok=True)
            open(flag, "w").close()
    except Exception:
        pass


_load_sign_map()

LABELS = {
    "query_sign": "成长金余额",
    "query_detail": "签到明细",
    "query_record": "补签记录",
    "query_history": "活动信息",
    "query_points": "积分余额",
}


def call(name, token, userid):
    info = SKEYS[SIGN_KEYS.get(name, name)]
    resp = post(info["api"], None, token=token, userid=userid,
                raw_envelope=envelope_with(info))
    code, msg, data = split_result(resp)
    if code == "101007":
        raise LoginError("101007", "令牌已过期")
    return code, msg, data


# 签到结果分类：success=签上了 / done=今天已经签过 / fail=真失败
SIGN_DONE_CODES = {"102007"}
SIGN_OK_CODES = {"1", "0", "0000", "000000"}


def do_sign(token, userid, name="sign"):
    """执行签到，并把 rtnCode 归一成 success/done/fail。

    业务结果在 data.rtnCode（外层 srvrt 永远是 1 成功），所以不能只看 split_result。
    """
    code, msg, data = call(name, token, userid)
    data = data if isinstance(data, dict) else {}
    rcode = str(data.get("rtnCode", ""))
    rmsg = str(data.get("rtnMessage") or msg or "")
    if name == "sign" and "补签" in rmsg:
        # 服务端哪天把两个动词倒过来，这里自动纠正并记住
        print("  ⚠️ 签到接口返回补签语义，自动对调签到/补签映射后重试")
        _flip_sign_map()
        code, msg, data = call(name, token, userid)
        data = data if isinstance(data, dict) else {}
        rcode = str(data.get("rtnCode", ""))
        rmsg = str(data.get("rtnMessage") or msg or "")
    if rcode in SIGN_DONE_CODES or "次数已用完" in rmsg or "已签到" in rmsg:
        status = "done"
    elif rcode in SIGN_OK_CODES:
        status = "success"
    else:
        status = "fail"
    return status, rmsg, rcode


def envelope_with(info):
    """复用抓包里的 skey/data，只重算 timestamp 和 sign"""
    ts = str(int(time.time() * 1000))
    sign = sm3.sm3_hash(list((info["skey"] + info["data"] + ts).encode("utf-8"))).lower()
    return {"data": info["data"], "sign": sign, "skey": info["skey"], "timestamp": ts}


def fmt(data):
    if data is None:
        return "无"
    if isinstance(data, (int, str, float)):
        return str(data)
    if isinstance(data, dict):
        rtn = data.get("rtnData")
        msg = data.get("rtnMessage", "")
        if rtn is not None:
            return "%s（%s）" % (msg, rtn) if rtn else str(msg)
        return str(msg) or json.dumps(data, ensure_ascii=False)[:120]
    if isinstance(data, list):
        return "共 %d 条" % len(data)
    return str(data)[:120]


def banner():
    print()
    print("  ╭────────────────────────────────╮")
    print("  │   网上国网 · 签到（自动登录）   │")
    print("  ╰────────────────────────────────╯")


# ────────────────────────────── 主流程 ──────────────────────────────
def cmd_smoke():
    """用假令牌打一次查询：只要能拿到业务码，就说明加密/签名链路是通的"""
    print("🔧 加密链路自检（假令牌）")
    resp = post(SKEYS["query_sign"]["api"], None, token="SMOKE", userid="0",
                raw_envelope=envelope_with(SKEYS["query_sign"]))
    code, msg, _ = split_result(resp)
    print("  网关原始返回：%s" % json.dumps(resp, ensure_ascii=False)[:400])
    print("  业务码：%s  消息：%s" % (code, msg))
    if code and code != "101007":
        print("  ℹ️ 非 101007 也算正常：说明信封被网关接受，只是令牌无效")
    return 0


RESULT_STORE = []
NOTIFY_TITLE = "网上国网签到 ⚡"


def parse_accounts():
    """wsgw_gpt 支持把 IP 直接写进变量行尾：

        手机号#密码                      → IP 用 wsgw_IP 或默认 127.0.0.1
        手机号#密码#111.162.201.189      → 该账号独立 IP（行内）
        张三#手机号#密码#111.162.201.189 → 带备注 + 行内 IP
        多账号 & 或换行分隔，每行可各自带 IP
    """
    raw = (os.environ.get("wsgw_gpt") or "").strip()
    if not raw:
        a = (os.environ.get("SGCC_ACCOUNT") or "").strip()
        pw = (os.environ.get("SGCC_PASSWORD") or "").strip()
        return [{"name": a, "account": a, "password": pw, "ip": None}] if (a and pw) else []

    out = []
    for line in raw.replace("\n", "&").split("&"):
        line = line.strip()
        if not line:
            continue
        parts = [x.strip() for x in line.split("#")]
        name = ip = None
        if len(parts) >= 4:
            name, acc, pwd, ip = parts[0], parts[1], parts[2], parts[3]
        elif len(parts) == 3:
            # 判断第三段是不是 IP（含点且全是数字点）
            if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", parts[2]):
                acc, pwd, ip = parts[0], parts[1], parts[2]
            else:
                name, acc, pwd = parts[0], parts[1], parts[2]
        elif len(parts) == 2:
            acc, pwd = parts[0], parts[1]
        else:
            continue
        if acc and pwd:
            out.append({"name": name or acc, "account": acc,
                        "password": pwd, "ip": ip or None})
    return out


def notify(title, content, timeout=60):
    """青龙 notify.py，最多等 timeout 秒，超时直接放弃"""
    r = {"ok": False, "err": None}

    def _go():
        try:
            from notify import send
            send(title, content)
            r["ok"] = True
            return
        except ImportError:
            pass
        except Exception as e:
            r["err"] = str(e)[:120]
            return
        for pth in ("/ql/scripts/notify.py", "/ql/data/scripts/notify.py"):
            if not os.path.exists(pth):
                continue
            try:
                import importlib.util
                sp = importlib.util.spec_from_file_location("qlnotify", pth)
                mm = importlib.util.module_from_spec(sp)
                sp.loader.exec_module(mm)
                mm.send(title, content)
                r["ok"] = True
                return
            except Exception as e:
                r["err"] = "%s: %s" % (pth, str(e)[:100])

    t = threading.Thread(target=_go, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        print("⚠️ 推送超时（%ds），已放弃" % timeout)
        return False
    if r["ok"]:
        print("✅ 推送已发送")
        return True
    print("⚠️ 推送未发送：%s" % (r["err"] or "未找到 notify.py"))
    return False


def _fmt_balance(v):
    """国网返回形如 {rtnCode:1, rtnData:145, rtnMessage:"查询成功"}，余额在 rtnData"""
    if v is None:
        return "—"
    if isinstance(v, (int, float, str)):
        return str(v)
    if isinstance(v, dict):
        # 优先国网标准字段
        if v.get("rtnData") not in (None, ""):
            return str(v["rtnData"])
        for k in ("balance", "amount", "score", "point", "value",
                  "growValue", "growScore", "totalAmount", "availableAmount"):
            if v.get(k) is not None:
                return str(v[k])
        for vv in v.values():
            if isinstance(vv, (int, float, str)):
                return str(vv)
    return "—"


def build_report(results):
    if not results:
        return ""
    lines = ["【网上国网】"]
    for name, rc in results:
        rec = next((x for x in RESULT_STORE if (x.get("name") or "") == name), None)
        if not rec:
            lines.append("❌  %s  无结果" % name)
            continue
        if rec.get("expired"):
            lines.append("⏰  %s  令牌过期" % name)
            continue
        st = rec.get("status")
        mark = "✅" if st == "success" else ("·" if st == "done" else "❌")
        bal = _fmt_balance(rec.get("balance_after") or rec.get("balance_before"))
        line = "%s  %s  成长金 %s" % (mark, name, bal)
        if st == "fail":
            line += "  ⚠️%s" % (rec.get("msg") or "失败")[:20]
        lines.append(line)
    return "\n".join(lines)


def main():
    args = sys.argv[1:]

    if "--smoke" in args:
        return cmd_smoke()

    accounts = parse_accounts()
    if not accounts:
        print("❌ 未配置变量 wsgw_gpt（手机号#密码，多账号用 & 或换行）")
        return 1

    # 非日常动作（登录/验证码/查状态等）只跑第一个账号
    one_shot = any(a in ("--status", "--login", "--send-sms", "--code",
                         "--sms-login", "--resign", "--query") for a in args)

    if len(accounts) > 1 and not one_shot:
        results = []
        for i, acct in enumerate(accounts):
            print("\n" + "=" * 55)
            print("账号 %d/%d：%s" % (i + 1, len(accounts), acct["name"]))
            print("=" * 55)
            rc = _run_one(acct, args)
            results.append((acct["name"], rc))
            if i < len(accounts) - 1:
                time.sleep(8)
        text = build_report(results)
        print("\n" + text)
        if "--no-push" not in args:
            notify(NOTIFY_TITLE, text)
        return 0 if all(rc == 0 for _, rc in results) else 1

    acct = accounts[0]
    rc = _run_one(acct, args)
    if not one_shot and "--no-push" not in args:
        text = build_report([(acct["name"], rc)])
        if text:
            print("\n" + text)
            notify(NOTIFY_TITLE, text)
    return rc


def _run_one(acct, args):
    global DEVICE_IP
    account = acct["account"]
    password = acct["password"]

    # 行内 IP 优先，其次 wsgw_IP / SGCC_DEVICE_IP，最后 127.0.0.1
    if acct.get("ip"):
        DEVICE_IP = acct["ip"]
        print("🌐 使用行内 IP：%s" % DEVICE_IP)
    else:
        print("🌐 使用 IP：%s" % DEVICE_IP)

    banner()

    def _key_file(kind):
        os.makedirs(STATE_DIR, exist_ok=True)
        return os.path.join(STATE_DIR, "sms_%s.json" % kind)

    def _save_key(kind, key):
        with open(_key_file(kind), "w", encoding="utf-8") as f:
            json.dump({"account": account, "code_key": key, "at": time.time()},
                      f, ensure_ascii=False)

    def _read_key(kind):
        try:
            with open(_key_file(kind), encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            return ""
        if d.get("account") != account:
            return ""
        if time.time() - float(d.get("at", 0)) > 600:
            return ""
        return str(d.get("code_key") or "")

    def _request_code(kind="device"):
        key = send_sms(account, "logindevice" if kind == "device" else "login")
        _save_key(kind, key)
        if kind == "device":
            print("📱 新设备安全校验：验证码已发送到 %s（5 分钟内有效）" % mask_account(account))
            print("   下一步：python3 网上国网.py --code <6位验证码>")
        else:
            print("📱 免密登录验证码已发送到 %s（5 分钟内有效）" % mask_account(account))
            print("   下一步：python3 网上国网.py --sms-login <6位验证码>")
        return key

    def _arg_value(flag):
        idx = args.index(flag)
        return args[idx + 1] if len(args) > idx + 1 else ""

    if "--status" in args:
        try:
            with open(_state_file(account), encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            d = None
        if not d:
            print("📭 还没有缓存令牌（%s）" % mask_account(account))
            return 1
        left = (float(d.get("expires_at", 0)) - time.time()) / 86400
        print("🔑 userId=%s  省份=%s  剩余 %.1f 天  保存于 %s"
              % (d.get("userid"), d.get("province") or "-", left, d.get("saved_at")))
        return 0 if left > 0 else 1

    if "--send-sms" in args:
        try:
            _request_code("device")
        except LoginError as e:
            print("❌ 验证码发送失败：%s" % e)
            return 1
        return 0

    if "--sms-send" in args:
        try:
            _request_code("login")
        except LoginError as e:
            print("❌ 验证码发送失败：%s" % e)
            return 1
        return 0

    if "--code" in args:
        code = _arg_value("--code").strip()
        if len(code) != 6 or not code.isdigit():
            print("❌ 验证码必须是 6 位数字")
            return 1
        key = _read_key("device")
        if not key:
            print("❌ 没有可用的 codeKey（或已超过 5 分钟）")
            print("   先执行：python3 网上国网.py --send-sms")
            return 1
        try:
            token, userid, lifetime = do_login(account, password, code, key)
        except LoginError as e:
            print("❌ 密码 + 验证码登录失败：%s" % e)
            if e.code == DEVICE_VERIFY_CODE:
                print("   ℹ️ 服务端仍在要求新设备校验，请重新 --send-sms 后再试")
            return 1
        save_token(account, token, userid, lifetime)
        try:
            os.remove(_key_file("device"))
        except Exception:
            pass
        print("✅ 密码 + 验证码登录成功，令牌已缓存（%d 天后过期），userId=%s"
              % (int(lifetime / 86400), userid))
        return 0

    if "--sms-login" in args:
        code = _arg_value("--sms-login").strip()
        key = _read_key("login")
        if not key:
            print("❌ 没有可用的 codeKey，请先执行 --sms-send")
            return 1
        try:
            token, userid, lifetime = sms_login(account, code, key)
        except LoginError as e:
            print("❌ 短信登录失败：%s" % e)
            return 1
        save_token(account, token, userid, lifetime)
        print("✅ 短信登录成功，令牌已缓存（%d 天后过期），userId=%s"
              % (int(lifetime / 86400), userid))
        return 0

    if "--login" in args:
        try:
            token, userid, lifetime = do_login(account, password)
        except LoginError as e:
            print("❌ 登录失败：%s" % e)
            if e.code == DEVICE_VERIFY_CODE:
                print("   ℹ️ 服务端要求「新设备安全校验」，正在自动发送验证码…")
                try:
                    _request_code("device")
                except LoginError as send_err:
                    print("   ❌ 验证码发送失败：%s" % send_err)
            return 1
        save_token(account, token, userid, lifetime)
        print("✅ 密码登录成功，令牌已缓存（%d 天后过期），userId=%s"
              % (int(lifetime / 86400), userid))
        return 0

    # 正常签到流程
    try:
        token, userid, source = ensure_token(account, password)
    except LoginError as e:
        print("❌ 获取令牌失败：%s" % e)
        if e.code == DEVICE_VERIFY_CODE:
            print("   ℹ️ 新设备校验：先 --send-sms 再 --code <验证码>")
        return 1
    print("🔑 令牌来源：%s | userId=%s" % (source, userid))

    expired = {"hit": False}

    result = {"status": "", "balance_before": None, "balance_after": None,
              "msg": "", "expired": False}

    def safe_query(name):
        """只读查询：失败只提示，不影响退出码"""
        if expired["hit"]:
            return None
        try:
            code, msg, data = call(name, token, userid)
        except LoginError as e:
            print("  ⚠️ %s" % e)
            expired["hit"] = True
            return None
        except Exception as e:
            print("  ❌ 请求异常：%s" % str(e)[:120])
            return None
        print("  %s：%s" % (LABELS.get(name, name), fmt(data)))
        if name == "query_sign":
            result["balance_after" if result["status"] else "balance_before"] = data
        return data

    def run_sign(name="sign"):
        """写操作：success/done 都算成功，fail 才算失败"""
        label = "签到" if name == "sign" else "补签"
        try:
            status, rmsg, rcode = do_sign(token, userid, name)
        except LoginError as e:
            print("  ⚠️ %s" % e)
            expired["hit"] = True
            result["status"] = "fail"
            return "fail"
        except Exception as e:
            print("  ❌ 请求异常：%s" % str(e)[:120])
            result["status"] = "fail"
            return "fail"
        if status == "success":
            print("  %s结果：✅ %s" % (label, rmsg or "成功"))
        elif status == "done":
            print("  %s结果：ℹ️ 今日已%s（%s）" % (label, label, rmsg))
        else:
            print("  %s结果：❌ %s（rtnCode=%s）" % (label, rmsg, rcode))
        result["status"] = status
        result["msg"] = rmsg or ""
        return status

    if MODE == "query":
        for n in ["query_sign", "query_detail", "query_record", "query_history", "query_points"]:
            if safe_query(n) is None and expired["hit"]:
                break
            time.sleep(1.5)
    elif MODE == "sign":
        run_sign("sign")
    elif MODE == "resign":
        run_sign("resign")
    else:
        print("\n📊 第一步：成长金余额")
        safe_query("query_sign")
        time.sleep(1.5)
        print("\n✍️ 第二步：执行签到")
        run_sign("sign")
        if not expired["hit"]:
            time.sleep(1.5)
            print("\n📊 第三步：签到后余额")
            safe_query("query_sign")

    result["expired"] = expired["hit"]
    result["name"] = acct.get("name") or account
    RESULT_STORE.append(result)

    if expired["hit"]:
        print("\n❌ 令牌已过期：请用 --login（密码）或 --send-sms/--code（短信）重新获取")
        return 1
    if result["status"] == "fail":
        print("\n❌ 签到失败")
        return 1
    print("\n✅ 完成")
    return 0




# ==================================================================
# 以下为图灵盾设备令牌模块（原 sgcc_turing/ 合并内联）
# ==================================================================

_REFERENCE_ENVIRONMENT_JSON = r"""{
  "schema_version": 1,
  "reference": "official App on Android 13 arm64 reference environment",
  "contains_account_data": false,
  "excluded_dynamic_or_identifying_tags": [
    16,
    17,
    26,
    34,
    125,
    126,
    129,
    141,
    146,
    148
  ],
  "stable_feature_values": {
    "21": "0",
    "108": "5",
    "7": "",
    "10": "5939.59,5939.59",
    "105": "100",
    "14": "1080*2154",
    "101": "0",
    "152": "0",
    "103": "0",
    "104": "2",
    "33": "33",
    "11": "type=1:name=Goldfish 3-axis Accelerometer:vendor=The Android Open Source Project:resolution=2.480159E-4:,type=4:name=Goldfish 3-axis Gyroscope:vendor=The Android Open Source Project:resolution=0.001:,type=2:name=Goldfish 3-axis Magnetic field sensor:vendor=The Android Open Source Project:resolution=0.5:,type=5:name=Goldfish Light sensor:vendor=The Android Open Source Project:resolution=1.0:,type=6:name=Goldfish Pressure sensor:vendor=The Android Open Source Project:resolution=1.0:,",
    "24": "sdk_gphone64_arm64",
    "12": "",
    "23": "google",
    "8": "google/sdk_gphone64_arm64/emu64a:13/TE1A.240213.009/12342917:userdebug/dev-keys",
    "13": "1000.0",
    "25": "13",
    "20": "0",
    "100": "",
    "22": "",
    "102": "0",
    "106": "",
    "115": "",
    "113": "0",
    "114": "0",
    "44": "",
    "35": "",
    "42": "Google",
    "107": "com.sgcc.wsgw.cn_DAB5608ECD1ABEF80E5FA277CF3B8D50",
    "200": "32,2,3,4,36,5,6,40,136,43,45,143,144,10002,19,10003",
    "116": "",
    "117": "1",
    "45": "",
    "118": "v4;-1_v6;-1",
    "120": "rwxp;/apex/com.android.art/lib64/libart.so,rwxp;/apex/com.android.runtime/lib64/bionic/libdl.so,rwxp;/system/lib64/libz.so",
    "121": "",
    "122": "",
    "135": "tmpfs:/dev:tmpfs:rw&seclabel&nosuid&relatime&mode=755,tmpfs:/mnt:tmpfs:rw&seclabel&nosuid&nodev&noexec&relatime&mode=755&gid=1000,/dev/fuse:/mnt/installer/0/emulated:fuse:rw&lazytime&nosuid&nodev&noexec&noatime&user_id=0&group_id=0&allow_other,/dev/block/platform/a003c00.virtio_mmio/by-name/metadata:/metadata:ext4:rw&seclabel&nosuid&nodev&noatime,tmpfs:/apex:tmpfs:rw&seclabel&nosuid&nodev&noexec&relatime&mode=755,tmpfs:/apex/apex-info-list.xml:tmpfs:rw&seclabel&nosuid&nodev&noexec&relatime&mode=755,/dev/fuse:/storage/emulated:fuse:rw&lazytime&nosuid&nodev&noexec&noatime&user_id=0&group_id=0&allow_other,tmpfs:/data/data:tmpfs:rw&seclabel&nosuid&nodev&noexec&relatime&mode=751,tmpfs:/data/user:tmpfs:rw&seclabel&nosuid&nodev&noexec&relatime&mode=751,tmpfs:/data/user_de:tmpfs:rw&seclabel&nosuid&nodev&noexec&relatime&mode=751,tmpfs:/data/misc/profiles/cur:tmpfs:rw&seclabel&nosuid&nodev&noexec&relatime&mode=751,tmpfs:/data/misc/profiles/ref:tmpfs:rw&seclabel&nosuid&nodev&noexec&relatime&mode=751,/dev/block/loop::13,/dev/block/dm::71",
    "124": "",
    "131": "0,0,Built-in Screen,1080x2220,131,1,0,",
    "137": "-8233870966039513836:enforcing,7247450386580705748:0,3920320800439300925:T-Mobile",
    "133": "android.content.pm.IPackageManager$Stub$Proxy,,",
    "140": "0,0,,0,,",
    "138": "5,1",
    "149": "en-US",
    "150": "Asia/Shanghai",
    "151": "310260",
    "9": "/dev/block/dm-0:802660,tmpfs:1006268,tmpfs:1006268,/dev/block/dm-4:98620,/dev/block/dm-3:2122372,/dev/block/dm-2:175392,tmpfs:1006268,/dev/block/loop4:784,",
    "15": "Ring_Synth_04.ogg",
    "46": "0",
    "51": ""
  },
  "java_sdk_constants": {
    "2013": "/dev/qemu_pipe,/dev/vboxguest,/dev/vboxuser,/system/framework/org.lineageos.platform-res.apk,/system/lib/ccc,/dev/socket/baseband_genyd,/sys/class/misc/vboxguest,/system/priv-app/CMSettingsProvider,/system/lib64/ccc,/ueventd.ttVM_x86.rc,/init.ttVM_x86.rc,/system/lib/hw/sensors.vbox86.so,/hmdocker,/system/lib/libnemuVMprop.so,/system/bin/magiskpolicy,/system/bin/qemud,/ueventd.vbox86.rc,/system/bin/androVM_setprop,/dev/socket/genyd,/sys/module/vboxguest,/system/priv-app/LineageSettingsProvider,/system/lib/hw/gps.vbox86.so,/system/etc/init.buildroid.sh,/system/lib/hw/audio.primary.vbox86.so,/sys/qemu_trace,/sys/class/misc/vboxuser,/system/bin/mount.nemusf,/ueventd.andy.rc,/system/priv-app/vmos-pro-intent,/sys/module/vboxvideo,/system/lib/hw/gralloc.vbox86.so,/proc/device-tree/goldfish_pipe@a060000,/system/addon.d/50-lineage.sh,/system/priv-app/romex,/system/lib64/arm64/nb/libc.so,/dev/socket/qemud,/data/.bluestacks.prop,/ueventd.android_x86.rc,/.dockerenv,/system/bin/magisk,/system/lib64/libc.so,/system/lib64/.aa,/fstab.andy,/dev/apksafe_pipe,/fstab.vbox86,/ueventd.nox.rc,/init.nox.rc,/system/app/DotPapers,/system/lib/.aa,/init.goldfish.rc,/x86.prop,/dev/mumuvmmguest,/dev/mumuvmmuser,/proc/device-tree/virtio_mmio@a000000,/system/bin/nemuVM-nemu-service,/sys/module/vboxsf,/dev/goldfish_pipe,/system/lib/hw/camera.vbox86.so,/system/priv-app/MuMuAudio,/sys/devices/virtual/misc/vboxguest,/system/bin/androVM-prop,/sys/devices/virtual/misc/vboxuser,/system/addon.d/99-magisk.sh,/fstab.nox,/system/lib64/libriruloader.so,/fstab.ttVM_x86,/system/etc/mumu-configs,/init.vbox86.rc",
    "2016": "com/wind/xpatch/proxy/XpatchProxyApplication,de/robv/android/xposed/XposedHelpers,com/wind/xposed/entry/XposedModuleEntry,com/bug/fuck/fuck",
    "2017": "ro.boot.flash.locked,ro.boot.vbmeta.device_state,ro.boot.verifiedbootstate,ro.boot.veritymode,ro.boot.warranty_bit,ro.debuggable,ro.secure,ro.secureboot.lockstate,ro.vendor.boot.warranty_bit,ro.vendor.warranty_bit,ro.warranty_bit,vendor.boot.vbmeta.device_state,ro.lineage.device,ro.dotOS.device,ro.dalvik.vm.native.bridge,nemud.player_package,init.svc.nemuinit"
  }
}"""

# ---- hash_python.py ----
"""Pure Python port of TuringFD's feature digest helpers."""


from collections.abc import Mapping

_MULTIPLIER = 0xC6A4A7935BD1E995
_LCG_MULTIPLIER = 214_013
_LCG_INCREMENT = 2_531_011
_MASK_64 = 0xFFFFFFFFFFFFFFFF


def _u64(value: int) -> int:
    return value & _MASK_64


def _mix47(value: int) -> int:
    value = _u64(value)
    return _u64(value ^ (value >> 47))


def turing_hash64(data: bytes, seed: int = 0) -> int:
    """Port ``sub_40f154(buffer, length, seed)`` exactly."""
    raw = bytes(data)
    length = len(raw)
    state = _u64(_u64(length) * _MULTIPLIER) ^ _u64(seed)
    block_count = length // 8

    for index in range(block_count):
        word = int.from_bytes(raw[index * 8 : index * 8 + 8], "little")
        word = _u64(_mix47(_u64(word * _MULTIPLIER)) * _MULTIPLIER)
        state = _u64((word ^ state) * _MULTIPLIER)

    tail = raw[block_count * 8 :]
    if len(tail) >= 7:
        state ^= tail[6] << 48
    if len(tail) >= 6:
        state ^= tail[5] << 40
    if len(tail) >= 5:
        state ^= tail[4] << 32
    if len(tail) >= 4:
        state ^= tail[3] << 24
    if len(tail) >= 3:
        state ^= tail[2] << 16
    if len(tail) >= 2:
        # This unusual AND is present in the ARM64 implementation.
        state ^= 0x118727C03FB9FFC6 & (tail[1] << 8)
    if tail:
        state ^= tail[0]
        state = _u64(state * _MULTIPLIER)

    state = _mix47(state)
    state = _u64(state * _MULTIPLIER)
    return _mix47(state)


def build_feature_hash_buffer(features: Mapping[int, str], timestamp_ms: int) -> bytes:
    """Port the hash-buffer construction performed by ``sub_439e3c``.

    The native map is traversed in ascending integer-key order. Its UTF-8
    values are concatenated without separators. For every four map entries
    (including a final partial group), the function advances a 15-bit LCG
    seeded by the request timestamp and appends the lowercase hexadecimal
    state without padding.
    """
    if timestamp_ms < 0:
        raise ValueError("timestamp_ms must be non-negative")

    entries = sorted(features.items())
    parts = [str(value) for _key, value in entries]
    state = int(timestamp_ms)
    for _ in range((len(entries) + 3) // 4):
        state = ((state * _LCG_MULTIPLIER + _LCG_INCREMENT) >> 16) & 0x7FFF
        parts.append(f"{state:x}")
    return "".join(parts).encode("utf-8")


def generate_feature_hash_tags(
    features: Mapping[int, str], timestamp_ms: int
) -> tuple[str, str]:
    """Return the direct-map tag-10 and tag-12 values."""
    digest = f"{turing_hash64(build_feature_hash_buffer(features, timestamp_ms)):x}"
    return "01" + digest, digest

# ---- jce.py ----
#!/usr/bin/env python3
"""Small, dependency-free JCE/Tars writer for the recovered Turing envelope.

This module implements the standard primitive/map/struct wire rules recovered
from the original ARM64 implementation. It deliberately does not invent device values:
callers must supply their own feature values.  ``serialize_m90_minimal`` is
the complete top-level shape for the base-token (mode-0) branch and the
nested tag-4 feature object used by mode 1.
"""


import base64
import hashlib
import secrets
import struct
import time
import zlib
from collections.abc import Iterable

DELTA = 0x9E3779B9
STATIC_KEY = b"HtIs!oM*4d4zcDf^"
TURING_RSA_PUBLIC_KEY_B64 = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAtZX4OooO48FiYa3fwzU+"
    "fhPHO3YsMBP1MiV1Kp+osNDDPtf4EPCjuzI75ea+8Dbq8uQeCXWszSpWUWH1c2F"
    "0khl5BOCzDLyd166i4Hnh3jWW3PNu9ETh81aZh9c353U+c2fok/nPcepmGm3jl3"
    "GZrcxpN9/2wYcCl+t0TWRz+PuTNn0X8/Uf4inmxUJDWclZhao8oYKZM09rH6LRY"
    "ZiszAV9HpO3b9OFd0+/BARTqd+qUbfObMh3jGvIQUjsbpIT+ttPlqS4RyiVPUVG1"
    "gyYuS+/XNXbB59MkMlorIzj7esvC96F5OY5nKl7hvruFfHvEpbo4OL4jmveAMUgu"
    "4cSJQIDAQAB"
)

# JCE/Tars type identifiers.
BYTE, SHORT, INT, LONG = 0, 1, 2, 3
FLOAT, DOUBLE, STRING1, STRING4 = 4, 5, 6, 7
MAP, LIST, STRUCT_BEGIN, STRUCT_END = 8, 9, 10, 11
ZERO_TAG, SIMPLE_LIST = 12, 13


def _u32(value: int) -> int:
    return value & 0xFFFFFFFF


def _u64(value: int) -> int:
    return value & 0xFFFFFFFFFFFFFFFF


def _mix47(value: int) -> int:
    value = _u64(value)
    return _u64(value ^ (value >> 47))


def turing_hash64(data: bytes, seed: int = 0) -> int:
    """Port native ``sub_40f154(buffer, length, seed)`` exactly.

    The function is a small 64-bit multiplicative hash with an 8-byte block
    loop and byte-tail constants; it has no Android/libc dependencies.
    """
    raw = bytes(data)
    length = len(raw)
    multiplier = 0xC6A4A7935BD1E995
    state = _u64(_u64(length) * multiplier) ^ _u64(seed)
    blocks = length // 8
    for index in range(blocks):
        word = int.from_bytes(raw[index * 8 : index * 8 + 8], "little")
        word_mix = _mix47(_u64(word * multiplier))
        word_mix = _u64(word_mix * multiplier)
        # The K constant is XORed into both operands immediately before the
        # final eor, so it cancels; retaining it in the expression would
        # produce a different hash.
        state = _u64((word_mix ^ state) * multiplier)
    tail = raw[blocks * 8 :]
    if len(tail) >= 7:
        state ^= tail[6] << 48
    if len(tail) >= 6:
        b = tail[5] << 40
        # K XORs are paired around the byte, leaving a plain XOR.
        state ^= b
    if len(tail) >= 5:
        b = tail[4] << 32
        state ^= b
    if len(tail) >= 4:
        state ^= tail[3] << 24
    if len(tail) >= 3:
        b = tail[2] << 16
        state ^= b
    if len(tail) >= 2:
        k = 0x118727C03FB9FFC6
        b = tail[1] << 8
        state ^= k & b
    if len(tail) >= 1:
        state ^= tail[0]
    # Non-empty tail paths enter the switch epilogue through one extra
    # multiply; the zero-tail branch jumps directly to the avalanche.
    if tail:
        state = _u64(state * multiplier)
    state = _mix47(state)
    state = _u64(state * multiplier)
    return _mix47(state)


def _mx(sum_value: int, y: int, z: int, p: int, e: int, key: list[int]) -> int:
    return _u32(
        (((z >> 5) ^ _u32(y << 2)) + ((y >> 3) ^ _u32(z << 4)))
        ^ ((y ^ sum_value) + (z ^ key[(p & 3) ^ e]))
    )


def _xxtea_key(key_bytes: bytes | None) -> list[int]:
    material = b"DFG#$%^#%$RGHR(&*M<><" if key_bytes is None else key_bytes
    if len(material) > 16:
        material = hashlib.md5(material).digest()
    material = material.ljust(16, b"\0")
    return list(struct.unpack("<4I", material[:16]))


def xxtea_encrypt(plain: bytes, key_bytes: bytes | None) -> bytes:
    """Match ``Quarenden.a`` encryption, including its embedded length word."""
    if not plain:
        return plain
    count = len(plain) // 4 + (1 if len(plain) % 4 == 0 else 2)
    values = [0] * count
    for index, byte in enumerate(plain):
        values[index // 4] |= byte << ((index % 4) * 8)
    values[-1] = len(plain)
    n = count - 1
    key = _xxtea_key(key_bytes)
    rounds = 6 + 52 // (n + 1)
    total = 0
    z = values[n]
    while rounds:
        total = _u32(total + DELTA)
        e = (total >> 2) & 3
        for p in range(n):
            y = values[p + 1]
            values[p] = _u32(values[p] + _mx(total, y, z, p, e, key))
            z = values[p]
        y = values[0]
        values[n] = _u32(values[n] + _mx(total, y, z, n, e, key))
        z = values[n]
        rounds -= 1
    return struct.pack(f"<{count}I", *values)


class JceWriter:
    def __init__(self) -> None:
        self.buf = bytearray()

    def head(self, tag: int, type_id: int) -> None:
        if not 0 <= tag <= 255:
            raise ValueError("JCE tag must fit in one byte")
        if tag < 15:
            self.buf.append((tag << 4) | type_id)
        else:
            self.buf.append(0xF0 | type_id)
            self.buf.append(tag)

    def zero(self, tag: int) -> None:
        self.head(tag, ZERO_TAG)

    def byte(self, tag: int, value: int) -> None:
        value = int(value)
        if value == 0:
            self.zero(tag)
        else:
            self.head(tag, BYTE)
            self.buf += struct.pack("<b", value)

    def short(self, tag: int, value: int) -> None:
        value = int(value)
        if -128 <= value <= 127:
            self.byte(tag, value)
        else:
            self.head(tag, SHORT)
            self.buf += struct.pack(">h", value)

    def int32(self, tag: int, value: int) -> None:
        value = int(value)
        if -32768 <= value <= 32767:
            self.short(tag, value)
        else:
            self.head(tag, INT)
            self.buf += struct.pack(">i", value)

    def int64(self, tag: int, value: int) -> None:
        value = int(value)
        if -2147483648 <= value <= 2147483647:
            self.int32(tag, value)
        else:
            self.head(tag, LONG)
            self.buf += struct.pack(">q", value)

    def string(self, tag: int, value: str | bytes) -> None:
        raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        if len(raw) <= 255:
            self.head(tag, STRING1)
            self.buf.append(len(raw))
        else:
            self.head(tag, STRING4)
            self.buf += struct.pack(">i", len(raw))
        self.buf += raw

    def map_int_string(self, tag: int, entries: Iterable[tuple[int, str]]) -> None:
        items = list(entries)
        self.head(tag, MAP)
        self.int32(0, len(items))
        for key, value in items:
            self.int32(0, key)
            self.string(1, value)

    def raw_struct(self, tag: int, fields: bytes) -> None:
        self.head(tag, STRUCT_BEGIN)
        self.buf += fields
        self.head(0, STRUCT_END)

    def finish(self) -> bytes:
        return bytes(self.buf)


def serialize_m90_minimal(
    *,
    channel: str,
    brand: str,
    model: str,
    timestamp_ms: int,
    cache_flag: int,
    base_token: str,
    context_value: str | None = None,
) -> bytes:
    """Serialize the recovered top-level mode-0 ``m90`` object.

    Native ``sub_411018`` writes map entries in the order model (2), brand
    (1), channel (3), and optional context (4).  Keeping that order matters
    for byte-for-byte regression tests even though a JCE decoder treats maps
    semantically.
    """
    writer = JceWriter()
    entries = [(2, model), (1, brand), (3, channel)]
    if context_value is not None:
        entries.append((4, context_value))
    writer.map_int_string(0, entries)
    writer.int64(1, timestamp_ms)
    # ``sub_4470fc`` only emits tag 2 when the parsed 3002 value is nonzero;
    # a Java value of "0" is therefore represented by omission, not ZERO_TAG.
    if int(cache_flag):
        writer.int32(2, cache_flag)
    writer.string(3, base_token)
    return writer.finish()


def serialize_feature_struct(
    values: dict[int, str],
    *,
    helper_tag10: str | None = None,
    helper_tag12: str | None = None,
    hash_buffer: bytes | None = None,
) -> bytes:
    """Write the recovered direct tag-4 string fields.

    The native `0x410734/0x4107cc` branches are represented by the integer
    keys in `native/turing-feature-writes.tsv`.  The two flattened native
    helper outputs (tags 10 and 12) are optional until their input hashes are
    recovered; omitting an unavailable optional field matches the SDK's
    empty-string branches.
    """
    if hash_buffer is not None:
        digest = f"{turing_hash64(hash_buffer, 0):x}"
        helper_tag10 = "01" + digest
        helper_tag12 = digest
    writer = JceWriter()
    for tag, value in sorted(values.items()):
        if value is not None and value != "":
            writer.string(int(tag), value)
    if helper_tag10:
        writer.string(10, helper_tag10)
    if helper_tag12:
        writer.string(12, helper_tag12)
    return writer.finish()


def serialize_turing_nested(
    *,
    timestamp_ms: int,
    metadata: dict[int, str | int],
    feature_values: Iterable[tuple[int, str]] = (),
    network: tuple[str, str] = ("", ""),
    outer_string4: str = "",
    direct_values: Iterable[tuple[int, str]] = (),
    extra_map_values: Iterable[tuple[int, str]] = (),
    outer_string7: str = "",
    type8_values: dict[int, str | int] | None = None,
) -> bytes:
    """Serialize the complete recovered tag-0..8 nested struct shape.

    The field layout comes directly from `sub_4177d0`, `sub_418358`,
    `sub_4186ac`, and `sub_417d5c`.  `feature_values` is the map populated by
    `0x410734/0x4107cc`; `direct_values` is the separate map populated by
    `0x410e2c/0x439e3c`.  Values are caller-supplied because several of them
    are device-specific.
    """
    out = JceWriter()
    out.int64(0, timestamp_ms)

    # a0[4] / sub_418358: V90 metadata object.
    meta = JceWriter()
    meta.int32(0, int(metadata.get(0, 90)))
    meta.string(1, str(metadata.get(1, "")))
    meta.string(2, str(metadata.get(2, "")))
    meta.string(3, str(metadata.get(3, "")))
    meta.int32(4, int(metadata.get(4, 2)))
    out.raw_struct(1, meta.finish())

    # a0[5] / sub_417d5c: feature map.
    out.map_int_string(2, feature_values)

    # a0[6] / sub_4186ac: two-string network object.
    net = JceWriter()
    net.string(0, network[0])
    net.string(1, network[1])
    out.raw_struct(3, net.finish())

    if outer_string4:
        out.string(4, outer_string4)

    # a0[8]: native direct map.  This is a map<int32,string>, not a nested
    # struct; its tags include 3,4,7,8,9,10,12 in the current build.
    direct = list(direct_values)
    if direct:
        out.map_int_string(5, direct)

    extra = list(extra_map_values)
    if extra:
        out.map_int_string(6, extra)
    if outer_string7:
        out.string(7, outer_string7)

    # a0[11] / sub_417d5c: parsed 401..406 metadata and optional map.
    v8 = type8_values or {}
    tail = JceWriter()
    tail.string(0, str(v8.get(0, "")))
    if v8.get(1):
        tail.string(1, str(v8[1]))
    tail.string(2, str(v8.get(2, "")))
    tail.int32(3, int(v8.get(3, 0)))
    tail.string(4, str(v8.get(4, "")))
    tail.string(5, str(v8.get(5, "")))
    if v8.get(6):
        tail.string(6, str(v8[6]))
    if v8.get(7):
        tail.string(7, str(v8[7]))
    tail_map = v8.get(8, ())
    if tail_map:
        tail.map_int_string(8, tail_map)
    out.raw_struct(8, tail.finish())
    return out.finish()


def serialize_m90_full(
    *,
    channel: str,
    brand: str,
    model: str,
    timestamp_ms: int,
    cache_flag: int,
    nested_fields: bytes,
    context_value: str | None = None,
    status_code: int | None = None,
    status_text: str | None = None,
    extra_field: str | None = None,
) -> bytes:
    """Serialize the recovered mode-1 top-level object around tag 4."""
    writer = JceWriter()
    entries = [(2, model), (1, brand), (3, channel)]
    if context_value:
        entries.append((4, context_value))
    writer.map_int_string(0, entries)
    writer.int64(1, timestamp_ms)
    if cache_flag:
        writer.int32(2, cache_flag)
    writer.raw_struct(4, nested_fields)
    if status_code:
        writer.int32(5, status_code)
    if status_text:
        writer.string(6, status_text)
    if extra_field:
        writer.string(7, extra_field)
    return writer.finish()


def encode_mode0(
    native_blob: bytes,
    *,
    timestamp_ms: int | None = None,
) -> str:
    """Apply the recovered Java-level mode-0 ``v3:`` envelope."""
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    timestamp = struct.pack(">Q", int(timestamp_ms))
    compressed = zlib.compress(b"\x02" + bytes(native_blob))
    cipher = xxtea_encrypt(compressed, STATIC_KEY + timestamp)
    packed = b"\0" + timestamp + cipher
    # Android Base64 flag 2 means NO_WRAP; padding is retained.
    return "v3:" + base64.b64encode(packed).decode("ascii")


def _rsa_public_numbers() -> tuple[int, int]:
    """Read the 2048-bit modulus/exponent from Cherry.java's SPKI blob."""
    der = base64.b64decode(TURING_RSA_PUBLIC_KEY_B64)
    marker = b"\x02\x82\x01\x01"
    pos = der.find(marker)
    if pos < 0:
        raise ValueError("RSA modulus not found")
    # DER INTEGER is 257 bytes because a leading 00 keeps the 2048-bit
    # unsigned modulus positive.  The old parser included that 00 and dropped
    # the final modulus byte, accidentally creating a 2040-bit public key.
    encoded_modulus = der[pos + 4 : pos + 4 + 257]
    if len(encoded_modulus) != 257 or encoded_modulus[0] != 0:
        raise ValueError("unexpected RSA modulus encoding")
    modulus = int.from_bytes(encoded_modulus[1:], "big")
    exp_marker = b"\x02\x03\x01\x00\x01"
    exp_pos = der.find(exp_marker, pos + 4 + 257)
    if exp_pos < 0:
        raise ValueError("RSA exponent not found")
    return modulus, 65537


def rsa_pkcs1_v15_encrypt(message: bytes) -> bytes:
    modulus, exponent = _rsa_public_numbers()
    size = (modulus.bit_length() + 7) // 8
    if len(message) > size - 11:
        raise ValueError("RSA message too long")
    padding = bytearray()
    while len(padding) < size - len(message) - 3:
        padding.extend(byte for byte in secrets.token_bytes(32) if byte)
    encoded = b"\0\x02" + bytes(padding[: size - len(message) - 3]) + b"\0" + message
    return pow(int.from_bytes(encoded, "big"), exponent, modulus).to_bytes(size, "big")


def encode_mode1(native_blob: bytes, *, random_key: bytes | None = None) -> str:
    """Apply the recovered mode-1 RSA/XXTEA envelope.

    ``native_blob`` must already be the exact m90 tag stream.  The RSA public
    key is sufficient for client-side construction; the matching private key
    is intentionally not present here and is not needed by this function.
    """
    if random_key is None:
        alphabet = b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        key = bytes(secrets.choice(alphabet) for _ in range(16))
    else:
        key = bytes(random_key)
    if len(key) != 16:
        raise ValueError("mode-1 key must be exactly 16 bytes")
    # Carambola's exact smali builds a new byte array with marker 0x02 at
    # index zero for both mode 0 and mode 1 before DeflaterOutputStream.
    compressed = zlib.compress(b"\x02" + bytes(native_blob))
    cipher = xxtea_encrypt(compressed, key)
    packed = b"\x01" + rsa_pkcs1_v15_encrypt(key) + cipher
    return "v3:" + base64.b64encode(packed).decode("ascii")

# ---- feature_profile.py ----
"""Stable synthetic Android feature profiles for local Turing protocol tests.

The profile is anchored by a random 256-bit seed persisted in a caller-chosen
state file.  It is stable for one installation, does not expose raw host
identifiers, and avoids profile collisions between independently initialized
machines.  Do not copy the state file to another host: doing so deliberately
clones the simulated device.

This module models the *shape* of the Turing feature input discovered in this
analysis.  It is for offline/native-harness regression, not a claim that a
synthetic profile will pass a production risk decision.
"""


import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 1
CHANNEL = "10000191"
PACKAGE_NAME = "com.sgcc.wsgw.cn"
BUILD_MARKER = "77F2B17A00A92C71"
REFERENCE_ENVIRONMENT = json.loads(_REFERENCE_ENVIRONMENT_JSON)
REFERENCE_NATIVE_FEATURES = {
    int(key): str(value)
    for key, value in REFERENCE_ENVIRONMENT["stable_feature_values"].items()
}
JAVA_SDK_CONSTANTS = {
    int(key): str(value)
    for key, value in REFERENCE_ENVIRONMENT["java_sdk_constants"].items()
}

_DEVICE_CATALOG = (
    {
        "brand": "google",
        "manufacturer": "Google",
        "model": "sdk_gphone64_arm64",
        "device": "emu64a",
        "product": "sdk_gphone64_arm64",
        "board": "goldfish_arm64",
        "hardware": "ranchu",
    },
)

# Exact 52-entry Java Map captured from the official App.  Values may change,
# but presence and key set are part of the App/SDK shape.
OFFICIAL_JAVA_KEYS: tuple[int | str, ...] = (
    1,
    4,
    205,
    210,
    240,
    250,
    251,
    252,
    253,
    254,
    255,
    256,
    257,
    258,
    259,
    264,
    266,
    267,
    268,
    269,
    270,
    271,
    272,
    273,
    279,
    280,
    281,
    282,
    284,
    285,
    286,
    287,
    288,
    303,
    308,
    309,
    402,
    406,
    2001,
    2005,
    2013,
    2016,
    2017,
    2018,
    2019,
    2021,
    2029,
    3001,
    3002,
    3004,
    3005,
    "com.google.android.providers.gsf.permission.READ_GSERVICES",
)

JAVA_PERFORMANCE_ORDER: tuple[int | str, ...] = (
    133,
    138,
    140,
    -17,
    17,
    145,
    146,
    149,
    22,
    150,
    151,
    24,
    100,
    101,
    102,
    103,
    104,
    105,
    106,
    107,
    44,
    113,
    114,
    115,
    116,
    117,
    118,
    120,
    122,
    "s9",
)
JAVA_PERFORMANCE_REFERENCE = {
    133: 0,
    138: 1,
    140: 0,
    -17: 0,
    17: 1,
    145: 0,
    146: 0,
    149: 0,
    22: 1,
    150: 0,
    151: 0,
    24: 0,
    100: 6,
    101: 114,
    102: 0,
    103: 0,
    104: 0,
    105: 0,
    106: 3,
    107: 0,
    44: 0,
    113: 2,
    114: 1,
    115: 1,
    116: 0,
    117: 0,
    118: 0,
    120: 8,
    122: 0,
    "s9": 1,
}
NATIVE_PERFORMANCE_ORDER: tuple[int, ...] = (
    21,
    7,
    10,
    14,
    33,
    11,
    24,
    12,
    23,
    8,
    13,
    25,
    20,
    35,
    42,
    121,
    135,
    124,
    125,
    129,
    131,
    134,
    132,
    137,
    142,
    141,
    9,
    15,
    16,
)
NATIVE_PERFORMANCE_REFERENCE = {
    21: 1,
    7: 0,
    10: 3,
    14: 0,
    33: 0,
    11: 0,
    24: 0,
    12: 0,
    23: 0,
    8: 0,
    13: 0,
    25: 0,
    20: 0,
    35: 0,
    42: 0,
    121: 1,
    135: 2,
    124: 0,
    125: 0,
    129: 0,
    131: 0,
    134: 0,
    132: 0,
    137: 1,
    142: 0,
    141: 0,
    9: 16,
    15: 0,
    16: 0,
}

# sign, kind, owner (None means App UID), three fixed tuple fields, and the
# final metric width.  This preserves the official seven-entry shape while
# keeping hashes and request metrics profile-specific.
PROCESS_ENTRY_TEMPLATES = (
    (-1, 3, 1000, 17, 0, 438, 3),
    (-1, 1, 0, 20, 0, 493, 3),
    (1, 1, 1000, 1864, 1036632, 420, 2),
    (-1, 1, 0, 20, 0, 493, 2),
    (1, 3, 1000, 17, 0, 438, 3),
    (1, 1, None, 19, 0, 292, 6),
    (-1, 1, None, 19, 0, 292, 6),
)


@dataclass(frozen=True)
class AndroidIdentity:
    profile_id: str
    android_id: str
    oaid: str
    wifi_mac: str
    serial: str
    build_id: str
    incremental: str
    release: str
    sdk_int: int
    brand: str
    manufacturer: str
    model: str
    device: str
    product: str
    board: str
    hardware: str
    package_name: str = PACKAGE_NAME
    channel: str = CHANNEL


class StableProfile:
    def __init__(
        self,
        seed: bytes,
        *,
        boot_epoch_ms: int | None = None,
        install_epoch_ms: int | None = None,
    ):
        if len(seed) != 32:
            raise ValueError("Turing profile seed must be 32 bytes")
        self._seed = seed
        self._boot_epoch_ms = boot_epoch_ms
        self._install_epoch_ms = install_epoch_ms

    def bytes(self, label: str, size: int = 32) -> bytes:
        if size < 1:
            raise ValueError("size must be positive")
        output = bytearray()
        counter = 0
        while len(output) < size:
            output.extend(
                hmac.new(
                    self._seed,
                    b"wsgw-turing-profile-v1\0"
                    + label.encode("utf-8")
                    + counter.to_bytes(4, "big"),
                    hashlib.sha256,
                ).digest()
            )
            counter += 1
        return bytes(output[:size])

    def hex(self, label: str, size: int = 16) -> str:
        return self.bytes(label, size).hex()

    def integer(self, label: str, lower: int, upper: int) -> int:
        if lower > upper:
            raise ValueError("invalid integer range")
        value = int.from_bytes(self.bytes(label, 8), "big")
        return lower + value % (upper - lower + 1)

    def request_integer(
        self, label: str, timestamp_ms: int, lower: int, upper: int
    ) -> int:
        """Derive a plausible request-scoped number without host identifiers."""
        return self.integer(f"request:{timestamp_ms}:{label}", lower, upper)

    def uuid(self, label: str) -> str:
        value = bytearray(self.bytes(label, 16))
        value[6] = (value[6] & 0x0F) | 0x40
        value[8] = (value[8] & 0x3F) | 0x80
        return str(uuid.UUID(bytes=bytes(value)))

    def identity(self) -> AndroidIdentity:
        model = _DEVICE_CATALOG[
            self.integer("device-catalog", 0, len(_DEVICE_CATALOG) - 1)
        ]
        mac = bytearray(self.bytes("wifi-mac", 6))
        mac[0] = (mac[0] & 0xFE) | 0x02  # locally administered, unicast
        return AndroidIdentity(
            profile_id=self.uuid("profile-id"),
            android_id=self.hex("android-id", 8),
            oaid=self.uuid("oaid"),
            wifi_mac=":".join(f"{part:02x}" for part in mac),
            serial="TUR" + self.hex("serial", 7).upper(),
            build_id="TE1A.240213.009",
            incremental="12342917",
            release="13",
            sdk_int=33,
            **model,
        )

    def _timing_value(self, label: str, timestamp_ms: int, reference: int) -> int:
        if reference <= 0:
            return 0
        radius = max(1, reference // 4)
        digits = len(str(reference))
        same_width_lower = 10 ** (digits - 1) if digits > 1 else 0
        same_width_upper = 10**digits - 1
        return self.request_integer(
            f"timing:{label}",
            timestamp_ms,
            max(same_width_lower, reference - radius),
            min(same_width_upper, reference + radius),
        )

    def java_performance_context(self, timestamp_ms: int) -> str:
        values = []
        for feature in JAVA_PERFORMANCE_ORDER:
            elapsed = self._timing_value(
                str(feature), timestamp_ms, JAVA_PERFORMANCE_REFERENCE[feature]
            )
            values.append(f"{feature}_{elapsed}")
        return ",".join(values)

    def direct_performance_context(self, timestamp_ms: int) -> str:
        values = []
        for feature in NATIVE_PERFORMANCE_ORDER:
            elapsed = self._timing_value(
                f"native-{feature}",
                timestamp_ms,
                NATIVE_PERFORMANCE_REFERENCE[feature],
            )
            values.append(f"{feature}_{elapsed}")
        suffix = ",".join(values)
        return f"{self.java_performance_context(timestamp_ms)},{suffix}"

    def native_feature_map(self, now_ms: int | None = None) -> dict[int, str]:
        """Build the complete native 62-tag reference feature map."""
        identity = self.identity()
        current = int(time.time() * 1000) if now_ms is None else int(now_ms)
        boot_epoch_ms = self._boot_epoch_ms
        if boot_epoch_ms is None:
            boot_epoch_ms = current - self.integer(
                "boot-age-ms", 3_600_000, 86_400_000 * 14
            )
        uid = self.integer("uid", 10000, 19999)
        install_epoch_ms = self._install_epoch_ms
        if install_epoch_ms is None:
            install_epoch_ms = boot_epoch_ms - self.integer(
                "install-age-ms", 86_400_000, 86_400_000 * 180
            )

        install_a = base64.urlsafe_b64encode(self.bytes("install-a", 16)).decode()
        install_b = (
            base64.urlsafe_b64encode(self.bytes("install-b", 16)).decode().rstrip("=")
            + "="
        )
        install_context = (
            f"1:1:{uid}:{install_epoch_ms}:"
            f"{identity.package_name}::"
            f"{self.integer('install-slot', 100_000_000, 999_999_999)}:"
            f"app/~~{install_a}/-{install_b},"
        )

        process_entries = []
        for index, template in enumerate(PROCESS_ENTRY_TEMPLATES):
            sign, kind, owner_value, first, second, third, metric_digits = template
            magnitude = self.integer(
                f"process-hash-{index}",
                1_000_000_000_000_000_000,
                9_000_000_000_000_000_000,
            )
            process_hash = magnitude * sign
            owner = uid if owner_value is None else owner_value
            metric = self.request_integer(
                f"process-metric-{index}",
                current,
                10 ** (metric_digits - 1),
                10**metric_digits - 1,
            )
            process_entries.append(
                f"{process_hash}:{kind}:{owner}_{first}_{second}_{third}_{metric}"
            )

        interface_id = self.hex("interface-id", 3)
        network_interfaces = (
            f"8,eth0;69699;3;fe80::5054:ff:fe{interface_id[:2]}:{interface_id[2:]};"
            f"fec0::5054:ff:fe{interface_id[:2]}:{interface_id[2:]};10.0.2.15,"
            "dummy0;65731;1;fe80::8c95:a4ff:fe59:322"
        )
        collection_latency = self.request_integer(
            "collection-latency", current, 8000, 16000
        )

        values = dict(REFERENCE_NATIVE_FEATURES)
        values.update(
            {
                16: identity.android_id,
                17: install_context,
                26: self.hex("native-device-id", 16),
                34: f"C:1,T:{current},LT:{collection_latency}",
                125: ",".join(process_entries),
                126: str(boot_epoch_ms),
                129: network_interfaces,
                141: str(self.integer("native-property-141", 1_000_000, 9_999_999)),
                146: "1,272494655,eth0,10.0.2.15,10.0.2.3",
                148: identity.oaid,
            }
        )
        if len(values) != 62:
            raise RuntimeError(
                f"native feature map must contain 62 tags, got {len(values)}"
            )
        return values

    def feature_map(
        self,
        now_ms: int | None = None,
        *,
        fallback_status_code: int = -22056,
        token_nonce: str | None = None,
    ) -> dict[int | str, str]:
        """Build the official 52-key Java Map from the same native state."""
        identity = self.identity()
        current = int(time.time() * 1000) if now_ms is None else int(now_ms)
        native = self.native_feature_map(current)
        nonce = token_nonce or self.hex(f"diagnostic-nonce:{current}", 16)
        stage_values = [
            self.request_integer(f"stage-{index}", current, 1000, 2200)
            for index in range(3)
        ]
        feature_map: dict[int | str, str] = {
            1: identity.channel,
            4: self.java_performance_context(current),
            205: native[22],
            210: native[34],
            240: native[44],
            250: native[100],
            251: native[101],
            252: native[102],
            253: native[103],
            254: native[104],
            255: native[105],
            256: native[106],
            257: native[107],
            258: native[113],
            259: native[114],
            264: native[24],
            266: native[45],
            267: native[17],
            268: native[115],
            269: native[116],
            270: native[117],
            271: native[46],
            272: native[118],
            273: "",
            279: native[120],
            280: native[122],
            281: native[126],
            282: native[138],
            284: native[146],
            285: native[149],
            286: native[150],
            287: native[151],
            288: native[152],
            303: f"5_1_{fallback_status_code}_{'_'.join(map(str, stage_values))}",
            308: "1",
            309: "",
            402: "",
            406: "",
            2001: native[200],
            2005: "0",
            2013: JAVA_SDK_CONSTANTS[2013],
            2016: JAVA_SDK_CONSTANTS[2016],
            2017: JAVA_SDK_CONSTANTS[2017],
            2018: str(self.integer("uid", 10000, 19999)),
            2019: native[133],
            2021: native[140],
            2029: "0",
            3001: str(fallback_status_code),
            3002: "1",
            3004: nonce,
            3005: "1",
            "com.google.android.providers.gsf.permission.READ_GSERVICES": "1",
        }
        if tuple(feature_map) != OFFICIAL_JAVA_KEYS:
            raise RuntimeError("Java feature Map key order/coverage drifted")
        return feature_map


def load_or_create_profile(state_file: Path) -> StableProfile:
    """Load a persistent profile seed or atomically create one with mode 0600."""
    if state_file.exists():
        payload = json.loads(state_file.read_text(encoding="utf-8"))
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported profile state schema")
        return StableProfile(
            base64.b64decode(payload["seed_b64"], validate=True),
            boot_epoch_ms=payload.get("boot_epoch_ms"),
            install_epoch_ms=payload.get("install_epoch_ms"),
        )
    state_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    seed = secrets.token_bytes(32)
    now_ms = int(time.time() * 1000)
    boot_epoch_ms = now_ms - secrets.randbelow(86_400_000 * 14 - 3_600_000) - 3_600_000
    install_epoch_ms = (
        boot_epoch_ms - secrets.randbelow(86_400_000 * 180 - 86_400_000) - 86_400_000
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "seed_b64": base64.b64encode(seed).decode("ascii"),
        "boot_epoch_ms": boot_epoch_ms,
        "install_epoch_ms": install_epoch_ms,
        "created_at": datetime.now(UTC).isoformat(),
    }
    temporary = state_file.with_name(state_file.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(state_file)
    state_file.chmod(0o600)
    return StableProfile(
        seed,
        boot_epoch_ms=boot_epoch_ms,
        install_epoch_ms=install_epoch_ms,
    )


def profile_document(profile: StableProfile, now_ms: int | None = None) -> dict:
    identity = profile.identity()
    return {
        "schema_version": SCHEMA_VERSION,
        "identity": asdict(identity),
        "feature_map": {
            str(key): value for key, value in profile.feature_map(now_ms).items()
        },
    }

# ---- device_token.py ----
"""Build a pure-Python TuringFD V90 ``deviceTokenTX`` candidate."""


import json
import secrets
import string
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


CHANNEL = "10000191"
BUILD_MARKER = "77F2B17A00A92C71"
FALLBACK_STATUS_CODE = -22056
TOKEN_SCHEMA_VERSION = 6
# Java Cantaloupe feature key -> nested tag written by 0x410734/0x4107cc.
NESTED_TAG_BY_FEATURE_KEY = {
    205: 22,
    207: 28,
    250: 100,
    251: 101,
    252: 102,
    253: 103,
    254: 104,
    255: 105,
    256: 106,
    268: 115,
    258: 113,
    259: 114,
    240: 44,
    210: 34,
    267: 17,
    257: 107,
    274: 47,
    276: 49,
    275: 48,
    2001: 200,
    269: 116,
    270: 117,
    273: 119,
    278: 50,
    266: 45,
    272: 118,
    279: 120,
    280: 122,
    281: 126,
    282: 138,
    2019: 133,
    2021: 140,
    283: 145,
    284: 146,
    285: 149,
    286: 150,
    287: 151,
    288: 152,
    2029: 147,
    2024: 147,
    271: 46,
    501: 136,
    2020: 143,
    2023: 144,
}


@dataclass(frozen=True)
class GeneratedDeviceToken:
    token: str
    token_time: str
    timestamp_ms: int
    profile_id: str
    feature_count: int
    nested_feature_count: int
    fallback_status_code: int

    def cache_document(self) -> dict[str, str | int]:
        return {
            "schema_version": TOKEN_SCHEMA_VERSION,
            "token": self.token,
            "token_time": self.token_time,
            "timestamp_ms": self.timestamp_ms,
            "profile_id": self.profile_id,
            "feature_count": self.feature_count,
            "nested_feature_count": self.nested_feature_count,
            "fallback_status_code": self.fallback_status_code,
        }


def _tail_map(value: str) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    for entry in value.split(","):
        if "_" not in entry:
            continue
        key, item = entry.split("_", 1)
        try:
            result.append((int(key), item))
        except ValueError:
            continue
    return result


def _mapped_nested_features(features: Mapping[int, str]) -> list[tuple[int, str]]:
    mapped: dict[int, str] = {}
    for source_key, nested_tag in NESTED_TAG_BY_FEATURE_KEY.items():
        value = features.get(source_key)
        if value is not None:
            mapped[nested_tag] = str(value)
    return sorted(mapped.items())


def _direct_values(
    features: Mapping[int | str, str],
    tag10: str,
    tag12: str,
    process_context: str,
    performance_context: str,
) -> list[tuple[int, str]]:
    return [
        (3, str(features[303])),
        (8, str(features[308])),
        (9, str(features[309])),
        (7, process_context),
        (10, tag10),
        (12, tag12),
        (4, performance_context),
    ]


def _fallback_nonce() -> str:
    alphabet = string.digits + string.ascii_lowercase + string.ascii_uppercase
    return "".join(secrets.choice(alphabet) for _ in range(32))


def generate_device_token(
    profile: StableProfile,
    *,
    timestamp_ms: int | None = None,
    fallback_status_code: int = FALLBACK_STATUS_CODE,
) -> GeneratedDeviceToken:
    """Generate one fresh mode-1 candidate without Android or Frida."""
    timestamp_ms = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    identity = profile.identity()
    nonce = _fallback_nonce()
    features = profile.feature_map(
        timestamp_ms,
        fallback_status_code=fallback_status_code,
        token_nonce=nonce,
    )

    nested_features = sorted(profile.native_feature_map(timestamp_ms).items())
    tag10, tag12 = generate_feature_hash_tags(dict(nested_features), timestamp_ms)
    uid = features[2018]
    process_context = f"{identity.package_name}:tools,{uid},untrusted_app,{uid},,init"
    nested = serialize_turing_nested(
        timestamp_ms=timestamp_ms,
        metadata={0: 90, 1: "90", 2: BUILD_MARKER, 3: CHANNEL, 4: 2},
        feature_values=nested_features,
        network=("3.2.3,113", identity.package_name),
        direct_values=_direct_values(
            features,
            tag10,
            tag12,
            process_context,
            profile.direct_performance_context(timestamp_ms),
        ),
        extra_map_values=((2, ""),),
        type8_values={
            1: features.get(405, ""),
            2: features.get(402, ""),
            3: features.get(403, "0"),
            4: features.get(404, ""),
            5: features.get(401, ""),
            8: _tail_map(features.get(406, "")),
        },
    )
    m90 = serialize_m90_full(
        channel=identity.channel,
        brand=identity.brand,
        model=identity.model,
        timestamp_ms=timestamp_ms,
        cache_flag=1,
        nested_fields=nested,
        # sub_11018 obtains top-level map key 4 via Context.getPackageName().
        context_value=identity.package_name,
        status_code=fallback_status_code,
        status_text=str(fallback_status_code),
        extra_field=nonce,
    )
    token = encode_mode1(m90)
    return GeneratedDeviceToken(
        token=token,
        token_time=str(timestamp_ms)[:10],
        timestamp_ms=timestamp_ms,
        profile_id=identity.profile_id,
        feature_count=len(nested_features),
        nested_feature_count=len(nested_features),
        fallback_status_code=fallback_status_code,
    )


def load_or_generate_cached_device_token(
    profile: StableProfile,
    *,
    cache_file: Path,
    max_age_ms: int = 14_400_000,
    fallback_status_code: int = FALLBACK_STATUS_CODE,
) -> GeneratedDeviceToken:
    """Mirror the App's four-hour Hawk cache with a private JSON state file."""
    now_ms = int(time.time() * 1000)
    identity = profile.identity()
    if cache_file.is_file():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            timestamp_ms = int(data["timestamp_ms"])
            if (
                data.get("schema_version") == TOKEN_SCHEMA_VERSION
                and data.get("profile_id") == identity.profile_id
                and int(data.get("fallback_status_code", 0)) == fallback_status_code
                and now_ms <= timestamp_ms + max_age_ms
                and str(data.get("token", "")).startswith("v3:")
            ):
                return GeneratedDeviceToken(
                    token=str(data["token"]),
                    token_time=str(data["token_time"]),
                    timestamp_ms=timestamp_ms,
                    profile_id=identity.profile_id,
                    feature_count=int(data.get("feature_count", 0)),
                    nested_feature_count=int(data.get("nested_feature_count", 0)),
                    fallback_status_code=fallback_status_code,
                )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            pass
    generated = generate_device_token(
        profile,
        timestamp_ms=now_ms,
        fallback_status_code=fallback_status_code,
    )
    cache_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = cache_file.with_name(cache_file.name + ".tmp")
    temporary.write_text(
        json.dumps(generated.cache_document(), separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(cache_file)
    cache_file.chmod(0o600)
    return generated


if __name__ == "__main__":
    sys.exit(main())
