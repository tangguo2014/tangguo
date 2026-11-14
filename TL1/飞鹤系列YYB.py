#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞鹤系列（两个小程序一次跑完，最后统一推送）

vx小程序 飞鹤 · 北纬47度好物 wx4205ec55b793245e
vx小程序 星妈会   wxc83b55d61c7fc51d 
      
YYB 取码 → accessToken，缓存复用失效再取码

定时：cron 0 8 * * * 每天一次或两次

变量：
  yyb_gpt = http://52.25.0.6:8800#1
            多账号：& 或换行分隔，可带备注  
            张三#http://ip:8800#1

  YYB_PROTOCOL_TOKEN   可选，YYB 开了协议鉴权才填
  DRYRUN=1             只打印账号解析，不联网

"""
import os
import re
import sys
import json
import time
import random
import hashlib
import requests
import threading

# ==================== 常量 ====================
# 飞鹤
FH_BASE = 'https://www.feihevip.com/api'
FH_APPID = 'xmyx'
FH_APPKEY = 'TwUQ01lKS1Km5zlV2f7amsZc5EQYkTbv'
FH_APPID_WX = 'wx4205ec55b793245e'
FH_REFERER = 'https://servicewechat.com/%s/366/page-frame.html' % FH_APPID_WX
FH_UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 15_8 like Mac OS X) AppleWebKit/605.1.15 '
         '(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.48(0x1800302b) NetType/4G Language/zh_CN')
FH_MOM = 'https://mom.feihe.com'

# 星妈会
XMH_BASE = 'https://momclub.feihe.com'
XMH_APPID = 'wxc83b55d61c7fc51d'
XMH_REFERER = 'https://servicewechat.com/%s/194/page-frame.html' % XMH_APPID
XMH_UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) AppleWebKit/605.1.15 '
          '(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.48(0x1800302b) NetType/4G Language/zh_CN')
CHECKIN_ID = 1111
# 星妈会可自动任务（实测 activityId）
XMH_TASKS = [
    (1139, '玩一玩连连看小游戏', 5),
    (1179, '阅览北纬47°好物小程序', 1),
    (1117, '观看《东北真行》', 10),
]
XMH_SKIP_TYPES = {'Perfect', 'FollowWx', 'AddQw', 'FirstOrder'}

YYB_TOKEN = (os.environ.get('YYB_PROTOCOL_TOKEN') or '').strip()
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'feihe_cache.json')


# ==================== 工具 ====================
def log(tag, *args):
    pre = '[%s] ' % tag if tag else ''
    print(pre + ' '.join(str(a) for a in args), flush=True)


def wait(sec):
    time.sleep(sec)


def rd(lo, hi, tag='', label=''):
    ms = random.randint(lo, hi)
    if label:
        log(tag, '%s，随机等待 %.1fs' % (label, ms / 1000.0))
    time.sleep(ms / 1000.0)


def mask(s):
    s = str(s or '')
    return s[:6] + '****' + s[-4:] if len(s) > 14 else (s[:2] + '****' if s else '(空)')


def load_cache():
    try:
        with open(CACHE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(c):
    try:
        with open(CACHE, 'w') as f:
            json.dump(c, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log('', '缓存写入失败:', e)


# ==================== 账号解析 ====================
def parse_env(env_name, need_ref):
    """need_ref=True: 地址#ref 格式；False: 备注#token 格式"""
    raw = (os.environ.get(env_name) or '').strip()
    if not raw:
        return []
    out = []
    for line in re.split(r'[&\n@]', raw):
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split('#')]
        if need_ref:
            dq = next((i for i, p in enumerate(parts) if p.startswith('http')), -1)
            if dq < 0 or len(parts) <= dq + 1:
                continue
            name = parts[0] if dq > 0 and parts[0] else '账号%d' % (len(out) + 1)
            out.append({'name': name, 'base': parts[dq].rstrip('/'), 'ref': parts[dq + 1]})
        else:
            if len(parts) >= 2:
                name, token = parts[0] or '账号%d' % (len(out) + 1), '#'.join(parts[1:])
            else:
                name, token = '账号%d' % (len(out) + 1), parts[0]
            token = re.sub(r'\s+', '', token)   # 去掉换行/空格（青龙粘贴常被折行）
            if token:
                # JWT 校验：必须 3 段且以 eyJ 开头
                seg = token.count('.')
                if not token.startswith('eyJ') or seg != 2:
                    log('', '⚠️ %s 的 token 格式异常（段数=%d，开头=%s），跳过' % (
                        name, seg + 1, token[:8]))
                    continue
                out.append({'name': name, 'token': token})
    return out


