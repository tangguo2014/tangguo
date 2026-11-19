#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
vx小程序 劲友家
================================================================================

【变量】
    yyb_gpt = YYB地址#ref
             例：http://265.32.0.1:8000#1
    多账号：& 或换行分隔
             

【可选变量】
    YYB_PROTOCOL_TOKEN   YYB 开了协议鉴权才填

================================================================================
【定位开关】★★★ 只改下面这一行 ★★★
    CITY = '天津'      ← 换成 北京 / 上海 / … 即可，坐标自动带出
    想用不在列表里的地方，就把 CITY 写成 (纬度, 经度) 元组，例：CITY = (39.22, 117.28)

    内置：天津(默认) 北京 上海 广州 深圳 成都 郑州 重庆 杭州 南京
          武汉 西安 长沙 沈阳 济南 青岛 石家庄 太原

================================================================================

【任务清单】（签到 + 7 项）
    1. 签到              task/queryUserTaskListV2 → taskContinuousRecord
    2. 测一测 YBTI       jjysj2026/game  start→end→draw
    3. 调一调 DIAOYIN    同上
    4. 刮一刮 GUA_GE_YANG 同上
    5. 换酒              庄园酒瓶换积分（约 5 分/瓶）
    6. 抓鸭 CATCH_DUCK   share→start→end→draw（默认 2 局）
    7. 集章手册          三章集齐后 draw
    8. 酒馆夹美食        playClampStart→End，靠任务换次数

================================================================================

【说明】
     token 有效期 24 小时，缓存同目录         jinyoujia_cache.json，当天复用不重复取码
  

【定时】 
    0 8,16 * * * 缓存24小时有效，建议每天运行两次，无需重复取码，缓存兜底。
    
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

# ==================== 定位配置（改这里）====================
# 只有这一件事要改：把 CITY 换成你要的城市名即可。
#   CITY = '北京'
# 不在下面列表里的地方，直接写坐标元组：
#   CITY = (39.221856, 117.280393)
CITY = '天津'

CITY_LIST = {
    '天津': (39.221856, 117.280393),
    '北京': (39.904200, 116.407396),
    '上海': (31.230416, 121.473701),
    '广州': (23.129163, 113.264435),
    '深圳': (22.543096, 114.057865),
    '成都': (30.572815, 104.066801),
    '郑州': (34.746484, 113.625359),
    '重庆': (29.563010, 106.551556),
    '杭州': (30.274085, 120.155070),
    '南京': (32.060255, 118.796877),
    '武汉': (30.592760, 114.305392),
    '西安': (34.341574, 108.939770),
    '长沙': (28.228209, 112.938814),
    '沈阳': (41.805698, 123.431474),
    '济南': (36.651216, 117.120125),
    '青岛': (36.067082, 120.382639),
    '石家庄': (38.042307, 114.514862),
    '太原': (37.870590, 112.548879),
}
# ==========================================================

ENV_NAME = 'yyb_gpt'
APP_ID = 'wx10bc773e0851aedd'
BASE = 'https://jjw.jingjiu.com'
# 以下三项以 HAR 实测为准（JS 源码与真实抓包不一致，信 HAR）
UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) AppleWebKit/605.1.15 '
      '(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.48(0x1800302b) NetType/WIFI Language/zh_CN')
REFERER = 'https://servicewechat.com/%s/1218/page-frame.html' % APP_ID
YYB_TOKEN = (os.environ.get('YYB_PROTOCOL_TOKEN') or '').strip()
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'jinyoujia_cache.json')
CACHE_VER = 1


def resolve_location():
    """把文件顶部的 CITY 解析成 (城市名, 纬度, 经度)"""
    global LAT, LNG, CITY

    c = CITY
    # 直接写了坐标元组
    if isinstance(c, (tuple, list)) and len(c) == 2:
        LAT, LNG, CITY = float(c[0]), float(c[1]), '自定义'
        return CITY, LAT, LNG

    name = str(c).strip()
    if name in CITY_LIST:
        LAT, LNG = CITY_LIST[name]
        CITY = name
        return CITY, LAT, LNG

    # 未知城市：退到天津，不崩
    print('⚠️ 未知城市「%s」，已回退天津。可用：%s' % (name, '/'.join(CITY_LIST)), flush=True)
    CITY = '天津'
    LAT, LNG = CITY_LIST[CITY]
    return CITY, LAT, LNG

_ACC = None


def log(*args):
    tag = '[%s] ' % _ACC['name'] if _ACC else ''
    print(tag + ' '.join(str(a) for a in args), flush=True)


def wait(sec):
    time.sleep(sec)


def rd(lo, hi, label=''):
    ms = random.randint(lo, hi)
    if label:
        log('%s，随机等待 %.1fs' % (label, ms / 1000.0))
    time.sleep(ms / 1000.0)


def mask(s):
    s = str(s or '')
    return s[:6] + '****' + s[-4:] if len(s) > 14 else (s[:3] + '***' if s else '(空)')


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
        log('缓存写入失败:', e)


# ==================== 账号解析 ====================
def parse_entries():
    raw = (os.environ.get(ENV_NAME) or '').strip()
    if not raw:
        return []
    out = []
    for line in re.split(r'[&\n]', raw):
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
            d = r.json()
            data = d.get('data') or {}
            if data.get('circuit_open'):
                raise RuntimeError('YYB 熔断，需等 %s 秒' % data.get('retry_after', 600))
            inner = data.get('result') or d.get('result') or {}
            code = inner.get('code') or data.get('code')
            if code:
                return code
            last = '取码失败: %s' % json.dumps(d, ensure_ascii=False)[:180]
        except RuntimeError:
            raise
        except Exception as e:
            last = str(e)[:150]
        if re.search(r'熔断|code=-14|shortlink|授权已过期|409', last):
            raise RuntimeError(last)
        if i < retries:
            wait(3)
    raise RuntimeError(last or 'YYB 取码失败')


