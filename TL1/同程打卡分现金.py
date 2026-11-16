#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
vx小程序：同程旅行 · 周周打卡拿现金（每日签到 + 任务）
活动时间：2026年8月24日00:00-2026年12月31日 23:59
================================================================================

【入口】
     #小程序://同程旅行/V0r0vmU0TKFkYeH

【变量】
     tcdk_gpt = 备注#unionId#openid
     多个账号：& 或换行分隔
     
     可选：
       tcdk_ACTID = 活动ID，默认已内置（活动改版时才需要改）

【定时】
     cron 0 8 * * *  （自行修改）


        type 1  每周累计签到3次   +10
        type 2  每周累计签到6次   +20
        type 3  浏览AI旅行工具     +5
        type 4  试玩旅途解闷小游戏 +5
        type 5  每日浏览热门活动   +5
        type 6  分享好友助力     +10需好友点击

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

# ==================== 常量 ====================
ENV_NAME = 'tcdk_gpt'
API_ROOT = 'https://cvg.17usoft.com/activity/signInCash'
DEFAULT_ACTID = 'c60c3ca52ec79260203998db4578c913'
UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) AppleWebKit/605.1.15 '
      '(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.48(0x1800302b) NetType/WIFI Language/zh_CN')
REFERER = 'https://wx.17u.cn/'
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tcdk_cache.json')

# 可尝试自动完成的任务类型（浏览类）
AUTO_TASK_TYPES = [3, 4, 5]
# 需要真人参与，跳过
SKIP_TASK_TYPES = {6}

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
def parse_accounts():
    """tcdk_gpt = 备注#unionId#openid  （IP 不需要）"""
    raw = (os.environ.get(ENV_NAME) or '').strip()
    if not raw:
        return []
    out = []
    for line in raw.replace('\n', '&').split('&'):
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split('#')]
        if len(parts) >= 3:
            name, union_id, openid = parts[0], parts[1], parts[2]
        elif len(parts) == 2:
            name, union_id, openid = '账号%d' % (len(out) + 1), parts[0], parts[1]
        else:
            log('⚠️ 格式错误（需要 备注#unionId#openid）：%s' % mask(line))
            continue
        if not union_id or not openid:
            continue
        out.append({'name': name or ('账号%d' % (len(out) + 1)),
                    'unionId': union_id, 'openid': openid})
    return out


# ==================== 接口 ====================
def api(acc, path, extra=None, timeout=20):
    """统一 POST JSON"""
    body = {
        'actId': acc.get('actId') or DEFAULT_ACTID,
        'unionId': acc['unionId'],
        'idenId': acc['openid'],
    }
    if extra:
        body.update(extra)
    headers = {
        'Content-Type': 'application/json',
        'Accept': '*/*',
        'Referer': REFERER,
        'User-Agent': UA,
    }
    try:
        r = requests.post(API_ROOT + path, headers=headers,
                          data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                          timeout=timeout)
        j = r.json()
        return j
    except Exception as e:
        return {'code': 'EXC', 'message': str(e)[:150]}


def ok(j):
    return bool(j) and j.get('code') == 1000


# ==================== 业务 ====================
def get_index(acc):
    j = api(acc, '/getIndexInfo')
    if not ok(j):
        return None, j.get('message') or str(j.get('code'))
    return j.get('data') or {}, None


def do_sign(acc):
    j = api(acc, '/signIn')
    if not ok(j):
        return None, j.get('message') or str(j.get('code'))
    return j.get('data') or {}, None


def get_tasks(acc):
    j = api(acc, '/getTaskInfo')
    if not ok(j):
        return None, j.get('message') or str(j.get('code'))
    return (j.get('data') or {}).get('taskList') or [], None


def complete_task(acc, task_type):
    j = api(acc, '/completeTask', {'taskType': task_type})
    if not ok(j):
        return None, j.get('message') or str(j.get('code'))
    # data 可能是 dict / str / list / None，原样返回
    return j.get('data'), None


def claim_reward(acc, record_id):
    j = api(acc, '/claimTaskReward', {'taskRecordId': record_id})
    if not ok(j):
        return None, j.get('message') or str(j.get('code'))
    return j.get('data'), None


def _pick_record_id(d):
    """从 completeTask 的 data 里尽力取 taskRecordId；取不到返回 None"""
    if isinstance(d, dict):
        for k in ('taskRecordId', 'recordId', 'id', 'record_id'):
            v = d.get(k)
            if v not in (None, '', 0):
                return v
        # 嵌一层
        for v in d.values():
            if isinstance(v, dict):
                r = _pick_record_id(v)
                if r:
                    return r
    elif isinstance(d, (int, str)):
        t = str(d).strip()
        if t.isdigit() and len(t) >= 6:
            return t
    return None


