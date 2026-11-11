# -*- coding: utf-8 -*-
"""
vx小程序  金豆助手（wx16b9cdfa10a713f2）· 签到 + 看视频 + 答题 + 收益
================================================================

【变量】
  yyb_gpt     
  http://256.25.36.1:8000#1
  yyb登录取code换token，之后所有请求的签名 key + Authorization
  多账号换行或&分割
  单位：1000 金豆 = 1 元，最低提现 0.1 元

【签名】
  message = "\n".join([METHOD, PATH, sha256hex(body), ts, nonce, device_id])
  X-Mini-Request-Sign   = HMAC-SHA256(key = JWT token )
  X-Reward-Sign         = HMAC-SHA256(key = reward_session.

"""

import base64
import hashlib
import hmac
import json
import os
import random
import re
import string
import sys
import time
import traceback

try:
    import requests
except ImportError:
    requests = None

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# ---------------------------------------------------------------- 常量
APPID = 'wx16b9cdfa10a713f2'
API_BASE = 'https://golden.yifanglimin.com'
REFERER = 'https://servicewechat.com/%s/11/page-frame.html' % APPID
UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) '
      'AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 '
      'MicroMessenger/8.0.76(0x18004c3a) NetType/4G Language/zh_CN')

ID_ALPHABET = string.ascii_letters + string.digits + '_-'
TOKEN_SKEW = 300

STATUS_HINT = {
    40100: '登录态失效',
    40310: '账号被风控限制',
    40320: '签名校验失败',
    40321: '未通过金豆规则门禁',
    40322: '需先同意用户协议',
}

# 需要 reward 签名的接口
REWARD_PATHS = {
    '/api/sign/claim',
    '/api/sign/makeup/start',
    '/api/sign/makeup/complete',
    '/api/ads/start',
    '/api/ads/complete',
    '/api/daily-tasks/claim',
}
ARTICLE_REWARD_SUFFIX = ('/start', '/heartbeat', '/quiz', '/answer',
                         '/double/start', '/double/complete')

CACHE_NAME = 'jdzx_session.json'
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(SCRIPT_DIR, CACHE_NAME)
ANSWER_DB = os.path.join(SCRIPT_DIR, 'jdzx_answers.json')

WATCH_MIN, WATCH_MAX = 17, 25
READ_MIN, READ_MAX = 32, 45
ARTICLE_TARGET = 5        # 每日任务要求 5 篇


# ---------------------------------------------------------------- 基础工具
def log(msg=''):
    print(msg)


def rand_id(n):
    return ''.join(random.choice(ID_ALPHABET) for _ in range(n))


def norm_path(path):
    p = str(path or '').split('?')[0]
    if not p.startswith('/'):
        p = '/' + p
    if p.startswith('/api/') or p == '/api':
        return p
    return '/api/' + p.lstrip('/')


def needs_reward(path):
    p = norm_path(path)
    if p in REWARD_PATHS:
        return True
    return '/articles/' in p and p.endswith(ARTICLE_REWARD_SUFFIX)


def build_sign(method, path, payload, device_id, key):
    """签名核心，与实测完全一致"""
    m = str(method or 'GET').upper()
    body = '' if m in ('GET', 'HEAD') else (payload or '')
    bsha = hashlib.sha256(body.encode('utf-8')).hexdigest()
    ts = str(int(time.time()))
    nonce = rand_id(24)
    msg = '\n'.join([m, norm_path(path), bsha, ts, nonce, str(device_id or '')])
    sign = hmac.new(str(key or '').encode('utf-8'),
                    msg.encode('utf-8'), hashlib.sha256).hexdigest()
    return {'ts': ts, 'nonce': nonce, 'body_sha256': bsha, 'sign': sign}