# ==================== HTTP ====================
def headers(token=None, blackbox=True):
    """照 HAR 实测构造：appId 是驼峰，Referer 用 /1218/，UA 是 iPhone 微信 8.0.48"""
    h = {
        'Content-Type': 'application/json',
        'appId': APP_ID,
        'Accept': 'application/json, text/plain, */*',
        'User-Agent': UA,
        'Referer': REFERER,
    }
    if token:
        h['Authorization'] = token
    if blackbox:
        # HAR 里每次请求都带，值是随机串（服务端只校验格式）
        h['X-Td-Blackbox'] = 'iMPHY17950684942fqVXZxmnfa'
    return h


def api(token, path, body=None, method='POST', blackbox=True, timeout=25, params=None):
    url = BASE + path
    try:
        if method.upper() == 'GET':
            r = requests.get(url, headers=headers(token, blackbox), timeout=timeout,
                             params=params)
        else:
            r = requests.post(url, headers=headers(token, blackbox),
                              data=json.dumps(body if body is not None else {}, ensure_ascii=False),
                              timeout=timeout, params=params)
        return r.json()
    except Exception as e:
        return {'code': 'EXC', 'msg': str(e)[:150]}


def ok(j):
    return bool(j) and str(j.get('code')) == '200'


# ==================== AES 加密（v1 参数）====================
# 密钥来自小程序解包源码：AES_SECRET_KEY = "Z0J7M480h6kppf67"
# v1 = base64( AES-128-ECB( PKCS7( json(payload, separators=(',',':')) ) ) )
AES_KEY = b'Z0J7M480h6kppf67'

_SBOX = bytes.fromhex(
    '637c777bf26b6fc53001672bfed7ab76ca82c97dfa5947f0add4a2af9ca472c0'
    'b7fd9326363ff7cc34a5e5f171d8311504c723c31896059a071280e2eb27b275'
    '09832c1a1b6e5aa0523bd6b329e32f8453d100ed20fcb15b6acbbe394a4c58cf'
    'd0efaafb434d338545f9027f503c9fa851a3408f929d38f5bcb6da2110fff3d2'
    'cd0c13ec5f974417c4a77e3d645d197360814fdc222a908846eeb814de5e0bdb'
    'e0323a0a4906245cc2d3ac629195e479e7c8376d8dd54ea96c56f4ea657aae08'
    'ba78252e1ca6b4c6e8dd741f4bbd8b8a703eb5664803f60e613557b986c11d9e'
    'e1f8981169d98e949b1e87e9ce5528df8ca1890dbfe6426841992d0fb054bb16'
)


def _xtime(a):
    a <<= 1
    return (a ^ 0x11B) if a & 0x100 else a


def _expand_key(key):
    sbox = _SBOX
    w = [list(key[i * 4:i * 4 + 4]) for i in range(4)]
    rcon = 1
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = t[1:] + t[:1]
            t = [sbox[x] for x in t]
            t[0] ^= rcon
            rcon = _xtime(rcon)
        w.append([w[i - 4][j] ^ t[j] for j in range(4)])
    return [b for word in w for b in word]


def _aes_encrypt_pure(text):
    """纯 Python AES-128-ECB（无 pycryptodome 时兜底）"""
    import base64
    data = text.encode('utf-8')
    padlen = 16 - len(data) % 16
    data += bytes([padlen]) * padlen
    sbox = _SBOX
    rk = _expand_key(AES_KEY)

    def encrypt_block(b):
        s = [b[i] ^ rk[i] for i in range(16)]
        for rnd in range(1, 11):
            s = [sbox[x] for x in s]          # SubBytes
            t = [0] * 16                       # ShiftRows（列优先 s[c*4+r]）
            for c in range(4):
                for r in range(4):
                    t[c * 4 + r] = s[((c + r) % 4) * 4 + r]
            s = t
            if rnd != 10:                      # MixColumns
                t = []
                for c in range(4):
                    a0, a1, a2, a3 = s[c * 4:c * 4 + 4]
                    t += [_xtime(a0) ^ (_xtime(a1) ^ a1) ^ a2 ^ a3,
                          a0 ^ _xtime(a1) ^ (_xtime(a2) ^ a2) ^ a3,
                          a0 ^ a1 ^ _xtime(a2) ^ (_xtime(a3) ^ a3),
                          (_xtime(a0) ^ a0) ^ a1 ^ a2 ^ _xtime(a3)]
                s = t
            s = [s[i] ^ rk[rnd * 16 + i] for i in range(16)]   # AddRoundKey
        return bytes(s)

    out = b''.join(encrypt_block(data[i:i + 16]) for i in range(0, len(data), 16))
    return base64.b64encode(out).decode()


def _aes_encrypt(obj):
    """dict → base64 密文"""
    plain = json.dumps(obj, separators=(',', ':'), ensure_ascii=False)
    try:
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad
        import base64
        c = AES.new(AES_KEY, AES.MODE_ECB)
        return base64.b64encode(c.encrypt(pad(plain.encode('utf-8'), 16))).decode()
    except ImportError:
        return _aes_encrypt_pure(plain)