# ==================== 单账号 ====================
def run_account(acc):
    out = {'name': acc['name'], 'sign': '-', 'tasks': '-', 'points': None,
           'before': None, 'after': None, 'weekSigned': None, 'weekRequired': None,
           'lines': [], 'error': None}
    lines = out['lines']

    # ① 查状态
    log('📊 查询签到状态…')
    data, err = get_index(acc)
    if data is None:
        log('❌ 状态查询失败: %s' % err)
        out['error'] = err
        lines.append('❌ 状态查询失败：%s' % str(err)[:40])
        return out

    signed = data.get('signedDays')
    required = data.get('requiredSignedDays')
    status = data.get('actionStatus')
    pts = data.get('pointsBalance')
    out['before'] = pts
    out['weekSigned'] = signed
    out['weekRequired'] = required
    log('   本周已签 %s/%s 天 ｜ 萌力值 %s ｜ 状态 %s' % (signed, required, pts, status))

    # ② 签到
    if status == 'SIGNED':
        log('📅 签到：ℹ️ 今日已签到（本周已签 %s 天）' % signed)
        lines.append('📅 签到：ℹ️ 今日已签')
        out['sign'] = 'already'
    elif status == 'EXPIRED':
        log('📅 签到：ℹ️ 本期签到已结束（本周已签 %s/%s 天，等下周重新开始）' % (signed, required))
        lines.append('📅 签到：ℹ️ 本期已结束')
        out['sign'] = 'already'
    else:
        log('📅 签到：今日未签，开始签到…')
        rd(1200, 2500)
        d, e2 = do_sign(acc)
        if d is None:
            log('📅 签到：❌ %s' % e2)
            lines.append('📅 签到：❌ %s' % str(e2)[:30])
            out['sign'] = 'fail'
        else:
            rp = d.get('rewardPoints')
            cd = d.get('consecutiveDays')
            bal = d.get('pointsBalance')
            log('📅 签到：✅ 成功  +%s 萌力值 ｜ 连续 %s 天 ｜ 余额 %s' % (rp, cd, bal))
            lines.append('📅 签到：✅ +%s（连签 %s 天）' % (rp, cd))
            out['sign'] = 'ok'
            out['points'] = bal

    # ③ 任务
    rd(1200, 2500, '等待后查询任务')
    tasks, e3 = get_tasks(acc)
    if tasks is None:
        log('⚠️ 任务列表获取失败: %s' % e3)
        lines.append('📝 任务：⚠️ 获取失败')
        out['tasks'] = 'fail'
    else:
        log('📝 任务列表 %d 项：' % len(tasks))
        for t in tasks:
            tt = t.get('type')
            log('   [%s] %-16s +%-2s  进度 %s/%s  %s' % (
                tt, t.get('title'), t.get('rewardPoints'),
                t.get('progress'), t.get('targetCount'),
                '✅完成' if t.get('completed') else ('可做' if t.get('couldComplete') else '不可做')))

        ok_c = skip_c = fail_c = 0
        detail = []
        for t in tasks:
            tt = t.get('type')
            title = t.get('title') or ('任务%s' % tt)
            rp = t.get('rewardPoints')

            # 只处理这三个浏览类任务
            if tt not in AUTO_TASK_TYPES:
                skip_c += 1
                continue

            completed = t.get('completed')
            could = t.get('couldComplete')

            if completed and not could:
                # 已完成且不可再领 = 今天已领过
                log('   ℹ️ %s：今日已领取' % title)
                detail.append('  ℹ️ %s 今日已领' % title)
                skip_c += 1
                continue

            # 尝试完成拿 recordId（不管 completed 状态，都试一次）
            log('   🚀 %s' % title)
            d, e4 = complete_task(acc, tt)
            if d is None:
                log('   ⚠️ %s 失败：%s' % (title, e4))
                detail.append('  ⚠️ %s 失败：%s' % (title, str(e4)[:20]))
                fail_c += 1
                continue

            rid = _pick_record_id(d)
            log('   ✅ %s 返回: %s' % (title, str(d)[:60]))

            if not rid:
                log('   ⚠️ %s 未返回 taskRecordId' % title)
                detail.append('  ⚠️ %s 无记录ID' % title)
                fail_c += 1
                continue

            rd(1500, 3000)
            d2, e5 = claim_reward(acc, rid)
            if d2 is None:
                log('   ⚠️ %s 领奖失败：%s' % (title, e5))
                detail.append('  ⚠️ %s 领奖失败' % title)
                fail_c += 1
            else:
                got = (d2.get('rewardPoints') if isinstance(d2, dict) else None) or rp
                bal = d2.get('pointsBalance') if isinstance(d2, dict) else None
                log('   ✅ %s +%s ｜ 余额 %s' % (title, got, bal))
                detail.append('  ✅ %s +%s' % (title, got))
                ok_c += 1
                if bal is not None:
                    out['after'] = bal

            rd(1500, 3000)

        if detail:
            lines.extend(detail)
        out['tasks'] = '%d/%d' % (ok_c + skip_c, len(tasks))
        log('📝 任务小结：成功 %d，跳过 %d，失败 %d' % (ok_c, skip_c, fail_c))

    # ④ 复查
    rd(1500, 3000, '等待后复查状态')
    d3, _ = get_index(acc)
    if d3:
        out['after'] = d3.get('pointsBalance')
        out['weekSigned'] = d3.get('signedDays', out['weekSigned'])
        out['weekRequired'] = d3.get('requiredSignedDays', out['weekRequired'])
        log('💰 萌力值：%s → %s ｜ 本周已签 %s/%s 天' % (
            out['before'], out['after'], d3.get('signedDays'), d3.get('requiredSignedDays')))
        lines.append('💰 萌力值：%s → %s ｜ 本周 %s/%s 天' % (
            out['before'], out['after'], d3.get('signedDays'), d3.get('requiredSignedDays')))
        if d3.get('qualified'):
            log('🎁 本周已达标，可参与抽奖（%s）' % d3.get('drawOpenTime'))
            lines.append('🎁 本周已达标，%s 开奖' % str(d3.get('drawOpenTime') or '')[:16])
    return out