def http(method, url, headers=None, body=None, timeout=30):
    headers = headers or {}
    if requests is not None:
        r = requests.request(method, url, headers=headers,
                             data=body.encode('utf-8') if body is not None else None,
                             timeout=timeout)
        return r.status_code, r.text
    import urllib.request
    import urllib.error
    req = urllib.request.Request(
        url, data=body.encode('utf-8') if body is not None else None,
        headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.getcode(), r.read().decode('utf-8', 'ignore')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'ignore')


def jwt_exp(token):
    try:
        seg = token.split('.')[1]
        seg += '=' * (-len(seg) % 4)
        return int(json.loads(base64.urlsafe_b64decode(seg)).get('exp') or 0)
    except Exception:
        return 0


def parse_iso(s):
    try:
        import datetime
        return datetime.datetime.strptime(str(s)[:19], '%Y-%m-%dT%H:%M:%S').timestamp()
    except Exception:
        return 0


def env(key, default=''):
    v = (os.environ.get(key) or '').strip()
    return v or default


# ---------------------------------------------------------------- 推送
def push(title, content):
    key = env('QYWX_KEY')
    if key:
        url = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=' + key
        data = {"msgtype": "text", "text": {"content": '%s\n%s' % (title, content)}}
        try:
            http('POST', url, {'Content-Type': 'application/json'},
                 json.dumps(data, ensure_ascii=False))
            return
        except Exception as e:
            log('推送失败：%s' % e)
    log('\n【%s】\n%s' % (title, content))


# ---------------------------------------------------------------- YYB 取码
YYB_URL = env('yyb_gpt', '')


def yyb_get_code(ref):
    """从 YYB 取 wx.login code（与爱裹回收 YYB.py 实现保持一致）

    接口：POST {base}/wxapp/getCode  {"ref":"1","app_id":"..."}
    成功：{"code":0,"msg":"success","data":{...,"result":{"code":"..."}}}
    异常：409 = 账号授权过期，需重新扫码（不要重试）
          429 = 熔断，等待 35 秒再试
    ⚠️ 同一账号连续取两次 code 必失败，每次运行只取一次
    """
    if not YYB_URL:
        raise RuntimeError('未配置 yyb_gpt 变量')

    last = ''
    for i in range(3):
        try:
            base, _, r = YYB_URL.partition('#')
            refx = (r or ref or '1').strip()
            base = base.rstrip('/')

            headers = {'Content-Type': 'application/json'}
            tok = env('YYB_PROTOCOL_TOKEN')
            if tok:
                headers['Authorization'] = 'Bearer ' + tok

            st, txt = http('POST', base + '/wxapp/getCode', headers,
                           json.dumps({'ref': str(refx), 'app_id': APPID},
                                      ensure_ascii=False), 40)
            if st == 409:
                raise RuntimeError('YYB 账号授权过期，请重新扫码')

            j = json.loads(txt)
            if j.get('code') in (0, '0'):
                inner = j.get('result')
                if inner is None:
                    dd = j.get('data') or {}
                    inner = dd.get('result', dd) if isinstance(dd, dict) else dd
                code = (inner or {}).get('code')
                if code:
                    acct = (j.get('data') or {}).get('account') or {}
                    nick = acct.get('nickname') or acct.get('alias') or ''
                    if nick:
                        log('   YYB 账号：%s' % nick)
                    return code
                last = 'YYB 未返回 code'
            else:
                last = str(j.get('msg') or j.get('error') or j)[:100]
        except RuntimeError:
            raise
        except Exception as e:
            last = str(e)[:100]

        wait = 35 if ('429' in last or '熔断' in last) else 8
        if i < 2:
            log('   ⏳ YYB 取码失败(%s)，%d 秒后重试' % (last, wait))
            time.sleep(wait)
    raise RuntimeError('YYB 取码失败: ' + last)