# ==================== 三个游戏（jjysj2026 框架）====================
GAMES = [
    {'type': 'JJYSJ_2026_YBTI',         'name': '测一测'},
    {'type': 'JJYSJ_2026_DIAOYIN',      'name': '调一调'},
    {'type': 'JJYSJ_2026_GUA_GE_YANG',  'name': '刮一刮'},
]
_GAME_BASE = '/app-jingyoujia/app/jingyoujia/jjysj2026/game'


def game_call(acc, action, payload):
    return api(acc['token'], '%s/%s' % (_GAME_BASE, action),
               {'v1': _aes_encrypt(payload)})


def get_game_info(acc, gtype):
    return api(acc['token'], '%s/info/%s' % (_GAME_BASE, gtype),
               None, method='GET')


def play_game(acc, g):
    name, gtype = g['name'], g['type']
    r = {'name': name, 'status': 'skip', 'msg': '', 'award': ''}

    info = get_game_info(acc, gtype)
    d = (info.get('data') or {}) if ok(info) else {}
    state = d.get('joinState')
    cfg = d.get('config') or {}
    cost = cfg.get('invokeIntegral') or d.get('invokeIntegral') or 0
    exchange_on = str(cfg.get('pointExchangeSwitch')) == '1'

    # joinState: 0=可玩 / 1=今日已完成 / 2=需积分兑换 / 3=需先分享
    if state == 1:
        r['status'] = 'already'
        r['msg'] = '今日已完成'
        log('🎮 [%s]ℹ️ 今日已完成' % name)
        return r
    # 要花积分换次数时不自作主张（JS 作者同样不做）
    if state == 2 and exchange_on and cost:
        r['status'] = 'skip'
        r['msg'] = '需花%d积分兑换，跳过' % cost
        log('🎮 [%s]ℹ️ 需花 %d 积分兑换次数，不自动兑换，跳过' % (name, cost))
        return r

    log('🎮 [%s]开始（joinState=%s）…' % (name, state))
    j = game_call(acc, 'start', {'activityType': gtype})
    if not ok(j):
        r['status'] = 'fail'
        r['msg'] = str(j.get('msg') or j.get('code'))[:24]
        log('🎮 [%s]❌ start 失败：%s' % (name, r['msg']))
        return r
    rd(4000, 7000, '模拟游戏时长')

    j = game_call(acc, 'end', {'activityType': gtype, 'challengeResult': 'SUCCESS',
                               'lat': LAT, 'lon': LNG})
    if not ok(j):
        r['status'] = 'fail'
        r['msg'] = str(j.get('msg') or j.get('code'))[:24]
        log('🎮 [%s]❌ end 失败：%s' % (name, r['msg']))
        return r
    rd(2000, 3500)

    # 分享（部分活动 draw 前必须完成分享，否则报「请先完成分享好友」）
    sj = api(acc['token'], '%s/share/%s' % (_GAME_BASE, gtype), {})
    if ok(sj):
        log('🎮 [%s]✅ 分享完成' % name)
    else:
        log('🎮 [%s]分享：%s' % (name, sj.get('msg') or str(sj.get('code'))))
    rd(1500, 2500)

    # draw 也要带经纬度（HAR 实测）
    j = game_call(acc, 'draw', {'activityType': gtype, 'lat': LAT, 'lon': LNG})
    if ok(j):
        dd = j.get('data') or {}
        award = dd.get('awardName') or dd.get('integral') or dd.get('awardNum') or ''
        r['status'] = 'ok'
        r['award'] = str(award)
        log('🎮 [%s]✅ 完成 %s' % (name, ('领取: ' + str(award)) if award else ''))
    else:
        msg = str(j.get('msg') or j.get('code'))[:24]
        # 兜底：抽奖要求先分享时，补分享再重试一次
        if '分享' in msg:
            log('🎮 [%s]draw 要求分享，补分享后重试' % name)
            api(acc['token'], '%s/share/%s' % (_GAME_BASE, gtype), {})
            rd(1500, 2500)
            j = game_call(acc, 'draw', {'activityType': gtype, 'lat': LAT, 'lon': LNG})
            if ok(j):
                dd = j.get('data') or {}
                award = dd.get('awardName') or dd.get('integral') or ''
                r['status'] = 'ok'
                r['award'] = str(award)
                log('🎮 [%s]✅ 完成 领取: %s' % (name, award))
                return r
            msg = str(j.get('msg') or j.get('code'))[:24]
        r['status'] = 'fail'
        r['msg'] = 'draw: ' + msg
        log('🎮 [%s]❌ draw 失败：%s' % (name, msg))
    return r


def run_games(acc):
    log('=' * 40)
    log('🎮 开始处理 3 个游戏任务')
    res = []
    for i, g in enumerate(GAMES):
        try:
            res.append(play_game(acc, g))
        except Exception as e:
            log('🎮 [%s]❌ 异常: %s' % (g['name'], e))
            res.append({'name': g['name'], 'status': 'fail', 'msg': str(e)[:24], 'award': ''})
        if i < len(GAMES) - 1:
            rd(5000, 8000, '游戏间隔')
    return res


