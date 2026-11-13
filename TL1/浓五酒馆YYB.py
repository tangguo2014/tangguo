# -*- coding: utf-8 -*-
"""
浓五酒馆 · 签到领积分 + 浓友购抽奖（YYB 取码版）

两套系统、两个 appid、各取一次 code：
  ① 浓五酒馆   wxed3cf95a14b58a26   https://stdcrm.dtmiller.com
     签到 → +5 积分，累计/连续天数
  ② 浓友购     wx99fa98e883130aa3   https://www.wlnxjc.com:8088
     积分 ≥84 才抽奖，最多 3 次，抽完自动领奖

变量：
  yyb_gpt = YYB地址#ref，多账号用 & 或换行
            http://12.17.0.25:8000#1
  YYB_PROTOCOL_TOKEN   YYB 开了协议鉴权时填（可选）

推送：走 notify.py（青龙系统配置的企业微信机器人）
"""

import json
import os
import random
import re
import time
import traceback

import requests

# ==================== 常量 ====================
BASE_API = 'https://stdcrm.dtmiller.com'
APP_ID = 'wxed3cf95a14b58a26'
PROMOTION_ID = 'PI6a954b807db355000ad6cc8e'      # 兜底签到活动 id

NYG_BASE_API = 'https://www.wlnxjc.com:8088'
APP_ID_NYG = 'wx99fa98e883130aa3'
NYG_ACTIVITY_ID = ''

YYB_TOKEN = (os.environ.get('YYB_PROTOCOL_TOKEN') or '').strip()

USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 '
              'MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI '
              'MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) '
              'UnifiedPCWindowsWechat(0xf254191e) XWEB/19769')

CACHE_DIR = '/ql/data/cache'
MIN_POINTS_FOR_LOTTERY = 84
MAX_DRAW = 3

_resolved_promotion_id = ''

# ==================== 日志 ====================
def log(msg):
    print('[浓五酒馆] %s' % msg, flush=True)


def wait(sec):
    time.sleep(sec)


def random_wait():
    wait(random.uniform(8, 15))


def mask_mobile(m):
    s = str(m or '')
    if len(s) >= 7:
        return s[:3] + '****' + s[-4:]
    return s or '未绑定'


# ==================== 缓存 ====================
def _cache_path(ref, kind='token'):
    return os.path.join(CACHE_DIR, 'nwjg_%s_ref%s.json' % (kind, ref))


def _read_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _write_json(path, data):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log('缓存写入失败: %s' % e)


def get_cached_token(ref):
    d = _read_json(_cache_path(ref, 'token'))
    t = d.get('token')
    if t and d.get('date') == time.strftime('%Y-%m-%d'):
        return t
    return ''


def save_token_cache(ref, token):
    _write_json(_cache_path(ref, 'token'),
                {'token': token, 'date': time.strftime('%Y-%m-%d')})


def is_today_lottery_done(ref):
    d = _read_json(_cache_path(ref, 'lottery'))
    return d.get('date') == time.strftime('%Y-%m-%d')


def save_lottery_done(ref):
    _write_json(_cache_path(ref, 'lottery'),
                {'date': time.strftime('%Y-%m-%d')})


# ==================== YYB 取码 ====================
def parse_yyb_entries():
    raw = (os.environ.get('yyb_gpt') or '').strip()
    if not raw:
        return []
    out = []
    for item in re.split(r'[&\n]', raw):
        item = item.strip()
        if not item:
            continue
        if '#' in item:
            base, ref = item.rsplit('#', 1)
        else:
            base, ref = item, ''
        base = base.rstrip('/')
        ref = ref.strip()
        if base and ref:
            out.append({'base': base, 'ref': ref})
    return out