# ==================== 飞鹤签名 ====================
def _nonce(n=16):
    chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
    return ''.join(random.choice(chars) for _ in range(n))


def fh_sign(json_body=''):
    """sign = md5(fhAppid{appid}fhNonceStr{nonce}fhTimestamp{ts}{json}{appKey}).upper()"""
    nonce = _nonce()
    ts = int(time.time())
    s = 'fhAppid%s%s%s' % (FH_APPID, nonce, ts)
    s = 'fhAppid' + FH_APPID + 'fhNonceStr' + nonce + 'fhTimestamp' + str(ts) + (json_body or '') + FH_APPKEY
    return nonce, str(ts), hashlib.md5(s.encode()).hexdigest().upper()


# ==================== 飞鹤请求 ====================
def fh_req(token, path, method='GET', params=None, body=None, json_body=None, tag=''):
    """飞鹤统一请求"""
    url = FH_BASE + path
    if params:
        qs = '&'.join('%s=%s' % (k, v) for k, v in params.items())
        url += ('&' if '?' in url else '?') + qs
    nonce, ts, sign = fh_sign(json_body or '')
    headers = {
        'Host': 'www.feihevip.com',
        'token': token,
        'User-Agent': FH_UA,
        'Referer': FH_REFERER,
        'fhAppid': FH_APPID,
        'source': '1',
        'content-type': 'application/json',
        'fhNonceStr': nonce,
        'fhTimestamp': ts,
        'fhSign': sign,
    }
    try:
        if method == 'POST':
            data = json.dumps(body if body is not None else {}, ensure_ascii=False)
            r = requests.post(url, headers=headers, data=data, timeout=30)
        else:
            r = requests.get(url, headers=headers, timeout=30)
        try:
            return r.json()
        except Exception:
            return {'code': 'PARSE_ERR', 'msg': r.text[:150]}
    except Exception as e:
        return {'code': 'NET_ERR', 'msg': str(e)[:150]}


def _jwt_exp(token):
    """从 JWT 里读 exp（秒级时间戳），失败返回 None"""
    try:
        import base64 as _b64
        payload = token.split('.')[1]
        payload += '=' * (-len(payload) % 4)
        d = json.loads(_b64.urlsafe_b64decode(payload))
        return int(d.get('exp') or 0) or None
    except Exception:
        return None


def fh_get_token_by_code(code, tag=''):
    """GET /starMember/getUserToken?code=xxx → data.token
    实测 token 头可以为空，只需 fhSign 签名"""
    j = fh_req('', '/starMember/getUserToken', params={'code': code}, tag=tag)
    if not j:
        return None, '请求无返回'
    d = j.get('data') or {}
    tk = d.get('token')
    if tk:
        return tk, None
    return None, j.get('msg') or ('code=%s' % j.get('code'))


def fh_ok(j):
    return bool(j) and str(j.get('code')) == '200'


def fh_user(token, tag):
    j = fh_req(token, '/starMember/getMemberInfo', method='POST', json_body='{}', tag=tag)
    if not fh_ok(j):
        return None, j.get('msg') or str(j.get('code'))
    d = j.get('data') or {}
    score = (d.get('memberPoints') or {}).get('scoreValue')
    level = (d.get('memberGrade') or {}).get('currentGrade')
    nick = (d.get('baseInfo') or {}).get('nickName')
    mobile = ''
    for f in [(d.get('baseInfo') or {}).get('mobile'), (d.get('baseInfo') or {}).get('fullName'), d.get('mobile')]:
        if f and re.match(r'^1\d{10}$', str(f)):
            mobile = f
            break
    return {'score': score, 'level': level, 'nick': nick, 'mobile': mobile}, None