# ==================== 业务 ====================
def login(acc):
    code = yyb_get_code(acc['entry'])
    log('取 wx.login code 成功: %s' % mask(code))
    # 与 JS 完全一致：{code, invite, channel}
    j = api(None, '/app-jingyoujia/login', {'code': code, 'invite': '', 'channel': ''})
    if not ok(j):
        raise RuntimeError('登录失败: %s' % (j.get('msg') or j.get('code')))
    d = j.get('data') or {}
    token = d.get('accessToken') or d.get('token') or d.get('authorization')
    if not token:
        raise RuntimeError('登录未返回 accessToken')
    acc['token'] = token
    acc['info'] = d
    expire = int(d.get('expireTime') or 0)
    ttl = max(3600, int((expire - time.time() * 1000) / 1000)) if expire else 86400
    cache = load_cache()
    cache[acc['ref']] = {'token': token, 'openId': d.get('openId', ''),
                         'custId': d.get('custId', ''), 'savedAt': int(time.time()),
                         'expiresAt': int(time.time()) + ttl}
    save_cache(cache)
    log('✅ 登录成功 ｜ 昵称: %s ｜ token 剩余 %.1f 小时' % (
        d.get('nickname') or '?', ttl / 3600.0))
    return token


def prepare(acc):
    cached = load_cache().get(acc['ref']) or {}
    token = cached.get('token')
    if token and cached.get('expiresAt', 0) > time.time() + 1800:
        log('使用缓存 token（剩 %.1f 小时）' % ((cached['expiresAt'] - time.time()) / 3600.0))
        # 验证有效性（用任务列表，比旧签到接口可靠）
        j = api(token, '/app-jingyoujia/app/jingyoujia/member/query/bigmember/task/sign/info', {})
        if ok(j):
            log('✅ 缓存 token 有效')
            acc['token'] = token
            return True
        log('缓存 token 失效，重新取码')
    login(acc)
    return True


# ---------- 签到（路径以 HAR 实测为准，前缀是 /app-jingyoujia/app/jingyoujia/）----------
SIGN_INFO_URL = '/app-jingyoujia/app/jingyoujia/member/query/bigmember/task/sign/info'
SIGN_RECORD_POST_URL = '/app-jingyoujia/app/jingyoujia/taskContinuousRecord'
SIGN_RECORD_URL = '/app-jingyoujia/app/jingyoujia/member/query/bigmember/task/sign/record'
POINT_SUMMARY_URL = '/app-jingyoujia/app/jingyoujia/member/query/point/summary'


def get_sign_info(acc):
    """POST {} → {rewardIntegral, signExtraIntegral, userContinuousDays,
                  isSignedToday, currentIntegral}"""
    j = api(acc['token'], SIGN_INFO_URL, {})
    if not ok(j):
        log('   签到接口返回: %s' % json.dumps(j, ensure_ascii=False)[:300])
        return None, (j.get('msg') or j.get('code') or '未知错误')
    return j.get('data') or {}, None


def do_sign(acc, sign_date=None):
    """POST taskContinuousRecord {"v1": AES({"taskId":0})}
       taskId 传 0 即可（HAR 实测），不需要先查 findCheckTask"""
    body = {'v1': _aes_encrypt({'taskId': 0})}
    j = api(acc['token'], SIGN_RECORD_POST_URL, body)
    if not ok(j):
        return None, (j.get('msg') or j.get('code') or '未知错误')
    return j.get('data'), None


def get_sign_record(acc):
    """查签到日历：POST {"month":"2026-11"}"""
    import datetime
    body = {'month': datetime.datetime.now().strftime('%Y-%m')}
    j = api(acc['token'], SIGN_RECORD_URL, body)
    return (j.get('data') or []) if ok(j) else []


def get_points_raw(acc):
    for method in ('GET', 'POST'):
        j = api(acc['token'], POINT_SUMMARY_URL,
                None if method == 'GET' else {}, method=method)
        if ok(j):
            return j.get('data')
    return None


def _pick_points(d):
    if isinstance(d, dict):
        for k in ('currentIntegral', 'integral', 'point', 'totalIntegral', 'balance'):
            if d.get(k) is not None:
                return d[k]
    if isinstance(d, (int, str)):
        return d
    return None

# ==================== 抓鸭（JJYSJ_2026_CATCH_DUCK）====================
DUCK_TYPE = 'JJYSJ_2026_CATCH_DUCK'
DUCK_MAX_ROUNDS = int(os.environ.get('DUCK_MAX_ROUNDS') or 2)


def duck_get_info(acc):
    j = api(acc['token'], '%s/info/%s' % (_GAME_BASE, DUCK_TYPE),
            None, method='GET')
    return (j.get('data') or {}) if ok(j) else {}


def duck_share(acc):
    j = api(acc['token'], '%s/share/%s' % (_GAME_BASE, DUCK_TYPE),
            {})
    return ok(j), (j.get('msg') or '')


