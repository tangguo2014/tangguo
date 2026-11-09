#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
====================================================================
vx小程序  爱裹旧衣回收 · 签到 / 抽奖 / 开奖推送（YYB 自动登录版）
====================================================================

【变量】
  yyb_gpt
      格式： YYB地址#ref
      例：   http://172.17.0.1:18000#1
      多账号：一行一个
      定时 cron 15 12 * * * 定时十二点以后吧

【功能】
  1. 每日签到
  2. 自动参与所有进行中的抽奖
  3. 自动预约待开始的抽奖
  4. 检查开奖结果，中奖/未中奖都推送

【流程】
  1. YYB 取 wx.login code（appid wx4ff260333d3c5ddd）
  2. 拉服务器时间 m
  3. s = md5( ''.join(sorted(m + json(data))) )
  4. POST /recy/api/user/identityIdByAuthCode 换 token
  5. 签到 / 抽奖 / 预约 / 开奖检查

====================================================================
"""

import hashlib
import json
import os
import sys
import time

import requests

# ==================== 配置 ====================
ENV_YYB = 'yyb_gpt'
BASE = 'https://alipay.haliaeetus.cn'
APP_ID = 'wx4ff260333d3c5ddd'          # 爱裹旧衣回收小程序
PUSH_ENABLE = (os.environ.get('fuli_push') or '1') not in ('0', 'false', 'no')

UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) '
      'AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 '
      'MiniProgramEnv/ios')

EP_TIME = '/fuli/currentTime'
EP_LOGIN = '/recy/api/user/identityIdByAuthCode'
EP_SIGNED_INFO = '/fuli/api/fuli/signedInfo'
EP_SIGNED = '/fuli/api/fuli/signed'
EP_ACCOUNT = '/fuli/api/jf/account'
EP_DRAW_START = '/fuli/api/drawDay/list/start'
EP_DRAW_NOSTART = '/fuli/api/drawDay/list/no/start'
EP_PARK = '/fuli/api/drawDay/user/park'
EP_REMIND = '/fuli/api/drawDay/user/remind'
EP_MY_RECORD = '/fuli/api/drawDay/user/listUserRecodByDrawId'
EP_REMIND_LIST = '/fuli/api/drawDay/user/listRemindByDrawId'


# ==================== 签名 ====================
def make_sign(m, data):
    """s = md5( 逐字符排序后的 (m + JSON(data)) )
    ⚠️ data 必须是 JSON 紧凑序列化，str(dict) 会算错"""
    ds = data if isinstance(data, str) else json.dumps(data, separators=(',', ':'))
    raw = f'{m}{ds}'
    return hashlib.md5(''.join(sorted(raw)).strip().encode()).hexdigest()


# ==================== YYB ====================
def parse_accounts(raw):
    """yyb_gpt: 地址#ref，一行一个"""
    out = []
    for line in (raw or '').replace('\n', '&').split('&'):
        line = line.strip()
        if not line or '#' not in line:
            continue
        url, ref = line.rsplit('#', 1)
        out.append({'url': url.strip().rstrip('/'), 'ref': ref.strip()})
    return out


def yyb_get_code(yyb_url, ref):
    """取 wx.login code"""
    last = ''
    for i in range(3):
        try:
            r = requests.post(
                yyb_url + '/wxapp/getCode',
                headers={'Content-Type': 'application/json'},
                json={'ref': str(ref), 'app_id': APP_ID},
                timeout=40)
            if r.status_code == 409:
                raise RuntimeError('YYB 账号授权过期，请重新扫码')
            j = r.json()
            if j.get('code') in (0, '0'):
                inner = j.get('result')
                if inner is None:
                    dd = j.get('data') or {}
                    inner = dd.get('result', dd) if isinstance(dd, dict) else dd
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
        # 429 熔断窗口默认 30s，重试间隔要比它长，否则一直撞
        wait = 35 if '429' in last or '熔断' in last else 8
        if i < 2:
            print(f'   ⏳ YYB 取码失败({last})，{wait} 秒后重试')
            time.sleep(wait)
    raise RuntimeError('YYB 取码失败: ' + last)


