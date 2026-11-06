#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
====================================================================
vx小程序  爱裹旧衣回收 · 签到 / 抽奖 / 开奖推送
====================================================================

【功能】
  1. 每日签到
  2. 自动参与所有进行中的抽奖
  3. 自动预约待开始的抽奖
  4. 检查开奖结果，中奖/未中奖都推送

【签名规则】
  s = md5( sorted( m + str(data) ) )
  m = 服务器毫秒时间戳（GET /fuli/currentTime）
  已用两组真实抓包验证一致

【使用方法】
  1. 抓包：Authorization
  2. 青龙变量：ag_gpt = 备注#Authorization值
  3. 定时：cron 30 12 * * *

====================================================================
"""

import hashlib
import json
import os
import sys
import time

import requests

# ==================== 配置 ====================
ENV_TOKEN = 'ag_gpt'
BASE = 'https://alipay.haliaeetus.cn'
PUSH_ENABLE = (os.environ.get('fuli_push') or '1') not in ('0', 'false', 'no')

UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) '
      'AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 '
      'MiniProgramEnv/ios')

EP_TIME = '/fuli/currentTime'
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
    """s = md5( 字符排序后的 (m + str(data)) )"""
    raw = f'{m}{data}'
    return hashlib.md5(''.join(sorted(raw)).strip().encode()).hexdigest()


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
        self.results = []          # 开奖结果

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

    # ---- 查询 ----
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
        """参与所有进行中的抽奖。
        park 接口幂等：已参与再调用不会新增记录，只返回原记录。
        所以这里先查是否已参与，日志区分「已参与」和「本次参与成功」。"""
        rows = self.draw_list(start=True)
        if not rows:
            return 0
        path = f"{EP_PARK}"
        done = 0
        for it in rows:
            did = it.get('id')
            name = it.get('goodName') or f'抽奖{did}'
            part = it.get('partInNum') or 0
            # 注意：maxPartInNum 不是总人数上限（实测 partInNum=215 > maxPartInNum=100
            # 仍可参与），它更可能是中奖名额或每人次数上限。不做本地拦截，交给服务端判断。

            # 先查是否已参与
            mine = self._call(EP_MY_RECORD, 'POST', {'data': did})
            has_record = bool((mine.get('data') or [])) if not mine.get('_err') else False

            j = self._call(path, 'POST', {})
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
        """预约所有待开始的抽奖"""
        rows = self.draw_list(start=False)
        if not rows:
            return 0
        # 先查已预约的
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
        """检查所有参与过的抽奖，看开没开奖"""
        rows = self.draw_list(start=True) + self.draw_list(start=False)
        now = time.time()
        for it in rows:
            did = it.get('id')
            name = it.get('goodName') or f'抽奖{did}'
            j = self._call(EP_MY_RECORD, 'POST', {'data': did})
            if j.get('_err'):
                continue
            recs = j.get('data') or []
            if not recs:
                continue
            dd = recs[0].get('drawDay') or {}
            luck = dd.get('luckUser')
            open_time = dd.get('openTime') or ''
            # 判断是否已开奖：有 luckUser 或已过开奖时间
            try:
                ot = time.mktime(time.strptime(open_time, '%Y-%m-%d %H:%M:%S'))
            except Exception:
                ot = 0
            opened = bool(luck) or (ot and now > ot)
            if opened:
                self.results.append({
                    'id': did,
                    'name': name,
                    'luck': luck or '',
                    'open': open_time,
                })

    def run(self):
        print(f'\n{"=" * 52}')
        print(f'账号: {self.name}')
        print('=' * 52)

        p0 = self.get_points()
        if p0 is None:
            print('❌ 无法读取积分，Authorization 可能失效')
            return False
        print(f'💰 签到前积分: {p0}')

        # 签到
        ok, msg = self.sign()
        print(f'{"✅" if ok else "❌"} {msg}')
        time.sleep(1.5)

        # 参与抽奖
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

        # 预约
        nr = self.reserve()
        if self.reserved:
            print(f'\n📅 预约（新预约 {nr} 个）:')
            for x in self.reserved:
                st = '✅预约' if x.get('ok') else ('⏭ ' + x.get('skip', '') if x.get('skip') else '❌ ' + x.get('err', ''))
                print(f'   {x["name"]}: {st}')

        # 开奖结果
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
def parse_accounts(raw):
    out = []
    for line in raw.replace('&', '\n').splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [x.strip() for x in line.split('#') if x.strip()]
        auth = ''
        name = ''
        for x in parts:
            if len(x) >= 60 and all(c in '0123456789abcdefABCDEF' for c in x):
                auth = x
            elif not name:
                name = x
        if auth:
            out.append((name or auth[:10], auth))
    return out


def main():
    raw = (os.environ.get(ENV_TOKEN) or '').strip()
    if not raw:
        print(f'❌ 未设置环境变量 {ENV_TOKEN}')
        return 1
    accounts = parse_accounts(raw)
    if not accounts:
        print(f'❌ {ENV_TOKEN} 格式错误，应为 备注#Authorization值')
        return 1

    print('=' * 52)
    print(f'爱裹旧衣回收 · {len(accounts)} 个账号')
    print('=' * 52)

    reports = []
    for name, auth in accounts:
        app = AiGuo(auth, name)
        try:
            ok = app.run()
        except Exception as e:
            print(f'异常: {e}')
            ok = False
        reports.append({
            'name': name, 'ok': ok,
            'points': app.points,
            'signed': app.signed,
            'signed_already': app.signed_already,
            'participated': app.participated,
            'reserved': app.reserved,
            'results': app.results,
        })
        time.sleep(2)

    # 推送
    if PUSH_ENABLE:
        send = load_notify()
        if send:
            lines = []
            for r in reports:
                if not r['ok']:
                    lines.append(f"{r['name']}\n  ❌ 执行失败")
                    continue
                sign_txt = '已签到' if r['signed_already'] else '签到成功'
                lines.append(f"【{r['name']}】")
                lines.append(f"签到：{sign_txt}")
                lines.append(f"积分：{r['points']}")

                # 抽奖状态（全部列出）
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

                # 预约状态
                if r['reserved']:
                    new_r = [x for x in r['reserved'] if x.get('ok')]
                    old_r = [x for x in r['reserved'] if x.get('skip')]
                    if new_r:
                        lines.append(f"新预约：{len(new_r)} 个")
                        for x in new_r:
                            lines.append(f"  · {x['name']}")
                    if old_r:
                        lines.append(f"已预约：{len(old_r)} 个")

                # 开奖结果
                if r['results']:
                    lines.append("🏆 开奖：")
                    for x in r['results']:
                        lucky = '已开奖' if x['luck'] else '待公布'
                        lines.append(f"  · {x['name']}（{lucky}）")
            title = '爱裹回收 · 完成'
            try:
                send(title, '\n'.join(lines))
                print('\n推送已发送')
            except Exception as e:
                print(f'推送失败: {e}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