def duck_play(acc, rounds=None):
    name = '抓鸭'
    r = {'name': name, 'status': 'skip', 'msg': '', 'award': ''}
    rounds = rounds or DUCK_MAX_ROUNDS

    info = duck_get_info(acc)
    st = info.get('joinState')
    if st == 1:
        r['status'] = 'already'
        r['msg'] = '今日已完成'
        log('🦆 [%s]ℹ️ 今日已完成' % name)
        return r

    # 分享领次数（失败不阻断）
    s_ok, s_msg = duck_share(acc)
    if s_ok:
        log('🦆 [%s]✅ 分享领次数成功' % name)
    else:
        log('🦆 [%s]分享领次数：%s' % (name, s_msg or '无返回'))

    awards = []
    played = 0
    for i in range(rounds):
        j = game_call(acc, 'start', {'activityType': DUCK_TYPE})
        if not ok(j):
            log('🦆 [%s]第%d局 start 失败：%s' % (name, i + 1, j.get('msg')))
            break
        start_data = j.get('data')
        rd(9000, 12000, '模拟抓鸭时长')

        payload = {'activityType': DUCK_TYPE, 'challengeResult': 'SUCCESS',
                   'lat': LAT, 'lon': LNG}
        if isinstance(start_data, dict):
            for k in ('joinId', 'id', 'gameTime'):
                if start_data.get(k) is not None:
                    payload['joinId' if k == 'id' else k] = start_data[k]

        j = game_call(acc, 'end', payload)
        if not ok(j):
            log('🦆 [%s]第%d局 end 失败：%s' % (name, i + 1, j.get('msg')))
            break
        played += 1
        d = j.get('data') or {}
        log('🦆 [%s]第%d局完成 consumed=%s joinState=%s' % (
            name, i + 1, d.get('consumed'), d.get('joinState')))
        if d.get('consumed') is True or (d.get('joinState') not in (None, 0, '0')):
            log('🦆 [%s]次数已用尽，停止' % name)
            break
        rd(2000, 3500)

    # 奖励统一在 draw 领
    if played:
        rd(1500, 2500)
        j = game_call(acc, 'draw', {'activityType': DUCK_TYPE, 'lat': LAT, 'lon': LNG})
        if ok(j):
            d = j.get('data') or {}
            award = d.get('awardName') or '未知'
            num = d.get('awardNum') or ''
            if num and str(num) != '1.00':
                award = '%s×%s' % (award, num)
            awards.append(award)
            r['award'] = award
        else:
            log('🦆 [%s]draw：%s' % (name, j.get('msg')))
            r['msg'] = (j.get('msg') or '')[:20]

    if played:
        r['status'] = 'ok'
        log('🦆 [%s]✅ 完成 %d 局 %s' % (name, played, ('领奖: ' + awards[0]) if awards else ''))
    elif r['status'] != 'already':
        r['status'] = 'fail'
        if not r['msg']:
            r['msg'] = '无法开始'
    return r


# ==================== 劲酒酒馆（夹美食）====================
def tavern_game_num(acc):
    j = api(acc['token'], '/app-jingyoujia/app/jingyoujia/game/extend/gameNum',
            None, method='GET')
    if ok(j):
        try:
            return int(j.get('data') or 0)
        except Exception:
            return 0
    return 0


def tavern_user_info(acc):
    j = api(acc['token'], '/app-jingyoujia/app/jingyoujia/game/extend/userInfo',
            None, method='GET')
    return (j.get('data') or {}) if ok(j) else {}


def tavern_play_once(acc):
    j = api(acc['token'], '/app-jingyoujia/app/jingyoujia/game/extend/playClampGuide',
            None, method='GET')
    if not ok(j):
        return None, (j.get('msg') or '进入游戏失败')

    j = game_call(acc, 'start', {'lat': LAT, 'lon': LNG})
    # 酒馆用的是自己的接口，不是 jjysj2026，这里需要重新发
    j = api(acc['token'], '/app-jingyoujia/app/jingyoujia/game/extend/playClampStart',
            {'v1': _aes_encrypt({'lat': LAT, 'lon': LNG})})
    if not ok(j) or not j.get('data'):
        return None, (j.get('msg') or 'start失败')
    play_id = str(j.get('data'))
    rd(2000, 4000)

    j = api(acc['token'], '/app-jingyoujia/app/jingyoujia/game/extend/playClampEnd',
            {'v1': _aes_encrypt({'id': play_id, 'lat': LAT, 'lon': LNG, 'done': True})})
    if not ok(j):
        return None, (j.get('msg') or 'end失败')
    d = j.get('data') or {}
    award = ((d.get('award') or {}).get('awardName')) or '未知'
    return award, None


def tavern_jump(acc, url, label):
    j = api(acc['token'], url, {'v1': _aes_encrypt({'lat': LAT, 'lon': LNG})})
    log('🍺 [酒馆]任务「%s」%s' % (label, '✅' if ok(j) else ('⚠️ ' + str(j.get('msg')))))
    return ok(j)


def tavern_claim_task_chance(acc):
    j = api(acc['token'], '/app-jingyoujia/app/jingyoujia/task/finishTaskV2',
            {'taskType': 2, 'latitude': LAT, 'longitude': LNG})
    return ok(j)