def fh_refresh_token(token, tag):
    """跑完刷新 token，延长有效期"""
    nonce, ts, sign = fh_sign()
    s = 'fhAppidxmhfhNonceStr' + nonce + 'fhTimestamp' + str(ts) + '98d9fe9b613a479dbcb111ca261e3ce1'
    sign2 = hashlib.md5(s.encode()).hexdigest().upper()
    headers = {
        'Host': 'mom.feihe.com', 'token': token, 'User-Agent': FH_UA,
        'Referer': FH_REFERER, 'fhAppid': 'xmh', 'source': '1',
        'fhNonceStr': nonce, 'fhTimestamp': str(ts), 'fhSign': sign2,
    }
    try:
        r = requests.get(FH_MOM + '/program/token/refreshToken', headers=headers, timeout=30)
        j = r.json()
        nt = j.get('data')
        if nt:
            log(tag, '🔄 token 已续期')
            return nt
        log(tag, '⚠️ token 续期返回为空')
    except Exception as e:
        log(tag, '⚠️ token 续期失败: %s' % str(e)[:80])
    return None


# ==================== 飞鹤主流程 ====================
def run_feihe(acc):
    tag = acc['name']
    out = {'name': tag, 'nick': None, 'sign': '-', 'tasks': '-', 'before': None, 'after': None,
           'fail': 0, 'lines': [], 'error': None}
    token = acc['token']
    lines = out['lines']

    u, err = fh_user(token, tag)
    if not u:
        log(tag, '❌ 用户信息获取失败: %s' % err)
        if '40001' in str(err) or '登录' in str(err):
            lines.append('❌ token 失效，请更新 fh_gpt')
        else:
            lines.append('❌ 用户信息获取失败：%s' % err)
        out['error'] = err
        return out

    who = u['nick'] or '未知'
    if u['mobile']:
        who += ' (%s)' % u['mobile']
    if u['level']:
        who += ' ｜ %s' % u['level']
    log(tag, '🙋 %s' % who)
    lines.append('🙋 %s' % who)
    out['nick'] = u['nick'] or u['mobile'] or tag
    out['before'] = u['score']
    log(tag, '💰 积分: %s' % u['score'])

    # 签到
    j = fh_req(token, '/member/signin/getSignInfo', params={'signType': 1}, tag=tag)
    if fh_ok(j) and j.get('data'):
        st = (j.get('data') or {}).get('signStatus')
        if st == 1:
            log(tag, '📅 签到：ℹ️ 今日已签到')
            lines.append('📅 签到：ℹ️ 今日已签到')
            out['sign'] = 'already'
        elif st == 2:
            rd(1000, 2500, tag)
            j2 = fh_req(token, '/member/signin/sign', method='POST', body={}, json_body='{}', tag=tag)
            if fh_ok(j2):
                log(tag, '📅 签到：✅ 成功')
                lines.append('📅 签到：✅ 成功')
                out['sign'] = 'ok'
            else:
                log(tag, '📅 签到：❌ %s' % (j2.get('msg') or j2.get('code')))
                lines.append('📅 签到：❌ %s' % (j2.get('msg') or j2.get('code')))
                out['sign'] = 'fail'
        else:
            log(tag, '📅 签到：⚠️ 状态未知 signStatus=%s' % st)
            lines.append('📅 签到：⚠️ 状态未知')
    else:
        log(tag, '📅 签到：⚠️ 状态查询失败 %s' % (j.get('msg') or j.get('code')))
        lines.append('📅 签到：⚠️ 状态查询失败')

    # 任务列表
    jt = fh_req(token, '/member/signin/getTaskList', tag=tag)
    tasks = (jt.get('data') or []) if fh_ok(jt) else []
    if not tasks:
        log(tag, '📝 任务列表为空或获取失败')
        lines.append('📝 任务：⚠️ 列表获取失败')
        out['tasks'] = 'fail'
    else:
        log(tag, '📝 任务列表 %d 项' % len(tasks))
        ok_c = skip_c = fail_c = 0
        for t in tasks:
            name = t.get('taskName') or '任务'
            ttype = t.get('taskType')
            total = t.get('totalCount')
            done = t.get('completedCount')
            if t.get('isCompleted') is True or (total is not None and done is not None and int(done) >= int(total)):
                log(tag, '  ℹ️ %s: 今日已完成' % name)
                skip_c += 1
                continue
            # 开始任务
            j1 = fh_req(token, '/member/signin/tofinish', params={'taskType': ttype}, tag=tag)
            if not fh_ok(j1):
                msg = j1.get('msg') or j1.get('code')
                if '已完成' in str(msg) or '重复' in str(msg):
                    log(tag, '  ℹ️ %s: 已完成' % name)
                    skip_c += 1
                else:
                    log(tag, '  ⚠️ %s: 开始失败 %s' % (name, msg))
                    fail_c += 1
                continue
            log(tag, '  🚀 %s' % name)
            dur = t.get('completeTaskDuration') or 3
            rd(int(dur) * 1000, int(dur) * 1000 + 1500, tag)
            # 完成任务
            j2 = fh_req(token, '/member/signin/completeTask', params={'taskType': ttype}, tag=tag)
            if fh_ok(j2):
                data = j2.get('data') or {}
                pt = data.get('awardSendPoints')
                if pt:
                    log(tag, '  ✅ %s +%s 积分' % (name, pt))
                else:
                    log(tag, '  ✅ %s 完成' % name)
                ok_c += 1
            else:
                msg = j2.get('msg')
                if '已完成' in str(msg) or '重复' in str(msg):
                    log(tag, '  ℹ️ %s: 已完成' % name)
                    skip_c += 1
                else:
                    log(tag, '  ⚠️ %s: 完成失败 %s' % (name, msg or j2.get('code')))
                    fail_c += 1
            rd(1000, 3000, tag)
        out['tasks'] = '%d/%d' % (ok_c + skip_c, len(tasks))
        out['fail'] = fail_c
        lines.append('📝 任务：%d/%d 完成%s' % (ok_c + skip_c, len(tasks),
                                            ('，%d 失败' % fail_c) if fail_c else ''))

    # 最终积分
    rd(1500, 2500, tag, '等待后查询积分')
    u2, _ = fh_user(token, tag)
    if u2:
        out['after'] = u2['score']
        gained = None
        try:
            gained = int(u2['score']) - int(out['before'])
        except Exception:
            pass
        log(tag, '💰 积分：%s → %s%s' % (out['before'], u2['score'],
                                       ('  (%+d)' % gained) if gained is not None else ''))
        lines.append('💰 积分：%s → %s%s' % (out['before'], u2['score'],
                                          ('  (%+d)' % gained) if gained is not None else ''))

    # 刷新 token
    nt = fh_refresh_token(token, tag)
    if nt:
        acc['newToken'] = nt
    return out


