#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
vx小程序 惠家友.农场 签到 / 浇水 / 收雨水 / 任务/ 偷水
================================================================================

【变量】
    hjync_gpt = 备注#token
             从抓包 请求体里的 api_token 
             多账号：& 或换行分隔

【可选】
    HJC_TASKS   默认 all（签到,浇水,收水,任务）
          例：HJC_TASKS=sign,water  只做签到和浇水

【签名算法】（已用 55/55 个真实请求验证通过）
    T = "amiauhsnehnujiauhz"          ← 把 huijiayuanshenhua 倒过来
    api_secret = MD5(api_target + api_key + api_input + api_timespan + T).upper()

【签到奖励】7天阶梯 4/8/12/... g 雨水

【定时】
   cron: 0 8 * * *

================================================================================
"""
import os
import sys
import json
import time
import random
import hashlib
import tempfile
import threading

# ⚠️ 该站服务器用老式 TLS 重协商（OpenSSL 3.x 默认拒绝连接）
# 只能通过 OPENSSL_CONF 环境变量解决，运行时修改无效 → 自动重启一次
_CNF = os.path.join(tempfile.gettempdir(), 'hjy_ossl.cnf')
_CNF_CONTENT = """openssl_conf = default_conf
[default_conf]
ssl_conf = ssl_sect
[ssl_sect]
system_default = system_default_sect
[system_default_sect]
MinProtocol = TLSv1.2
CipherString = DEFAULT@SECLEVEL=0
Options = UnsafeLegacyRenegotiation
"""

if os.environ.get('OPENSSL_CONF') != _CNF:
    try:
        with open(_CNF, 'w') as _f:
            _f.write(_CNF_CONTENT)
        _env = dict(os.environ)
        _env['OPENSSL_CONF'] = _CNF
        os.execve(sys.executable,
                  [sys.executable, os.path.abspath(__file__)] + sys.argv[1:],
                  _env)
    except Exception as _e:
        print('⚠️ TLS 兼容配置失败: %s' % _e, flush=True)

import requests

ENV_NAME = 'hjync_gpt'
BASE = 'https://api-family.huijiayou.cn/cfamily/jsonapi/'
API_KEY = 'appfamilyhas'
SECRET_KEY = 'amiauhsnehnujiauhz'
UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.48'
CLIENT = {"os": "vueshop", "app_vision": "6.4.6", "channelId": "449747430003"}
SIGN_CODE = 'QD200202100001'      # 签到码
EVENT_CODE = 'CX200320100007'     # 农场事件码
TASKS = (os.environ.get('HJC_TASKS') or 'all').strip()
WATER_TIMES = int(os.environ.get('HJC_WATER') or 6)   # 每次运行浇几次水
STEAL_LIMIT = int(os.environ.get('HJC_STEAL_LIMIT') or 10)   # 最多偷几位好友/路人
NO_STEAL = (os.environ.get('HJC_NO_STEAL') or '').strip() in ('1', 'true', 'on')

_ACC = None


def log(*a):
    tag = '[%s] ' % _ACC['name'] if _ACC else ''
    print(tag + ' '.join(str(x) for x in a), flush=True)


def rd(lo=800, hi=2000):
    time.sleep(random.randint(lo, hi) / 1000.0)


def mask(s):
    s = str(s or '')
    return s[:8] + '****' + s[-4:] if len(s) > 16 else s


# ==================== 账号 ====================
def parse_entries():
    raw = (os.environ.get(ENV_NAME) or '').strip()
    if not raw:
        return []
    out = []
    import re as _re
    for line in _re.split(r'[&\n]+', raw):
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split('#')]
        # 支持 备注#token 或 纯 token
        if len(parts) >= 2:
            name, token = parts[0], parts[1]
        else:
            name, token = '账号%d' % (len(out) + 1), parts[0]
        out.append({'name': name, 'token': token})
    return out


# ==================== 签名 ====================
def sign(target, api_input, timespan, token):
    raw = target + API_KEY + api_input + timespan + SECRET_KEY
    return hashlib.md5(raw.encode('utf-8')).hexdigest().upper()


# ==================== HTTP ====================
def call(target, inp=None, token=None, timeout=20):
    """调用农场接口"""
    inp_s = json.dumps(inp if inp is not None else {}, separators=(',', ':'), ensure_ascii=False)
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    data = {
        'api_input': inp_s,
        'api_target': target,
        'api_token': token or '',
        'api_key': API_KEY,
        'api_timespan': ts,
        'api_secret': sign(target, inp_s, ts, token or ''),
        'api_client': json.dumps(CLIENT, separators=(',', ':')),
        'api_project': 'hjy_shop',
    }
    try:
        r = requests.post(BASE + target, data=data, timeout=timeout,
                          headers={
                              'Content-Type': 'application/x-www-form-urlencoded;charset=utf-8',
                              'User-Agent': UA,
                              'Referer': 'https://vue-wxshop.jiayougo.cn/',
                          })
        return r.json()
    except Exception as e:
        return {'resultCode': -999, 'resultMessage': str(e)[:150]}


def ok(j):
    return str(j.get('resultCode')) == '1'


# ==================== 业务 ====================
def do_sign(acc):
    """签到"""
    j = call('com_cmall_familyhas_api_ApiForFarmSign',
             {'linkType': '2', 'signCode': SIGN_CODE}, acc['token'])
    if ok(j):
        lst = j.get('farmSignList') or []
        got = sum(int(x.get('signWater') or 0) for x in lst)
        return 'ok', '签到成功 +%dg雨水' % got, got
    msg = j.get('resultMessage') or ''
    if '已经签到' in msg or '已签到' in msg:
        return 'already', msg, 0
    return 'fail', msg or ('code=%s' % j.get('resultCode')), 0


def get_home(acc):
    """首页数据"""
    j = call('com_cmall_familyhas_api_ApiForFarmHome', {}, acc['token'])
    return (j if ok(j) else {})


def get_task_list(acc):
    j = call('com_cmall_familyhas_api_ApiFarmTaskList',
             {'eventCode': EVENT_CODE}, acc['token'])
    return (j.get('taskList') or []) if ok(j) else []


def finish_task(acc, task_code):
    j = call('com_cmall_familyhas_api_ApiFarmFinishTask',
             {'taskCode': task_code}, acc['token'])
    return ok(j), (j.get('resultMessage') or '')


def do_watering(acc, tree_code):
    """浇水"""
    j = call('com_cmall_familyhas_api_ApiFarmWatering',
             {'treeCode': tree_code, 'byMemberCode': '', 'eventCode': EVENT_CODE},
             acc['token'])
    if ok(j):
        return 'ok', '水壶余量 %s，本次消耗 %s' % (
            j.get('kettleWater'), j.get('waterNum'))
    return 'fail', (j.get('resultMessage') or 'code=%s' % j.get('resultCode'))


def pick_water(acc, water_code):
    """收雨水"""
    j = call('com_cmall_familyhas_api_ApiForFarmPickWater',
             {'waterCode': water_code}, acc['token'])
    if ok(j):
        return 'ok', '收水 %s g，水壶 %s' % (j.get('stealWaterNum'), j.get('kettleWater'))
    return 'fail', (j.get('resultMessage') or 'code=%s' % j.get('resultCode'))


def do_pick_all(acc, home=None, max_rounds=4):
    """收取全部待收雨水
       雨水列表来自 ApiForFarmHome 的 list[]，
       注意字段是拼错的 wateCode（少一个 r）；stealFlag=1 表示可收
       返回 (收水次数, 总雨水, 剩余待收数)
    """
    total = 0
    picked = 0
    remain = 0
    for rnd in range(1, max_rounds + 1):
        h = home if (rnd == 1 and home) else get_home(acc)
        if not h:
            break
        lst = h.get('list') or []
        pend = [x for x in lst if str(x.get('stealFlag')) == '1' and x.get('wateCode')]
        remain = len(pend)
        if not pend:
            if rnd == 1:
                log('   ℹ️ 暂无可收雨水（下次发放：%s）' % (h.get('nextGiveWaterTime') or '-'))
            break
        log('   第%d轮：发现 %d 滴待收雨水' % (rnd, len(pend)))
        for x in pend:
            code = x.get('wateCode')
            rd(1200, 2500)
            st, msg = pick_water(acc, code)
            if st == 'ok':
                picked += 1
                total += int(x.get('waterNum') or 0)
                log('      ✅ %s +%sg → 水壶 %s g' % (code, x.get('waterNum'), msg.split('水壶 ')[-1]))
            else:
                log('      ❌ %s %s' % (code, msg[:40]))
        time.sleep(1.5)
    return picked, total, remain



def get_friends(acc, page=1):
    """好友列表: friendList[] + randomMemberCode(随机路人，每次调用都换)"""
    j = call('com_cmall_familyhas_api_ApiForFarmGetFriends',
             {'pageIndex': page}, acc['token'])
    return j if ok(j) else {}


def friend_home(acc, member_code):
    """查看好友/路人农场主页，list[] 里 stealFlag=1 才是可偷的"""
    j = call('com_cmall_familyhas_api_ApiForFarmHome',
             {'othersMemberCode': member_code}, acc['token'])
    return j if ok(j) else {}


def steal_water(acc, limit=10):
    """偷好友/路人水滴（主要雨水来源）
       返回 (偷水次数, 总雨水, 来源人数)
    """
    total = 0
    picked = 0
    stolen_from = 0
    visited = set()
    empty_rounds = 0
    random_seen = False

    for _ in range(limit * 5):
        fr = get_friends(acc)
        if not fr:
            break
        if str(fr.get('randomFlag')) == '1' and fr.get('randomMemberCode'):
            random_seen = True

        # 优先级：随机路人 > 真好友
        targets = []
        rc = fr.get('randomMemberCode') or ''
        if rc and rc not in visited:
            targets.append(rc)
        for f in (fr.get('friendList') or []):
            fc = f.get('friendMemberCode') or ''
            if fc and fc not in visited and fc not in targets:
                targets.append(fc)

        if not targets:
            empty_rounds += 1
            if empty_rounds >= 5:
                if random_seen:
                    log('   ℹ️ 暂时没有可偷的好友')
                else:
                    log('   ℹ️ 服务端未分配随机路人（randomFlag=0），等刷新后再来')
                break
            continue
        empty_rounds = 0

        for code in targets:
            visited.add(code)
            fh = friend_home(acc, code)
            if not fh:
                continue
            nickname = fh.get('othersNickName') or code
            stealable = [w for w in (fh.get('list') or [])
                         if w.get('wateCode') and str(w.get('stealFlag')) == '1']
            if not stealable:
                continue
            got = 0
            for w in stealable:
                rd(800, 1600)
                st, msg = pick_water(acc, w.get('wateCode'))
                if st == 'ok':
                    g = int(w.get('waterNum') or 0)
                    got += g
                    total += g
                    picked += 1
                else:
                    break
            if got:
                stolen_from += 1
                log('      ✅ 偷 %s 的 %dg 雨水' % (nickname, got))
        if len(visited) >= limit:
            break

    return picked, total, stolen_from


def get_stage_prize(acc, tree_code):
    """查询阶段奖励（未领取的带 userStagePrizeUid）"""
    j = call('com_cmall_familyhas_api_ApiForFarmGetUserStagePrize',
             {'treeCode': tree_code}, acc['token'])
    return j if ok(j) else {}


def draw_stage_prize(acc, uid):
    """领取阶段奖励"""
    j = call('com_cmall_familyhas_api_ApiForFarmDrawStagePrize',
             {'stagePrizeUidStr': uid}, acc['token'])
    return ok(j), (j.get('resultMessage') or '')


def do_stage_prizes(acc, tree_code):
    """自动领取所有可领的阶段奖励，返回领取数量"""
    j = get_stage_prize(acc, tree_code)
    if not j:
        return 0
    got = 0
    for p in (j.get('stagePrizeList') or []):
        uid = p.get('userStagePrizeUid')
        if not uid:
            continue
        rd(1000, 2000)
        st, msg = draw_stage_prize(acc, uid)
        if st:
            got += 1
            log('      🎁 阶段奖励领取成功：%s' % (p.get('productName') or p.get('stageName') or uid))
        else:
            log('      ❌ 阶段奖励领取失败：%s' % (msg or '-')[:40])
    return got


def get_user_tree(acc):
    """获取我的树信息；treeCode 在 farmTree 里"""
    j = call('com_cmall_familyhas_api_ApiGetHuodongFarmUserTree', {}, acc['token'])
    if ok(j):
        return j
    # 兜底：从首页取
    home = get_home(acc)
    ft = home.get('farmTree') or {}
    return {'farmTree': ft} if ft else {}


# 可用 FinishTask 直接刷满的任务（浏览类）—— 按任务名匹配，taskCode 每天都会变
AUTO_TASK_NAMES = [
    '每日首次浇水',
    '热销爆款',
    '限时秒杀',
    '平价好货',
    '精选好货',
    '每日新品',
]
# 名称变化的兜底关键字（名称不完全一致时也能命中）
AUTO_TASK_KEYWORDS = ['浇水', '热销', '秒杀', '平价', '精选', '新品']
# 需要真实操作、不自动做的
MANUAL_TASKS = {
    '下单领雨滴': '需真实下单',
    '商品评论': '需真实评论',
    '买家秀评论': '需发图评论',
}


def run_tasks(acc, max_rounds=3):
    """批量完成任务：直接调 FinishTask，无需模拟浏览
       返回 (完成任务数, 总获得雨水, 明细列表)
    """
    detail = []
    total_water = 0
    done = 0

    tasks = {t.get('taskCode'): t for t in get_task_list(acc)}
    if not tasks:
        return 0, 0, []

    # 打印接口当天下发的全部任务码，方便排查
    log('     📥 接口下发 %d 个任务码：' % len(tasks))
    for t in tasks.values():
        log('        %-14s %-16s 奖励%sg %s/%s' % (
            t.get('taskCode'), t.get('taskName'),
            t.get('awardWaterNum'), t.get('alreadyNum'), t.get('taskNum')))

    # 按名称/关键字挑出可自动完成的任务（taskCode 每天变化，不能写死）
    targets = []
    for t in tasks.values():
        nm = (t.get('taskName') or '').strip()
        if nm in AUTO_TASK_NAMES or any(k in nm for k in AUTO_TASK_KEYWORDS):
            targets.append(t)
            if nm not in AUTO_TASK_NAMES:
                log('     ℹ️ 关键字兜底命中：%s' % nm)
        elif nm and nm not in MANUAL_TASKS:
            log('     ❔ 未知任务（未自动处理）：%s' % nm)

    for t in targets:
        code = t.get('taskCode')
        name = t.get('taskName')
        task_num = int(t.get('taskNum') or 0)
        already = int(t.get('alreadyNum') or 0)
        award = int(t.get('awardWaterNum') or 0)
        need = task_num - already
        if need <= 0:
            log('     · %-10s 已完成 %s/%s' % (name, already, task_num))
            detail.append({'name': name, 'status': 'done'})
            continue
        log('     ▶️ %-10s %s/%s 需补 %d 次' % (name, already, task_num, need))
        okc = 0
        for i in range(min(need, max_rounds * 10)):
            rd(1200, 2600)
            r_ok, msg = finish_task(acc, code)
            if r_ok:
                okc += 1
                total_water += award
            else:
                log('        ❌ 第%d次失败: %s' % (i + 1, msg[:40]))
                break
        if okc:
            done += 1
            log('        ✅ 完成 %d 次，+%dg 雨水' % (okc, award * okc))
            detail.append({'name': name, 'status': 'ok', 'water': award * okc})
        else:
            detail.append({'name': name, 'status': 'fail'})

    return done, total_water, detail


# ==================== 单账号 ====================
def run_account(acc):
    out = {'name': acc['name'], 'sign': '-', 'water': '-', 'tasks': 0, 'task_water': 0, 'sign_water': 0, 'pick_water': 0, 'pick_times': 0, 'steal_water': 0, 'steal_times': 0, 'stage_prize': 0,
           'reason': '', 'error': None}
    want = TASKS.lower()

    log('👤 账号：%s ｜ token=%s' % (acc['name'], mask(acc['token'])))
    log('')

    # ---------- 1. 校验 token ----------
    log('🔐 [1/4] 校验登录态')
    home = get_home(acc)
    if not home:
        out['error'] = 'token 失效或接口异常'
        log('   ❌ 校验失败（token 可能已过期，需重新抓包）')
        return out
    kettle = home.get('kettleWater')
    log('   ✅ 登录有效')
    log('   水壶余量：%s g' % kettle)
    log('')

    # ---------- 2. 签到 ----------
    log('📅 [2/4] 每日签到')
    if want not in ('all',) and 'sign' not in want:
        log('   ⏭️ 已按 HJC_TASKS 跳过')
        out['sign'] = 'skip'
    else:
        rd()
        st, msg, sign_water = do_sign(acc)
        out['sign'] = st
        out['sign_water'] = sign_water
        mark = {'ok': '✅', 'already': 'ℹ️', 'fail': '❌'}.get(st, '·')
        log('   %s %s' % (mark, msg))
    log('')

    # ---------- 3. 任务 + 浇水 ----------
    log('💧 [3/4] 农场任务与浇水')
    # treeCode 在首页返回的 farmTree 里
    ft = (home.get('farmTree') or {})
    tree_code = ft.get('treeCode') if isinstance(ft, dict) else None
    if tree_code:
        stage = ft.get('treeStage')
        pct = ft.get('surplusContribute')
        log('   🌳 我的树：%s (阶段 %s，还差 %s%%)' % (tree_code, stage, pct))
    else:
        tree_code = 'GS250818100001'
        log('   ℹ️ 未取到 treeCode，使用默认值 %s' % tree_code)

    # 任务列表
    tasks = get_task_list(acc)
    if tasks:
        log('   任务列表 %d 个：' % len(tasks))
        for t in tasks:
            log('     · %-10s %-14s 奖励%sg 进度%s/%s' % (
                t.get('taskName'), t.get('taskCode'),
                t.get('awardWaterNum'), t.get('alreadyNum'), t.get('taskNum')))

    # 自动完成任务（浏览类）
    if want in ('all',) or 'task' in want:
        log('')
        log('   📋 自动完成任务（直接 FinishTask）')
        tdone, twater, tdetail = run_tasks(acc)
        out['tasks'] = tdone
        out['task_water'] = twater
        if tdone:
            log('   ✅ 完成 %d 个任务，共 +%dg 雨水' % (tdone, twater))
        else:
            log('   ℹ️ 无可完成任务')
        # 列出不支持自动的
        for t in tasks:
            nm = t.get('taskName') or ''
            if nm in MANUAL_TASKS and int(t.get('alreadyNum') or 0) < int(t.get('taskNum') or 0):
                log('     ⏭️ %-10s %s（%s/%s）' % (
                    nm, MANUAL_TASKS[nm], t.get('alreadyNum'), t.get('taskNum')))

    # 收雨水（系统每天多次发放，list[] 里 stealFlag=1 的都能收）
    if want in ('all',) or 'pick' in want or 'water' in want:
        log('   🌧️ 收取雨水')
        pnum, pwater, premain = do_pick_all(acc, home=home)
        out['pick_times'] = pnum
        out['pick_water'] = pwater
        if pnum:
            log('   ✅ 收取 %d 滴，共 +%dg 雨水' % (pnum, pwater))
        if premain:
            log('   ⚠️ 仍有 %d 滴未收成功' % premain)
    else:
        log('   ⏭️ 收雨水已跳过')

    # 偷好友/路人水滴（雨水主要来源）
    if (want in ('all',) or 'steal' in want) and STEAL_LIMIT > 0 and not NO_STEAL:
        log('   🥷 偷好友水滴（上限 %d 人）' % STEAL_LIMIT)
        snum, swater, sfrom = steal_water(acc, STEAL_LIMIT)
        out['steal_times'] = snum
        out['steal_water'] = swater
        if swater:
            log('   ✅ 偷到 %dg 雨水（来自 %d 人）' % (swater, sfrom))
        else:
            log('   ℹ️ 无可偷水滴')
    elif NO_STEAL:
        log('   ⏭️ 偷水滴已关闭（NO_STEAL=1）')
    else:
        log('   ⏭️ 偷水滴已跳过')

    # 浇水（做 WATER_TIMES 次）
    if want in ('all',) or 'water' in want:
        log('   💧 浇水 %d 次' % WATER_TIMES)
        okc = 0
        for i in range(1, WATER_TIMES + 1):
            rd(1500, 3000)
            st, msg = do_watering(acc, tree_code)
            if st == 'ok':
                okc += 1
                log('      第%d次 ✅ %s' % (i, msg))
            else:
                log('      第%d次 ❌ %s' % (i, msg))
                break
        out['water'] = 'ok' if okc else 'fail'
        out['water_times'] = okc
        log('   ✅ 共浇水 %d/%d 次' % (okc, WATER_TIMES))

        # 浇水可能让树升级，顺手领阶段奖励
        rd(1200, 2000)
        h3 = get_home(acc)
        st_code = ((h3.get('farmTree') or {}).get('treeCode')) or tree_code
        pz = do_stage_prizes(acc, st_code)
        out['stage_prize'] = pz
        if pz:
            log('   🎁 领取 %d 个阶段奖励' % pz)
    else:
        log('   ⏭️ 浇水已跳过')
        out['water'] = 'skip'
        out['water_times'] = 0
    log('')

    # ---------- 4. 复查 ----------
    log('📊 [4/4] 复查')
    rd(1200, 2200)
    home2 = get_home(acc)
    if home2:
        log('   水壶余量：%s → %s g' % (kettle, home2.get('kettleWater')))
        if home2.get('nextGiveWaterTime'):
            log('   下次发水：%s' % home2.get('nextGiveWaterTime'))
    out['end_water'] = home2.get('kettleWater')
    return out


# ==================== 推送 ====================
NOTIFY_TITLE = '惠家友农场 🌱'


def notify(title, content, timeout=15):
    key = os.environ.get('QYWX_KEY') or ''
    if not key:
        log('⚠️ 提示：未在环境变量配置 QYWX_KEY，跳过通知发送。')
        return False
    body = {'msgtype': 'text',
            'text': {'content': '🔔 %s\n%s\n%s\n\n统计时间：%s' % (
                title, '-' * 20, content, time.strftime('%m-%d %H:%M:%S'))}}
    try:
        r = requests.post(
            'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=%s' % key,
            data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json'}, timeout=timeout)
        j = r.json()
        if j.get('errcode') == 0:
            log('🚀 企业微信机器人推送成功！')
            return True
        log('❌ 推送失败: %s' % j.get('errmsg'))
    except Exception as e:
        log('💥 推送异常: %s' % e)
    return False


def build_report(results):
    if not results:
        return ''
    lines = ['【惠家友农场】']
    for r in results:
        if r.get('error'):
            lines.append('❌ %s：%s' % (r['name'], r['error']))
            continue
        sw = r.get('sign_water') or 0
        tw = r.get('task_water') or 0
        wt = r.get('water_times') or 0
        pw = r.get('pick_water') or 0
        stw = r.get('steal_water') or 0
        gain = sw + tw + pw + stw
        sign_txt = ('签到+%dg' % sw) if sw else (
            '签到已做' if r.get('sign') == 'already' else '签到✗')
        seg = [sign_txt]
        if tw:
            seg.append('任务+%dg(%d个)' % (tw, r.get('tasks') or 0))
        elif r.get('tasks'):
            seg.append('任务%d个' % r['tasks'])
        if pw:
            seg.append('收水+%dg(%d滴)' % (pw, r.get('pick_times') or 0))
        if stw:
            seg.append('偷水+%dg(%d次)' % (stw, r.get('steal_times') or 0))
        if r.get('stage_prize'):
            seg.append('阶段奖%d个' % r['stage_prize'])
        if wt:
            seg.append('浇水%d次' % wt)
        lines.append('%s  %s' % (r['name'], ' ｜ '.join(seg)))
        lines.append('   水壶：%s g   今日新增：+%dg' % (r.get('end_water'), gain))
    if len(results) > 1:
        total = sum((r.get('sign_water') or 0) + (r.get('task_water') or 0) +
                    (r.get('pick_water') or 0) + (r.get('steal_water') or 0) for r in results)
        lines.append('')
        lines.append('合计今日新增：+%dg' % total)
    return '\n'.join(lines)


# ==================== 主流程 ====================
def main():
    global _ACC
    entries = parse_entries()
    if not entries:
        log('未设置或格式错误：%s = 备注#token' % ENV_NAME)
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
            log('❌ 执行异常: %s' % ex)
            results.append({'name': acc['name'], 'error': str(ex)[:50],
                            'sign': '-', 'water': '-', 'tasks': 0})
        if i < len(entries) - 1:
            time.sleep(4)

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