def play_tavern(acc, max_play=10):
    name = '酒馆'
    r = {'name': name, 'status': 'skip', 'msg': '', 'award': ''}
    played = 0
    last_num = -1
    awards = []

    def try_play(reason):
        nonlocal played, last_num
        if played >= max_play:
            return False
        num = tavern_game_num(acc)
        # 兜底阶段：连续两次 gameNum 都没减少，说明次数接口不生效，停止避免空转
        if reason == '兜底' and num > 0 and num == last_num:
            log('🍺 [%s]gameNum 未变化（%s），停止兜底' % (name, num))
            return False
        last_num = num
        if num <= 0:
            info = tavern_user_info(acc)
            if not (played == 0 and info.get('free')):
                log('🍺 [%s]无次数（gameNum=0），停止' % name)
                return False
        award, err = tavern_play_once(acc)
        if err:
            log('🍺 [%s]%s 游玩失败：%s' % (name, reason, err))
            return False
        played += 1
        awards.append(award or '未知')
        log('🍺 [%s]%s 完成一局，奖励：%s' % (name, reason, award))
        rd(3000, 5000)
        return True

    # 1) 每日进入赠送
    try_play('每日赠送')
    # 2) 做任务换次数
    info = tavern_user_info(acc)
    tasks = []
    if info.get('water') and not info.get('alreadyWater') and not info.get('alreadyDoneWater'):
        tasks.append(('浇水', '/app-jingyoujia/app/jingyoujia/game/extend/waterJump', True))
    if info.get('healthChinaTour') and not info.get('alreadyHealthChinaTour'):
        tasks.append(('健康中国游', '/app-jingyoujia/app/jingyoujia/game/extend/jumpHealthChinaTour', False))
    if info.get('wine') and not info.get('alreadyWine'):
        tasks.append(('去酿造', '/app-jingyoujia/app/jingyoujia/game/extend/jumpWine', False))
    if not info.get('alreadyShare') and not info.get('alreadyDoneShare'):
        tasks.append(('分享', '/app-jingyoujia/app/jingyoujia/game/extend/share', False))

    for label, url, need_water in tasks:
        tavern_jump(acc, url, label)
        rd(1000, 2000)
        if need_water:
            j = api(acc['token'], '/app-jingyoujia/app/jingyoujia/game/water',
                    {'v1': _aes_encrypt({'lat': LAT, 'lon': LNG})})
            log('💧 [酒馆]庄园浇水 %s' % ('✅' if ok(j) else (j.get('msg') or '')))
            rd(1000, 2000)
        if tavern_claim_task_chance(acc):
            log('✅ [酒馆]领取任务次数（%s）' % label)
            rd(1000, 2000)
            try_play('任务奖励·' + label)
    # 3) 兜底
    while try_play('兜底'):
        pass

    if played:
        r['status'] = 'ok'
        r['award'] = ('%d局' % played) + (' ' + awards[0] if awards else '')
        log('🍺 [%s]✅ 共游玩 %d 局' % (name, played))
    else:
        r['status'] = 'fail'
        r['msg'] = '无可用次数'
        log('🍺 [%s]ℹ️ 无可用次数' % name)
    return r


# ==================== 庄园换酒 ====================
def wine_convert(acc):
    name = '换酒'
    r = {'name': name, 'status': 'skip', 'msg': '', 'award': ''}

    j = api(acc['token'], '/app-jingyoujia/app/jingyoujia/game/wineConvert/rule',
            None, method='GET')
    rule = (j.get('data') or {}) if ok(j) else {}
    if str(rule.get('isEnable')) != '1':
        r['msg'] = '未开启'
        log('🍷 [换酒]未开启，跳过')
        return r

    j = api(acc['token'], '/app-jingyoujia/app/jingyoujia/game/user/userInfo',
            None, method='GET', params={'lat': LAT, 'lon': LNG})
    wine = int((j.get('data') or {}).get('wineNum') or 0) if ok(j) else 0
    if wine <= 0:
        r['msg'] = '无酒可换'
        log('🍷 [换酒]没有可换的酒')
        return r

    j = api(acc['token'], '/app-jingyoujia/app/jingyoujia/game/wineConvert/convert',
            {'latitude': LAT, 'longitude': LNG, 'wineNum': wine})
    if ok(j):
        rate = int(rule.get('integralNum') or 5)
        pts = wine * rate
        r['status'] = 'ok'
        r['award'] = '%d瓶 → +%d积分' % (wine, pts)
        log('🍷 [换酒]✅ %d 瓶 × %d 分 = +%d 积分' % (wine, rate, pts))
    else:
        r['status'] = 'fail'
        r['msg'] = (j.get('msg') or '')[:20]
        log('🍷 [换酒]❌ %s' % r['msg'])
    return r


# ==================== 集章手册 ====================
MANUAL_TYPE = 'JJYSJ_2026_CHECKIN_MANUAL'


def stamp_manual(acc):
    name = '集章'
    r = {'name': name, 'status': 'skip', 'msg': '', 'award': ''}
    j = api(acc['token'], '%s/manual/state' % _GAME_BASE, None, method='GET')
    st = (j.get('data') or {}) if ok(j) else {}
    cnt = st.get('collectedCount') or 0
    if not st.get('collected'):
        r['msg'] = '未集齐(%s章)' % cnt
        log('📖 [集章]还没集齐（%s/3），跳过' % cnt)
        return r
    if st.get('drawnToday'):
        r['status'] = 'already'
        r['msg'] = '今日已抽'
        log('📖 [集章]今日已抽过')
        return r

    j = game_call(acc, 'draw', {'activityType': MANUAL_TYPE})
    if ok(j):
        d = j.get('data') or {}
        award = d.get('awardName') or '未知'
        r['status'] = 'ok'
        r['award'] = award
        log('📖 [集章]🎁 %s' % award)
    else:
        r['status'] = 'fail'
        r['msg'] = (j.get('msg') or '')[:20]
    return r


# ==================== 其他活动汇总 ====================
def run_extra_tasks(acc):
    """换酒 + 集章 + 抓鸭"""
    log('=' * 40)
    log('🧩 开始处理扩展任务')
    res = []
    for fn in (wine_convert,):
        try:
            res.append(fn(acc))
        except Exception as e:
            log('❌ 扩展任务异常: %s' % e)
            res.append({'name': '换酒', 'status': 'fail', 'msg': str(e)[:20], 'award': ''})
        rd(1500, 2500)
    try:
        res.append(duck_play(acc))
    except Exception as e:
        log('❌ 抓鸭异常: %s' % e)
        res.append({'name': '抓鸭', 'status': 'fail', 'msg': str(e)[:20], 'award': ''})
    rd(2000, 3000)
    try:
        res.append(stamp_manual(acc))
    except Exception as e:
        log('❌ 集章异常: %s' % e)
        res.append({'name': '集章', 'status': 'fail', 'msg': str(e)[:20], 'award': ''})
    return res