# ==================== YYB 取码（飞鹤/星妈会共用） ====================
def yyb_get_code(entry, app_id, retries=2):
    headers = {'Content-Type': 'application/json'}
    if YYB_TOKEN:
        headers['Authorization'] = 'Bearer ' + YYB_TOKEN
    last = ''
    for i in range(retries + 1):
        try:
            r = requests.post('%s/wxapp/getCode' % entry['base'], headers=headers,
                              json={'ref': str(entry['ref']), 'app_id': app_id}, timeout=40)
            d = r.json()
            data = d.get('data') or {}
            if data.get('circuit_open'):
                raise RuntimeError('YYB 熔断，需等 %s 秒' % data.get('retry_after', 600))
            inner = data.get('result') or d.get('result') or {}
            code = inner.get('code') or data.get('code')
            if code:
                return code
            last = '取码失败: %s' % json.dumps(d, ensure_ascii=False)[:200]
        except RuntimeError:
            raise
        except Exception as e:
            last = str(e)[:160]
        if re.search(r'熔断|code=-14|shortlink|授权已过期|409', last):
            raise RuntimeError(last)
        if i < retries:
            wait(3)
    raise RuntimeError(last or 'YYB 取码失败')


# ==================== 星妈会请求 ====================
OK_CODES = {'000000', '00000', '200', '0'}


