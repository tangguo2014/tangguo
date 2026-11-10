#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
====================================================================
vx小程序  创维会员中心 · 任务领维豆 + 成长值（YYB 全自动）
====================================================================

【变量】
  yyb_gpt
      格式： YYB地址#ref
      例： http://256.36.2.1:8000#1
      cron：26 14 * * * 每天一次自行修改

【流程】
  1. YYB 取 wx.login code（appid wxff438d3c60c63fb6）
  2. POST /v2/user/exchange {"code"}   
  3. POST /v2/user/signin  {"ticket"}  
  4. GET /v1/complete-task/{code}

【签名】
  明文 = "nonce={nonce}&timestamp={timestamp}&"
  sign = base64( RSA-SHA256(明文, 私钥) )

【任务】
   每日签到➕浏览商品详情➕分享商品➕浏览商品详情➕浏览图片详情      

====================================================================
"""

import base64
import json
import os
import subprocess
import sys
import time
import uuid

import requests

# ==================== 配置 ====================
ENV_YYB = 'yyb_gpt'
APP_ID_WX = 'wxff438d3c60c63fb6'
UC_API = 'https://uc-api.skyallhere.com/miniprogram/api'
UC_ADMIN = 'https://uc-admin.skyallhere.com/api'

APP_VERSION = '8.0.76'
SDK_VERSION = '3.17.6'
APP_SYSTEM = 'iOS 16.3'
APP_MODEL = 'iPhone 14 Pro<iPhone15,2>'

# 任务清单：(taskCode, 名称, 该任务对应的 App-Path)
TASKS = [
    ('TS00016', '每日签到',           '/pages/webview/webview?taskCode=TS00016'),
    ('TS00210', '浏览商品详情',       '/pages-cwshop/goods/detail/detail?wd=2'),
    ('TS00211', '浏览商品详情(成长值)', '/pages-cwshop/goods/detail/detail?czz=2'),
    ('TS00203', '分享商品',           '/pages-cwshop/goods/detail/detail?czz=1'),
    ('TS00213', '浏览图片详情',       '/pages-picture/picture/page-picture-detail'),
]

# 小程序里硬编码的 512 位 RSA 私钥（calcSystemSignAndParam）
PKCS1_KEY = '''-----BEGIN RSA PRIVATE KEY-----
MIIBPAIBAAJBAMJrqTwwvDRo/NP3Pjq0wfeHtfAcwRu5vk5yTfdGmKAAqG9M9Bu8
COIBN/B0lGUcUx4HP4eIvK17HoIut8shun8CAwEAAQJAXVNWymjOfw4ChzFAsud/
0HVZlWgIHmn7+yYNXOyLaQnv8I7GTrVe85lnAvcmboSvpr5KFGzhY0KDpAnCcDsh
QQIhAPzyeP4ncY7cLkftHPUTSg7Mkve/gJUFZN7q2pW0KEGfAiEAxMRcDf8yqSXP
VfUmJpnzranrFRIAs9Eqi1jzbB4KmyECIQCu2hJHZg66uXuInuEQjKf5+PJzLj79
RIBJFEHLkIDvcwIhALvLwSQmvd5MVN9wU1IiOz0zYEfC3+K/LkDCy8kTvwGhAiEA
8OKljQOdOhQcWver4UsvF5jwGPC5CqkPq/not9YLtU4=
-----END RSA PRIVATE KEY-----'''

UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) '
      'AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 '
      'MicroMessenger/8.0.76(0x18004c3a) NetType/WIFI Language/zh_CN')


def _hdr(app_path='/pages/index/index', extra=None):
    h = {
        'content-type': 'application/json',
        'App-Version': APP_VERSION,
        'App-Sdkversion': SDK_VERSION,
        'App-System': APP_SYSTEM,
        'App-Model': APP_MODEL,
        'App-Path': app_path,
        'User-Agent': UA,
        'Referer': 'https://servicewechat.com/%s/386/page-frame.html' % APP_ID_WX,
    }
    if extra:
        h.update(extra)
    return h


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


# ==================== RSA 签名 ====================
_KEY_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.cw_key.pem')


def _write_key():
    if not os.path.exists(_KEY_TMP):
        with open(_KEY_TMP, 'w') as f:
            f.write(PKCS1_KEY)
    return _KEY_TMP


def make_sign(nonce, timestamp):
    """明文 = nonce=xxx&timestamp=xxx& ，签名 = base64(RSA-SHA256(明文, 私钥))
    用 Node crypto 实现（pycryptodome 对这份 jsrsasign 生成的密钥签名结果不一致）"""
    plain = 'nonce=%s&timestamp=%s&' % (nonce, timestamp)
    key = _write_key()
    js = ('const c=require("crypto"),fs=require("fs");'
          'process.stdout.write(c.createSign("RSA-SHA256")'
          '.update(process.argv[1]).sign(fs.readFileSync(process.argv[2],"utf8"),"base64"));')
    p = subprocess.run(['node', '-e', js, plain, key], capture_output=True, timeout=20)
    if p.returncode != 0:
        raise RuntimeError('RSA 签名失败: %s' % p.stderr.decode()[:120])
    return p.stdout.decode().strip()


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
class Skyworth:
    def __init__(self, name=''):
        self.name = name
        self.sess = requests.Session()
        self.token = ''
        self.before = {}
        self.after = {}
        self.done = []
        self.already = []
        self.failed = []

    def exchange(self, code):
        """code → ticket"""
        r = self.sess.post(UC_API + '/v2/user/exchange', headers=_hdr(),
                           json={'code': code}, timeout=20)
        j = r.json()
        d = j.get('data')
        ticket = (d.get('ticket') if isinstance(d, dict) else d) if d else ''
        if str(j.get('code')) != '0' or not ticket:
            raise RuntimeError('exchange 失败: %s' % (j.get('msg') or j.get('code')))
        return ticket

    def signin(self, ticket):
        """ticket → token"""
        r = self.sess.post(UC_API + '/v2/user/signin', headers=_hdr(),
                           json={'ticket': ticket}, timeout=20)
        j = r.json()
        tok = (j.get('data') or {}).get('token') or ''
        if str(j.get('code')) != '0' or not tok:
            raise RuntimeError('signin 失败: %s' % (j.get('msg') or j.get('code')))
        self.token = tok
        return tok

    def user_info(self):
        r = self.sess.get(UC_API + '/v1/get-user',
                          headers=_hdr(extra={'Authorization': 'Bearer ' + self.token}), timeout=20)
        j = r.json()
        if j.get('code') == 0:
            return j.get('data') or {}
        return {}

    def wd_status(self):
        """任务完成状态"""
        r = self.sess.get(UC_ADMIN + '/userEquityScoreLog/miniProgram/userScoreStatusInfo',
                          headers=_hdr('/pages-user/get-wd/get-wd',
                                       {'Authorization': 'Bearer ' + self.token,
                                        'isNoToken': '1'}), timeout=20)
        j = r.json()
        arr = j.get('data') or []
        return {x.get('taskLabel'): int(x.get('isComplete') or 0) for x in arr if isinstance(x, dict)}

    def complete(self, task_code, app_path):
        nonce = str(uuid.uuid4())
        ts = str(int(time.time() * 1000))
        sign = make_sign(nonce, ts)
        h = _hdr(app_path, {'Authorization': 'Bearer ' + self.token,
                            'nonce': nonce, 'timestamp': ts, 'sign': sign})
        url = '%s/v1/complete-task/%s?taskCode=%s&nonce=%s&timestamp=%s' % (
            UC_API, task_code, task_code, nonce, ts)
        r = self.sess.get(url, headers=h, timeout=20)
        j = r.json()
        code = str(j.get('code'))
        msg = j.get('msg') or ''
        data = j.get('data')
        if code == '0':
            return 'ok', str(data if data is not None else '成功')
        if '已' in msg or '重复' in msg or '完成' in msg or '不存在' in msg:
            return 'done', msg
        return 'fail', msg or code

    def run(self, yyb_url, ref):
        print(f'\n{"─" * 46}\n👤 {self.name}\n{"─" * 46}')

        # 1) 登录
        code = yyb_get_code(yyb_url, ref)
        print('   ✅ YYB 取码成功')
        ticket = self.exchange(code)
        print('   ✅ 换取 ticket')
        self.signin(ticket)
        print('   ✅ 登录成功')

        info = self.user_info()
        bi = info.get('baseInfo') or {}
        self.before = bi
        print('   👤 %s  维豆 %s  成长值 %s' % (
            bi.get('userName') or bi.get('nickName') or '', bi.get('userScore', 0), bi.get('growthValue', 0)))

        # 2) 先查任务状态
        st = self.wd_status()
        print('   📋 任务状态：%s' % st)

        # 3) 逐个执行
        for tc, title, path in TASKS:
            if st.get(tc) == 1:
                self.already.append(title)
                print(f'   ℹ️ {title} 已完成，跳过')
                continue
            r, msg = self.complete(tc, path)
            if r == 'ok':
                self.done.append('%s → %s' % (title, msg))
                print(f'   ✅ {title} → {msg}')
            elif r == 'done':
                self.already.append(title)
                print(f'   ℹ️ {title} → {msg}')
            else:
                self.failed.append('%s → %s' % (title, msg))
                print(f'   ❌ {title} → {msg}')
            time.sleep(2)

        # 4) 结果
        time.sleep(1)
        info2 = self.user_info()
        self.after = info2.get('baseInfo') or {}
        delta = int(self.after.get('userScore') or 0) - int(self.before.get('userScore') or 0)
        gdelta = int(self.after.get('growthValue') or 0) - int(self.before.get('growthValue') or 0)

        lines = ['👤 %s' % self.name]
        if self.done:
            lines.append('✅ 完成 %d 项：' % len(self.done))
            for x in self.done:
                lines.append('  · ' + x)
        if self.already:
            lines.append('ℹ️ 已完成/跳过：%s' % '、'.join(self.already))
        if self.failed:
            lines.append('❌ 失败 %d 项：' % len(self.failed))
            for x in self.failed:
                lines.append('  · ' + x)
        lines.append('💰 维豆 %s（%+d）　成长值 %s（%+d）' % (
            self.after.get('userScore', 0), delta,
            self.after.get('growthValue', 0), gdelta))
        return len(self.done) > 0, '\n'.join(lines)


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
    print('📺 创维会员中心 · 每日任务')
    print('=' * 46)
    print('🕒 启动时间 : %s' % time.strftime('%Y-%m-%d %H:%M:%S'))
    print('🆔 小程序   : %s' % APP_ID_WX)
    print('🔐 登录     : YYB 取码 → exchange → signin')
    print('📝 任务     : %s' % '、'.join(t[1] for t in TASKS))
    print('=' * 46)

    blocks, okn = [], 0
    for acc in accounts:
        name = '账号' + acc['ref']
        app = Skyworth(name)
        try:
            ok, text = app.run(acc['url'], acc['ref'])
            okn += 1 if ok else 0
        except Exception as e:
            ok = False
            text = '👤 %s\n❌ %s' % (name, str(e)[:120])
            print(f'   ❌ {e}')
        blocks.append(text)
        time.sleep(3)

    print('\n%s\n📊 %d/%d 成功\n%s' % ('=' * 46, okn, len(accounts), '=' * 46))
    send_push('创维会员任务 · %s' % ('✅' if okn == len(accounts) else '⚠️'), '\n\n'.join(blocks))
    return 0


if __name__ == '__main__':
    sys.exit(main())