def get_points(acc):
    for method in ('GET', 'POST'):
        j = api(acc['token'], '/app-jingyoujia/app/jingyoujia/member/query/point/summary',
                None if method == 'GET' else {}, method=method)
        if ok(j):
            d = j.get('data')
            if isinstance(d, dict):
                for k in ('currentIntegral', 'integral', 'point', 'totalIntegral', 'balance'):
                    if d.get(k) is not None:
                        return d[k]
            if isinstance(d, (int, str)):
                return d
    return None


# ==================== 单账号 ====================
def run_account(acc):
    out = {'name': acc['name'], 'sign': '-', 'before': None, 'after': None,
           'cont': 0, 'lines': [], 'error': None}
    lines = out['lines']

    prepare(acc)

    # 查状态（新接口：任务列表 + 积分）
    log('📊 查询签到状态…')
    info, err = get_sign_info(acc)
    if info is None:
        # 签到查询失败不再终止，后面任务照跑，避免一个接口拖垮全部
        log('⚠️ 签到状态查询失败: %s（继续跑后续任务）' % err)
        out['error'] = err
        lines.append('📅 签到：❌ 状态查询失败 %s' % str(err)[:24])
        out['sign'] = 'fail'
        out['before'] = _pick_points(get_points_raw(acc))
        info = {}

    signed = info.get('isSignedToday')
    cur = info.get('currentIntegral')
    cont = info.get('userContinuousDays')
    reward = info.get('rewardIntegral')
    extra = info.get('signExtraIntegral')
    out['before'] = cur
    log('   今日已签: %s ｜ 积分 %s ｜ 连签 %s 天 ｜ 今日可得 +%s%s' % (
        '是' if signed else '否', cur, cont, reward,
        ('（额外 +%s）' % extra) if extra else ''))

    # 签到
    if signed:
        log('📅 签到：ℹ️ 今日已签到')
        lines.append('📅 签到：ℹ️ 今日已签')
        out['sign'] = 'already'
        out['after'] = cur
        out['cont'] = cont
    else:
        log('📅 签到：今日未签，开始签到…')
        rd(1200, 2500)
        d, e2 = do_sign(acc)
        if d is None:
            log('📅 签到：❌ %s' % e2)
            lines.append('📅 签到：❌ %s' % str(e2)[:30])
            out['sign'] = 'fail'
        else:
            got = (d.get('currentSignIntegral') if isinstance(d, dict) else None) or reward
            log('📅 签到：✅ 成功 %s' % (('+%s' % got) if got else ''))
            out['sign'] = 'ok'
            rd(1000, 2000, '等待入账')
            out['after'] = _pick_points(get_points_raw(acc))
            info2, _ = get_sign_info(acc)
            if info2:
                if out['after'] is None:
                    out['after'] = info2.get('currentIntegral')
                out['cont'] = info2.get('userContinuousDays')
            lines.append('📅 签到：✅ 成功  +%s' % (got or '?'))

    # 签到后复查（此时还只做了签到，先记录一个中间值）
    if out['after'] is None:
        info3, _ = get_sign_info(acc)
        if info3:
            out['after'] = info3.get('currentIntegral')
            out['cont'] = info3.get('userContinuousDays')
        if out['after'] is None:
            out['after'] = _pick_points(get_points_raw(acc))

    # 三个游戏任务（AES v1）
    rd(2500, 4500)
    games = run_games(acc)
    out['games'] = games
    gok = [g for g in games if g['status'] in ('ok', 'already')]
    log('🎮 游戏完成 %d/%d' % (len(gok), len(games)))
    for g in games:
        mark = {'ok': '✅', 'already': 'ℹ️', 'fail': '❌'}.get(g['status'], '·')
        lines.append('🎮 %s：%s %s' % (g['name'], mark,
                                      g.get('award') or g.get('msg') or ''))

    # 扩展任务：换酒 → 抓鸭 → 集章
    rd(4000, 6000)
    extras = run_extra_tasks(acc)
    out['extras'] = extras
    for g in extras:
        mark = {'ok': '✅', 'already': 'ℹ️', 'fail': '❌'}.get(g['status'], '·')
        lines.append('🧩 %s：%s %s' % (g['name'], mark,
                                      g.get('award') or g.get('msg') or ''))

    # 酒馆（独立模块，有次数上限，放最后避免和庄园浇水打架）
    rd(2000, 3500)
    try:
        tv = play_tavern(acc)
        out['tavern'] = tv
        mark = {'ok': '✅', 'already': 'ℹ️', 'fail': '❌'}.get(tv['status'], '·')
        lines.append('🍺 酒馆：%s %s' % (mark, tv.get('award') or tv.get('msg') or ''))
    except Exception as e:
        log('❌ 酒馆异常: %s' % e)
        out['tavern'] = {'name': '酒馆', 'status': 'fail', 'msg': str(e)[:20]}
        lines.append('🍺 酒馆：❌ %s' % str(e)[:20])

    # 任务后再复查一次积分
    if out.get('games') or out.get('extras') or out.get('tavern'):
        rd(1200, 2200, '等待入账')
        info4, _ = get_sign_info(acc)
        if info4 and info4.get('currentIntegral') is not None:
            out['after'] = info4.get('currentIntegral')
            out['cont'] = info4.get('userContinuousDays')

    if out['after'] is not None:
        gained = None
        try:
            gained = int(out['after']) - int(out['before'])
        except Exception:
            pass
        log('💰 积分：%s → %s%s ｜ 连签 %s 天' % (
            out['before'], out['after'],
            ('  (%+d)' % gained) if gained else '', out['cont']))
        lines.append('💰 积分：%s → %s%s ｜ 连签 %s 天' % (
            out['before'], out['after'],
            ('  (%+d)' % gained) if gained else '', out['cont']))
    else:
        log('⚠️ 积分查询失败')
        lines.append('💰 积分：—')
    return out