def yyb_get_code(entry, app_id, retries=2):
    """从 YYB 取 wx.login code"""
    base, ref = entry['base'], entry['ref']
    last = ''
    for i in range(retries + 1):
        try:
            headers = {'Content-Type': 'application/json'}
            if YYB_TOKEN:
                headers['Authorization'] = 'Bearer ' + YYB_TOKEN
            r = requests.post('%s/wxapp/getCode' % base,
                              headers=headers,
                              json={'ref': str(ref), 'app_id': app_id},
                              timeout=40)
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
            last = str(e)[:150]

        # 熔断 / 微信拒绝：重试无意义
        if re.search(r'熔断|code=-14|shortlink|授权已过期|409', last):
            raise RuntimeError(last)
        if i < retries:
            wait(3)
    raise RuntimeError(last or 'YYB 取码失败')


# ==================== 浓五酒馆 ====================
def wly_login(code, retries=2):
    last = ''
    for i in range(retries + 1):
        try:
            r = requests.post('%s/std-weixin-mp-service/miniApp/custom/login' % BASE_API,
                              headers={'User-Agent': USER_AGENT,
                                       'Content-Type': 'application/json'},
                              json={'code': code, 'appId': APP_ID},
                              timeout=45)
            d = r.json()
            token = None
            if d.get('code') == 0 and d.get('data'):
                data = d['data']
                if isinstance(data, str):
                    token = data
                elif isinstance(data, dict):
                    token = data.get('token') or data.get('accessToken') or data.get('access_token')
            if not token:
                token = d.get('token') or d.get('accessToken') or d.get('access_token')
            if token:
                log('登录成功，token: %s...' % token[:20])
                return token
            last = '登录失败: %s' % (d.get('msg') or d.get('message') or json.dumps(d, ensure_ascii=False)[:150])
        except Exception as e:
            last = '登录异常: %s' % e
        if i < retries:
            wait(2)
    raise RuntimeError(last)


def wly_member_info(token, idx):
    try:
        r = requests.get('%s/scrm-promotion-service/mini/wly/user/info' % BASE_API,
                         headers={'Authorization': 'Bearer %s' % token, 'User-Agent': USER_AGENT},
                         timeout=30)
        d = r.json()
        if d.get('code') == 0 and d.get('data'):
            member = d['data'].get('member') or d['data']
            info = {
                'nickName': member.get('nick_name') or member.get('nickName') or '未知',
                'mobile': member.get('mobile') or '未绑定',
                'points': member.get('points') or 0,
                'levelCode': member.get('level_code') or member.get('levelCode') or '普通会员',
                'avatar': member.get('avatar_url') or member.get('avatarUrl') or '',
            }
            log('账号[%d] | 昵称: %s | 手机号: %s | 积分: %s | 等级: %s'
                % (idx, info['nickName'], mask_mobile(info['mobile']),
                   info['points'], info['levelCode']))
            return info
        log('账号[%d] 获取会员信息失败: %s' % (idx, d.get('msg', '')))
        return None
    except Exception as e:
        log('账号[%d] 获取会员信息异常: %s' % (idx, e))
        return None


def extract_sign_promotion_ids(config_list):
    ids = []

    def push(i):
        if i and i not in ids:
            ids.append(i)

    for module in (config_list or []):
        for detail in (module.get('detailList') or []):
            try:
                dj = detail.get('detailJson')
                obj = json.loads(dj) if isinstance(dj, str) else dj
                page_path = ((obj or {}).get('jumpData') or {}).get('pagePath') or ''
                title = (obj or {}).get('title') or ''
                m = re.search(r'promotionId=(PI[a-zA-Z0-9]+)', page_path)
                if m:
                    push(m.group(1))
                elif '每日签到' in title and m:
                    push(m.group(1))
            except Exception:
                pass

    if not ids:
        raw = json.dumps(config_list or '', ensure_ascii=False)
        for m in re.finditer(r'signUp/signUp\?[^"\']*?promotionId=(PI[a-zA-Z0-9]+)', raw):
            push(m.group(1))
    return ids


