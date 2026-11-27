#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
vx小程序  好想来零食乐园 · 签到
================================================================================

【变量】
    hxl_gpt = 备注#Cookie＋_ref_extra（张三#ck;ref）
              Cookie 从抓包 sign/action 请求头的 Cookie 整行复制，
              必须包含 appKey / openId / userId / ls / unionId 这几项
              多账号：& 或换行分隔

【_ref_extra】

    取值：Referer 里 devVersion=DV100& 后面那一整段
    例： devVersion=DV100&这里开始复制ckuiq=scee9ssg&qrg=st3uankbplw9
    
【可选】
    XL_HOST        活动域名（后缀会变 l077/ohpk，脚本会自动探测）
    XL_PLAN_ID     活动ID，每月轮换时改
    XL_ENABLED     0/false/off 停用

【每月轮换提醒】
    这是月度活动，每月会新建一个新活动（新的 activityPlanId）。
    换月后：抓包新的 sign/action 请求，从 Referer 里取 activityPlanId 填到 XL_PLAN_ID
    脚本会自动核对：活动名前缀（如「11月」）+ 活动起止日期，
    过期或名称不符会明确提示，不会静默失败。

【定时】
    cron: 0 9 * * 

【签名算法】（已破解验证）
    xmSign = MD5( 按 key 升序拼接的参数值 + 盐 )
    - GET : 参数 = URL 查询串 + nonceStr + xmTimestamp
    - POST: 参数 = body + nonceStr + xmTimestamp + xmToken
    盐 = "uh3$Hg&^HK876%gbxVG7f$%p=0M~>s1x"（base64 解出）

