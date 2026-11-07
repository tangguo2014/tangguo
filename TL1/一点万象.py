#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
APP：一点万象    签到 + 免费抽奖
    （只抽一次，后两次需要积分默认关闭）
变量：ydwx_gpt=备注#token#deviceParams（多账号&分割或换行）数据在请求体
定时：cron 0 6 * * * 每天一次，自行修改
解密：md5+SECRET，密钥

"""
import json, os, sys, time, base64, hashlib, random, subprocess, urllib.parse, urllib.request

APPID = '68a91a5bac6a4f3e91bf4b42856785c6'
SECRET = 'P@Gkbu0shTNHjhM!7F'
GATEWAY = 'https://app.mixcapp.com/mixc/gateway'
UA = 'mixc/4.3.0 (iPhone; iOS 16.3; Scale/3.00)'
API_VERSION = '1.0'
DEFAULT_MALL = '20064'
SALE_ID = 'a0744445efd84903acd90187e4b4e44f'   # 天津万象城 11月幸运星抽奖
ALLOW_PAID = False                              # 免费次数用完后是否继续花 9 星抽
DAILY_CHANCE = 3                                # 每天机会总数（第 1 次免费，之后每次 9 星）
LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mixc_prize.log')


# ---------------------------------------------------------------- 加密/请求
def _sign(q):
    items = sorted((k, v) for k, v in q.items() if k != 'sign')
    return hashlib.md5((''.join('%s=%s&' % (k, v) for k, v in items) + SECRET).encode()).hexdigest()


def _q(params):
    """lottery 系列只认 base64 编码（urlencode 会报 30001）；memberSign 系列 urlencode 与 base64 都吃"""
    return base64.b64encode(json.dumps(params or {}, separators=(',', ':'), ensure_ascii=False).encode()).decode()


def build(token, device, mall, action, params=None, lottery=False):
    """lottery=True 走 H5 页面那套参数（多 t/date/X-Mixc-Swimlane 并参与签名）"""
    ms = int(time.time() * 1000)
    if ms % 1000 == 0:            # 容器时间被截断到秒 → 补随机毫秒，避免毫秒恒为 000
        ms += random.randint(1, 999)
    ts = str(ms + TIME_OFFSET)
    q = {'mallNo': mall, 'action': action, 'appId': APPID, 'platform': 'h5',
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


TIME_OFFSET = 0          # 服务器与本机的毫秒偏差，遇到 20108 自动校准


def api(token, device, mall, action, params=None, lottery=False, retry=3):
    global TIME_OFFSET
    last = {}
    for i in range(retry):
        try:
            body = urllib.parse.urlencode(build(token, device, mall, action, params, lottery)).encode()
            r = json.loads(_http(GATEWAY, body))
            if str(r.get('code')) == '20108' and r.get('timestamp'):
                new_off = int(r['timestamp']) - int(time.time() * 1000)
                if abs(new_off - TIME_OFFSET) > 50:      # 服务器时间与本机差太多，校准后重试
                    TIME_OFFSET = new_off
                    continue
            msg = r.get('message') or ''
            if '频繁' in msg and i < retry - 1:      # 风控冷却
                last = r
                time.sleep(30)
                continue
            return r
        except Exception as e:
            last = {'code': 'EXC', 'message': str(e)}
            time.sleep(5)
    return last


# ---------------------------------------------------------------- 业务
def get_point(token, device, mall):
    r = api(token, device, mall, 'mixc.app.member.basicInfo', {'mallNo': mall}, lottery=True)
    d = r.get('data') or {}
    return d.get('point'), (d.get('bindCard') or {}).get('availablePoint')


def available_point(token, device, mall):
    """当前可用万象星：basicInfo.point（与 bindCard.availablePoint 一致）；
    注意 bindCard.totalPoint 是历史累计（含已过期/已兑换），不能当余额用"""
    r = api(token, device, mall, 'mixc.app.member.basicInfo', {'mallNo': mall}, lottery=True)
    d = r.get('data') or {}
    if d.get('point') is not None:
        return int(d.get('point'))
    return (d.get('bindCard') or {}).get('availablePoint')


def sign_in(token, device, mall):
    r = api(token, device, mall, 'mixc.app.memberSign.point.sign', {})
    return r


def sign_date(token, device, mall):
    """签到状态：totalGet 累计 / canSign=1 表示今日已签 / continuousSign 连续天数 / todayPoint 今日应得"""
    return api(token, device, mall, 'mixc.app.memberSign.signDate', {'mallNo': mall})


def lottery_free(token, device, mall, sale_id):
    """只抽免费的那一次。
    实测口径：availableLotteryOpportunityNum = 当天剩余总机会（每日 3 次），
    第 1 次免费 → 只有「剩余机会 == DAILY_CHANCE」时才说明免费次数还在。
    usedNum 接口不返回，不能用 freeCount-usedNum 判断（会误以为还有免费次数而扣 9 星）。
    """
    out = {'sale': None, 'chance': None, 'prize': None, 'cost': 0, 'free': True}
    r = api(token, device, mall, 'mixc.app.lottery.detail', {'mallNo': mall, 'saleId': sale_id}, lottery=True)
    d = r.get('data')
    if not d:
        out['err'] = '抽奖信息查询失败：%s（code=%s），未能确认剩余次数' % (
            r.get('message') or '空数据', r.get('code'))
        out['unconfirmed'] = True
        return out
    out['sale'] = d.get('marketTitle')
    left = int(d.get('availableLotteryOpportunityNum') or 0)
    out['chance'] = left
    if left <= 0:
        out['skip'] = '今日次数已用完'
        return out
    free_left = left >= DAILY_CHANCE if DAILY_CHANCE else left > 0
    if not free_left and not ALLOW_PAID:
        out['skip'] = '免费次数已用完（剩 %s 次需 %s 星/次，默认不抽）' % (left, d.get('point') or 9)
        return out
    before = get_point(token, device, mall)[0]
    r = api(token, device, mall, 'mixc.app.lottery.do',
            {'mallNo': mall, 'saleId': sale_id, 'inviteCode': ''}, lottery=True)
    after = get_point(token, device, mall)[0]
    d = r.get('data') or {}
    if str(r.get('code')) != '0':
        out['err'] = r.get('message') or str(r.get('code'))
        return out
    out['prize'] = d.get('prizeName')
    out['prize_type'] = d.get('prizeType')
    out['time'] = d.get('createTime')
    if before is not None and after is not None:
        out['cost'] = max(0, before - after)
    out['free'] = out['cost'] == 0
    out['after'] = after
    return out


def ledger_add(name, prize, prize_type, cost, when):
    """本月中奖台账：TSV，一天一行，成功抽奖才写"""
    try:
        day = (when or time.strftime('%Y-%m-%d %H:%M:%S'))[:10]
        with open(LEDGER, 'a') as f:
            f.write('%s\t%s\t%s\t%s\t%s\n' % (day, name, prize, prize_type, cost))
    except Exception:
        pass


def pick_sale_id(token, device, mall):
    """从首页/礼遇数据里找抽奖活动 saleId；失败返回 None"""
    r = api(token, device, mall, 'mixc.app.gift.homepage.index', {})
    txt = json.dumps(r, ensure_ascii=False)
    return None, txt


# ---------------------------------------------------------------- 推送
NOTIFY_TITLE = '万象城签到🙋‍♀️'


def send_msg(title, content):
    """企业微信机器人推送（QYWX_KEY），失败只打印不抛错"""
    print('【通知】%s\n%s' % (title, content))
    key = os.environ.get('QYWX_KEY') or ''
    if not key:
        print('⚠️ 提示：未在环境变量配置 QYWX_KEY，跳过通知发送。')
        return
    body = {'msgtype': 'text', 'text': {'content': '🔔 %s\n%s\n%s\n\n统计时间：%s' % (
        title, '-' * 20, content, time.strftime('%M:%S'))}}
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
    """格式: 备注#token  或  备注#token#deviceParams；多账号用 & 或换行分隔"""
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
        return '👤 %s\n❌ 缺少 deviceParams：请在 ydwx_gpt 第三段补上抓包原值（deviceId 参与风控，不能用别人的）' % acc['name']

    lines = ['👤 %s' % acc['name']]
    avail = available_point(acc['token'], acc['device'], mall)

    # 1) 签到：以 signDate 为准；任何异常码都要回查一次再下结论
    st = sign_date(acc['token'], acc['device'], mall)
    sd = st.get('data') or {}

    def _refresh_state():
        """回查真实状态：返回 (今天是否已签, 连续天数, 今日得分, 可用积分)"""
        s2 = sign_date(acc['token'], acc['device'], mall).get('data') or {}
        return (str(s2.get('canSign')) == '1', s2.get('continuousSign'),
                s2.get('todayPoint'), s2.get('totalGet'))

    signed_ok = False
    if str(st.get('code')) == '0':
        if str(sd.get('canSign')) == '1':
            signed_ok = True
            avail = available_point(acc['token'], acc['device'], mall) or avail
            lines.append('📝 状态：✅ 今日已签到')
            lines.append('📅 连续签到 %s 天 · 今日 +%s 星' % (sd.get('continuousSign'), sd.get('todayPoint')))
            lines.append('💰 可用积分：%s' % avail)
        else:
            r = sign_in(acc['token'], acc['device'], mall)
            code, msg, data = r.get('code'), r.get('message') or '', r.get('data') or {}
            if str(code) == '0':
                signed_ok = True
                lines.append('📝 状态：✅ 签到成功  +%s 万象星（可用 %s）' % (data.get('point'), data.get('remaindPoints')))
                st2 = sign_date(acc['token'], acc['device'], mall).get('data') or {}
                if st2:
                    lines.append('📅 连续签到 %s 天' % st2.get('continuousSign'))
            elif '已签到' in msg:
                signed_ok = True
                lines.append('📝 状态：✅ 今日已签到')
            else:
                # 任何异常码（30012/20108/…）都不直接下结论，回查一次
                ok2, days2, today2, _ = _refresh_state()
                if ok2:
                    signed_ok = True
                    lines.append('📝 状态：✅ 今日已签到（签到请求返回 %s，已回查确认）' % (msg or code))
                    lines.append('📅 连续签到 %s 天 · 今日 +%s 星' % (days2, today2))
                elif '频繁' in msg:
                    lines.append('📝 状态：⚠️ 请求频繁（冷却中，稍后重跑）')
                elif code == 30012:
                    lines.append('📝 状态：❌ 签到未成功，返回 30012（回查 canSign=0，非误报）')
                elif '授权' in msg or 'token' in msg.lower():
                    lines.append('📝 状态：❌ token 失效（%s）' % msg)
                else:
                    lines.append('📝 状态：❌ 签到失败 %s (code=%s)' % (msg, code))
    else:
        # 状态查询本身失败：再尝试一次签到，之后再回查
        r = sign_in(acc['token'], acc['device'], mall)
        c2, m2, d2 = r.get('code'), r.get('message') or '', r.get('data') or {}
        ok2, days2, today2, _ = _refresh_state()
        if ok2:
            signed_ok = True
            lines.append('📝 状态：✅ 今日已签到')
            lines.append('📅 连续签到 %s 天 · 今日 +%s 星' % (days2, today2))
            lines.append('💰 可用积分：%s' % avail)
        elif str(c2) == '0':
            signed_ok = True
            lines.append('📝 状态：✅ 签到成功  +%s 万象星（可用 %s）' % (d2.get('point'), d2.get('remaindPoints')))
        elif '频繁' in m2:
            lines.append('📝 状态：⚠️ 请求频繁（冷却中，稍后重跑）')
        elif '授权' in m2 or 'token' in m2.lower():
            lines.append('📝 状态：❌ token 失效（%s），需要重新抓包更新 ydwx_gpt' % m2)
        else:
            lines.append('📝 状态：❌ 状态查询 %s(%s) / 签到 %s(%s)' % (st.get('message'), st.get('code'), m2, c2))

    # 2) 免费抽奖（每天只抽免费的这一次，付费次数不动）
    sale_id = acc.get('sale') or SALE_ID
    lot = lottery_free(acc['token'], acc['device'], mall, sale_id)
    if lot.get('err'):
        lines.append('🎰 抽奖：⚠️ %s' % lot['err'])
    elif lot.get('skip'):
        lines.append('🎰 抽奖：ℹ️ %s' % lot['skip'])
    else:
        ptype = str(lot.get('prize_type'))
        icon = '🎉' if ptype == '500' else ('🎟️' if ptype == '505' else '😅')
        cost = lot.get('cost') or 0
        lines.append('🎰 抽奖：%s %s（%s，剩余 %s 次）' % (
            icon, lot.get('prize'), '消耗 %s 星' % cost if cost else '本次免费', lot.get('chance')))
        ledger_add(acc['name'], lot.get('prize') or '', ptype, cost, lot.get('time'))
        if lot.get('after') is not None:
            lines.append('💰 可用积分：%s' % lot.get('after'))
        if cost:
            lines.append('⚠️ 注意：本次扣了 %s 星（免费次数已用完）' % cost)
    return '\n'.join(lines)


def main():
    accounts = parse_env()
    reports = [run_account(a) for a in accounts]
    text = '\n\n'.join(reports)
    print(text)
    if '--no-push' not in sys.argv:
        send_msg(NOTIFY_TITLE, text)
    if '❌' in text:
        print('⚠️ 需要人工处理：见上方 ❌ 行（token 失效需重新抓包，或 deviceParams 不匹配）')
    


if __name__ == '__main__':
    main()
