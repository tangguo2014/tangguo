#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
====================================================================
VX小程序 绿蜜蜂 · 天天签到领红包（YYB 全自动 / 免抓包）
====================================================================

【变量】
  yyb_gpt
      格式： YYB地址#ref
      例：   http://352.1.16.1:8000#1
      多账号：一行一个

【流程】
  1. YYB 取 wx.login code（appid wx6fcde446296d9588）
  2. 签名换 access_token（有效期 2 小时）
       signature = md5
  3. 用 code 换 utoken
  4. GET ?m=toSign 签到

【签到奖励（服务端返回的阶梯）】
  奖励不会过期，最低三元提提现
  day1 0.2元 / day2 0.1元 / day3 30积分 / ... 详见 getSignInEveryDayInfo
====================================================================
"""

import hashlib
import json
import os
import sys
import time
import random

import requests

# ==================== 配置 ====================
ENV_YYB = 'yyb_gpt'
BASE = 'https://lmf.lvmifo.com'
APP_ID_WX = 'wx6fcde446296d9588'          # 绿蜜蜂小程序 appid（YYB 取码用）
APP_ID = '75762944'                        # 平台 app_id
APP_SECRET = 'ZNsLuCwAnnrDuQuyvTQcGpthsmASHSeG'
RAND_STR = 'lv_mi_feng_uni_app'

EP_TOKEN = '/api/5a60c77b79875'            # 换 access_token
EP_LOGIN = '/api/5e05692405c63'            # code 换 utoken
EP_SIGN = '/api/5dca57afa379e'             # 签到模块（?m=xxx）

UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) '
      'AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 '
      'MicroMessenger/8.0.76(0x18004c3a) NetType/WIFI Language/zh_CN')


# ==================== YYB ====================
def parse_accounts(raw):
    out = []
    for line in (raw or '').replace('\n', '&').split('&'):
        line = line.strip()
        if not line or '#' not in line:
            continue
        url, ref = line.rsplit('#', 1)
        out.append({'url': url.strip().rstrip('/'), 'ref': ref.strip()})
    return out


def yyb_get_code(yyb_url, ref):
    last = ''
    for i in range(3):
        try:
            r = requests.post(yyb_url + '/wxapp/getCode',
                              headers={'Content-Type': 'application/json'},
                              json={'ref': str(ref), 'app_id': APP_ID_WX},
                              timeout=40)
            if r.status_code == 409:
                raise RuntimeError('YYB 账号授权过期，请重新扫码')
            j = r.json()
            if j.get('code') in (0, '0'):
                inner = j.get('result')
                if inner is None:
                    d = j.get('data') or {}
                    inner = d.get('result', d) if isinstance(d, dict) else d
                code = (inner or {}).get('code')
                if code:
                    return code
                last = 'YYB 未返回 code'
            else:
                last = str(j.get('msg') or j.get('error') or j)[:100]
        except RuntimeError:
            raise
        except Exception as e:
            last = str(e)[:100]
        wait = 35 if '429' in last or '熔断' in last else 8
        if i < 2:
            print(f'   ⏳ YYB 取码失败({last})，{wait} 秒后重试')
            time.sleep(wait)
    raise RuntimeError('YYB 取码失败: ' + last)


# ==================== 推送 ====================
def banner():
    print('=' * 46)
    print('🐝 绿蜜蜂 · 天天签到领红包')
    print('=' * 46)
    print('🕒 启动时间 : %s' % time.strftime('%Y-%m-%d %H:%M:%S'))
    print('🆔 小程序   : %s' % APP_ID_WX)
    print('🔐 登录     : YYB 自动取码 → 签名换 token（免抓包）')
    print('=' * 46)


def send_push(title, content):
    print(f'【通知】{title}\n{content}')
    key = os.environ.get('QYWX_KEY') or ''
    if not key:
        print('⚠️ 提示：未在环境变量配置 QYWX_KEY，跳过通知发送。')
        return
    body = {'msgtype': 'text',
            'text': {'content': '🔔 %s\n%s\n%s\n\n统计时间：%s' % (
                title, '-' * 20, content, time.strftime('%M:%S'))}}
    try:
        r = requests.post('https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=%s' % key,
                          json=body, timeout=15).json()
        if r.get('errcode') == 0:
            print('🚀 企业微信机器人推送成功！')
        else:
            print('❌ 推送失败: %s' % r.get('errmsg'))
    except Exception as e:
        print('💥 推送异常: %s' % str(e)[:100])


# ==================== 业务 ====================
class LvMiFeng:
    def __init__(self, name=''):
        self.name = name
        self.sess = requests.Session()
        self.access_token = ''
        self.user_token = ''
        self.user = {}
        self.sign_result = {}
        self.sign_info = {}

    def _headers(self, with_user=False):
        h = {
            'content-type': 'application/x-www-form-urlencoded',
            'this-shop-id': '0',
            'version': 'v1.0.0',
            'User-Agent': UA,
            'access-token': self.access_token,
        }
        if with_user:
            h['user-token'] = self.user_token
        return h

    # ---- 换 access_token ----
    def get_access_token(self):
        ts = str(int(time.time()))
        device = ts + str(random.randint(100, 9999))
        raw = 'app_id=%s&app_secret=%s&device_id=%s&rand_str=%s&timestamp=%s' % (
            APP_ID, APP_SECRET, device, RAND_STR, ts)
        sig = hashlib.md5(raw.encode()).hexdigest()
        body = 'app_id=%s&device_id=%s&rand_str=%s&timestamp=%s&signature=%s' % (
            APP_ID, device, RAND_STR, ts, sig)
        r = self.sess.post(BASE + EP_TOKEN + '?appType=WX_APP',
                           data=body, headers={
                               'content-type': 'application/x-www-form-urlencoded',
                               'this-shop-id': '0',
                               'version': 'v1.0.0',
                               'User-Agent': UA,
                           }, timeout=20)
        j = r.json()
        if j.get('code') != 1:
            raise RuntimeError('换 access_token 失败: %s' % (j.get('msg') or j))
        self.access_token = j['data']['access_token']
        return self.access_token

    # ---- code 换 utoken ----
    def login(self, code):
        r = self.sess.post(BASE + EP_LOGIN, data='code=' + code,
                           headers=self._headers(), timeout=20)
        j = r.json()
        if j.get('code') != 1:
            raise RuntimeError('登录失败: %s' % (j.get('msg') or j))
        d = j.get('data') or {}
        self.user_token = d.get('utoken') or ''
        self.user = d
        if not self.user_token:
            raise RuntimeError('登录未返回 utoken')
        return self.user_token

    # ---- 查询签到信息 ----
    def get_sign_info(self):
        r = self.sess.get(BASE + EP_SIGN, params={'m': 'getSignInEveryDayInfo'},
                          headers=self._headers(True), timeout=20)
        j = r.json()
        if j.get('code') != 1:
            return {}
        self.sign_info = j.get('data') or {}
        return self.sign_info

    # ---- 签到 ----
    def do_sign(self):
        r = self.sess.get(BASE + EP_SIGN, params={'m': 'toSign'},
                          headers=self._headers(True), timeout=20)
        j = r.json()
        if j.get('code') != 1:
            return False, (j.get('msg') or str(j))[:80]
        d = j.get('data') or {}
        self.sign_result = d
        if str(d.get('is_sign')) == 'yes':
            rp = d.get('get_red_packet') or '0'
            day = d.get('sign_in_day') or '?'
            return True, ('第 %s 天，红包 %s 元' % (day, rp)) if float(rp or 0) > 0 else ('第 %s 天，签到成功' % day)
        return False, '签到未生效'

    # ---- 余额 ----
    def get_amount(self):
        try:
            r = self.sess.get(BASE + EP_SIGN, params={'m': 'getUserInfo'},
                              headers=self._headers(True), timeout=20)
            j = r.json()
            if j.get('code') == 1:
                self.user = j.get('data') or self.user
                return self.user.get('amount')
        except Exception:
            pass
        return None

    def run(self, yyb_url, ref):
        print(f'\n{"─" * 46}\n👤 {self.name}\n{"─" * 46}')

        # 1) 换 access_token
        self.get_access_token()
        print('   ✅ access_token 获取成功')

        # 2) YYB 取码 + 登录
        code = yyb_get_code(yyb_url, ref)
        print('   ✅ YYB 取码成功')
        self.login(code)
        print('   ✅ 登录成功  %s  %s' % (self.user.get('nick_name') or '', self.user.get('phone') or ''))

        # 3) 签到前余额
        amount_before = self.get_amount()
        print('   💰 签到前余额：%s 元' % amount_before)

        # 4) 查签到状态
        info = self.get_sign_info()
        rows = info.get('sign_data') or []
        today = next((x for x in rows if x.get('status') == 1), None) if rows else None

        # 5) 签到
        ok, msg = self.do_sign()
        print('   %s %s' % ('✅' if ok else '❌', msg))

        # 6) 签到后余额（服务端有延迟，重试 3 次取最后一次）
        amount = None
        for _ in range(3):
            time.sleep(2)
            amount = self.get_amount()
        print('   💰 签到后余额：%s 元' % amount)

        lines = ['👤 %s' % self.name]
        lines.append('📝 签到：%s' % ('✅ ' + msg if ok else '❌ ' + msg))
        lines.append('🧧 今日红包：%s 元' % (self.sign_result.get('get_red_packet') or '0'))
        if amount is not None:
            if amount_before is not None and amount != amount_before:
                lines.append('💰 账户余额：%s 元（签到前 %s，+%s）' % (
                    amount, amount_before, round(float(amount) - float(amount_before), 2)))
            else:
                lines.append('💰 账户余额：%s 元' % amount)
        # 阶梯信息
        if rows:
            step = ' '.join('%s天=%s%s' % (
                x.get('sign_in_day'),
                x.get('get_red_packet') if x.get('type_reward') == 'OnlyRedPacket' else (str(x.get('get_integral')) + '积分'),
                '✓' if x.get('status') == 1 else '') for x in rows[:7])
            lines.append('📅 阶梯：%s' % step)
        return ok, '\n'.join(lines)


# ==================== 主流程 ====================
def main():
    raw = (os.environ.get(ENV_YYB) or '').strip()
    if not raw:
        print(f'❌ 未设置环境变量 {ENV_YYB}，格式：YYB地址#ref')
        return 1
    accounts = parse_accounts(raw)
    if not accounts:
        print(f'❌ {ENV_YYB} 格式错误，应为 YYB地址#ref')
        return 1

    banner()

    blocks, okn = [], 0
    for acc in accounts:
        name = '账号' + acc['ref']
        app = LvMiFeng(name)
        try:
            ok, text = app.run(acc['url'], acc['ref'])
            okn += 1 if ok else 0
        except Exception as e:
            ok = False
            text = '👤 %s\n📝 状态：❌ %s' % (name, str(e)[:120])
            print(f'   ❌ {e}')
        blocks.append(text)
        time.sleep(3)

    print('\n%s\n📊 %d/%d 成功\n%s' % ('=' * 46, okn, len(accounts), '=' * 46))
    send_push('绿蜜蜂签到 · %s' % ('✅' if okn == len(accounts) else '⚠️'), '\n\n'.join(blocks))
    return 0


if __name__ == '__main__':
    sys.exit(main())
