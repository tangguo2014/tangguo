#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
====================================================================
小程序： Roki签到  （#小程序://老板电器/Do0pD58WaOXIRPj）

功能：只有签到功能，每日签到，+50 分，连续签到10天+100分
变量：roki_gpt，抓X-USER-TOKEN，或者备注#token（多账号换行或&分割）
     token（30 天有效，到期需重新获取）
定时：8:30 运行一次（cron: 30 8 * * *）

====================================================================
"""

import base64
import hashlib
import hmac
import json
import os
import sys
import time
from urllib.parse import quote

import requests

# ==================== 配置 ====================
ENV_TOKEN = 'roki_gpt'
BASE = 'https://aio.myroki.com'
APP_ID = 'roki_app'
APP_VER = '5000'
NONCE = '1234567890123456'
SIGN_SECRET = 'ee8694419924a22f04ac0e01368683521daa659f'
PUSH_ENABLE = (os.environ.get('roki_push') or '1') not in ('0', 'false', 'no')

UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) '
      'AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 '
      'MiniProgramEnv/ios')
REFERER = 'https://servicewechat.com/wxba70fb8e3eb3aab9/527/page-frame.html'

API_PROFILE = '/api/v1/mini-app/user/profile'
API_POINTS = '/api/v1/mini-app/user/member/points'
API_CHECKIN = '/api/v1/mini-app/user/check-in-record/check-in'
API_RECENT = '/api/v1/mini-app/user/check-in-record/recent/record'


# ==================== 签名 ====================
def make_sign():
    ts = int(time.time() * 1000)
    msg = (f'aesEncryptSecret={NONCE}&appId={APP_ID}&nonce={NONCE}'
           f'&secret={SIGN_SECRET}&timestamp={ts}')
    digest = hmac.new(SIGN_SECRET.encode(), msg.encode(), hashlib.sha256).digest()
    sig = quote(base64.b64encode(digest).decode(), safe='')
    return ts, sig


def headers(token):
    ts, sig = make_sign()
    return {
        'app-id': APP_ID,
        'content-type': 'application/json',
        'secret': NONCE,
        'X-App-Env': 'release',
        'signature': sig,
        'X-USER-TOKEN': token,
        'timestamp': str(ts),
        'app-version': APP_VER,
        'nonce': NONCE,
        'charset': 'utf-8',
        'Referer': REFERER,
        'User-Agent': UA,
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
class Roki:
    def __init__(self, token, name=''):
        self.token = token.strip()
        self.name = name
        self.sess = requests.Session()
        self.nick = ''
        self.points = None
        self.sign_points = 0
        self.consecutive = 0
        self.already = False

    def _call(self, path, method='GET', body=None):
        url = BASE + path
        h = headers(self.token)
        try:
            if method == 'GET':
                r = self.sess.get(url, headers=h, timeout=20)
            else:
                r = self.sess.post(url, headers=h, json=body if body is not None else {}, timeout=20)
            j = r.json()
        except Exception as e:
            return {'_err': str(e)[:120]}
        if j.get('code') != 200:
            j['_err'] = j.get('message') or f"code={j.get('code')}"
        return j

    def profile(self):
        j = self._call(API_PROFILE)
        if j.get('_err'):
            return None, j['_err']
        d = j.get('data') or {}
        self.nick = d.get('nickName') or self.name or '未知'
        return d, None

    def get_points(self):
        j = self._call(API_POINTS)
        if j.get('_err'):
            return None
        try:
            self.points = int((j.get('data') or {}).get('point') or 0)
        except Exception:
            pass
        return self.points

    def recent(self):
        j = self._call(API_RECENT)
        d = j.get('data') or {}
        try:
            self.consecutive = int(d.get('consecutiveDays') or 0)
            self.already = (d.get('todayIsCheckIn') == 1)
        except Exception:
            pass
        return d

    def checkin(self):
        j = self._call(API_CHECKIN, 'POST', {})
        if j.get('_err'):
            msg = str(j['_err'])
            if '已签到' in msg or '19014' in msg:
                self.already = True
                return True, '今日已签到'
            return False, msg
        d = j.get('data') or {}
        self.sign_points = int(d.get('dailyCheckInPoints') or 0)
        self.consecutive = int(d.get('consecutiveDays') or 0)
        return True, f'+{self.sign_points} 分（连签 {self.consecutive} 天）'


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
            if x.count('.') == 2 and len(x) > 50:
                token = x
            elif not name:
                name = x
        if token:
            out.append((name or token[:10], token))
    return out


def run_one(name, token):
    print(f'\n{"=" * 52}')
    print(f'账号: {name}')
    print('=' * 52)

    app = Roki(token, name)

    prof, err = app.profile()
    if err:
        print(f'❌ {err}')
        return {'ok': False, 'name': name, 'msg': err}

    before = app.get_points()
    print(f'👤 {app.nick}')
    print(f'💰 签到前: {before} 分')

    ok, msg = app.checkin()
    print(f'{"✅" if ok else "❌"} {msg}')

    time.sleep(2)
    after = app.get_points()
    gain = (after - before) if (after is not None and before is not None) else 0
    print(f'💰 签到后: {after} 分  (+{gain})')

    app.recent()
    if app.consecutive:
        print(f'🔥 连续签到: {app.consecutive} 天')

    return {
        'ok': ok,
        'name': app.nick or name,
        'before': before,
        'after': after,
        'gain': gain,
        'days': app.consecutive,
        'msg': msg,
    }


def main():
    raw = (os.environ.get(ENV_TOKEN) or '').strip()
    if not raw:
        print(f'❌ 未设置环境变量 {ENV_TOKEN}')
        return 1

    accounts = parse_accounts(raw)
    if not accounts:
        print(f'❌ {ENV_TOKEN} 中没有找到有效 token')
        print('   格式：备注#token')
        return 1

    print('=' * 52)
    print(f'Roki 每日签到 · {len(accounts)} 个账号')
    print('=' * 52)

    results = []
    for name, token in accounts:
        try:
            r = run_one(name, token)
            results.append(r)
        except Exception as e:
            print(f'账号 {name} 异常: {e}')
            results.append({'ok': False, 'name': name, 'msg': str(e)[:100]})
        time.sleep(3)

    okn = sum(1 for r in results if r.get('ok'))
    print(f'\n{"=" * 52}')
    print(f'📊 {okn}/{len(results)} 成功')
    print('=' * 52)

    if PUSH_ENABLE and results:
        send = load_notify()
        if send:
            lines = []
            for r in results:
                if r.get('ok'):
                    lines.append(
                        f"{r['name']}\n"
                        f"  签到积分: +{r.get('gain', 0)}\n"
                        f"  总积分: {r.get('after', 0)}\n"
                        f"  连续: {r.get('days', 0)} 天"
                    )
                else:
                    lines.append(f"{r['name']}\n  ❌ {r.get('msg', '失败')}")
            title = f"Roki签到 · {'✅' if okn == len(results) else '⚠️'} {okn}/{len(results)}"
            content = '\n\n'.join(lines)
            try:
                send(title, content)
                print('\n推送已发送')
            except Exception as e:
                print(f'推送失败: {e}')

    return 0 if okn == len(results) else 2


if __name__ == '__main__':
    sys.exit(main())