def resolve_sign_promotion_id(token, idx):
    """动态找当前进行中的签到活动，失败则用兜底 id"""
    global _resolved_promotion_id
    if _resolved_promotion_id:
        return _resolved_promotion_id

    try:
        r = requests.post('%s/scrm-promotion-service/mini/module/config/list' % BASE_API,
                          headers={'Authorization': 'Bearer %s' % token,
                                   'Content-Type': 'application/json',
                                   'User-Agent': USER_AGENT},
                          json={}, timeout=30)
        d = r.json()
        if d.get('code') == 0:
            cands = extract_sign_promotion_ids(d.get('data'))
            if cands:
                log('账号[%d] 从首页配置获取到签到活动候选: %s' % (idx, ', '.join(cands)))
                for pid in cands:
                    try:
                        rr = requests.get('%s/scrm-promotion-service/promotion/sign/userinfo' % BASE_API,
                                          params={'promotionId': pid},
                                          headers={'Authorization': 'Bearer %s' % token,
                                                   'User-Agent': USER_AGENT},
                                          timeout=30)
                        dd = rr.json()
                        info = dd.get('data')
                        if dd.get('code') == 0 and info:
                            if info.get('promotionStatus') == 1:
                                _resolved_promotion_id = pid
                                log('账号[%d] 签到活动动态获取成功: [%s] 有效期 %s ~ %s'
                                    % (idx, info.get('promotionName'),
                                       info.get('startTime'), info.get('endTime')))
                                return _resolved_promotion_id
                    except Exception:
                        pass
            else:
                log('账号首页配置中未找到签到入口')
    except Exception as e:
        log('动态获取签到活动异常: %s' % e)

    log('使用兜底签到活动ID: %s' % PROMOTION_ID)
    return PROMOTION_ID


def wly_sign_userinfo(token, idx, promotion_id):
    try:
        r = requests.get('%s/scrm-promotion-service/promotion/sign/userinfo' % BASE_API,
                         params={'promotionId': promotion_id},
                         headers={'Authorization': 'Bearer %s' % token,
                                  'User-Agent': USER_AGENT},
                         timeout=30)
        d = r.json()
        if d.get('code') == 0 and d.get('data'):
            u = d['data']
            log('账号[%d] | 今日状态: %s | 累计签到: %s天 | 连续签到: %s天'
                % (idx, '已签到' if u.get('today') else '未签到',
                   u.get('signDays'), u.get('nextContinuousDay')))
            return {'today': u.get('today'),
                    'signDays': u.get('signDays'),
                    'nextContinuousDay': u.get('nextContinuousDay')}
        log('账号[%d] 获取签到信息失败: %s' % (idx, d.get('msg', '')))
        return None
    except Exception as e:
        log('账号[%d] 获取签到信息异常: %s' % (idx, e))
        return None


def wly_sign(token, idx, promotion_id):
    try:
        r = requests.get('%s/scrm-promotion-service/promotion/sign/today' % BASE_API,
                         params={'promotionId': promotion_id},
                         headers={'Authorization': 'Bearer %s' % token,
                                  'User-Agent': USER_AGENT},
                         timeout=30)
        d = r.json()
        if d.get('code') == 0 and d.get('data'):
            res = d['data']
            if res.get('today'):
                log('账号[%d] 今日已签到，无需重复签到' % idx)
                return 'already'
            prize = (res.get('prize') or {}).get('prizeName') or '未知奖励'
            log('账号[%d] 签到成功 🎉 | 获得: %s' % (idx, prize))
            return 'success'
        msg = str(d.get('msg') or '未知错误')
        # 服务端用文案表示"已签到"，这不算失败
        if re.search(r'已签到|今天已签|重复签到', msg):
            log('账号[%d] 今日已签到（服务端）' % idx)
            return 'already'
        log('账号[%d] 签到失败: %s' % (idx, msg))
        return False
    except Exception as e:
        raise RuntimeError('签到请求异常: %s' % e)


