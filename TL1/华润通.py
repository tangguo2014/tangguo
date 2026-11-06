#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
====================================================================
VX小程序：华润通 · 每日签到
====================================================================

【环境变量】
  hrt_gpt    必填，格式 备注#token（多账号每行一个）
  hrt_push   可选，默认 1（推送），0 关闭

【功能】
  1. 每日签到（+6 积分）
  2. 查询总积分、连续签到天数
  3. 推送签到结果
  4. 定时 cron 0 6 * * * 每天一次自行修改

【加密说明】
    已完整逆向 mid.huaruntong.cn 
    网关 {apiPath, timestamp, appId, signature}
    signature = HMAC-MD5(SECRET)
    请求体key: RSA-OAEP(AES密钥), data: AES-128-CBC


====================================================================
"""

import base64
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from urllib.parse import quote

import requests
from Crypto.Cipher import AES, PKCS1_OAEP
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import pad

# ==================== 配置 ====================
ENV_TOKEN = 'hrt_gpt'
PUSH_ENABLE = (os.environ.get('hrt_push') or '1') not in ('0', 'false', 'no')

HRT_SECRET = 'c274fc67-19f9-47ba-bb84-585a2e3a1f6a'
HRT_APP_ID = 'API_AUTH_WEB'
HRT_SYS_ID = 'T0000001'
HRT_CHANNEL_ID = 'APP'
HRT_MERCHANT_CODE = '1641000001532'
HRT_STORE_CODE = 'qiandaosonjifen'
HRT_BASE_URL = 'https://mid.huaruntong.cn'

HRT_PUB_KEY = (
    '-----BEGIN PUBLIC KEY-----\n'
    'MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDuAiqDmvn9Rf15o21qkDxN0rUf\n'
    'ZsX6rVBrtfgY6tamN2Yn+1D3eHZJuKNlucyqeBr6nmfN2srYAX+oyCXr5vWwFclj\n'
    'PuWh8aSASqyk7MfbAv5Q4VqYS7lsYUQRdw4plZG0NASDeBvHWi3lsHjGfNb7iUvg\n'
    'rk312EDfBHtRgDvB0QIDAQAB\n'
    '-----END PUBLIC KEY-----'
)

API_WEEK_SIGNIN = '/api/points/queryWeekSignin'
API_SIGNIN = '/api/points/saveQuestionSignin'
API_SUMMARY = '/api/points/querySummary'

CODE_OK = 'S0A00000'

UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) '
      'AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148')

_RSA_KEY = RSA.import_key(HRT_PUB_KEY)


# ==================== 加密 ====================
def sign_plain(payload):
    """HMAC-MD5(SECRET, 排序后 k=v 用 & 拼接)"""
    parts = []
    for k in sorted(payload.keys()):
        v = payload[k]
        if v is None:
            continue
        if isinstance(v, (dict, list)):
            v = json.dumps(v, separators=(',', ':'))
        parts.append(f'{k}={v}')
    s = '&'.join(parts)
    return hmac.new(HRT_SECRET.encode(), s.encode(), hashlib.md5).hexdigest()


def encrypt_body(api_path, params):
    """构造 {key, data}"""
    ts = int(time.time() * 1000)
    payload = {
        'apiPath': quote(api_path, safe=''),
        'timestamp': ts,
        'appId': HRT_APP_ID,
    }
    payload.update(params)
    payload['signature'] = sign_plain(payload)

    aes_key = os.urandom(16)
    pt = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    ct = AES.new(aes_key, AES.MODE_CBC, b'\x00' * 16).encrypt(pad(pt, 16))

    return {
        'key': base64.b64encode(PKCS1_OAEP.new(_RSA_KEY).encrypt(aes_key)).decode(),
        'data': base64.b64encode(ct).decode(),
    }


# ==================== 推送 ====================
def load_notify():
    cur = os.path.abspath(os.path.dirname(__file__))
    for f in (os.path.join(cur, 'notify.py'), '/ql/data/scripts/notify.py'):
        if os.path.exists(f):
            try:
                sys.path.insert(0, os.path.dirname(f))
                from notify import send
                return send
            except ImportError:
                continue
    return None


# ==================== 业务 ====================
class HuaRunTong:
    def __init__(self, token, name=''):
        self.token = token.strip()
        self.name = name
        self.sess = requests.Session()
        self.points = None
        self.days = 0
        self.signed = False
        self.already = False
        self.gain = 0

    def _call(self, path, params=None):
        body = encrypt_body(path, params or {})
        headers = {
            'Content-Type': 'application/json;charset=utf-8',
            'Origin': 'https://cloud.huaruntong.cn',
            'Referer': 'https://cloud.huaruntong.cn/',
            'X-Hrt-Mid-Appid': HRT_APP_ID,
            'X-HRT-MID-NEWRISK': 'newRisk',
            'User-Agent': UA,
        }
        try:
            r = self.sess.post(HRT_BASE_URL + path, headers=headers, json=body, timeout=20)
            j = r.json()
        except Exception as e:
            return {'_err': str(e)[:120]}
        if str(j.get('code')) != CODE_OK:
            j['_err'] = j.get('msg') or f"code={j.get('code')}"
        return j

    def _base_params(self):
        return {
            'channelId': HRT_CHANNEL_ID,
            'sysId': HRT_SYS_ID,
            'transactionUuid': str(uuid.uuid4()),
            'token': self.token,
        }

    # ---- 查询 ----
    def status(self):
        """查询本周签到状态和积分"""
        j = self._call(API_WEEK_SIGNIN, self._base_params())
        if j.get('_err'):
            return None
        d = j.get('data') or {}
        self.days = int(d.get('signinDays') or 0)
        try:
            self.points = int(d.get('totalPoints') or 0)
        except Exception:
            pass
        # 判断今天是否已签
        today = time.strftime('%Y-%m-%d')
        for item in (d.get('list') or []):
            if str(item.get('signinTime') or '').startswith(today):
                self.already = True
                break
        return d

    # ---- 签到 ----
    def signin(self):
        params = self._base_params()
        params.update({
            'merchantCode': HRT_MERCHANT_CODE,
            'storeCode': HRT_STORE_CODE,
            'answerResult': 1,
        })
        j = self._call(API_SIGNIN, params)
        if j.get('_err'):
            msg = str(j['_err'])
            if '已签' in msg or '重复' in msg:
                self.already = True
                return True, '今日已签到'
            return False, msg
        d = j.get('data') or {}
        try:
            self.gain = int(d.get('point') or 0)
        except Exception:
            self.gain = 6
        return True, f'+{self.gain} 积分'

    def run(self):
        print(f'\n{"=" * 52}')
        print(f'账号: {self.name}')
        print('=' * 52)

        st = self.status()
        if st is None:
            print('❌ 查询失败，token 可能失效')
            return False

        print(f'💰 签到前: {self.points} 分（连续 {self.days} 天）')

        if self.already:
            self.signed = True
            print('✅ 今日已签到')
            return True

        ok, msg = self.signin()
        print(f'{"✅" if ok else "❌"} {msg}')

        if ok:
            time.sleep(2)
            self.status()
            print(f'💰 签到后: {self.points} 分（连续 {self.days} 天）')
            self.signed = True
        return ok


# ==================== 主流程 ====================
def parse_accounts(raw):
    out = []
    for line in raw.replace('&', '\n').splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [x.strip() for x in line.split('#') if x.strip()]
        token = ''
        name = ''
        for x in parts:
            if len(x) >= 20 and ' ' not in x and not x.startswith('eyJ'):
                token = x
            elif not name:
                name = x
        if token:
            out.append((name or token[:8], token))
    return out


def main():
    raw = (os.environ.get(ENV_TOKEN) or '').strip()
    if not raw:
        print(f'❌ 未设置环境变量 {ENV_TOKEN}')
        return 1

    accounts = parse_accounts(raw)
    if not accounts:
        print(f'❌ {ENV_TOKEN} 格式错误，应为 备注#token')
        return 1

    print('=' * 52)
    print(f'华润通签到 · {len(accounts)} 个账号')
    print('=' * 52)

    results = []
    for name, token in accounts:
        app = HuaRunTong(token, name)
        try:
            ok = app.run()
        except Exception as e:
            print(f'异常: {e}')
            ok = False
        results.append({
            'name': name, 'ok': ok, 'signed': app.signed,
            'already': app.already, 'points': app.points,
            'days': app.days, 'gain': app.gain,
        })
        time.sleep(3)

    okn = sum(1 for r in results if r['ok'])
    print(f'\n{"=" * 52}')
    print(f'📊 {okn}/{len(results)} 成功')
    print('=' * 52)

    # 推送
    if PUSH_ENABLE and results:
        send = load_notify()
        if send:
            lines = []
            for r in results:
                if not r['ok']:
                    lines.append(f'{r["name"]}: ❌ 失败')
                    continue
                st = '已签到' if r['already'] else f'签到成功 +{r["gain"]}'
                lines.append(f'{r["name"]}')
                lines.append(f'  签到: {st}')
                lines.append(f'  总积分: {r["points"]}')
                lines.append(f'  连续: {r["days"]} 天')
            title = f"华润通签到 · {'✅' if okn == len(results) else '⚠️'} {okn}/{len(results)}"
            try:
                send(title, '\n'.join(lines))
                print('\n推送已发送')
            except Exception as e:
                print(f'推送失败: {e}')

    return 0 if okn == len(results) else 2


if __name__ == '__main__':
    sys.exit(main())