def xmh_ok(j):
    if not j:
        return False
    return str(j.get('code')) in OK_CODES


def xmh_req(method, path, body=None, token=None, raw_body=None, timeout=30):
    headers = {
        'content-type': 'application/json',
        'locale': 'zh_CN',
        'X-Member-Account-Version': 'V3',
        'User-Agent': XMH_UA,
        'Referer': XMH_REFERER,
    }
    if token:
        headers['Authorization'] = token
    url = XMH_BASE + path
    try:
        if method == 'GET':
            r = requests.get(url, headers=headers, timeout=timeout)
        else:
            data = raw_body if raw_body is not None else (
                json.dumps(body, ensure_ascii=False) if body is not None else None)
            r = requests.post(url, headers=headers, data=data, timeout=timeout)
        try:
            return r.json()
        except Exception:
            return {'code': 'PARSE_ERR', 'msg': r.text[:150]}
    except Exception as e:
        return {'code': 'NET_ERR', 'msg': str(e)[:150]}


def xmh_login(entry, tag):
    code = yyb_get_code(entry, XMH_APPID)
    log(tag, '取 wx.login code 成功: %s' % mask(code))
    j = xmh_req('POST', '/capis/social/ma', raw_body=code)
    if not xmh_ok(j):
        raise RuntimeError('登录失败: %s' % (j.get('msg') or json.dumps(j, ensure_ascii=False)[:150]))
    data = j.get('data') or {}
    ti = data.get('tokenInfo') or {}
    token = ti.get('accessToken')
    if not token:
        raise RuntimeError('登录未返回 accessToken')
    log(tag, '✅ 登录成功，token 有效期 %d 天' % int((ti.get('expiresIn') or 604800) / 86400))
    return token, int(ti.get('expiresIn') or 604800)


def xmh_score(token):
    j = xmh_req('GET', '/capis/c/user/memberInfo', token=token)
    if not xmh_ok(j):
        return None, None, j.get('msg') or j.get('code')
    d = j.get('data') or {}
    return d.get('points'), d.get('crmId'), None


