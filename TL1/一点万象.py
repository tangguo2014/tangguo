#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
APP：一点万象    签到 + 免费抽奖（每日首抽免费）
变量：ydwx_gpt=备注#token#deviceParams（多账号 & 或换行分隔）数据在请求体
定时：cron 0 6 * * * 每天一次，自行修改

================= 2026-11-12 修正（基于真实 HAR 实测） =================
签名：App 实际走 platform=h5 + appId 68a91a5b… + SECRET P@Gkbu0shTNHjhM!7F
      抓包里 lottery.do / member.basicInfo 全部是 platform=h5，不是 iOS。
      旧注释说「App 原生 iOS 签名 ySd8qSgZ8#!12OL*Xu」与实际抓包不符，已改回 h5。

抽奖次数：**不是** lottery.detail 里的 availableLotteryOpportunityNum（该字段不存在，永远是 None）
          正确的接口是 mixc.app.lottery.opportunity → data = 今日剩余次数（3/2/1/0）
          lottery.detail 里 usedNum 是 null，freeCount 也不存在。

免费规则：每天 3 次机会，**只有第 1 次免费**。
          opportunity 返回 3 → 本次免费
          返回 2/1  → 再抽要扣 9 星（默认不抽，ALLOW_PAID 控制）
          返回 0    → 今日用完

积分：只认 member.basicInfo.point（实测 point == bindCard.availablePoint，及时更新）
      签到返回的 point 是本次应得，remaindPoints 是服务端参考值，均不当作余额