================================================================================
"""
import os
import re
import sys
import time
import json
import base64
import random
import string
import hashlib

import requests

ENV_NAME = 'hxl_gpt'
# 小满实例后缀会变（l077 → ohpk → ...），运行时自动探测
HOST_CANDIDATES = [
    'https://p8913620478250ohpk-saas.bx-activity.meta-xuantan.com',
    'https://p8913620478250l077-saas.bx-activity.meta-xuantan.com',
]
HOST = (os.environ.get('XL_HOST') or '').strip() or HOST_CANDIDATES[0]

# ============ 活动开关（每月活动会换，需要改这里） ============
# 每月活动轮换时，用抓包里的新 activityPlanId 覆盖下面这个即可
DEFAULT_PLAN_ID = os.environ.get('XL_PLAN_ID') or '64734114048'
# 活动名包含这个关键字才继续执行；抓包里 getConfig 返回的 activityTitle 形如「11月签到」
ACTIVITY_KEYWORD = (os.environ.get('XL_ACTIVITY_KEYWORD') or '签到').strip()
# 总开关：设为 0 / false / off 则整个脚本跳过（不签到、不推送）
ENABLED = (os.environ.get('XL_ENABLED') or '1').strip().lower() not in ('0', 'false', 'off', 'no')

TOKEN_SALT = 'J7h8&^Bgs5#bn*7hn%!=kh308*bv2!s^'   # getUserToken 专用盐
SIGN_SALT = 'uh3$Hg&^HK876%gbxVG7f$%p=0M~>s1x'
# Referer 尾部会话参数（从 Cookie 里的 _ref_extra 项解析，无需单独配变量）
REF_EXTRA_KEY = '_ref_extra'
UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) AppleWebKit/605.1.15 '
      '(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.76(0x18004c3a) '
      'NetType/4G Language/zh_CN')

_ACC = None


def log(*a):
    tag = '[%s] ' % _ACC['name'] if _ACC else ''
    print(tag + ' '.join(str(x) for x in a), flush=True)


def mask(s):
    s = str(s or '')
    return s[:20] + '****' + s[-6:] if len(s) > 30 else s


def extract_ref_extra(cookie):
    """从 Cookie 串里取出 _ref_extra 的值（Referer 尾部参数）
       写法：_ref_extra=slulg=xxx&ggm=yyy
       注意值里是 & 分隔，不含 ;，因此不会与 Cookie 分隔符冲突"""
    m = re.search(r'_ref_extra=([^;]+)', cookie)
    return m.group(1).strip() if m else ''

def parse_entries():
    raw = (os.environ.get(ENV_NAME) or '').strip()
    if not raw:
        return []
    out = []
    # _ref_extra=xxx&yyy 里的 & 是参数分隔符，不能当账号分隔符
    # 先把它临时替换成占位符，解析完再还原
    raw = re.sub(r'_ref_extra=([^;\s]+)', lambda mm: '_ref_extra=' + mm.group(1).replace('&', '\x01'), raw)
    for line in re.split(r'[&\n]+', raw):
        line = line.strip()
        if not line:
            continue
        line = line.replace('\x01', '&')
        parts = [p.strip() for p in line.split('#', 1)]
        if len(parts) >= 2:
            name, cookie = parts[0], parts[1]
        else:
            name, cookie = '账号%d' % (len(out) + 1), parts[0]
        out.append({'name': name, 'cookie': cookie,
                    'ref_extra': extract_ref_extra(cookie)})
    return out


def cookie_get(cookie, key):
    """从 Cookie 串里取某个键的值（去掉 appKey= 前缀）"""
    for kv in cookie.split(';'):
        kv = kv.strip()
        if not kv or '=' not in kv:
            continue
        k, v = kv.split('=', 1)
        if k.strip() == key:
            v = v.strip()
            # 形如 hdzy_wcjt_libxpj=66600151751476 → 取最后一个 =
            if '=' in v:
                v = v.split('=', 1)[1]
            return v
    return ''


# ==================== 签名 ====================
def gen_nonce(n=32):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(n))


def xm_sign(params):
    """按 key 升序拼接参数值 + 盐 → MD5（小写）
       注意：xmTimestamp 需按数字拼接，不能用字符串"""
    parts = []
    for k in sorted(params):
        v = params[k]
        if isinstance(v, bool):
            v = 'true' if v else 'false'
        elif isinstance(v, (dict, list)):
            v = json.dumps(v, separators=(',', ':'), ensure_ascii=False)
        else:
            v = str(v)
        parts.append(v)
    return hashlib.md5((''.join(parts) + SIGN_SALT).encode('utf-8')).hexdigest()


def build_referer(acc):
    """页面 Referer，必须带 activityPlanId / li 等参数，否则服务端判定页面已变化"""
    c = acc['cookie']
    app_key = cookie_get(c, 'appKey')
    place_id = cookie_get(c, 'placeId')
    ls = cookie_get(c, 'ls')                       # 形如 4644...9814-1
    li = ls.rsplit('-', 1)[0] if '-' in ls else ls
    plan_id = acc.get('plan_id') or DEFAULT_PLAN_ID
    # 尾部两参数名随机（orcow/ssm, slulg/ggm, djqtt/aco...），与 Cookie 会话绑定
    # 存放在 Cookie 串的 _ref_extra 项里，格式：_ref_extra=slulg=xxx&ggm=yyy
    ref_extra = (os.environ.get('XL_REF_EXTRA') or '').strip() or acc.get('ref_extra', '')
    return ('%s/saas_marketing_signin.html?appKey=%s&placeId=%s&activityPlanId=%s'
            '&applicationId=11&li=%s&hi=xmps&channelType=1043&platformEnv=4'
            '&devVersion=DV100&%s'
            % (HOST, app_key, place_id, plan_id, li, ref_extra))


def build_headers(acc, sign_params, xm_token='', json_body=False, function_id='0'):
    h = {
        'User-Agent': UA,
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh-Hans;q=0.9',
        'Referer': build_referer(acc),
        'Origin': HOST,
        'Cookie': acc['cookie'],
        'functionId': str(function_id),
        'nonceStr': sign_params['nonceStr'],
        'xmTimestamp': str(sign_params['xmTimestamp']),
        'xmSign': xm_sign(sign_params),
    }
    if json_body:
        h['Content-Type'] = 'application/json'      # POST 必需，否则服务端不并入 body 算签名
    if xm_token:
        h['xmToken'] = xm_token
    return h


# ==================== 接口 ====================
def api_get(acc, path, query=None, xm_token='', function_id='0'):
    query = dict(query or {})
    sp = dict(query)
    # 前端规则：GET 签名剔除 functionId，但它要单独放 header
    sp.pop('functionId', None)
    sp['nonceStr'] = gen_nonce()
    sp['xmTimestamp'] = int(time.time() * 1000)
    if str(function_id) == '0' and query.get('functionId'):
        function_id = str(query['functionId'])
    url = HOST + path
    if query:
        url += '?' + '&'.join('%s=%s' % (k, v) for k, v in query.items())
    try:
        r = requests.get(url, headers=build_headers(acc, sp, xm_token,
                                                   function_id=function_id), timeout=20)
        return r.json()
    except Exception as e:
        return {'code': '-999', 'desc': '请求异常: %s' % str(e)[:120]}


def api_post(acc, path, payload, xm_token='', function_id='0'):
    """POST 签名顺序很重要：先取 xmToken 并入参，再算 xmSign"""
    sp = dict(payload)
    sp['nonceStr'] = gen_nonce()
    sp['xmTimestamp'] = int(time.time() * 1000)
    if xm_token:
        sp['xmToken'] = xm_token          # 必须在 xmSign 之前放进去
    try:
        r = requests.post(HOST + path, headers=build_headers(acc, sp, xm_token, json_body=True, function_id=function_id),
                          data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                          timeout=20)
        return r.json()
    except Exception as e:
        return {'code': '-999', 'desc': '请求异常: %s' % str(e)[:120]}


def get_xm_token(acc):
    """换取 xmToken（POST 签名需要）
       tokenSign = MD5(li + nonceStr + timestamp + token盐)
    """
    c = acc['cookie']
    ls = cookie_get(c, 'ls')
    li = ls.rsplit('-', 1)[0] if '-' in ls else ls
    ts = int(time.time() * 1000)
    nonce = gen_nonce()
    token_sign = hashlib.md5(
        ('%s%s%s%s' % (li, nonce, ts, TOKEN_SALT)).encode('utf-8')).hexdigest()
    # 必须手动构造：tokenSign 与 query 里的 timestamp/nonceStr 必须严格一致，
    # 走 api_get 会重新生成 nonceStr/xmTimestamp 导致 tokenSign 对不上
    url = ('%s/xm/token/getUserToken?timestamp=%d&nonceStr=%s&tokenSign=%s'
           % (HOST, ts, nonce, token_sign))
    try:
        r = requests.get(url, headers=build_headers(acc, {'nonceStr': nonce, 'xmTimestamp': ts}),
                         timeout=20)
        j = r.json()
    except Exception as e:
        log('   ⚠️ 获取 xmToken 异常：%s' % str(e)[:60])
        return ''
    if str(j.get('code')) == '0' and isinstance(j.get('data'), str):
        return j['data']
    log('   ⚠️ 获取 xmToken 失败：%s' % (j.get('desc') or j.get('code')))
    return ''



def probe_host(acc):
    """探测可用域名：小满每个实例后缀不同（l077/ohpk/...），
       用 sign/getConfig 返回 code=0 判断哪个通"""
    global HOST
    for cand in HOST_CANDIDATES:
        if cand == HOST:
            continue
        old = HOST
        HOST = cand
        try:
            r = api_get(acc, '/sign/getConfig', {'curDate': time.strftime('%Y-%m-%d')})
            if str(r.get('code')) == '0':
                log('   🌐 切换域名: %s' % cand.split('//')[1].split('-')[0][-4:])
                return
        except Exception:
            pass
        HOST = old


# ==================== 业务 ====================
def get_config(acc, today):
    return api_get(acc, '/sign/getConfig', {'curDate': today})


def get_activity_config(acc):
    j = api_get(acc, '/activity/function/getConfig')
    if isinstance(j, dict):
        j['_plan_id'] = acc.get('plan_id') or DEFAULT_PLAN_ID
    return j


def do_sign(acc, today, xm_token=None):
    """xm_token 为 None 时在签名前即时获取（顺序必须：取 token → 并入参 → 算签名）"""
    if xm_token is None:
        xm_token = get_xm_token(acc)
    return api_post(acc, '/sign/action', {'patchDate': today}, xm_token)


def parse_sign_result(j):
    """返回 (是否成功, 今日是否已签, 奖品列表)"""
    if str(j.get('code')) != '0':
        return False, False, []
    data = j.get('data') or {}
    prizes = []
    ar = (data.get('actionResult') or {}).get('actionAward') or {}
    for p in (ar.get('rewardList') or []):
        prizes.append({
            'name': p.get('prizeName') or p.get('prizeTitle') or '未知奖品',
            'desc': p.get('prizeDesc') or '',
        })
    return True, bool(data.get('todaySigned')), prizes


def count_signed(acc, today):
    """本月已签到天数"""
    j = get_config(acc, today)
    if str(j.get('code')) != '0':
        return None
    dates = (j.get('data') or {}).get('dateInfoList') or []
    return len([d for d in dates
                if d.get('curDate', '') <= today and d.get('signStatus') not in (4, None)])


# ==================== 单账号 ====================
def run_account(acc):
    today = time.strftime('%Y-%m-%d')
    out = {'name': acc['name'], 'ok': False, 'already': False,
           'prizes': [], 'error': None, 'days': None}

    log('👤 账号：%s' % acc['name'])
    log('   Cookie：%s' % mask(acc['cookie']))

    probe_host(acc)
    log('')

    # 1. 校验登录态（跑一次带签名的 getConfig）
    log('🔐 [1/4] 校验登录态')
    cfg = get_config(acc, today)
    if str(cfg.get('code')) != '0':
        out['error'] = 'Cookie 失效或签名异常：%s' % (cfg.get('desc') or cfg.get('code'))
        log('   ❌ %s' % out['error'])
        return out
    log('   ✅ 登录有效')
    log('')

    # 2. 活动信息 + 有效期/名称核对
    log('📋 [2/4] 活动信息')
    ac = get_activity_config(acc)
    if str(ac.get('code')) != '0':
        out['error'] = '读取活动配置失败：%s' % (ac.get('desc') or ac.get('code'))
        log('   ❌ %s' % out['error'])
        return out

    d = ac.get('data') or {}
    title = d.get('activityTitle') or ''
    begin = str(d.get('playBeginTime') or '')[:10]
    end = str(d.get('playEndTime') or '')[:10]
    log('   🎯 %s' % (title or '-'))
    log('   📅 %s ~ %s' % (begin or '-', end or '-'))
    log('   🆔 activityPlanId = %s' % (ac.get('_plan_id') or DEFAULT_PLAN_ID))

    # ② 名称关键字核对（活动换月时能及时报警）
    if ACTIVITY_KEYWORD and ACTIVITY_KEYWORD not in title:
        out['error'] = '活动已变更（当前「%s」不含关键字「%s」），请更新 XL_PLAN_ID' % (
            title or '未知', ACTIVITY_KEYWORD)
        log('   ⚠️ %s' % out['error'])
        return out

    # ③ 活动有效期核对
    if end and today > end:
        out['error'] = '活动已结束（%s）' % end
        log('   ⚠️ %s' % out['error'])
        return out
    if begin and today < begin:
        out['error'] = '活动未开始（%s）' % begin
        log('   ⚠️ %s' % out['error'])
        return out
    remain = d.get('remainSeconds')
    if isinstance(remain, int) and remain > 0:
        log('   ⏳ 剩余 %d 天' % (remain // 86400))
    log('')

    # 3. 取 xmToken
    log('🔑 [3/4] 获取 xmToken')
    xm_token = get_xm_token(acc)
    if xm_token:
        log('   ✅ %s' % mask(xm_token))
    log('')

    # 4. 签到
    log('📅 [4/4] 执行签到')
    res = do_sign(acc, today, None)
    ok, already, prizes = parse_sign_result(res)

    if not ok:
        desc = str(res.get('desc') or '')
        if '已签' in desc or '重复' in desc:
            out['ok'] = True
            out['already'] = True
            log('   ℹ️ 今日已签到')
        else:
            out['error'] = desc or ('code=%s' % res.get('code'))
            log('   ❌ 签到失败：%s' % out['error'])
            return out
    else:
        out['ok'] = True
        if already and not prizes:
            out['already'] = True
            log('   ℹ️ 今日已签到（无需重复）')
        else:
            log('   ✅ 签到成功')
        out['prizes'] = prizes
        for p in prizes:
            log('   🎁 %s' % p['name'])
            if p['desc']:
                log('      %s' % p['desc'])
        if not prizes:
            log('   🎁 本次未返回新奖品')

    time.sleep(1.2)
    out['days'] = count_signed(acc, today)
    if out['days'] is not None:
        log('   📊 本月累计签到：%d 天' % out['days'])

    return out


# ==================== 推送 ====================
NOTIFY_TITLE = '好想来签到 🍬'


def notify(title, content, timeout=15):
    key = os.environ.get('QYWX_KEY') or ''
    if not key:
        log('⚠️ 未配置 QYWX_KEY，跳过推送')
        return False
    body = {'msgtype': 'text',
            'text': {'content': '🔔 %s\n%s\n%s\n\n统计时间：%s' % (
                title, '-' * 20, content, time.strftime('%m-%d %H:%M:%S'))}}
    try:
        r = requests.post('https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=%s' % key,
                          data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                          headers={'Content-Type': 'application/json'}, timeout=timeout)
        j = r.json()
        if j.get('errcode') == 0:
            log('🚀 推送成功')
            return True
        log('❌ 推送失败：%s' % j.get('errmsg'))
    except Exception as e:
        log('💥 推送异常：%s' % e)
    return False


def build_report(results):
    if not results:
        return ''
    lines = ['【好想来零食乐园 · 签到】']
    for r in results:
        if r.get('error'):
            lines.append('❌ %s：%s' % (r['name'], r['error']))
            continue
        lines.append('%s  ✅ %s' % (r['name'], '今日已签' if r.get('already') else '签到成功'))
        if r.get('prizes'):
            for p in r['prizes']:
                lines.append('   🎁 %s' % p['name'])
                if p.get('desc'):
                    lines.append('      %s' % p['desc'])
        else:
            lines.append('   🎁 本次无新奖品')
        if r.get('days') is not None:
            lines.append('   📊 本月累计 %s 天' % r['days'])
    return '\n'.join(lines)


# ==================== 主流程 ====================
def main():
    global _ACC
    if not ENABLED:
        log('⏸️ 已通过 XL_ENABLED 关闭，本次跳过')
        return

    entries = parse_entries()
    if not entries:
        log('未设置或格式错误：%s = 备注#Cookie' % ENV_NAME)
        return

    log('共 %d 个账号' % len(entries))
    results = []
    for i, e in enumerate(entries):
        acc = dict(e)
        _ACC = acc
        log('=' * 50)
        log('📱 账号 %d/%d' % (i + 1, len(entries)))
        log('=' * 50)
        try:
            results.append(run_account(acc))
        except Exception as ex:
            log('❌ 执行异常：%s' % ex)
            results.append({'name': acc['name'], 'error': str(ex)[:80]})
        if i < len(entries) - 1:
            time.sleep(3)

    text = build_report(results)
    print('\n' + '=' * 50)
    print(text)
    print('=' * 50)
    notify(NOTIFY_TITLE, text)


if __name__ == '__main__':
    main()
