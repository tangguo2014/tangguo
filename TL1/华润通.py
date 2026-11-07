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

# ==================== 常量区 ====================
ENV_TOKEN = 'hrt_gpt'
NOTIFY_TITLE = '华润通签到🙋‍♀️'

HRT_SECRET = 'c274fc67-19f9-47ba-bb84-585a2e3a1f6a'
HRT_APP_ID = 'API_AUTH_WEB'
HRT_SYS_ID = 'T0000001'
HRT_CHANNEL_ID = 'APP'
HRT_MERCHANT_CODE = '1641000001532'
HRT_STORE_CODE = 'qiandaosonjifen'
HRT_BASE_URL = 'https://mid.huaruntong.cn'
HRT_POINTS_TYPE = '100000'

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
      'AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 '
      'MicroMessenger/8.0.76(0x18004c3a) NetType/4G Language/zh_CN')

_RSA_KEY = RSA.import_key(HRT_PUB_KEY)


# ==================== 推送 ====================
def send_msg(title, content):
    """企业微信机器人（QYWX_KEY）"""
    print('【通知】%s\n%s' % (title, content))
    key = os.environ.get('QYWX_KEY') or ''
    if not key:
        print('⚠️ 提示：未在环境变量配置 QYWX_KEY，跳过通知发送。')
        return
    payload = {'msgtype': 'text', 'text': {'content': '🔔 %s\n%s\n%s\n\n统计时间：%s' % (
        title, '-' * 20, content, time.strftime('%M:%S'))}}
    try:
        r = requests.post('https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=%s' % key,
                          headers={'Content-Type': 'application/json'}, json=payload, timeout=15).json()
        if r.get('errcode') == 0:
            print('🚀 企业微信机器人推送成功！')
        else:
            print('❌ 推送失败: %s' % r.get('errmsg'))
    except Exception as e:
        print('💥 推送异常: %s' % e)


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
        parts.append('%s=%s' % (k, v))
    return hmac.new(HRT_SECRET.encode(), '&'.join(parts).encode(), hashlib.md5).hexdigest()


def encrypt_body(api_path, params):
    """{key: RSA-OAEP(AES key), data: AES-128-CBC(零IV) 密文}"""
    ts = int(time.time() * 1000)
    payload = {'apiPath': quote(api_path, safe=''), 'timestamp': ts, 'appId': HRT_APP_ID}
    payload.update(params)
    payload['signature'] = sign_plain(payload)

    aes_key = os.urandom(16)
    pt = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    ct = AES.new(aes_key, AES.MODE_CBC, b'\x00' * 16).encrypt(pad(pt, 16))
    return {
        'key': base64.b64encode(PKCS1_OAEP.new(_RSA_KEY).encrypt(aes_key)).decode(),
        'data': base64.b64encode(ct).decode(),
    }