# ==================== 推送 ====================
def send_push(title, content):
    """优先企业微信机器人 QYWX_KEY（与同花顺模板一致），失败再退回青龙 notify.py"""
    print('【通知】%s\n%s' % (title, content))
    key = os.environ.get('QYWX_KEY') or ''
    if key:
        try:
            body = {'msgtype': 'text', 'text': {'content': '🔔 %s\n%s\n%s' % (title, '-' * 20, content)}}
            r = requests.post('https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=%s' % key,
                              json=body, timeout=15).json()
            if r.get('errcode') == 0:
                print('🚀 企业微信推送成功')
                return True
            print('❌ 企业微信推送失败: %s' % r.get('errmsg'))
        except Exception as e:
            print('💥 企业微信推送异常: %s' % str(e)[:80])
    send = load_notify()
    if send:
        try:
            send(title, content)
            print('推送已发送（notify.py）')
            return True
        except Exception as e:
            print('推送失败（notify.py）: %s' % str(e)[:120])
    return False


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
class AiGuo:
    def __init__(self, auth, name=''):
        self.auth = auth.strip()
        self.name = name
        self.sess = requests.Session()
        self.points = None
        self.signed = False
        self.signed_already = False
        self.participated = []
        self.reserved = []
        self.results = []
        self.phone = ''          # 自己的手机号，用于中奖比对
        self.uid = ''            # identityId

    def _headers(self):
        return {
            'Authorization': self.auth,
            'content-type': 'application/json',
            'User-Agent': UA,
            'Referer': 'https://serviceprovider.alipay.com/',
            'plateForm': 'WX',
            'channelNo': '',
        }

    def _call(self, path, method='GET', body=None):
        url = BASE + path
        try:
            if method == 'GET':
                r = self.sess.get(url, headers=self._headers(), timeout=20)
            else:
                r = self.sess.post(url, headers=self._headers(),
                                   json=body if body is not None else {}, timeout=20)
        except Exception as e:
            return {'_err': str(e)[:120]}

        txt = r.text.strip()
        if 'currentTime' in path:
            return txt
        try:
            j = json.loads(txt)
        except ValueError:
            return {'_err': f'非JSON: {txt[:80]}'}
        if int(j.get('status') or 0) != 200:
            j['_err'] = j.get('msg') or f"status={j.get('status')}"
        return j

    def server_time(self):
        t = self._call(EP_TIME)
        return str(t) if str(t).isdigit() else str(int(time.time() * 1000))

    # ---- 登录（YYB 取码换 token）----
    def login_with_yyb(self, yyb_url, ref):
        code = yyb_get_code(yyb_url, ref)
        data = {'authCode': code}
        m = self.server_time()
        s = make_sign(m, data)
        url = BASE + EP_LOGIN
        try:
            r = self.sess.post(url, headers={
                'content-type': 'application/json',
                'plateForm': 'WX',
                'channelNo': '',
                'User-Agent': UA,
            }, json={'data': data, 'm': m, 's': s}, timeout=20)
            j = r.json()
        except Exception as e:
            return False, f'登录请求异常: {str(e)[:80]}'
        if j.get('status') != 200:
            return False, f"登录失败: {j.get('msg') or j.get('status')}"
        d = j.get('data') or {}
        tok = d.get('token') or d.get('accessToken')
        if not tok:
            return False, '登录未返回 token'
        self.auth = tok
        self.uid = str(d.get('identityId') or '')
        self.phone = str(d.get('phone') or d.get('mobile') or '')
        return True, 'YYB 取码成功，已换取爱裹 token'

    # ---- 查询 ----
    def load_self_info(self):
        """拉用户信息，记录手机号/identityId，供中奖比对"""
        j = self._call('/recy/api/auth/asset/getInfo', 'POST', {})
        if j.get('_err'):
            return
        d = j.get('data') or {}
        self.uid = self.uid or str(d.get('identityId') or '')
        self.phone = self.phone or str(d.get('userName') or '')

    def get_points(self):
        j = self._call(EP_ACCOUNT, 'POST', {})
        if j.get('_err'):
            return None
        try:
            self.points = float(j.get('data') or 0)
        except Exception:
            pass
        return self.points

    def sign_info(self):
        j = self._call(EP_SIGNED_INFO)
        if j.get('_err'):
            return None
        return j.get('data') or {}

    # ---- 签到 ----
    def sign(self):
        info = self.sign_info()
        if info and info.get('isSign'):
            self.signed_already = True
            self.signed = True
            return True, f"今日已签到（连续 {info.get('signDays', 0)} 天）"

        j = self._call(EP_SIGNED)
        if j.get('_err'):
            msg = str(j['_err'])
            if '已签' in msg:
                self.signed_already = True
                self.signed = True
                return True, '今日已签到'
            return False, msg
        self.signed = True
        return True, '签到成功'

    # ---- 抽奖 ----
    def draw_list(self, start=True):
        path = EP_DRAW_START if start else EP_DRAW_NOSTART
        j = self._call(path, 'POST', {'page': {'limit': 20, 'page': 1}})
        if j.get('_err'):
            return []
        return j.get('data') or []

    def park(self):
        rows = self.draw_list(start=True)
        if not rows:
            return 0
        done = 0
        for it in rows:
            did = it.get('id')
            name = it.get('goodName') or f'抽奖{did}'
            part = it.get('partInNum') or 0
            mine = self._call(EP_MY_RECORD, 'POST', {'data': did})
            has_record = bool((mine.get('data') or [])) if not mine.get('_err') else False
            j = self._call(EP_PARK, 'POST', {})
            if j.get('_err'):
                self.participated.append({'id': did, 'name': name, 'err': j['_err'], 'part': part})
            elif has_record:
                self.participated.append({'id': did, 'name': name, 'already': True, 'part': part})
            else:
                done += 1
                self.participated.append({'id': did, 'name': name, 'ok': True, 'part': part})
            time.sleep(1.5)
        return done

    def reserve(self):
        rows = self.draw_list(start=False)
        if not rows:
            return 0
        reserved_ids = set()
        try:
            ids = ','.join(str(x.get('id')) for x in rows)
            j = self._call(EP_REMIND_LIST, 'POST', {'data': ids})
            for r in (j.get('data') or []):
                reserved_ids.add(r.get('drawDayId'))
        except Exception:
            pass

        done = 0
        for it in rows:
            did = it.get('id')
            name = it.get('goodName') or f'抽奖{did}'
            if did in reserved_ids:
                self.reserved.append({'id': did, 'name': name, 'skip': '已预约'})
                continue
            m = self.server_time()
            s = make_sign(m, did)
            j = self._call(EP_REMIND, 'POST', {'data': did, 'm': m, 's': s})
            if j.get('_err'):
                self.reserved.append({'id': did, 'name': name, 'err': j['_err']})
            else:
                done += 1
                self.reserved.append({'id': did, 'name': name, 'ok': True})
            time.sleep(1.5)
        return done

    def check_results(self):
        """从 park 接口直接读开奖结果（park 返回自己参与的所有抽奖 + 开奖状态 + 中奖者）
        实测字段：drawDay.status=2 表示已开奖，luckUser 是 [{"name":"xxx"}] 形式"""
        j = self._call(EP_PARK, 'POST', {})
        if j.get('_err'):
            return
        rows = j.get('data') or []
        for it in rows:
            dd = it.get('drawDay') or {}
            if not dd:
                continue
            did = dd.get('id')
            name = dd.get('goodName') or f'抽奖{did}'
            status = dd.get('status')
            open_time = dd.get('openTime') or ''
            lu = dd.get('luckUser')
            winners = []
            if lu:
                try:
                    arr = json.loads(lu) if isinstance(lu, str) else lu
                    winners = [w.get('name') for w in (arr or []) if w.get('name')]
                except Exception:
                    winners = [str(lu)]
            if not winners and status != 2:
                continue          # 还没开奖
            # 中奖判断：
            # 实测 luckUser 里的 name 是完整标识（如 "AGLn2_80"），界面脱敏成 "A**n2_80"
            # 自己的 nickName 是脱敏手机号 "136****6965"，所以优先用脱敏格式比对，再用尾号兜底
            win = False
            if winners and self.phone and len(self.phone) >= 7:
                masked = self.phone[:3] + '****' + self.phone[-4:]
                tail = self.phone[-4:]
                for w in winners:
                    ws = str(w)
                    if ws == masked or masked in ws:
                        win = True
                    elif tail in ws:            # 尾号兜底
                        win = True
                    if self.uid and self.uid == ws:
                        win = True
            self.results.append({
                'id': did, 'name': name, 'luck': '、'.join(winners),
                'open': open_time, 'win': win, 'status': status,
                'prize': name,
            })

    def run(self):
        print(f'\n{"=" * 52}\n账号: {self.name}\n{"=" * 52}')

        self.load_self_info()
        p0 = self.get_points()
        if p0 is None:
            print('❌ 无法读取积分，token 可能失效')
            return False
        print(f'💰 签到前积分: {p0}')

        ok, msg = self.sign()
        print(f'{"✅" if ok else "❌"} {msg}')
        time.sleep(1.5)

        n = self.park()
        if self.participated:
            print('\n🎰 抽奖:')
            for x in self.participated:
                if x.get('ok'):
                    st = '✅ 本次参与成功'
                elif x.get('already'):
                    st = '✅ 已参与'
                elif x.get('err'):
                    st = '❌ ' + x.get('err', '')
                else:
                    st = '⏭ ' + x.get('skip', '')
                cnt = f"（{x['part']} 人参与）" if x.get('part') else ''
                print(f'   {x["name"]}: {st}{cnt}')
            if n:
                print(f'   本次新增参与 {n} 个')

        nr = self.reserve()
        if self.reserved:
            print(f'\n📅 预约（新预约 {nr} 个）:')
            for x in self.reserved:
                st = '✅预约' if x.get('ok') else ('⏭ ' + x.get('skip', '') if x.get('skip') else '❌ ' + x.get('err', ''))
                print(f'   {x["name"]}: {st}')

        self.check_results()
        if self.results:
            print('\n🏆 开奖结果:')
            for r in self.results:
                print(f'   {r["name"]}: 中奖者={r["luck"] or "待公布"} 开奖={r["open"]}')

        time.sleep(1.5)
        p1 = self.get_points()
        print(f'\n💰 签到后积分: {p1}')
        return True


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

    print('=' * 52)
    print(f'爱裹旧衣回收 · {len(accounts)} 个账号')
    print('=' * 52)

    reports = []
    for acc in accounts:
        name = '账号' + acc['ref']
        app = AiGuo('', name)
        try:
            ok_login, msg = app.login_with_yyb(acc['url'], acc['ref'])
            print(f'\n🔑 [{name}] {msg}')
            if ok_login:
                ok = app.run()
            else:
                ok = False
        except Exception as e:
            print(f'异常: {e}')
            ok = False
        reports.append({
            'name': name, 'ok': ok,
            'points': app.points, 'signed': app.signed, 'signed_already': app.signed_already,
            'participated': app.participated, 'reserved': app.reserved, 'results': app.results,
        })
        time.sleep(2)

    if PUSH_ENABLE and reports:
        lines = []
        for r in reports:
            if not r['ok']:
                lines.append(f"{r['name']}\n  ❌ 执行失败")
                continue
            lines.append(f"【{r['name']}】")
            lines.append(f"签到：{'已签到' if r['signed_already'] else '签到成功'}")
            lines.append(f"积分：{r['points']}")
            if r['participated']:
                lines.append("抽奖：")
                for x in r['participated']:
                    if x.get('ok'):
                        st = '本次参与成功'
                    elif x.get('already'):
                        st = '已参与'
                    elif x.get('err'):
                        st = '失败'
                    else:
                        st = x.get('skip') or '跳过'
                    cnt = f" · {x['part']}人" if x.get('part') else ''
                    lines.append(f"  · {x['name']}（{st}{cnt}）")
            if r['reserved']:
                new_r = [x for x in r['reserved'] if x.get('ok')]
                old_r = [x for x in r['reserved'] if x.get('skip')]
                if new_r:
                    lines.append(f"新预约：{len(new_r)} 个")
                    for x in new_r:
                        lines.append(f"  · {x['name']}")
                if old_r:
                    lines.append(f"已预约：{len(old_r)} 个")
            if r['results']:
                lines.append("🏆 开奖：")
                for x in r['results']:
                    if x.get('win'):
                        lines.append(f"  · 🎉 {x['name']}（中奖！{x.get('prize') or ''}）")
                    elif x['luck']:
                        lines.append(f"  · {x['name']}（已开奖 · 未中）")
                    else:
                        lines.append(f"  · {x['name']}（待公布）")
        send_push('爱裹回收 · 完成', '\n'.join(lines))

    return 0


if __name__ == '__main__':
    sys.exit(main())