# ---------------------------------------------------------------- 会话
class Session(object):
    def __init__(self, ref, remark=''):
        self.ref = str(ref)
        self.remark = remark
        self.token = ''
        self.reward_session = {}
        self.device_id = ''
        self.nickname = ''
        self.uid = ''

    def restore(self, cache):
        rec = cache.get(self.ref) or {}
        self.token = rec.get('token') or ''
        self.reward_session = rec.get('reward_session') or {}
        self.device_id = rec.get('device_id') or ''
        self.nickname = rec.get('nickname') or ''
        self.uid = str(rec.get('uid') or '')
        return bool(self.token and self.device_id and self.reward_session.get('secret'))

    def alive(self):
        if not self.token:
            return False, '无缓存'
        exp = jwt_exp(self.token)
        now = time.time()
        if exp and exp - now < TOKEN_SKEW:
            return False, 'token 临期（剩 %ds）' % max(0, int(exp - now))
        r_exp = parse_iso(self.reward_session.get('expires_at') or '')
        if r_exp and r_exp - now < TOKEN_SKEW:
            return False, 'reward_session 临期'
        return True, '缓存有效（剩 %ds）' % max(0, int(exp - now))

    def save(self, cache):
        cache[self.ref] = {
            'token': self.token,
            'reward_session': self.reward_session,
            'device_id': self.device_id,
            'nickname': self.nickname,
            'uid': self.uid,
            'remark': self.remark,
            'updated': time.strftime('%Y-%m-%d %H:%M:%S'),
        }
        save_cache(cache)

    def login(self):
        code = yyb_get_code(self.ref)
        log('   ✅ YYB 取码成功：%s…' % code[:12])
        if not self.device_id:
            self.device_id = rand_id(40)
        body = json.dumps({
            'code': code,
            'spread_uid': '',
            'platform': 'ios',
            'system': 'iOS 16.3',
            'model': 'iPhone 14 Pro<iPhone15,2>',
        }, ensure_ascii=False, separators=(',', ':'))
        st, txt = http('POST', API_BASE + '/api/auth/login', {
            'Content-Type': 'application/json',
            'X-Device-Id': self.device_id,
            'User-Agent': UA,
            'Referer': REFERER,
        }, body)
        try:
            r = json.loads(txt)
        except Exception:
            raise RuntimeError('登录返回非 JSON（HTTP %s）：%s' % (st, txt[:200]))
        d = r.get('data') or {}
        if int(r.get('status') or 0) != 200 or not d.get('token'):
            raise RuntimeError('登录失败：%s（status=%s）'
                               % (r.get('msg') or txt[:120], r.get('status')))
        self.token = d['token']
        self.reward_session = d.get('reward_session') or {}
        u = d.get('user') or {}
        self.nickname = u.get('nickname') or ''
        self.uid = str(u.get('id') or '')
        if not self.reward_session.get('secret'):
            log('   ⚠️ 登录响应缺少 reward_session.secret —— 领奖类接口会失败')
        return True

    def call(self, path, data=None, method=None, timeout=30, _retry=True):
        m = (method or ('POST' if data is not None else 'GET')).upper()
        payload = '' if m in ('GET', 'HEAD') else json.dumps(
            data or {}, ensure_ascii=False, separators=(',', ':'))

        headers = {
            'Content-Type': 'application/json',
            'X-Device-Id': self.device_id,
            'User-Agent': UA,
            'Referer': REFERER,
            'Accept': '*/*',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        }
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
            ms = build_sign(m, path, payload, self.device_id, self.token)
            headers['X-Mini-Request-Ts'] = ms['ts']
            headers['X-Mini-Request-Nonce'] = ms['nonce']
            headers['X-Mini-Request-Body-Sha256'] = ms['body_sha256']
            headers['X-Mini-Request-Sign'] = ms['sign']

        if needs_reward(path):
            sec = self.reward_session.get('secret') or ''
            sid = self.reward_session.get('session_id') or ''
            if sec and sid:
                rs = build_sign(m, path, payload, self.device_id, sec)
                headers['X-Reward-Session-Id'] = sid
                headers['X-Reward-Ts'] = rs['ts']
                headers['X-Reward-Nonce'] = rs['nonce']
                headers['X-Reward-Body-Sha256'] = rs['body_sha256']
                headers['X-Reward-Sign'] = rs['sign']

        st, txt = http(m, API_BASE + path, headers,
                       payload if m not in ('GET', 'HEAD') else None, timeout)
        try:
            j = json.loads(txt)
        except Exception:
            raise RuntimeError('返回非 JSON（HTTP %s）：%s' % (st, txt[:160]))

        if _retry and (int(j.get('status') or 0) == 40100
                       or ((j.get('data') or {}).get('action') == 'relogin')):
            log('   ⚠️ 登录态失效，重新登录…')
            self.token = ''
            self.reward_session = {}
            self.login()
            return self.call(path, data, method, timeout, _retry=False)

        sc = int(j.get('status') or 0)
        if sc != 200:
            hint = STATUS_HINT.get(sc, '')
            raise RuntimeError('%s（status=%s%s）'
                               % (j.get('msg') or '请求失败', sc,
                                  ('，' + hint) if hint else ''))
        return j.get('data') or {}