# ==================== 业务 ====================
class HuaRunTong:
    def __init__(self, token, name=''):
        self.token = token.strip()
        self.name = name
        self.sess = requests.Session()
        self.avail = None       # 可用余额
        self.avail_before = None
        self.total = None       # 历史累计
        self.days = 0
        self.signed = False     # 今天是否为 S
        self.already = False    # 之前就已签（本轮没提交）
        self.gain = 0
        self.err = ''
        self.note = ''
        self.hold = 0

    # ---- 底层 ----
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
            j = self.sess.post(HRT_BASE_URL + path, headers=headers,
                               data=json.dumps(body), timeout=20).json()
        except Exception as e:
            return {'_err': str(e)[:120]}
        if str(j.get('code')) != CODE_OK:
            j['_err'] = j.get('msg') or ('code=%s' % j.get('code'))
        return j

    def _base(self):
        return {'channelId': HRT_CHANNEL_ID, 'sysId': HRT_SYS_ID,
                'transactionUuid': str(uuid.uuid4()), 'token': self.token}

    # ---- 查询 ----
    def status(self):
        j = self._call(API_WEEK_SIGNIN, self._base())
        if j.get('_err'):
            self.err = j['_err']
            return None
        d = j.get('data') or {}
        self.days = int(d.get('signinDays') or 0)
        try:
            self.total = int(d.get('totalPoints') or 0)
        except Exception:
            pass
        today_item = None
        for it in (d.get('list') or []):
            if it.get('today'):
                today_item = it
                break
        if today_item is None:
            self.err = '本周列表没有 today 项，接口结构可能变了'
            return d
        self.signed = str(today_item.get('isSignin')) == 'S'
        self.note = str(today_item.get('isSignin') or '')
        return d

    def summary(self):
        """可用余额：走 encrypted 版（appId=API_AUTH_WEB），与其他接口同一套签名
        返回结构两层：data.data.cPoints{points,availablePoints,holdPoints}"""
        p = self._base()
        p['pointsType'] = HRT_POINTS_TYPE
        j = self._call(API_SUMMARY, p)
        if j.get('_err'):
            self.note = (self.note + ' 概要失败:%s' % j['_err']).strip()
            return None
        try:
            cp = j['data']['data']['cPoints']
            v = cp.get('availablePoints')
            self.avail = int(v if v is not None else cp.get('points'))
            self.hold = int(cp.get('holdPoints') or 0)
        except Exception:
            return None
        return self.avail

    # ---- 签到 ----
    def signin(self):
        p = self._base()
        p.update({'merchantCode': HRT_MERCHANT_CODE,
                  'storeCode': HRT_STORE_CODE,
                  'answerResult': 1})
        j = self._call(API_SIGNIN, p)
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
            self.gain = 0
        return True, ('+%s 积分' % self.gain if self.gain else '操作成功')

    def run(self):
        print('\n%s\n账号: %s\n%s' % ('=' * 52, self.name, '=' * 52))
        self.summary()
        self.avail_before = self.avail

        st = self.status()
        if st is None:
            print('❌ 查询失败：%s（token 可能失效）' % self.err)
            return False

        if self.signed:
            self.already = True
            print('✅ 今日已签到（isSignin=S）')
            print('📅 连续 %s 天 · 💰 可用 %s' % (self.days, self.avail))
            return True

        print('⏳ 今日未签到（isSignin=%s），提交签到…' % (self.note or 'P'))
        ok, msg = self.signin()
        print(('✅ ' if ok else '❌ ') + msg)
        if not ok:
            self.err = msg
            return False

        # 提交后回查确认 S
        for i in range(2):
            time.sleep(3 if i else 2)
            self.status()
            if self.signed:
                print('✅ 回查确认：isSignin=S')
                break
            if i == 0:
                print('⚠️ 状态仍为 %s，重试一次提交' % (self.note or 'P'))
                ok2, msg2 = self.signin()
                if ok2 and not self.gain:
                    self.gain = 6
        if not self.signed:
            print('⚠️ 回查仍为 %s（签到未确认完成）' % (self.note or 'P'))

        self.summary()
        print('💰 签到后: 可用 %s（前 %s）· 累计 %s' % (self.avail, self.avail_before, self.total))
        return self.signed or ok


def parse_accounts(raw):
    out = []
    for line in raw.replace('\n', '&').split('&'):
        line = line.strip()
        if not line:
            continue
        parts = [x.strip() for x in line.split('#') if x.strip()]
        if len(parts) < 2:
            print('⚠️ 跳过格式错误账号: %s（应为 备注#token）' % line[:20])
            continue
        out.append((parts[0], parts[1]))
    return out


def main():
    raw = (os.environ.get(ENV_TOKEN) or '').strip()
    if not raw:
        print('❌ 未设置环境变量 %s（格式: 备注#token）' % ENV_TOKEN)
        return 1
    accounts = parse_accounts(raw)
    if not accounts:
        print('❌ %s 没有可用账号' % ENV_TOKEN)
        return 1

    print('=' * 52)
    print('华润通签到 · %d 个账号' % len(accounts))
    print('=' * 52)

    blocks, okn = [], 0
    for name, token in accounts:
        t = HuaRunTong(token, name)
        try:
            ok = t.run()
        except Exception as e:
            t.err = str(e)[:80]
            ok = False
            print('异常: %s' % e)
        okn += 1 if ok else 0

        lines = ['👤 %s' % name]
        if not ok and not t.already:
            lines.append('📝 状态：❌ %s' % (t.err or '签到失败'))
        elif t.already:
            lines.append('📝 状态：✅ 今日已签到')
        elif t.signed:
            lines.append('📝 状态：✅ 签到成功 +%s' % (t.gain or '?'))
        else:
            lines.append('📝 状态：⚠️ 已提交但未确认（isSignin=%s）' % (t.note or 'P'))
        lines.append('📅 连续：%s 天' % t.days)
        if t.avail is not None:
            extra = ''
            if t.avail_before not in (None, t.avail):
                extra = '（签到前 %s）' % t.avail_before
            lines.append('💰 可用：%s 分%s' % (t.avail, extra + (' 冻结 %s' % t.hold if t.hold else '')))
        if t.total is not None:
            lines.append('📊 累计：%s 分（历史，含过期/已用）' % t.total)
        blocks.append('\n'.join(lines))
        time.sleep(3)

    print('\n%s\n📊 %d/%d 成功\n%s' % ('=' * 52, okn, len(accounts), '=' * 52))
    send_msg('%s %s' % (NOTIFY_TITLE, '✅' if okn == len(accounts) else '⚠️'), '\n\n'.join(blocks))
    return 0 if okn == len(accounts) else 2


if __name__ == '__main__':
    sys.exit(main())