======================================================================
"""
import json, os, sys, time, base64, hashlib, random, urllib.parse, urllib.request

APPID = '68a91a5bac6a4f3e91bf4b42856785c6'     # H5/App 实际使用的 appId
SECRET = 'P@Gkbu0shTNHjhM!7F'                  # 与 H5 同一套密钥
PLATFORM = 'h5'                                 # 实测 App 请求也是 h5
GATEWAY = 'https://app.mixcapp.com/mixc/gateway'
UA = 'mixc/4.3.0 (iPhone; iOS 16.3; Scale/3.00)'
API_VERSION = '1.0'
DEFAULT_MALL = '20064'
SALE_ID = 'a0744445efd84903acd90187e4b4e44f'    # 天津万象城 11月幸运星抽奖
DAILY_CHANCE = 3                                # 每日总机会（第 1 次免费）
ALLOW_PAID = False                              # 免费次数用完后是否继续花 9 星抽
LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mixc_prize.log')
DEBUG = '--debug' in sys.argv

TIME_OFFSET = 0


# ---------------------------------------------------------------- 签名/请求
def _sign(q):
    items = sorted((k, v) for k, v in q.items() if k != 'sign')
    return hashlib.md5((''.join('%s=%s&' % (k, v) for k, v in items) + SECRET).encode()).hexdigest()


def _q(params):
    """统一 base64(json)：实测 lottery 与 member 系列都接受"""
    return base64.b64encode(json.dumps(params or {}, separators=(',', ':'), ensure_ascii=False).encode()).decode()


def build(token, device, mall, action, params=None, lottery=False):
    """lottery=True：抽奖系列必须带 X-Mixc-Swimlane / t / date，且一起参与签名
    （实测缺这三个参数 opportunity 报「请检查请求参数!」）"""
    ms = int(time.time() * 1000)
    if ms % 1000 == 0:                        # 容器时间被截断到秒
        ms += random.randint(1, 999)
    ts = str(ms + TIME_OFFSET)
    q = {'mallNo': mall, 'action': action, 'appId': APPID, 'platform': PLATFORM,
         'imei': '542AD5BC-6E97-4639-BE39-55F8D9517389', 'appVersion': '4.3.0',
         'osVersion': '16.3', 'apiVersion': API_VERSION, 'timestamp': ts,
         'deviceParams': device, 'params': _q(params), 'token': token}
    if lottery:
        q['X-Mixc-Swimlane'] = 's1'
        q['t'] = ts
        q['date'] = time.strftime('%Y-%m-%d %H:%M:%S')
    q['sign'] = _sign(q)
    return q


def _http(url, data=None, headers=None, timeout=15, method=None):
    h = {'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': UA}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    return urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', 'replace')


def api(token, device, mall, action, params=None, retry=3, lottery=False):
    global TIME_OFFSET
    last = {}
    for i in range(retry):
        try:
            body = urllib.parse.urlencode(build(token, device, mall, action, params, lottery)).encode()
            r = json.loads(_http(GATEWAY, body))
            if DEBUG:
                print('   [debug] %s → %s' % (action, json.dumps(r, ensure_ascii=False)[:600]))
            if str(r.get('code')) == '20108' and r.get('timestamp'):
                new_off = int(r['timestamp']) - int(time.time() * 1000)
                if abs(new_off - TIME_OFFSET) > 50:
                    TIME_OFFSET = new_off
                    continue
            msg = r.get('message') or ''
            if '频繁' in msg and i < retry - 1:
                last = r
                time.sleep(30)
                continue
            return r
        except Exception as e:
            last = {'code': 'EXC', 'message': str(e)}
            time.sleep(5)
    return last


# ---------------------------------------------------------------- 业务查询
def current_point(token, device, mall):
    """唯一可信余额来源：member.basicInfo.point（实测与 bindCard.availablePoint 一致）"""
    r = api(token, device, mall, 'mixc.app.member.basicInfo', {'mallNo': mall})
    d = r.get('data') or {}
    if d.get('point') is not None:
        return int(d['point'])
    return (d.get('bindCard') or {}).get('availablePoint')


def chance_left(token, device, mall, sale_id_hint=SALE_ID):
    """今日剩余抽奖次数：mixc.app.lottery.opportunity → data 直接就是次数"""
    r = api(token, device, mall, 'mixc.app.lottery.opportunity',
            {'mallNo': mall, 'channel': 2, 'saleId': sale_id_hint}, lottery=True)
    if str(r.get('code')) != '0':
        return None, r.get('message') or str(r.get('code'))
    try:
        return int(r.get('data') or 0), None
    except Exception:
        return None, '返回格式异常: %r' % (r.get('data'),)


def sign_date(token, device, mall):
    return api(token, device, mall, 'mixc.app.memberSign.signDate', {'mallNo': mall})


def sign_in(token, device, mall, retry_30012=True):
    r = api(token, device, mall, 'mixc.app.memberSign.point.sign', {})
    if not retry_30012:
        return r
    for wait in (60, 120, 180):
        if str(r.get('code')) != '30012':
            break
        print('   ⏳ 30012 账户异常（暂时性），%d 秒后重试…' % wait)
        time.sleep(wait)
        r = api(token, device, mall, 'mixc.app.memberSign.point.sign', {})
    return r


def do_lottery(token, device, mall, sale_id):
    """按 opportunity 的真实次数判断，只抽免费那一次（默认）"""
    out = {}
    left, err = chance_left(token, device, mall)
    out['chance_before'] = left
    if left is None:
        out['err'] = '次数查询失败：%s，未抽奖（不冒险扣星）' % err
        return out
    if left <= 0:
        out['skip'] = '今日 %d 次机会已用完' % DAILY_CHANCE
        return out
    # 只有「今日一次没抽过」（剩满额）才是免费那次
    is_free = (left >= DAILY_CHANCE)
    if not is_free and not ALLOW_PAID:
        out['skip'] = '剩余 %d/%d 次，再抽需扣 9 星（首抽免费已用），默认不抽' % (left, DAILY_CHANCE)
        return out

    before = current_point(token, device, mall)
    r = api(token, device, mall, 'mixc.app.lottery.do',
            {'mallNo': mall, 'saleId': sale_id, 'inviteCode': ''}, lottery=True)
    d = r.get('data') or {}
    out['raw_code'] = r.get('code')
    out['raw_msg'] = r.get('message')
    if str(r.get('code')) != '0':
        out['err'] = r.get('message') or str(r.get('code'))
        return out
    out['prize'] = d.get('prizeName')
    out['prize_type'] = d.get('prizeType')
    out['time'] = d.get('createTime') or d.get('updateTime')
    out['nine_num'] = d.get('nineNum')
    time.sleep(2)                              # 等积分入账
    after = current_point(token, device, mall)
    out['after'] = after
    out['cost'] = max(0, before - after) if (before is not None and after is not None) else 0
    out['free'] = out['cost'] == 0
    left2, _ = chance_left(token, device, mall)
    out['chance_after'] = left2
    return out


def ledger_add(name, prize, prize_type, cost, when):
    try:
        day = (when or time.strftime('%Y-%m-%d %H:%M:%S'))[:10]
        with open(LEDGER, 'a') as f:
            f.write('%s\t%s\t%s\t%s\t%s\n' % (day, name, prize, prize_type, cost))
    except Exception:
        pass


# ---------------------------------------------------------------- 推送
NOTIFY_TITLE = '万象城签到🙋‍♀️'


def send_msg(title, content):
    key = os.environ.get('QYWX_KEY') or ''
    if not key:
        print('⚠️ 提示：未在环境变量配置 QYWX_KEY，跳过通知发送。')
        return
    body = {'msgtype': 'text', 'text': {'content': '🔔 %s\n%s\n%s\n\n统计时间：%s' % (
        title, '-' * 20, content, time.strftime('%m-%d %H:%M:%S'))}}
    try:
        r = json.loads(_http('https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=%s' % key,
                             json.dumps(body, ensure_ascii=False).encode(),
                             {'Content-Type': 'application/json'}))
        if r.get('errcode') == 0:
            print('🚀 企业微信机器人推送成功！')
        else:
            print('❌ 推送失败: %s' % r.get('errmsg'))
    except Exception as e:
        print('💥 推送异常: %s' % e)


# ---------------------------------------------------------------- 主流程
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.mixc_device.json')


def _load_cache():
    try:
        with open(CACHE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(d):
    try:
        with open(CACHE, 'w') as f:
            json.dump(d, f)
    except Exception:
        pass


def parse_env():
    v = (os.environ.get('ydwx_gpt') or '').strip()
    if not v:
        print('[ERR] 未设置环境变量 ydwx_gpt，格式: 备注#token#deviceParams')
        sys.exit(1)
    cache = _load_cache()
    accounts = []
    for line in v.replace('\n', '&').split('&'):
        line = line.strip()
        if not line:
            continue
        p = line.split('#')
        name, token = (p[0] or 'mixc'), p[1] if len(p) > 1 else ''
        if not token:
            print('[ERR] 账号 %s 缺少 token' % name)
            continue
        device = p[2] if len(p) > 2 and p[2] else cache.get(token)
        accounts.append({'name': name, 'token': token, 'device': device})
    return accounts


def _remember(acc):
    c = _load_cache()
    c[acc['token']] = acc['device']
    _save_cache(c)


def run_account(acc):
    mall = DEFAULT_MALL
    if not acc.get('device'):
        return '👤 %s\n❌ 缺少 deviceParams：请在 ydwx_gpt 第三段补上抓包原值' % acc['name']

    lines = ['👤 %s' % acc['name']]

    # 1) 签到（状态以 signDate 为准，异常码一律回查）
    st = sign_date(acc['token'], acc['device'], mall)
    sd = st.get('data') or {}
    signed = False

    def refresh():
        s2 = sign_date(acc['token'], acc['device'], mall).get('data') or {}
        return (str(s2.get('canSign')) == '1', s2.get('continuousSign'), s2.get('todayPoint'))

    if str(st.get('code')) == '0' and str(sd.get('canSign')) == '1':
        signed = True
        lines.append('📝 状态：✅ 今日已签到')
        lines.append('📅 连续签到 %s 天 · 今日应得 %s 星' % (sd.get('continuousSign'), sd.get('todayPoint')))
    else:
        r = sign_in(acc['token'], acc['device'], mall)
        code, msg, data = r.get('code'), r.get('message') or '', r.get('data') or {}
        if str(code) == '0':
            signed = True
            lines.append('📝 状态：✅ 签到成功  本次 +%s 星' % data.get('point'))
            st2 = sign_date(acc['token'], acc['device'], mall).get('data') or {}
            if st2.get('continuousSign') is not None:
                lines.append('📅 连续签到 %s 天' % st2.get('continuousSign'))
        elif '已签到' in msg:
            signed = True
            lines.append('📝 状态：✅ 今日已签到')
        else:
            ok2, days2, today2 = refresh()
            if ok2:
                signed = True
                lines.append('📝 状态：✅ 今日已签到（签到请求 %s，已回查确认）' % (msg or code))
                lines.append('📅 连续签到 %s 天 · 今日应得 %s 星' % (days2, today2))
            elif '频繁' in msg:
                lines.append('📝 状态：⚠️ 请求频繁（冷却中，稍后重跑）')
            elif str(code) == '30012':
                lines.append('📝 状态：❌ 签到未成功，30012 重试 3 次仍失败')
            elif '授权' in msg or 'token' in msg.lower():
                lines.append('📝 状态：❌ token 失效（%s）' % msg)
            else:
                lines.append('📝 状态：❌ 签到失败 %s (code=%s)' % (msg, code))

    # 2) 签到后再读余额（唯一可信来源）
    pt = current_point(acc['token'], acc['device'], mall)
    if pt is not None:
        lines.append('💰 当前可用：%s 万象星' % pt)

    # 3) 抽奖（按 opportunity 真实次数）
    sale_id = acc.get('sale') or SALE_ID
    lot = do_lottery(acc['token'], acc['device'], mall, sale_id)
    if lot.get('err'):
        lines.append('🎰 抽奖：⚠️ %s' % lot['err'])
    elif lot.get('skip'):
        lines.append('🎰 抽奖：ℹ️ %s' % lot['skip'])
    else:
        ptype = str(lot.get('prize_type'))
        icon = '🎉' if ptype == '500' else ('🎟️' if ptype in ('505', '506', '510') and lot.get('prize') != '谢谢参与' else '😅')
        cost = lot.get('cost') or 0
        tag = '扣 %s 星' % cost if cost else '免费'
        lines.append('🎰 抽奖：%s %s（%s，今日机会 %s/3 → %s/3）' % (
            icon, lot.get('prize'), tag, lot.get('chance_before'), lot.get('chance_after')))
        ledger_add(acc['name'], lot.get('prize') or '', ptype, cost, lot.get('time'))
        if lot.get('after') is not None:
            lines.append('💰 抽奖后可用：%s 万象星' % lot.get('after'))
        if cost:
            lines.append('⚠️ 注意：本次扣了 %s 星（首抽免费已用）' % cost)
    return '\n'.join(lines)


def main():
    accounts = parse_env()
    reports = [run_account(a) for a in accounts]
    text = '\n\n'.join(reports)
    print(text)
    if '--no-push' not in sys.argv:
        send_msg(NOTIFY_TITLE, text)
    if '❌' in text:
        print('⚠️ 需要人工处理：见上方 ❌ 行')


if __name__ == '__main__':
    main()