# ==================== 推送 ====================
NOTIFY_TITLE = '同程打卡 🧳'


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
    lines = ['【同程旅行 · 周周打卡】']
    total_gain = 0
    for r in results:
        if r.get('error'):
            lines.append('❌  %s  %s' % (_pad(r['name'], 12), str(r['error'])[:20]))
            continue

        mark = {'ok': '✅', 'already': '·', 'fail': '❌'}.get(r.get('sign'), '·')
        line = '%s  %s' % (mark, _pad(r['name'], 12))

        before, after = r.get('before'), r.get('after')
        gain = None
        if before is not None and after is not None:
            try:
                gain = int(after) - int(before)
                total_gain += gain
            except Exception:
                pass

        # 萌力值：总余额 + 今日
        if after is not None:
            line += '  萌力值 %s' % after
            if gain:
                line += '  (今日 %+d)' % gain
        else:
            line += '  萌力值 —'

        # 本周签到进度
        ws, wr = r.get('weekSigned'), r.get('weekRequired')
        if ws is not None and wr is not None:
            line += '  ｜ 本周 %s/%s' % (ws, wr)

        lines.append(line)

    if total_gain:
        lines.append('')
        lines.append('📊 今日合计 +%d 萌力值' % total_gain)
    else:
        lines.append('')
        lines.append('📊 今日无新增')
    return '\n'.join(lines)


# ==================== 主流程 ====================
def main():
    global _ACC
    accounts = parse_accounts()
    if not accounts:
        log('未设置或格式错误：%s = 备注#unionId#openid（多账号 & 或换行）' % ENV_NAME)
        return

    actid = (os.environ.get('tcdk_ACTID') or '').strip()
    for a in accounts:
        a['actId'] = actid or DEFAULT_ACTID

    if os.environ.get('DRYRUN') == '1':
        print('共 %d 个账号:' % len(accounts))
        for a in accounts:
            print('  - %s | unionId=%s openid=%s' % (a['name'], mask(a['unionId']), mask(a['openid'])))
        print('\nactId:', accounts[0]['actId'])
        return

    log('共 %d 个账号 ｜ actId %s' % (len(accounts), accounts[0]['actId'][:12] + '…'))

    results = []
    for i, a in enumerate(accounts):
        _ACC = a
        log('=' * 50)
        log('📱 账号 %d/%d 开始' % (i + 1, len(accounts)))
        log('=' * 50)
        try:
            results.append(run_account(a))
        except Exception as e:
            log('❌ 执行异常: %s' % e)
            results.append({'name': a['name'], 'error': str(e)[:60],
                            'lines': [], 'before': None, 'after': None})
        if i < len(accounts) - 1:
            rd(5000, 10000, '账号间间隔')

    text = build_report(results)
    print('\n' + '=' * 50)
    print(text)
    print('=' * 50)
    if '--no-push' not in sys.argv:
        notify(NOTIFY_TITLE, text)


if __name__ == '__main__':
    main()
