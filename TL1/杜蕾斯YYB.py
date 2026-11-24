#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
vx小程序 杜蕾斯· 每日签到 + 抽奖
================================================================================

【变量】
    yyb_gpt = YYB地址#ref
    例：http://525.36.0.1:8000#1
    多账号：& 或换行分隔
    连续签到7天奖励 2/2/10/2/2/2/20

【定时】 
   cron: 30 9 * * *
   每天一次，token有效期30分钟所以未加入缓存
  
【奖池】
     5元代金券 1880 / 5积分 2127 / 5元优惠券 1726 / 20积分 690
    50积分 350 / 100积分 77 / 玻尿酸超薄3只 40 / 玻尿酸水润2只 33

================================================================================
"""
import os
import re
import sys
import json
import time
import random
import threading
import requests

ENV_NAME = 'yyb_gpt'
APP_ID = 'wxf6eb57b9b700b359'
BASE = 'https://mall-api.ixiliu.cn'
YYB_TOKEN = (os.environ.get('YYB_PROTOCOL_TOKEN') or '').strip()
DRY = (os.environ.get('YYB_DRY') or '0').strip() == '1'
UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) AppleWebKit/605.1.15 '
      '(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.48(0x1800302b) NetType/WIFI Language/zh_CN')
REFERER = 'https://servicewechat.com/%s/192/page-frame.html' % APP_ID

_ACC = None


def log(*a):
    tag = '[%s] ' % _ACC['name'] if _ACC else ''
    print(tag + ' '.join(str(x) for x in a), flush=True)


def rd(lo=800, hi=2000):
    time.sleep(random.randint(lo, hi) / 1000.0)


def mask(s):
    s = str(s or '')
    return s[:6] + '****' + s[-4:] if len(s) > 14 else (s or '(空)')


# ==================== 账号解析 ====================
def parse_entries():
    raw = (os.environ.get(ENV_NAME) or '').strip()
    if not raw:
        return []
    out = []
    for line in re.split(r'[&\n]+', raw):
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split('#')]
        dq = next((i for i, p in enumerate(parts) if p.startswith('http')), -1)
        if dq < 0 or len(parts) <= dq + 1:
            continue
        name = parts[0] if dq > 0 and parts[0] else '账号%d' % (len(out) + 1)
        out.append({'name': name, 'base': parts[dq].rstrip('/'), 'ref': parts[dq + 1]})
    return out


# ==================== YYB 取码 ====================
def yyb_get_code(entry, retries=2):
    headers = {'Content-Type': 'application/json'}
    if YYB_TOKEN:
        headers['Authorization'] = 'Bearer ' + YYB_TOKEN
    last = ''
    for i in range(retries + 1):
        try:
            r = requests.post('%s/wxapp/getCode' % entry['base'], headers=headers,
                              json={'ref': str(entry['ref']), 'app_id': APP_ID}, timeout=40)
            raw = r.text
            d = r.json()
            data = d.get('data') or {}
            if data.get('circuit_open') or str(d.get('code')) == '429':
                raise RuntimeError('YYB 熔断，需等 %s 秒' % (data.get('retry_after') or 600))
            if '-14' in raw or 'shortlink' in raw.lower() or data.get('request_rejected'):
                raise RuntimeError('微信拒绝取码(-14)：需在 YYB 重新扫码')
            if str(d.get('code')) == '403' or '取码已拒绝' in raw or '自动禁用' in raw:
                raise RuntimeError('取码被 YYB 拒绝(403)：appid 已被自动禁用\n'
                                   '    → 去 YYB 面板解除，或等待自动恢复')
            if str(d.get('code')) == '404':
                raise RuntimeError('ref=%s 在 YYB 中不存在' % entry['ref'])
            inner = data.get('result') or d.get('result') or {}
            code = inner.get('code') or data.get('code') or d.get('code')
            if code and isinstance(code, str):
                return code
            last = '取码失败: %s' % json.dumps(d, ensure_ascii=False)[:180]
        except RuntimeError:
            raise
        except Exception as e:
            last = str(e)[:150]
        if i < retries:
            time.sleep(3)
    raise RuntimeError(last or 'YYB 取码失败')


# ==================== HTTP ====================
def headers(token=None):
    h = {
        'Content-Type': 'application/json;charset=UTF-8',
        'Accept': '*/*',
        'Accept-Language': 'zh-CN,zh-Hans;q=0.9',
        'User-Agent': UA,
        'Referer': REFERER,
        'platform': 'WechatMiniProgram',
        'terminal': '10',
        'tenant-id': '1',
        'X-Cdp-Login-State': '',
    }
    if token:
        h['Authorization'] = token
    return h


def api(token, path, body=None, method='GET', timeout=20, params=None):
    url = BASE + path
    try:
        if method.upper() == 'GET':
            r = requests.get(url, headers=headers(token), params=params, timeout=timeout)
        else:
            r = requests.post(url, headers=headers(token), params=params,
                              data=json.dumps(body if body is not None else {}, ensure_ascii=False),
                              timeout=timeout)
        return r.json()
    except Exception as e:
        return {'code': 'EXC', 'msg': str(e)[:150]}


def ok(j):
    return bool(j) and str(j.get('code')) == '0'


# ==================== 业务 ====================
def login(acc):
    """wx code → token"""
    code = yyb_get_code(acc['entry'])
    log('   🔑 取码成功: %s' % mask(code))
    # 2026-11-23 实测：真实登录用的是 social-login (type=34)
    j = api(None, '/app-api/member/auth/social-login',
            {'type': 34, 'code': code, 'state': 'default'}, method='POST')
    if not ok(j):
        raise RuntimeError('登录失败: code=%s msg=%s' % (j.get('code'), j.get('msg')))
    d = j.get('data') or {}
    token = d.get('accessToken') or d.get('token')
    if not token:
        raise RuntimeError('登录未返回 accessToken')
    acc['token'] = token
    acc['userId'] = d.get('userId')
    log('   ✅ 登录成功 userId=%s token=%s' % (d.get('userId'), mask(token)))
    return token


def get_user(acc):
    j = api(acc['token'], '/app-api/member/user/get')
    return (j.get('data') or {}) if ok(j) else {}


def get_sign_config(acc):
    j = api(acc['token'], '/app-api/member/sign-in/config/list')
    return (j.get('data') or []) if ok(j) else []


def get_sign_summary(acc):
    j = api(acc['token'], '/app-api/member/sign-in/record/get-summary')
    return (j.get('data') or {}) if ok(j) else {}


def do_sign(acc):
    j = api(acc['token'], '/app-api/member/sign-in/record/create', {}, method='POST')
    return j


def get_lottery_detail(acc, act_id=6):
    j = api(acc['token'], '/app-api/promotion/lottery/detail/%d' % act_id)
    return (j.get('data') or {}) if ok(j) else {}


def do_lottery(acc, act_id=6):
    """真实抽奖接口：POST /promotion/lottery/draw  body {"activityId":6}"""
    j = api(acc['token'], '/app-api/promotion/lottery/draw',
            {'activityId': act_id}, method='POST')
    return j


def get_draw_logs(acc, act_id=6, size=10):
    """中奖记录"""
    j = api(acc['token'], '/app-api/promotion/lottery/my-draw-log-page',
            params={'pageNo': 1, 'pageSize': size, 'activityId': act_id})
    return (j.get('data') or {}) if ok(j) else {}


def get_point_records(acc, size=10):
    j = api(acc['token'], '/app-api/member/point/record/page',
            params={'pageNo': 1, 'pageSize': size})
    return (j.get('data') or {}) if ok(j) else {}


# ==================== 单账号 ====================
def run_account(acc):
    out = {'name': acc['name'], 'before': 0, 'after': 0, 'earned': 0,
           'sign': '-', 'lottery': '-', 'error': None, 'lines': [],
           'sign_point': 0, 'prize': None, 'prize_point': 0}

    log('👤 账号：%s' % acc['name'])
    log('')

    # ---------- 1. 登录 ----------
    log('🔐 [1/5] 微信登录')
    login(acc)
    log('')

    # ---------- 2. 用户信息 ----------
    log('👤 [2/5] 查询用户信息')
    u = get_user(acc)
    if not u:
        out['error'] = '用户信息获取失败'
        log('   ❌ 获取失败')
        return out
    point = u.get('point') or 0
    level = (u.get('level') or {}).get('name') or '-'
    out['before'] = point
    log('   昵称：%s' % (u.get('nickname') or '-'))
    log('   会员：%s' % level)
    log('   积分：%s' % point)
    log('')

    # ---------- 3. 签到 ----------
    log('📅 [3/5] 每日签到')
    cfg = get_sign_config(acc)
    if cfg:
        reward_str = ' / '.join('%d天%d分' % (c['day'], c['point']) for c in cfg)
        log('   7天奖励：%s' % reward_str)
    summ = get_sign_summary(acc)
    today = summ.get('todaySignIn')
    cont = summ.get('continuousDay') or 0
    total = summ.get('totalDay') or 0
    log('   状态：%s ｜ 连签 %s 天 ｜ 累计 %s 天' % (
        '今日已签' if today else '今日未签', cont, total))

    if today:
        out['sign'] = 'already'
        log('   ℹ️ 今日已签到，跳过')
    elif DRY:
        out['sign'] = 'dry'
        log('   🔒 只读模式，跳过签到')
    else:
        rd()
        log('   ▶️ 执行签到…')
        j = do_sign(acc)
        code, msg = j.get('code'), j.get('msg') or ''
        if str(code) == '0':
            out['sign'] = 'ok'
            d = j.get('data') or {}
            got = d.get('point') or 0
            day = d.get('day') or 0
            log('   ✅ 签到成功 第%s天 +%s积分 %s' % (day, got, msg))
            out['sign_point'] = got
        elif str(code) == '1004010000' or '已签到' in msg:
            out['sign'] = 'already'
            log('   ℹ️ 今日已签到：%s' % msg)
        else:
            out['sign'] = 'fail'
            log('   ❌ 签到失败 code=%s msg=%s' % (code, msg))
    log('')

    # ---------- 4. 抽奖 ----------
    log('🎰 [4/5] 会员抽奖（杜蕾斯）')
    detail = get_lottery_detail(acc, 6)
    if detail:
        log('   活动：%s' % detail.get('activityName'))
        log('   限制：每日 %s 次' % detail.get('drawLimitCount'))
        prizes = detail.get('prizes') or []
        if prizes:
            log('   奖品池 %d 种：%s' % (
                len(prizes),
                '、'.join('%s×%s' % (p.get('prizeName'), p.get('prizeCount')) for p in prizes[:4]) + '…'))

    if DRY:
        out['lottery'] = 'dry'
        log('   🔒 只读模式，跳过抽奖')
    else:
        rd()
        log('   ▶️ 执行抽奖…')
        j = do_lottery(acc)
        code, msg = j.get('code'), j.get('msg') or ''
        if str(code) == '0':
            out['lottery'] = 'ok'
            d = j.get('data') or {}
            prize = d.get('prizeName') or '未知'
            ptype = d.get('prizeType')
            pts = d.get('pointAmount')
            log('   🎁 中奖结果：%s%s' % (prize, ('（+%s积分）' % pts) if pts else ''))
            out['prize'] = prize
            out['prize_point'] = pts or 0
        elif '次数' in msg or '已完成' in msg or '上限' in msg or '不足' in msg:
            out['lottery'] = 'already'
            log('   ℹ️ 今日已抽过：%s' % msg)
        else:
            out['lottery'] = 'fail'
            log('   ❌ 抽奖失败 code=%s msg=%s' % (code, msg))
    log('')

    # ---------- 5. 复查 ----------
    log('💰 [5/5] 复查积分')
    rd(1200, 2200)
    u2 = get_user(acc)
    after = u2.get('point')
    if after is None:
        after = point
    out['after'] = after
    out['earned'] = after - point
    log('   积分：%s → %s  (%s%s)' % (
        point, after, '+' if out['earned'] >= 0 else '', out['earned']))

    # 积分明细
    rec = get_point_records(acc, 5)
    lst = rec.get('list') or []
    if lst:
        log('   最近明细：')
        for r in lst[:5]:
            log('     · %s  %s  %+d' % (
                time.strftime('%m-%d %H:%M', time.localtime((r.get('createTime') or 0) / 1000)),
                r.get('title'), r.get('point') or 0))
    return out


# ==================== 推送 ====================
NOTIFY_TITLE = '杜蕾斯签到 🎁'


def notify(title, content, timeout=15):
    """推送：青龙 QYWX_KEY 企业微信机器人（照 一点万象.py 写法）
       不依赖青龙 notify.py，避免多渠道并发卡死
    """
    key = os.environ.get('QYWX_KEY') or ''
    if not key:
        log('⚠️ 提示：未在环境变量配置 QYWX_KEY，跳过通知发送。')
        return False

    body = {
        'msgtype': 'text',
        'text': {
            'content': '🔔 %s\n%s\n%s\n\n统计时间：%s' % (
                title, '-' * 20, content, time.strftime('%m-%d %H:%M:%S'))
        }
    }
    try:
        r = requests.post(
            'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=%s' % key,
            data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            timeout=timeout)
        j = r.json()
        if j.get('errcode') == 0:
            log('🚀 企业微信机器人推送成功！')
            return True
        log('❌ 推送失败: %s' % j.get('errmsg'))
    except Exception as e:
        log('💥 推送异常: %s' % e)
    return False


def build_report(results):
    """推送格式：
       【杜蕾斯】
       今日积分：+7
         签到 +2 ｜ 抽奖 5积分
       总积分：7
    """
    if not results:
        return ''
    lines = ['【杜蕾斯】']

    total_point = 0
    total_earn = 0
    details = []

    for r in results:
        if r.get('error'):
            lines.append('❌ %s：%s' % (r['name'], str(r['error'])[:24]))
            continue
        total_point += int(r.get('after') or 0)
        total_earn += int(r.get('earned') or 0)

        # 明细：签到 + 抽奖
        seg = []
        sp = r.get('sign_point')
        if sp:
            seg.append('签到 +%s' % sp)
        prize = r.get('prize')
        pp = r.get('prize_point')
        sl = r.get('sign')
        if prize:
            seg.append('抽奖 %s' % prize)
        elif sl == 'already' or r.get('lottery') == 'already':
            sega = []
            if r.get('sign') == 'already':
                sega.append('签到已做')
            if r.get('lottery') == 'already':
                sega.append('抽奖已做')
            if sega:
                seg.append('、'.join(sega))
        if seg:
            details.append((r['name'] if len(results) > 1 else '', ' ｜ '.join(seg)))

    lines.append('今日积分：%s%s' % ('+' if total_earn >= 0 else '', total_earn))
    for who, txt in details:
        prefix = ('%s：' % who) if who else '  '
        lines.append('  %s%s' % (prefix, txt))
    lines.append('总积分：%s' % format(total_point, ','))
    return '\n'.join(lines)


# ==================== 主流程 ====================
def main():
    global _ACC
    entries = parse_entries()
    if not entries:
        log('未设置或格式错误：%s = YYB地址#ref' % ENV_NAME)
        return

    log('共 %d 个账号 ｜ 模式 %s' % (len(entries), '只读' if DRY else '正常'))
    results = []
    for i, e in enumerate(entries):
        acc = {'name': e['name'], 'entry': e, 'ref': e['ref']}
        _ACC = acc
        log('=' * 50)
        log('📱 账号 %d/%d' % (i + 1, len(entries)))
        log('=' * 50)
        try:
            results.append(run_account(acc))
        except Exception as ex:
            log('❌ 执行异常: %s' % ex)
            results.append({'name': acc['name'], 'error': str(ex)[:60],
                            'sign': '-', 'lottery': '-', 'before': 0, 'after': 0, 'earned': 0})
        if i < len(entries) - 1:
            time.sleep(5)

    text = build_report(results)
    print('\n' + '=' * 50)
    print(text)
    print('=' * 50)
    notify(NOTIFY_TITLE, text)


if __name__ == '__main__':
    try:
        main()
    finally:
        sys.stdout.flush()
        os._exit(0)