# ==================== 浓友购（抽奖） ====================
NYG_HEADERS_BASE = {'User-Agent': USER_AGENT,
                    'Content-Type': 'application/json;charset=UTF-8',
                    'platform': 'WechatMiniProgram',
                    'tenant-id': '1',
                    'terminal': '10'}


def nyg_headers(token):
    h = dict(NYG_HEADERS_BASE)
    h['authorization'] = token
    return h


def nyg_login(code, idx):
    try:
        r = requests.post('%s/app-api/member/auth/weixin-mini-app-login' % NYG_BASE_API,
                          headers=NYG_HEADERS_BASE,
                          json={'loginCode': code, 'state': 'default', 'sourceId': '1'},
                          timeout=45)
        d = r.json()
        if d.get('code') == 0 and d.get('data'):
            data = d['data']
            token = (data.get('accessToken') or data.get('token')
                     or (data.get('userInfo') or {}).get('token')) if isinstance(data, dict) else None
            if token:
                log('账号[%d] 浓友购登录成功' % idx)
                return token
        log('账号[%d] 浓友购登录失败: %s' % (idx, d.get('msg', '')))
        return None
    except Exception as e:
        log('账号[%d] 浓友购登录异常: %s' % (idx, e))
        return None


def nyg_integral(token, idx):
    try:
        r = requests.get('%s/app-api/member/integral/get' % NYG_BASE_API,
                         headers=nyg_headers(token), timeout=30)
        d = r.json()
        if d.get('code') == 0:
            v = (d.get('data') or {}).get('integral')
            log('账号[%d] 浓友购积分: %s' % (idx, v))
            return v or 0
        return 0
    except Exception as e:
        log('账号[%d] 查询浓友购积分异常: %s' % (idx, e))
        return 0


def nyg_active_activity(token):
    try:
        r = requests.get('%s/app-api/promotion/activity/list' % NYG_BASE_API,
                         params={'type': 2, 'pageNo': 1, 'pageSize': 10},
                         headers=nyg_headers(token), timeout=30)
        d = r.json()
        if d.get('code') == 0:
            data = d.get('data') or {}
            lst = data.get('list') or data.get('records') or (data if isinstance(data, list) else [])
            if lst:
                active = next((a for a in lst
                               if a.get('status') == 1 or a.get('statusName') == '进行中'), lst[0])
                log('获取到活动: [%s]' % active.get('name'))
                return str(active.get('id'))
    except Exception as e:
        log('获取活动列表异常: %s' % e)
    log('未找到可用活动，使用默认活动ID')
    return NYG_ACTIVITY_ID


def nyg_draw(token, activity_id, idx):
    try:
        r = requests.post('%s/app-api/promotion/activity/draw' % NYG_BASE_API,
                          params={'activityId': activity_id},
                          headers=nyg_headers(token),
                          json={'activityId': activity_id},
                          timeout=30)
        d = r.json()
        if d.get('code') == 0:
            data = d.get('data') or {}
            lottery = data.get('lottery')
            if lottery:
                log('账号[%d] 抽奖成功: [%s] - %s'
                    % (idx, lottery.get('awardName'), lottery.get('prizeName')))
                return {'recordId': data.get('recordId'),
                        'awardName': lottery.get('awardName'),
                        'prizeName': lottery.get('prizeName'),
                        'integral': lottery.get('integral')}
        log('账号[%d] 抽奖失败: %s' % (idx, d.get('msg', '未知错误')))
        return None
    except Exception as e:
        log('账号[%d] 抽奖异常: %s' % (idx, e))
        return None


