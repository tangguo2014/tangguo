#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
====================================================================
vx小程序  天牛旧衣服回收 · 每日签到（YYB 全自动 / 免抓包）
====================================================================

【变量】
  yyb_gpt
      格式： YYB地址#ref
      例：   http://165.23.36.1:8000#1
      多账号：一行一个
      定时 cron 6 10 * * * 每天一次自行修改

【流程】
  1. YYB 取 wx.login code（appid wx887c2f947bffa76e）
  2. POST /api/login/getWxMiniProgramSessionKey → token

【签到】
  有时候是积分有时候是现金具体没验证
  奖励不过期，最低两元提现

====================================================================
"""

import json
import os
import sys
import time

import requests

# ==================== 配置 ====================
ENV_YYB = 'yyb_gpt'
BASE = 'https://tianniunew.fzjingzhou.com'
APP_ID_WX = 'wx887c2f947bffa76e'          # 天牛小程序 appid（YYB 取码用）
FIXED_TOKEN = 'wek2020123456788wek'        # 登录接口的固定占位 token

EP_LOGIN = '/api/login/getWxMiniProgramSessionKey'
EP_INFO = '/api/Person/index'
EP_SIGN = '/api/Person/sign'

UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) '
      'AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 '
      'MicroMessenger/8.0.76(0x18004c3a) NetType/WIFI Language/zh_CN')

HDR = {
    'content-type': 'application/x-www-form-urlencoded',
    'platform': 'MP-WEIXIN',
    'User-Agent': UA,
    'Referer': 'https://servicewechat.com/%s/7/page-frame.html' % APP_ID_WX,
}


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
class TianNiu:
    def __init__(self, name=''):
        self.name = name
        self.sess = requests.Session()
        self.token = ''
        self.before = {}
        self.after = {}
        self.sign_data = None

    def _post(self, path, data):
        return self.sess.post(BASE + path, headers=HDR, data=data, timeout=20).json()

    def login(self, code):
        j = self._post(EP_LOGIN, 'code=%s&gdtVid=&token=%s' % (code, FIXED_TOKEN))
        if j.get('code') != 1000:
            raise RuntimeError('登录失败: %s' % (j.get('msg') or j))
        self.token = (j.get('data') or {}).get('token') or ''
        if not self.token:
            raise RuntimeError('登录未返回 token')
        self.before = (j.get('data') or {}).get('personInfo') or {}
        return self.token

    def info(self):
        j = self._post(EP_INFO, 'token=' + self.token)
        if j.get('code') != 1000:
            raise RuntimeError('查询信息失败: %s' % (j.get('msg') or j))
        return j.get('data') or {}

    def sign(self):
        j = self._post(EP_SIGN, 'token=' + self.token)
        if j.get('code') != 1000:
            return False, (j.get('msg') or str(j))[:80]
        self.sign_data = j.get('data')
        return True, str(j.get('data'))

    def run(self, yyb_url, ref):
        print(f'\n{"─" * 46}\n👤 {self.name}\n{"─" * 46}')

        # 1) 登录
        code = yyb_get_code(yyb_url, ref)
        print('   ✅ YYB 取码成功')
        self.login(code)
        print('   ✅ 登录成功  %s  %s' % (
            self.before.get('nickname') or '', self.before.get('mobile') or ''))

        # 2) 签到
        before_score = int(self.before.get('score') or 0)
        before_exchange = float(self.before.get('exchange') or 0)
        before_days = self.before.get('sign_in_num')
        print('   📊 签到前：环保币 %s，余额 %s 元，连签 %s 天' % (
            before_score, before_exchange, before_days))

        ok, msg = self.sign()
        print('   %s 签到：%s' % ('✅' if ok else '❌', msg))

        # 3) 签到后查询
        time.sleep(2)
        try:
            self.after = self.info()
        except Exception:
            self.after = {}

        after_score = int(self.after.get('score') or before_score)
        after_exchange = float(self.after.get('exchange') or before_exchange)
        days = self.after.get('sign_in_num') or before_days

        gain = after_score - before_score
        money_gain = round(after_exchange - before_exchange, 2)

        # 4) 结果
        lines = ['👤 %s' % self.name]
        if ok:
            lines.append('📝 签到：✅ 成功')
        else:
            lines.append('📝 签到：❌ %s' % msg)
        lines.append('📅 连签：%s 天' % days)
        if gain > 0:
            lines.append('🪙 环保币：+%s（共 %s）' % (gain, after_score))
        else:
            lines.append('🪙 环保币：%s（本次 +0）' % after_score)
        if money_gain > 0:
            lines.append('💰 余额：%s 元（+%s）' % (after_exchange, money_gain))
        else:
            lines.append('💰 余额：%s 元' % after_exchange)
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

    print('=' * 46)
    print('♻️  天牛旧衣服回收 · 每日签到')
    print('=' * 46)
    print('🕒 启动时间 : %s' % time.strftime('%Y-%m-%d %H:%M:%S'))
    print('🆔 小程序   : %s' % APP_ID_WX)
    print('🔐 登录     : YYB 自动取码（免抓包）')
    print('=' * 46)

    blocks, okn = [], 0
    for acc in accounts:
        name = '账号' + acc['ref']
        app = TianNiu(name)
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
    send_push('天牛回收签到 · %s' % ('✅' if okn == len(accounts) else '⚠️'), '\n\n'.join(blocks))
    return 0


if __name__ == '__main__':
    sys.exit(main())