# ==================== 星妈会主流程 ====================
def run_xmh(acc, cache):
    tag = acc['name']
    out = {'name': tag, 'nick': None, 'sign': '-', 'tasks': '-', 'before': None, 'after': None,
           'fail': 0, 'lines': [], 'error': None}
    lines = out['lines']
    key = '%s#%s' % (acc['base'], acc['ref'])
    entry = {'base': acc['base'], 'ref': acc['ref']}

    # token 准备
    cached = cache.get(key) or {}
    token = cached.get('token')
    if token and cached.get('expiresAt', 0) > time.time() + 3600:
        log(tag, '使用缓存 token（剩 %d 小时）' % int((cached['expiresAt'] - time.time()) / 3600))
        pts, crm, err = xmh_score(token)
        if pts is not None:
            log(tag, '✅ 缓存 token 校验通过')
        else:
            log(tag, '缓存 token 失效，重新登录')
            token = None
    else:
        token = None

    if not token:
        token, exp = xmh_login(entry, tag)
        cache[key] = {'token': token, 'savedAt': int(time.time()), 'expiresAt': int(time.time()) + exp}
        save_cache(cache)

    pts, crm, err = xmh_score(token)
    if pts is not None:
        out['before'] = pts
        log(tag, '💰 签到前积分: %s' % pts)
    else:
        log(tag, '⚠️ 积分查询失败: %s' % err)

    # 用户信息
    ju = xmh_req('GET', '/capis/c/user/memberInfo', token=token)
    if xmh_ok(ju):
        d = ju.get('data') or {}
        who = d.get('nickName') or d.get('memberName') or '未知'
        if d.get('mobile'):
            who += ' (%s)' % d['mobile']
        log(tag, '🙋 %s' % who)
        lines.append('🙋 %s' % who)
        out['nick'] = d.get('nickName') or d.get('mobile') or tag

    # 任务列表
    jt = xmh_req('GET', '/capis/c/activity/todo/list?mockTime=%d' % int(time.time() * 1000), token=token)
    if not xmh_ok(jt):
        log(tag, '❌ 任务列表获取失败: %s' % (jt.get('msg') or jt.get('code')))
        lines.append('❌ 任务列表获取失败')
        out['error'] = jt.get('msg')
        return out
    todo = jt.get('data') or {}

    # 签到
    ck = todo.get('checkInTodo') or {}
    extra = ck.get('checkInExtra') or {}
    records = extra.get('joinRecord') or []
    today = next((r for r in records if r.get('today')), None)
    if today and today.get('joined'):
        log(tag, '📅 签到：ℹ️ 今日已签到（第 %s 天）' % today.get('index'))
        lines.append('📅 签到：ℹ️ 今日已签到')
        out['sign'] = 'already'
    else:
        log(tag, '📅 签到：今日未签，开始签到…')
        rd(1500, 3000, tag)
        jc = xmh_req('POST', '/capis/c/activity/todo/checkIn',
                     {'activityId': CHECKIN_ID, 'mockTime': int(time.time() * 1000)}, token=token)
        if xmh_ok(jc):
            cr = (jc.get('data') or {}).get('credits')
            log(tag, '📅 签到：✅ 成功 +%s' % cr)
            lines.append('📅 签到：✅ +%s' % cr)
            out['sign'] = 'ok'
        else:
            log(tag, '📅 签到：❌ %s' % (jc.get('msg') or jc.get('code')))
            lines.append('📅 签到：❌ %s' % (jc.get('msg') or jc.get('code')))
            out['sign'] = 'fail'

    # 三个任务
    done_map = {}
    for t in todo.get('taskTodo') or []:
        ex = t.get('taskTodoExtra') or {}
        done_map[t.get('id')] = (ex.get('type'), ex.get('completeCount'), ex.get('completeLimit'))

    ok_c = skip_c = fail_c = 0
    task_lines = []
    for aid, name, pt in XMH_TASKS:
        info = done_map.get(aid)
        if info:
            typ, cnt, lim = info
            if cnt is not None and lim is not None and int(cnt) >= int(lim):
                log(tag, '  ℹ️ %s: 今日已完成' % name)
                task_lines.append('  ℹ️ %s' % name)
                skip_c += 1
                continue
        log(tag, '  🚀 %s' % name)
        j1 = xmh_req('POST', '/capis/c/activity/todo/complete',
                     {'activityId': aid, 'mockTime': int(time.time() * 1000)}, token=token)
        if not xmh_ok(j1):
            msg = j1.get('msg') or j1.get('code')
            if '已完成' in str(msg) or '重复' in str(msg):
                log(tag, '  ℹ️ %s: 已完成' % name)
                task_lines.append('  ℹ️ %s' % name)
                skip_c += 1
            else:
                log(tag, '  ⚠️ %s: 开始失败 %s' % (name, msg))
                task_lines.append('  ⚠️ %s 失败' % name)
                fail_c += 1
            continue
        # 关键：间隔 6~7 秒，太短服务端不计分
        rd(6000, 7000, tag, '  等待任务结算')
        j2 = xmh_req('POST', '/capis/c/activity/todo/receive',
                     {'activityId': aid, 'mockTime': int(time.time() * 1000)}, token=token)
        if xmh_ok(j2):
            log(tag, '  ✅ %s +%s' % (name, pt))
            task_lines.append('  ✅ %s +%s' % (name, pt))
            ok_c += 1
        else:
            msg = j2.get('msg') or j2.get('code')
            log(tag, '  ⚠️ %s: 领奖失败 %s' % (name, msg))
            task_lines.append('  ⚠️ %s 领奖失败' % name)
            fail_c += 1
        rd(2000, 4000, tag)

    out['tasks'] = '%d/%d' % (ok_c + skip_c, len(XMH_TASKS))
    out['fail'] = fail_c
    if task_lines:
        lines.extend(task_lines)
        lines.append('📝 任务：%d/%d%s' % (ok_c + skip_c, len(XMH_TASKS),
                                       ('，%d 失败' % fail_c) if fail_c else ''))

    # 最终积分
    rd(1500, 3000, tag, '等待后查询积分')
    after, _, _ = xmh_score(token)
    if after is not None:
        out['after'] = after
        gained = None
        try:
            gained = int(after) - int(out['before'])
        except Exception:
            pass
        log(tag, '💰 积分：%s → %s%s' % (out['before'], after, ('  (%+d)' % gained) if gained else ''))
        lines.append('💰 积分：%s → %s%s' % (out['before'], after, ('  (%+d)' % gained) if gained else ''))
    else:
        log(tag, '⚠️ 积分查询失败')
    return out