def nyg_receive(token, record_id, idx):
    try:
        r = requests.post('%s/app-api/promotion/activity/receive' % NYG_BASE_API,
                          headers=nyg_headers(token),
                          json={'id': record_id}, timeout=30)
        d = r.json()
        if d.get('code') == 0:
            log('账号[%d] 领奖成功' % idx)
            return True
        log('账号[%d] 领奖失败: %s' % (idx, d.get('msg', '未知错误')))
        return False
    except Exception as e:
        log('账号[%d] 领奖异常: %s' % (idx, e))
        return False


def do_lottery(entry, points, idx):
    if points < MIN_POINTS_FOR_LOTTERY:
        log('账号[%d] 积分[%s]<%d，跳过抽奖任务' % (idx, points, MIN_POINTS_FOR_LOTTERY))
        return {'success': True, 'results': [],
                'message': '积分不足(需%d分)，当前%s分' % (MIN_POINTS_FOR_LOTTERY, points)}

    if is_today_lottery_done(entry['ref']):
        return {'success': True, 'results': [], 'message': '今日已完成抽奖'}

    log('账号[%d] 积分[%s]>=%d，开始浓友购抽奖' % (idx, points, MIN_POINTS_FOR_LOTTERY))

    code = yyb_get_code(entry, APP_ID_NYG)
    log('账号[%d] 取码成功: %s...' % (idx, code[:12]))
    nyg_token = nyg_login(code, idx)
    if not nyg_token:
        return {'success': False, 'results': [], 'message': '浓友购登录失败'}

    nyg_integral(nyg_token, idx)
    activity_id = nyg_active_activity(nyg_token)
    if not activity_id:
        return {'success': True, 'results': [], 'message': '未找到可用活动'}

    results = []
    for i in range(MAX_DRAW):
        wait(random.uniform(1, 3))
        r = nyg_draw(nyg_token, activity_id, idx)
        if r:
            results.append(r)
            nyg_receive(nyg_token, r['recordId'], idx)

    save_lottery_done(entry['ref'])
    log('========== 账号[%d] 抽奖结果 ==========' % idx)
    for i, r in enumerate(results):
        log('  第%d次: [%s] - %s' % (i + 1, r.get('awardName'), r.get('prizeName')))
    log('=====================================')

    nyg_integral(nyg_token, idx)
    return {'success': True, 'results': results,
            'message': '完成%d/%d次抽奖' % (len(results), MAX_DRAW)}


# ==================== 单账号主流程 ====================
def run_account(entry, idx):
    log('\n========== 账号%d [ref=%s] ==========' % (idx, entry['ref']))
    out = {'remark': '账号%d' % idx, 'success': False, 'signStatus': '未知',
           'signDays': '未知', 'nextContinuousDay': '未知', 'nickName': '未知',
           'mobile': '未绑定', 'points': 0, 'levelCode': '普通会员',
           'lotteryResults': [], 'lotteryMessage': '', 'error': None}

    try:
        # ---- 酒馆 token ----
        token = get_cached_token(entry['ref'])
        if token:
            log('账号[%d] 使用缓存 token' % idx)
        else:
            log('账号[%d] 无缓存 token，开始取码...' % idx)
            code = yyb_get_code(entry, APP_ID)
            log('账号[%d] 取码成功: %s...' % (idx, code[:12]))
            token = wly_login(code)
            save_token_cache(entry['ref'], token)
            log('账号[%d] ✅ Token 已缓存' % idx)

        member = wly_member_info(token, idx)
        if member:
            out.update(nickName=member['nickName'], mobile=member['mobile'],
                       points=member['points'], levelCode=member['levelCode'])

        # ---- 签到 ----
        pid = resolve_sign_promotion_id(token, idx)
        info = wly_sign_userinfo(token, idx, pid)

        signed_ok = False
        if info is None:
            out['signStatus'] = '获取签到信息失败'
            out['error'] = '请先成为品牌会员～'
            log('账号[%d] 签到失败: %s' % (idx, out['error']))
        else:
            out['signDays'] = info.get('signDays', '未知')
            out['nextContinuousDay'] = info.get('nextContinuousDay', '未知')
            if info.get('today'):
                log('账号[%d] 今日已签到（userinfo），跳过签到请求' % idx)
                res = 'already'
            else:
                res = wly_sign(token, idx, pid)
            if res == 'already':
                out['signStatus'] = '今日已签到'
                signed_ok = True
            elif res == 'success':
                out['signStatus'] = '签到成功'
                signed_ok = True
            else:
                out['signStatus'] = '签到失败'

        # 签到后刷新积分
        member2 = wly_member_info(token, idx)
        if member2:
            out['points'] = member2['points']

        # ---- 抽奖 ----
        lot = do_lottery(entry, out['points'], idx)
        out['lotteryResults'] = lot.get('results', [])
        out['lotteryMessage'] = lot.get('message', '')
        if not lot.get('success'):
            out['error'] = out['error'] or lot.get('message')

        out['success'] = signed_ok
        return out

    except Exception as e:
        log('账号[%d] 处理失败: %s' % (idx, e))
        out['signStatus'] = '异常'
        out['error'] = str(e)[:120]
        return out