# ==================== 推送 ====================
NOTIFY_TITLE = '劲友家签到 🍶'


def notify(title, content, timeout=60):
    res = {'ok': False, 'err': None}

    def _go():
        try:
            from notify import send
            send(title, content)
            res['ok'] = True
            return
        except ImportError:
            pass
        except Exception as e:
            res['err'] = str(e)[:120]
            return
        for p in ('/ql/scripts/notify.py', '/ql/data/scripts/notify.py'):
            if not os.path.exists(p):
                continue
            try:
                import importlib.util
                sp = importlib.util.spec_from_file_location('qlnotify', p)
                mm = importlib.util.module_from_spec(sp)
                sp.loader.exec_module(mm)
                mm.send(title, content)
                res['ok'] = True
                return
            except Exception as e:
                res['err'] = '%s: %s' % (p, str(e)[:100])

    t = threading.Thread(target=_go, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        log('⚠️ 推送超时（%ds），已放弃' % timeout)
        return False
    if res['ok']:
        log('✅ 推送已发送')
        return True
    log('⚠️ 推送未发送：%s' % (res['err'] or '未找到 notify.py'))
    return False


def _pad(s, width):
    s = str(s)
    w = sum(2 if ord(c) > 127 else 1 for c in s)
    return s + ' ' * max(0, width - w)


def build_report(results):
    if not results:
        return ''
    lines = ['【劲友家】']
    total = 0
    for r in results:
        if r.get('error'):
            lines.append('❌  %s  %s' % (_pad(r['name'], 12), str(r['error'])[:20]))
            continue
        mark = {'ok': '✅', 'already': '·', 'fail': '❌'}.get(r.get('sign'), '·')
        line = '%s  %s' % (mark, _pad(r['name'], 12))
        if r.get('after') is not None:
            line += '⭐%s' % r['after']
            try:
                g = int(r['after']) - int(r['before'])
                if g > 0:
                    line += '  (+%d)' % g
                    total += g
            except Exception:
                pass
        else:
            line += '⭐—'
        if r.get('cont'):
            line += '  ｜ 连签 %s 天' % r['cont']
        lines.append(line)
        # 游戏明细
        games = r.get('games') or []
        if games:
            gs = []
            for g in games:
                m = {'ok': '✅', 'already': '·', 'fail': '❌'}.get(g['status'], '·')
                tail = g.get('award') or g.get('msg') or ''
                gs.append('%s%s%s' % (m, g['name'], ('(' + tail + ')') if tail else ''))
            lines.append('    🎮 ' + ' '.join(gs))
        # 扩展任务明细
        extras = r.get('extras') or []
        if extras:
            es = []
            for g in extras:
                m = {'ok': '✅', 'already': '·', 'fail': '❌'}.get(g['status'], '·')
                tail = g.get('award') or g.get('msg') or ''
                es.append('%s%s%s' % (m, g['name'], ('(' + tail + ')') if tail else ''))
            lines.append('    🧩 ' + ' '.join(es))
        # 酒馆
        tv = r.get('tavern')
        if tv:
            m = {'ok': '✅', 'already': '·', 'fail': '❌'}.get(tv['status'], '·')
            tail = tv.get('award') or tv.get('msg') or ''
            lines.append('    🍺 酒馆%s%s' % (m, ('(' + tail + ')') if tail else ''))
    lines.append('')
    lines.append(('📊 今日 +%d 积分' % total) if total else '📊 今日无新增')
    return '\n'.join(lines)


# ==================== 主流程 ====================
def main():
    global _ACC
    resolve_location()          # 坐标开关：只改脚本顶部 CITY

    entries = parse_entries()
    if not entries:
        log('未设置或格式错误：%s = YYB地址#ref（多账号 & 或换行）' % ENV_NAME)
        log('   定位: %s (%.6f, %.6f)' % (CITY, LAT, LNG))
        return

    log('共 %d 个账号 ｜ 定位 %s (%.6f, %.6f)' % (len(entries), CITY, LAT, LNG))
    results = []
    for i, e in enumerate(entries):
        acc = {'name': e['name'], 'entry': e, 'ref': e['ref']}
        _ACC = acc
        log('=' * 50)
        log('📱 账号 %d/%d 开始' % (i + 1, len(entries)))
        log('=' * 50)
        try:
            results.append(run_account(acc))
        except Exception as ex:
            log('❌ 执行异常: %s' % ex)
            results.append({'name': acc['name'], 'error': str(ex)[:60],
                            'lines': [], 'before': None, 'after': None})
        if i < len(entries) - 1:
            rd(6000, 12000, '账号间间隔')

    text = build_report(results)
    print('\n' + '=' * 50)
    print(text)
    print('=' * 50)
    notify(NOTIFY_TITLE, text)


if __name__ == '__main__':
    main()