# ==================== 推送 ====================
NOTIFY_TITLE = '飞鹤系签到 🌟'


def notify(title, content, timeout=60):
    """青龙 notify.py，最多等 timeout 秒；超时或失败都直接放弃，绝不让主程序卡住"""
    result = {'ok': False, 'err': None}

    def _run():
        try:
            from notify import send
            send(title, content)
            result['ok'] = True
            return
        except ImportError:
            pass
        except Exception as e:
            result['err'] = str(e)[:120]
            return
        # 退回按路径加载
        for p in ('/ql/scripts/notify.py', '/ql/data/scripts/notify.py'):
            if not os.path.exists(p):
                continue
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location('qlnotify', p)
                m = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(m)
                m.send(title, content)
                result['ok'] = True
                return
            except Exception as e:
                result['err'] = '%s: %s' % (p, str(e)[:100])

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        log('', '⚠️ 推送超时（%ds），已放弃，主程序继续退出' % timeout)
        return False
    if result['ok']:
        log('', '✅ 推送已发送')
        return True
    log('', '⚠️ 推送未发送：%s' % (result['err'] or '未找到 notify.py'))
    return False


def _short_name(r):
    """推送里用短名：优先昵称，太长就截断"""
    n = r.get('nick') or r.get('name') or '账号'
    n = str(n).strip()
    return n[:12]


def _one_line(r):
    """单账号一行：签到  名字  任务  ⭐积分 (+增)"""
    seg = []
    if r.get('sign') == 'ok':
        seg.append('✅')
    elif r.get('sign') == 'fail':
        seg.append('❌')
    else:
        seg.append('·')
    seg.append(_pad(_short_name(r), 14))
    t = r.get('tasks') if r.get('tasks') not in (None, '-') else '—'
    seg.append(_pad(t, 6))
    after, before = r.get('after'), r.get('before')
    if after is not None:
        s_pt = '⭐%s' % after
        try:
            g = int(after) - int(before)
            if g > 0:
                s_pt += ' (+%d)' % g
        except Exception:
            pass
        seg.append(s_pt)
    else:
        seg.append('⭐—')
    return '  '.join(seg)


def _pad(s, width):
    """按显示宽度补空格（中文算 2 格）"""
    s = str(s)
    w = sum(2 if ord(c) > 127 else 1 for c in s)
    return s + ' ' * max(0, width - w)


def build_report(fh_results, xmh_results):
    parts = []
    total_gain = 0
    warnings = []

    for label, results in (('【飞鹤 · 北纬47度好物】', fh_results), ('【星妈会】', xmh_results)):
        if not results:
            continue
        parts.append(label)
        for r in results:
            parts.append(_one_line(r))
            try:
                if r.get('before') is not None and r.get('after') is not None:
                    total_gain += int(r['after']) - int(r['before'])
            except Exception:
                pass
            if r.get('error'):
                warnings.append('%s：%s %s' % (label.strip('【】'), _short_name(r), str(r['error'])[:40]))
        parts.append('')

    parts.append('📊 今日 %+d' % total_gain)
    for w in warnings[:3]:
        parts.append('⚠️ ' + w)
    return '\n'.join(parts)


