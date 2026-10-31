#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================
刷赞刷粉平台（没水，签到几分钱😂）
https://ta.gnx3.cn/user/fxrw.php?fh_go=1
------------------------------------------------------------
变量:dz_gpt 格式: 手机号#密码#备注
    多账号: 换行 或 & 分隔
定时：cron 12 6 * * * 每天一次自行修改    
依赖: pip install requests pillow
============================================================
"""
import os
import re
import sys
import io
import json
import time
import random
import base64
import subprocess
from datetime import datetime

try:
    import requests
    from PIL import Image
except ImportError:
    print('缺少依赖，请执行: pip install requests pillow')
    sys.exit(1)

# ==================================================================
#  ★★★ 配 置 区 —— 只改这里 ★★★
# ==================================================================
CONFIG = {
    # ---------- 行为 ----------
    'timeout': 90,              # 单请求超时（秒）
    'max_slide_retry': 6,       # 滑块最大重试次数（站点约 10 次限流）
    'debug': False,             # True 打印详细日志

    # 拟人节奏
    'human_pace': {
        'enable': True,
        'fail_wait': (2500, 5000),   # 失败后等待区间(毫秒)
        'cool_after': 3,             # 连续失败几次开始冷却
        'cool_step': 8000,           # 冷却基数(毫秒)
        'cool_max': 60000,           # 冷却上限
    },

    # ---------- 可选：手动注入 CK（一般不用，脚本会自动保存）----------
    'cookies': {
        # '13602006965': 'user_token=xxx; PHPSESSID=xxx; mysid=xxx',
    },

    # ---------- 推送（默认走青龙 notify.js，找不到则打印预览）----------
    'push': {
        'enable': True,
        'title': '🌾 刷粉刷赞签到🙋‍♀️',
    },
}
# ==================== 配置区结束 ====================

BASE = 'https://ta.gnx3.cn'
UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 16_3_0 like Mac OS X) '
      'AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/134.0.6998.99 '
      'Mobile/15E148 Safari/604.1')

QL_DIR = os.environ.get('QL_DIR', '')
CACHE_DIR = os.path.join(QL_DIR, 'data') if QL_DIR else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '.cache')
CACHE_FILE = os.path.join(CACHE_DIR, 'ta_gnx3_cookie.json')


def now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def log(*a):
    print(f'[{now()}]', *a)


def dbg(*a):
    if CONFIG.get('debug'):
        print('[调试]', *a)


def mask_user(u):
    if re.match(r'^1[3-9]\d{9}$', u):
        return u[:3] + '****' + u[-4:]
    return (u[0] + '***') if len(u) <= 4 else (u[:2] + '****' + u[-2:])


# ======================= 会话 =======================
class Session:
    """带 cookie 管理的会话。关键：已有 cookie 不覆盖，避免会话被冲掉"""

    def __init__(self, cookie_str=''):
        self.s = requests.Session()
        self.s.headers.update({
            'User-Agent': UA,
            'Accept-Language': 'zh-CN,zh-Hans;q=0.9',
            'Origin': BASE,
        })
        self.s.cookies.clear()
        if cookie_str:
            for kv in cookie_str.split(';'):
                kv = kv.strip()
                if '=' in kv:
                    k, v = kv.split('=', 1)
                    self.s.cookies.set(k.strip(), v.strip(), domain='ta.gnx3.cn')

    def cookie_str(self):
        return '; '.join(f'{c.name}={c.value}' for c in self.s.cookies)

    def force_set(self, name, value):
        self.s.cookies.set(name, value, domain='ta.gnx3.cn')

    def load(self, cookie_str):
        self.s.cookies.clear()
        for kv in cookie_str.split(';'):
            kv = kv.strip()
            if '=' in kv:
                k, v = kv.split('=', 1)
                self.s.cookies.set(k.strip(), v.strip(), domain='ta.gnx3.cn')

    def get(self, path, **kw):
        kw.setdefault('timeout', CONFIG['timeout'])
        return self.s.get(BASE + path, **kw)

    def post(self, path, **kw):
        kw.setdefault('timeout', CONFIG['timeout'])
        return self.s.post(BASE + path, **kw)


# ======================= 反爬 =======================
def solve_sec_defend(html):
    """提取 JSFuck 表达式并求值（借 node 执行，不需要安装任何包）"""
    tag = "setCookie('sec_defend',"
    i = html.find(tag)
    if i < 0:
        return None
    start = i + len(tag)
    depth = 0
    j = start
    while j < len(html):
        if html[j] == '(':
            depth += 1
        elif html[j] == ')':
            if depth == 0:
                break
            depth -= 1
        j += 1
    expr = html[start:j]
    if not expr or len(expr) < 50:
        return None
    try:
        out = subprocess.run(
            ['node', '-e', 'console.log((' + expr + '))'],
            capture_output=True, text=True, timeout=10
        ).stdout.strip()
        if re.match(r'^[0-9a-f]{64}$', out):
            return out
    except Exception as e:
        dbg('sec_defend 执行失败:', e)
    return None


def pass_sec_defend(sess):
    """
    【关键】只访问 login.php
    绝对不要访问 index.php：实测会让 cookie 从 4 个变成 8 个
    （多出 mysid / mall_store_template_*），导致滑块 verify 一律 4601
    """
    r = sess.get('/user/login.php', headers={'Accept': 'text/html,application/xhtml+xml'})
    v = solve_sec_defend(r.text)
    if v:
        sess.force_set('sec_defend', v)
        sess.force_set('sec_defend_time', '1')
        dbg('sec_defend 来源: login.php')
    return v


# ======================= 滑块 =======================
def mask_match(bg, pc, y0):
    """用拼图块的 alpha 掩码在背景里找缺口，返回 (x, 匹配度)"""
    W, H = bg.size
    PW, PH = pc.size
    bp = bg.load()
    pp = pc.load()

    best_score, best_x = -1.0, -1
    for x in range(0, W - PW + 1):
        hit = 0
        n = 0
        for y in range(PH):
            by = y0 + y
            if by >= H:
                continue
            for xx in range(PW):
                r, g, b, a = pp[xx, y]
                if a < 200:
                    continue
                px = bp[x + xx, by]
                if px[0] + px[1] + px[2] < 260:
                    hit += 1
                n += 1
        if n < 500:
            continue
        sc = hit / n
        if sc > best_score:
            best_score, best_x = sc, x
    return best_x, best_score


def find_slot(bg):
    """找缺口（暗色区域）左边缘，用于兜底"""
    W, H = bg.size
    bp = bg.load()
    mn, mx = 10 ** 9, -1
    for y in range(H):
        for x in range(W):
            px = bp[x, y]
            if px[0] + px[1] + px[2] < 260:
                mn = min(mn, x)
                mx = max(mx, x)
    if mx < 0:
        return None
    return mn, mx


def slide_api(sess, body):
    r = sess.post('/ds_slide/api.php', json=body, headers={
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/plain, */*',
        'Referer': BASE + '/user/login.php',
        'X-Requested-With': 'XMLHttpRequest',
    })
    try:
        return r.json()
    except Exception:
        return {'code': -1, 'msg': r.text[:150]}


def pass_slide(sess, retry=None):
    retry = retry or CONFIG['max_slide_retry']
    pace = CONFIG['human_pace']
    consecutive_fail = 0

    for i in range(1, retry + 1):
        # 连续失败冷却
        if i > 1 and consecutive_fail >= pace['cool_after']:
            cool = min(pace['cool_max'],
                       pace['cool_step'] * (2 ** (consecutive_fail - pace['cool_after'])))
            log(f'      连续失败 {consecutive_fail} 次，冷却 {cool // 1000}s 再试')
            time.sleep(cool / 1000)

        ch = slide_api(sess, {'action': 'challenge', 'scene': 'login'})

        # 429 = 限流
        if ch.get('code') == 429 or '频繁' in (ch.get('msg') or ''):
            log(f'      ⚠ 触发站点限流（{ch.get("msg")}），等待 65 秒')
            time.sleep(65)
            consecutive_fail = 0
            continue
        if ch.get('code') != 0 or not ch.get('data'):
            log(f'      [{i}/{retry}] challenge 失败: {ch.get("code")} {ch.get("msg")}')
            consecutive_fail += 1
            time.sleep(1.0)
            continue

        d = ch['data']
        try:
            bg = Image.open(io.BytesIO(base64.b64decode(d['background'].split(',')[1]))).convert('RGBA')
            pc = Image.open(io.BytesIO(base64.b64decode(d['piece'].split(',')[1]))).convert('RGBA')
        except Exception as e:
            log(f'      [{i}/{retry}] 图片解码失败: {e}')
            time.sleep(0.6)
            continue

        # 首选掩码匹配，兜底用阈值
        mx, mscore = mask_match(bg, pc, d['y'])
        if mx >= 0 and mscore > 0.5:
            x, src = mx, '掩码'
        else:
            slot = find_slot(bg)
            if not slot:
                log(f'      [{i}/{retry}] 未找到缺口，换图')
                time.sleep(0.6)
                continue
            x, src = slot[0] - 6, '阈值'

        if x < 0 or x > bg.size[0] - pc.size[0]:
            log(f'      [{i}/{retry}] 坐标越界 {x}')
            time.sleep(0.6)
            continue

        # 服务端校验 duration，<600ms 必挂，取 1500~2500
        dur = 1500 + random.randint(0, 1000)
        v = slide_api(sess, {'action': 'verify', 'scene': 'login',
                             'id': d['id'], 'x': x, 'duration': dur})
        tk = v.get('ticket') or (v.get('data') or {}).get('ticket')
        ok = isinstance(tk, str) and re.match(r'^[0-9a-f]{64}$', tk or '')

        log(f'      [{i}/{retry}] 掩码x={mx}(分{mscore:.2f}) 提交x={x}({src}) '
            f'dur={dur}ms → {v.get("code")} '
            f'{"验证成功" if ok else (v.get("msg") or "")[:14]}')

        if ok:
            log(f'      ✓ 滑块通过（第 {i} 次尝试）')
            return tk

        if v.get('code') == 429 or '频繁' in (v.get('msg') or ''):
            log('      ⚠ verify 阶段触发限流，等待 65 秒')
            time.sleep(65)
            consecutive_fail = 0
            continue

        consecutive_fail += 1
        if pace['enable']:
            w = random.randint(*pace['fail_wait'])
            time.sleep(w / 1000)

    return None


# ======================= 登录 / 签到 =======================
def login(sess, user, pwd, ticket):
    r = sess.post('/ajax_shop.php?act=login',
                  data={'user': user, 'pass': pwd, 'ds_slide_ticket': ticket},
                  headers={
                      'Content-Type': 'application/x-www-form-urlencoded',
                      'X-Requested-With': 'XMLHttpRequest',
                      'Accept': 'application/json, text/javascript, */*; q=0.01',
                      'Referer': BASE + '/?chadan=1',
                  })
    try:
        return r.json()
    except Exception:
        return {'code': -1, 'msg': r.text[:150]}


def qiandao(sess):
    r = sess.get('/user/ajax_user.php?act=qiandao', headers={
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': BASE + '/user/qiandao.php',
    })
    try:
        return r.json()
    except Exception:
        return {'code': -1, 'msg': r.text[:150]}


def profile(sess):
    """取余额 / 今日收益"""
    try:
        r = sess.get('/user/', headers={'Accept': 'text/html,application/xhtml+xml'})
        h = r.text
        def find(pat):
            m = re.search(pat, h)
            return m.group(1) if m else None
        return {
            'balance': find(r'余额[：:]\s*(?:<[^>]*>\s*)?([0-9.]+)'),
            'today': find(r'今日收益[\s\S]{0,80}?([0-9.]+)\s*元'),
            'month': find(r'近30天收益[\s\S]{0,80}?([0-9.]+)\s*元'),
        }
    except Exception:
        return {}


# ======================= 缓存 =======================
def load_cache():
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(obj):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    except Exception as e:
        dbg('缓存写入失败:', e)


# ======================= 推送 =======================
def send(title, content):
    """优先用青龙 notify.js（同目录），没有就打印预览"""
    if not CONFIG['push'].get('enable', True):
        log('推送已关闭')
        return

    notify_dir = os.path.dirname(os.path.abspath(__file__))
    notify_py = os.path.join(notify_dir, 'notify.py')
    if QL_DIR:
        alt = os.path.join(QL_DIR, 'scripts', 'notify.py')
        if os.path.exists(alt):
            notify_py = alt

    if os.path.exists(notify_py):
        try:
            sys.path.insert(0, os.path.dirname(notify_py))
            from notify import send as _send
            _send(title, content)
            log('推送结果: notify.py ✓')
            return
        except Exception as e:
            log('notify.py 调用失败:', e)

    # 兜底：企业微信机器人（如果配了 QYWX_KEY）
    key = os.environ.get('QYWX_KEY')
    if key:
        try:
            r = requests.post(
                f'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}',
                json={'msgtype': 'text',
                      'text': {'content': f'【{title}】\n{content}'[:1990]}},
                timeout=20)
            j = r.json()
            if j.get('errcode') == 0:
                log('推送结果: 企业微信 ✓')
                return
            log(f'推送结果: 企业微信 ✗ {j.get("errcode")} {j.get("errmsg")}')
            return
        except Exception as e:
            log('企业微信推送失败:', e)

    print(f'\n📣 推送预览\n【{title}】\n{content}\n')


# ======================= 单账号 =======================
def run_one(user, pwd, name, idx, total, cache):
    tag = f'账号{idx}/{total}({name or mask_user(user)})'
    log(f'\n========== {tag} ==========')
    display = name or mask_user(user)

    # ---------- 阶段 1：先试缓存 CK ----------
    cached = cache.get(user)
    if cached and cached.get('cookie'):
        sess = Session(cached['cookie'])
        log('  [1/2] 尝试上次保存的 CK…')
        q = qiandao(sess)
        if q.get('code') == 0 or re.search(r'已签到|已经签到', q.get('msg') or ''):
            p = profile(sess)
            state = q.get('msg') if q.get('code') == 0 else '今日已签到'
            log(f'  ✅ {state} 余额 {p.get("balance") or "?"} 元（CK 有效）')
            cache[user] = {'cookie': sess.cookie_str(), 'saved_at': now()}
            save_cache(cache)
            return {
                'tag': tag, 'success': True,
                'line': (f'👤 账号: {display}\n   📝 签到: {state}\n'
                         f'   💰 余额: {p.get("balance") or "?"} 元\n'
                         f'   📈 今日: {p.get("today") or "?"} 元'),
            }
        log(f'  CK 已失效（{q.get("msg") or q.get("code")}），改用账号密码登录')
    else:
        log('  [1/2] 无缓存 CK，直接账密登录')

    # ---------- 阶段 2：账密登录 ----------
    sess = Session()
    log('  [2/2] 账密登录')
    log('      过 sec_defend 反爬…')
    pass_sec_defend(sess)

    log(f'      过滑动验证（最多 {CONFIG["max_slide_retry"]} 次）…')
    ticket = pass_slide(sess)
    if not ticket:
        log('      ✗ 滑块未通过')
        return {'tag': tag, 'success': False,
                'line': (f'👤 账号: {display}\n   📝 签到: ❌ 滑动验证失败\n'
                         f'   💡 可在手机浏览器登录后把 CK 填进 CONFIG.cookies')}

    res = login(sess, user, pwd, ticket)
    log('      登录结果:', json.dumps(res, ensure_ascii=False))
    if res.get('code') not in (0, '0'):
        return {'tag': tag, 'success': False,
                'line': f'👤 账号: {display}\n   📝 签到: ❌ 登录失败 {res.get("msg")}'}

    cache[user] = {'cookie': sess.cookie_str(), 'saved_at': now()}
    save_cache(cache)
    log('      CK 已保存')

    # ---------- 阶段 3：签到 ----------
    # 【关键】登录接口会重置会话，必须先把登录页走一遍再签到，
    # 否则签到接口会返回「校验失败，请刷新页面重试」。
    # 顺序（实测有效）：login.php → user/ → user/qiandao.php → 签到
    q = {}
    for attempt in range(1, 5):
        try:
            sess.get('/user/login.php')
            sess.get('/user/', headers={'Accept': 'text/html,application/xhtml+xml'})
            sess.get('/user/qiandao.php', headers={'Accept': 'text/html,application/xhtml+xml'})
        except Exception as e:
            dbg('预热页面失败:', e)

        q = qiandao(sess)
        if q.get('code') == 0 or re.search(r'已签到|已经签到', q.get('msg') or ''):
            break
        if '校验失败' in (q.get('msg') or '') and attempt < 4:
            wait = attempt * 2
            log(f'      第 {attempt} 次签到返回「校验失败」，等 {wait}s 重试…')
            time.sleep(wait)
            continue
        break

    p = profile(sess)
    success = q.get('code') == 0
    already = bool(re.search(r'已签到|已经签到', q.get('msg') or ''))
    state = (q.get('msg') or '签到成功') if success else ('今日已签到' if already else (q.get('msg') or '未知错误'))
    mark = '✅' if (success or already) else '❌'
    log(f'  {mark} {state} 余额 {p.get("balance") or "?"} 元 今日收益 {p.get("today") or "?"} 元')
    return {
        'tag': tag, 'success': success or already,
        'line': (f'👤 账号: {display}\n   📝 签到: {state}\n'
                 f'   💰 余额: {p.get("balance") or "?"} 元\n'
                 f'   📈 今日: {p.get("today") or "?"} 元'),
    }


# ======================= 主流程 =======================
def parse_accounts(raw):
    if not raw:
        return []
    out = []
    for line in re.split(r'[\n&]+', raw):
        line = line.strip()
        if not line:
            continue
        parts = line.split('#')
        if len(parts) < 2:
            continue
        user, pwd = parts[0].strip(), parts[1].strip()
        name = parts[2].strip() if len(parts) > 2 else ''
        if user and pwd:
            out.append((user, pwd, name))
    return out


def main():
    log('════════ ta.gnx3.cn 自动签到 ════════')
    log('运行环境:', '青龙面板' if QL_DIR else '本地')
    log('Python 版本:', sys.version.split()[0])

    raw = (os.environ.get('dz_gpt') or '').strip()
    if not raw:
        print('✗ 未设置环境变量 dz_gpt（格式：手机号#密码#备注）')
        sys.exit(1)

    accounts = parse_accounts(raw)
    if not accounts:
        print('✗ dz_gpt 格式错误，应为 手机号#密码[#备注]')
        sys.exit(1)
    log(f'读取到 {len(accounts)} 个账号')

    cache = load_cache()
    # 配置里写死的 CK 优先
    for u, ck in (CONFIG.get('cookies') or {}).items():
        if ck:
            cache[u] = {'cookie': ck, 'saved_at': 'config'}
            log(f'  载入配置内 cookie: {mask_user(u)}')

    results = []
    for i, (user, pwd, name) in enumerate(accounts, 1):
        try:
            results.append(run_one(user, pwd, name, i, len(accounts), cache))
        except Exception as e:
            nm = name or mask_user(user)
            results.append({'tag': f'账号{i}', 'success': False,
                            'line': f'👤 账号: {nm}\n   📝 签到: ⚠️ 运行异常 {str(e)[:40]}'})
            log('  ✗ 异常:', e)
        if i < len(accounts):
            time.sleep(random.uniform(2, 4))

    ok_count = sum(1 for r in results if r['success'])
    lines = '\n'.join(r['line'] for r in results)
    log('\n════════ 执行结果 ════════')
    log(lines)
    if ok_count < len(results):
        log(f'  共 {len(results)} 个账号，成功 {ok_count} 个')

    send(CONFIG['push']['title'], lines)
    log('════════ 任务结束 ════════')
    sys.exit(0 if ok_count == len(results) else 2)


if __name__ == '__main__':
    main()