# ---------------------------------------------------------------- 缓存
def load_cache():
    try:
        if os.path.exists(CACHE_PATH):
            with open(CACHE_PATH, encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_cache(cache):
    try:
        with open(CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log('   ⚠️ 缓存写入失败：%s' % e)


def load_answers():
    try:
        if os.path.exists(ANSWER_DB):
            with open(ANSWER_DB, encoding='utf-8') as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}


def save_answers(db):
    try:
        with open(ANSWER_DB, 'w', encoding='utf-8') as f:
            json.dump(db, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


# ---------------------------------------------------------------- 业务步骤
def do_sign(se, lines):
    log('\n【签到】')
    st = se.call('/api/sign/status')
    if st.get('signed_today'):
        log('   今日已签到（连续 %s 天，累计 %s 天）'
            % (st.get('consecutive_days') or 0, st.get('total_sign_days') or 0))
        lines.append('签到：今日已签（连续 %s 天）' % (st.get('consecutive_days') or 0))
        return 0

    ticket = st.get('ticket') or ''
    if not ticket:
        log('   ❌ 签到票据为空')
        lines.append('签到：票据为空')
        return 0

    r = se.call('/api/sign/claim', {'ticket': ticket})
    beans = r.get('beans') or 0
    log('   ✅ 签到成功 +%s 金豆 ｜ 连续 %s 天 ｜ 余额 %s'
        % (beans, r.get('consecutive_days') or 0, r.get('balance') or '?'))
    lines.append('签到：+%s 金豆（连续 %s 天）'
                 % (beans, r.get('consecutive_days') or 0))
    return float(beans or 0)


def do_ads(se, lines, max_ads=20):
    log('\n【看视频】')
    st = se.call('/api/ads/status')
    done = int(st.get('count') or 0)
    limit = int(st.get('limit') or 20)
    remain = int(st.get('remaining') or 0)
    log('   今日 %s/%s，剩余 %s' % (done, limit, remain))

    todo = min(max_ads, remain)
    if todo <= 0:
        log('   ✅ 今日视频已完成')
        lines.append('视频：已完成 %s/%s' % (done, limit))
        return 0, done, limit

    total = 0.0
    ok = 0
    for i in range(1, todo + 1):
        try:
            st = se.call('/api/ads/status')
            ticket = st.get('ticket') or ''
            if not ticket:
                log('   [%d/%d] 无 ticket，停止' % (i, todo))
                break
            s = se.call('/api/ads/start', {'ticket': ticket})
            sid = s.get('session_id') or ''
            ch = s.get('challenge') or ''
            need = int(s.get('min_watch_seconds') or 15)
            if not sid or not ch:
                log('   [%d/%d] start 未返回 session/challenge' % (i, todo))
                break

            wait = random.randint(WATCH_MIN, WATCH_MAX)
            if wait < need + 2:
                wait = need + 2
            log('   [%d/%d] 播放中… %ds（要求≥%ds）' % (i, todo, wait, need))
            time.sleep(wait)

            c = se.call('/api/ads/complete',
                        {'session_id': sid, 'challenge': ch})
            b = float(c.get('beans') or 0)
            total += b
            ok += 1
            log('   [%d/%d] ✅ +%s 金豆 ｜ 今日第 %s 次 ｜ 余额 %s'
                % (i, todo, c.get('beans') or 0, c.get('count') or '?',
                   c.get('balance') or '?'))
            time.sleep(random.randint(2, 4))
        except Exception as e:
            log('   [%d/%d] ❌ %s' % (i, todo, e))
            if '40310' in str(e) or '风控' in str(e):
                break
            time.sleep(3)

    try:
        fin = int(se.call('/api/ads/status').get('count') or done)
    except Exception:
        fin = done
    log('   本轮 %d 个成功，共 +%g 金豆' % (ok, total))
    lines.append('视频：本轮 %d 个 +%g 金豆（今日 %s/%s）' % (ok, total, fin, limit))
    return total, fin, limit


def strip_html(h):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', str(h or ''))).strip()


def bigrams(s):
    s = re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]+', '', str(s or ''))
    return set(s[i:i + 2] for i in range(len(s) - 1)) or set(s[:1])


def pick_option(options, article_text, summary_text):
    """按与正文/摘要的二字词重合度猜答案，仅供参考"""
    base_all = bigrams(article_text)
    base_sum = bigrams(summary_text)
    rows = []
    for o in options or []:
        on = bigrams(o.get('text'))
        if not on:
            continue
        s_all = len(on & base_all) / float(len(on))
        s_sum = len(on & base_sum) / float(len(on)) if base_sum else 0.0
        rows.append((0.6 * s_all + 0.4 * s_sum, o.get('id')))
    rows.sort(reverse=True)
    return rows[0][1] if rows else (options[0].get('id') if options else '')


def do_articles(se, lines, target=ARTICLE_TARGET):
    log('\n【健康答题】')
    ts = se.call('/api/daily-tasks/status')
    cur = tgt = 0
    for t in (ts.get('tasks') or []):
        if t.get('code') == 'health_article':
            cur = int(t.get('current') or 0)
            tgt = int(t.get('target') or 5)
    if cur >= tgt:
        log('   ✅ 今日答题任务已完成 %s/%s' % (cur, tgt))
        lines.append('答题：已完成 %s/%s' % (cur, tgt))
        return 0, 0, 0

    need = min(target - cur, tgt - cur)
    log('   今日 %s/%s，计划再做 %d 篇' % (cur, tgt, need))

    lst = se.call('/api/articles?page=1&limit=20')
    pool = [x for x in (lst.get('list') or []) if not x.get('rewarded_today')]
    if not pool:
        log('   ✅ 没有可读的新文章')
        lines.append('答题：无可读文章')
        return 0, 0, 0

    answers = load_answers()
    ok = wrong = 0
    total = 0.0
    for i, it in enumerate(pool[:need], 1):
        aid = str(it.get('id') or '')
        title = (it.get('title') or aid)[:16]
        try:
            d = se.call('/api/articles/%s' % aid)
            body = strip_html(d.get('content'))
            summary = ' '.join([str(d.get('title') or ''),
                                str(d.get('summary') or '')])
            need_sec = int(d.get('min_read_seconds') or 30)

            st = se.call('/api/articles/%s/start' % aid, {})
            if st.get('already_answered'):
                log('   [%d/%d] %s 已答过' % (i, need, title))
                continue
            sid = st.get('session_id') or ''
            every = int(st.get('heartbeat_interval') or 10) or 10
            if not sid:
                log('   [%d/%d] %s start 无 session' % (i, need, title))
                continue

            dwell = random.randint(READ_MIN, READ_MAX)
            if dwell < need_sec:
                dwell = need_sec + random.randint(2, 5)
            seq, el = 0, 0
            while el < dwell:
                time.sleep(min(every, dwell - el))
                el = min(dwell, el + every)
                seq += 1
                se.call('/api/articles/%s/heartbeat' % aid,
                        {'session_id': sid, 'seq': seq})

            q = se.call('/api/articles/%s/quiz' % aid, {'session_id': sid})
            options = q.get('options') or []
            ticket = q.get('ticket') or ''
            if not ticket or not options:
                log('   [%d/%d] %s quiz 无 ticket/options' % (i, need, title))
                continue

            oid = answers.get(aid, {}).get('option') or ''
            if oid not in [o.get('id') for o in options]:
                oid = pick_option(options, body, summary)

            ans = se.call('/api/articles/%s/answer' % aid,
                          {'ticket': ticket, 'option_id': oid})
            correct = bool(ans.get('correct'))
            beans = float(ans.get('reward_beans') or 0)
            total += beans
            if ans.get('correct_option'):
                answers[aid] = {'option': ans['correct_option'],
                                'q': q.get('question') or '',
                                'ts': time.strftime('%Y-%m-%d')}
                save_answers(answers)
            if correct:
                ok += 1
            else:
                wrong += 1
            log('   [%d/%d] %s %s 选%s%s ｜ +%s 金豆 ｜ 第 %s 篇'
                % (i, need, title, '✅' if correct else '❌', oid,
                   '' if correct else '(答案 %s)' % ans.get('correct_option'),
                   beans, ans.get('count') or '?'))
            time.sleep(random.randint(3, 6))
        except Exception as e:
            log('   [%d/%d] %s ❌ %s' % (i, need, title, e))

    log('   本轮 %d 篇（对 %d / 错 %d），共 +%g 金豆' % (ok + wrong, ok, wrong, total))
    lines.append('答题：%d 篇（对%d/错%d）+%g 金豆' % (ok + wrong, ok, wrong, total))
    return total, ok, wrong


def do_task_reward(se, lines):
    log('\n【全勤奖励】')
    st = se.call('/api/daily-tasks/status')
    if st.get('claimed') or st.get('full_reward_claimed'):
        log('   ✅ 今日全勤奖已领')
        lines.append('全勤奖：已领')
        return 0
    if not st.get('finished'):
        log('   每日任务 %s/%s，未全部完成'
            % (st.get('completed_count') or 0, st.get('total_count') or 0))
        return 0
    tk = st.get('full_reward_ticket') or st.get('ticket') or ''
    if not tk:
        log('   无可用票据')
        return 0
    r = se.call('/api/daily-tasks/claim', {'ticket': tk})
    beans = r.get('reward_beans') or r.get('beans') or 0
    log('   ✅ 领取全勤奖 +%s 金豆' % beans)
    lines.append('全勤奖：+%s 金豆' % beans)
    return float(beans or 0)


def show_summary(se, lines, earned):
    log('\n【账户】')
    b = se.call('/api/bootstrap')
    u = b.get('user') or {}
    v = b.get('video') or {}
    s = b.get('sign') or {}
    dt = b.get('daily_tasks') or {}
    wd = se.call('/api/withdrawals/status')

    log('   用户：%s' % (u.get('nickname') or '?'))
    log('   余额：%s 金豆' % (u.get('golden_bean') or '?'))
    log('   签到：%s（连续 %s 天）'
        % ('已签' if s.get('signed_today') else '未签', s.get('consecutive_days') or 0))
    log('   视频：%s/%s' % (v.get('count') or 0, v.get('limit') or 20))
    log('   每日任务：%s/%s' % (dt.get('completed_count') or 0,
                              dt.get('total_count') or 0))
    log('   可提现：%s 元' % (wd.get('withdrawable_yuan') or '0.00'))

    lines.append('余额：%s 金豆 ｜ 今日 +%g' % (u.get('golden_bean') or '?', earned))
    lines.append('可提现：%s 元' % (wd.get('withdrawable_yuan') or '0.00'))


# ---------------------------------------------------------------- 主流程
def parse_accounts():
    raw = env('JDZX2_ACCOUNTS', '') or env('GOLDEN_ACCOUNTS', '1')
    out = []
    for line in raw.replace(',', '\n').splitlines():
        line = line.strip()
        if not line:
            continue
        if ':' in line:
            ref, rmk = line.split(':', 1)
        else:
            ref, rmk = line, ''
        if ref.strip():
            out.append((ref.strip(), rmk.strip()))
    return out or [('1', '')]


def run_one(ref, remark, cache, args):
    log('=' * 56)
    log('账号 %s%s' % (ref, ('（%s）' % remark) if remark else ''))
    log('=' * 56)

    se = Session(ref, remark)
    lines = []
    earned = 0.0

    if not args.get('relogin') and se.restore(cache):
        ok, why = se.alive()
        log('   登录态：%s' % why)
        if not ok:
            se.token = ''
            se.reward_session = {}
    else:
        log('   登录态：需登录')

    if not se.token:
        se.login()
        log('   ✅ 登录成功：%s' % (se.nickname or '?'))
        se.save(cache)
        log('   💾 已缓存：%s' % CACHE_PATH)
    log('   设备：%s…' % se.device_id[:10])

    if args.get('stat'):
        show_summary(se, lines, 0)
        return 0, lines

    if not args.get('no_sign'):
        earned += do_sign(se, lines)
    if not args.get('no_articles'):
        e, _, _ = do_articles(se, lines, args.get('articles') or ARTICLE_TARGET)
        earned += e
    if not args.get('no_ads'):
        e, _, _ = do_ads(se, lines, args.get('ads') or 20)
        earned += e
    do_task_reward(se, lines)
    show_summary(se, lines, earned)
    return 0, lines


def main():
    argv = sys.argv[1:]

    def has(f):
        return f in argv

    def val(f, d=None):
        if f not in argv:
            return d
        i = argv.index(f)
        return argv[i + 1] if len(argv) > i + 1 else d

    args = {
        'stat': has('--stat'),
        'no_sign': has('--no-sign'),
        'no_ads': has('--no-ads'),
        'no_articles': has('--no-articles'),
        'relogin': has('--relogin'),
        'ads': int(val('--ads', 20) or 20),
        'articles': int(val('--articles', ARTICLE_TARGET) or ARTICLE_TARGET),
    }

    accounts = parse_accounts()
    log('=' * 56)
    log('金豆赚赚乐 · 签到 + 看视频 + 健康答题')
    log('账号数：%d ｜ 视频上限 %d ｜ 答题 %d 篇'
        % (len(accounts), args['ads'], args['articles']))
    log('=' * 56)

    cache = load_cache()
    all_lines = []
    start = time.time()

    for i, (ref, rmk) in enumerate(accounts):
        if i:
            gap = random.randint(30, 90)
            log('\n（账号间隔 %ds）' % gap)
            time.sleep(gap)
        try:
            _, lines = run_one(ref, rmk, cache, args)
        except Exception as e:
            log('❌ 账号 %s 异常：%s' % (ref, e))
            traceback.print_exc()
            lines = ['❌ %s' % e]
        for l in lines:
            all_lines.append('%s｜%s' % (rmk or ('账号%s' % ref), l))

    cost = int(time.time() - start)
    body = '\n'.join(all_lines) or '（无输出）'
    body += '\n\n耗时 %d 分 %d 秒' % (cost // 60, cost % 60)
    log('\n' + '=' * 56)
    log('汇总')
    log('=' * 56)
    log(body)
    push('🔔 金豆赚赚乐 · 每日任务', body)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        push('❌ 金豆赚赚乐 异常',
             traceback.format_exc().splitlines()[-1][:300])
        sys.exit(3)