# ==================== 主流程 ====================
def main():
    yyb_accounts = parse_env('yyb_gpt', need_ref=True)

    if not yyb_accounts:
        log('', '未设置环境变量：yyb_gpt = 地址#ref（多账号 & 或换行）')
        return

    if os.environ.get('DRYRUN') == '1':
        print('yyb_gpt 共 %d 个账号:' % len(yyb_accounts))
        for a in yyb_accounts:
            print('  - %s | %s#%s' % (a['name'], a['base'], a['ref']))
        print('\n执行顺序：飞鹤（appid wx4205ec…）→ 间隔 30~60s → 星妈会（appid wxc83b55…）')
        return

    cache = load_cache()
    fh_results, xmh_results = [], []

    # ---- ① 飞鹤（YYB 取码 → getUserToken → 签到+任务） ----
    log('', '=' * 50)
    log('', '🐦 飞鹤 · 北纬47度好物（%d 个账号）' % len(yyb_accounts))
    log('', '=' * 50)
    for i, a in enumerate(yyb_accounts):
        name = a['name']
        try:
            ck = 'fh::%s#%s' % (a['base'], a['ref'])
            cached = cache.get(ck) or {}
            tk = cached.get('token')
            # 飞鹤 token 4 小时有效，留 20 分钟余量
            if tk and cached.get('expiresAt', 0) > time.time() + 1200:
                log(name, '使用缓存 token（剩 %d 分钟）' % int((cached['expiresAt'] - time.time()) / 60))
                u, _ = fh_user(tk, name)
                if not u:
                    log(name, '缓存 token 失效，重新取码')
                    tk = None
                else:
                    log(name, '✅ 缓存 token 校验通过')
            else:
                tk = None

            if not tk:
                log(name, '🔑 YYB 取码中…（appid %s）' % FH_APPID_WX)
                code = yyb_get_code(a, FH_APPID_WX)
                log(name, '取 code 成功: %s' % mask(code))
                tk, err = fh_get_token_by_code(code, name)
                if not tk:
                    raise RuntimeError('换取 token 失败: %s' % err)
                log(name, '✅ 换取 token 成功')
                # 解析 JWT exp 决定缓存时长
                exp_ts = _jwt_exp(tk)
                ttl = max(600, (exp_ts - int(time.time())) if exp_ts else 4 * 3600)
                cache[ck] = {'token': tk, 'savedAt': int(time.time()),
                             'expiresAt': int(time.time()) + ttl}
                save_cache(cache)

            acc = {'name': name, 'token': tk}
            r = run_feihe(acc)
            fh_results.append(r)
        except Exception as e:
            log(name, '❌ 执行异常: %s' % e)
            fh_results.append({'name': name, 'lines': ['❌ %s' % str(e)[:120]],
                               'before': None, 'after': None})
        if i < len(yyb_accounts) - 1:
            rd(8000, 15000, '')

    # ---- ② 间隔，避开同一微信连续取码 ----
    if yyb_accounts:
        gap = random.randint(30, 60)
        log('', '⏳ 与星妈会间隔 %ds（避免同一微信连续取码）…' % gap)
        wait(gap)

    # ---- ③ 星妈会（7 天 token 缓存，多数情况不取码） ----
    log('', '=' * 50)
    log('', '🌟 星妈会（%d 个账号）' % len(yyb_accounts))
    log('', '=' * 50)
    for i, a in enumerate(yyb_accounts):
        name = a['name']
        try:
            r = run_xmh(a, cache)
            xmh_results.append(r)
        except Exception as e:
            log(name, '❌ 执行异常: %s' % e)
            xmh_results.append({'name': name, 'lines': ['❌ %s' % str(e)[:120]],
                                'before': None, 'after': None})
        if i < len(yyb_accounts) - 1:
            rd(8000, 15000, '')

    # ---- 推送 ----
    text = build_report(fh_results, xmh_results)
    print('\n' + '=' * 50)
    print(text)
    print('=' * 50)
    if '--no-push' not in sys.argv:
        notify(NOTIFY_TITLE, text)


if __name__ == '__main__':
    main()