# ==================== 推送 ====================
def build_notify(results):
    ok = sum(1 for r in results if r['success'])
    lines = ['共 %d 个账号 ｜ 成功 %d ｜ 失败 %d' % (len(results), ok, len(results) - ok), '']

    for r in results:
        lines.append('【%s】' % r['remark'])
        lines.append('👤 %s ｜ %s ｜ 手机 %s'
                     % (r['nickName'], r['levelCode'], mask_mobile(r['mobile'])))

        icon = 'ℹ️' if r['signStatus'] == '今日已签到' else ('✅' if r['signStatus'] == '签到成功' else '❌')
        bits = ['%s 签到：%s' % (icon, r['signStatus'])]
        if r['nextContinuousDay'] not in ('未知', None):
            bits.append('连续 %s 天' % r['nextContinuousDay'])
        if r['signDays'] not in ('未知', None):
            bits.append('累计 %s 天' % r['signDays'])
        lines.append('📅 ' + ' ｜ '.join(bits))

        if r['lotteryResults']:
            names = [x.get('prizeName') or x.get('awardName') for x in r['lotteryResults']]
            lines.append('🎁 抽奖：%s' % ' / '.join([n for n in names if n]))
        elif r['lotteryMessage']:
            lines.append('🎁 抽奖：%s' % r['lotteryMessage'])

        lines.append('💰 积分 %s' % r['points'])
        if r['error']:
            lines.append('⚠️ %s' % r['error'])
        lines.append('')

    return '\n'.join(lines).strip()


def notify(title, content):
    """青龙 notify.py；找不到就只打日志"""
    try:
        from notify import send
        send(title, content)
        log('✅ 推送已发送')
        return True
    except ImportError:
        pass
    except Exception as e:
        log('推送异常: %s' % e)

    for p in ('/ql/scripts/notify.py', '/ql/data/scripts/notify.py'):
        if os.path.exists(p):
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location('qlnotify', p)
                m = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(m)
                m.send(title, content)
                log('✅ 推送已发送（%s）' % p)
                return True
            except Exception as e:
                log('推送异常(%s): %s' % (p, e))

    log('未找到 notify.py，仅输出日志：\n%s\n%s' % (title, content))
    return False


# ==================== 主流程 ====================
def main():
    entries = parse_yyb_entries()
    if not entries:
        log('未设置或格式错误：yyb_gpt = 地址#ref（多账号用 & 或换行）')
        return

    log('共加载 %d 个账号' % len(entries))

    results = []
    for i, entry in enumerate(entries, 1):
        results.append(run_account(entry, i))
        if i < len(entries):
            random_wait()

    content = build_notify(results)
    print('\n' + content, flush=True)
    notify('浓五酒馆签到+抽奖', content)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        notify('浓五酒馆 异常', traceback.format_exc().splitlines()[-1][:300])
