#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
vx小程序  刁嘴巴会员中心 · 签到 + 贪吃蛇游戏
================================================================================

【变量】
    dzb_gpt = 备注#miniappToken
             例：eyJ1bmlvbmlkIjoib1ozaWQ...
             多账号：& 或换行分隔
             也兼容：备注#unionId#openId

【配置】都在下面【配置区】里直接改，不用设环境变量
    GAME_ROUNDS   每轮玩几局（默认 8）
    SCORE_MIN/MAX 上报分数区间（默认 1500~2000 随机，返 15 和币）
    PLAY_GAME     是否玩游戏（False 只签到）
    ENABLED       总开关

【游戏机制】
    每局消耗 5 和币，分数达标返和币：
      500-999   → 5
      1000-1499 → 10
      1500 以上 → 15
    分数由客户端直接上报，服务端不校验真实性
    每日最多 20 局

    免费机会：签到后 checkFreeChance 可能返回 true，
    此时开局不扣和币（脚本会自动检测并使用）

【定时】
    cron: 0 9 * * *

================================================================================
"""
import os
import re
import sys
import time
import json
import base64
import random

import requests

ENV_NAME = 'dzb_gpt'
BASE = 'https://q.hcszyx.cn/api4/'
APP_ID = 'wxab0a86aa9720cfcc'
UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) AppleWebKit/605.1.15 '
      '(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.76(0x18004c3a) '
      'NetType/4G Language/zh_CN')
NOTIFY_TITLE = '刁嘴巴会员中心 🍬'

# ============================ 配置区（改这里） ============================
GAME_ROUNDS = 8        # 每轮玩几局（每局净赚 10 和币；每日上限 20）
SCORE_MIN = 1500       # 上报分数下限（>=1500 返 15 和币）
SCORE_MAX = 2000       # 上报分数上限
# 每局在 [SCORE_MIN, SCORE_MAX] 内随机，避免每局分数完全一样
PLAY_GAME = True       # False = 只签到，不玩游戏
ENABLED = True         # False = 整体停用
# ==========================================================================

_ACC = None


def log(*a):
    tag = '[%s] ' % _ACC['name'] if _ACC else ''
    print(tag + ' '.join(str(x) for x in a), flush=True)


def mask(s):
    s = str(s or '')
    return s[:12] + '****' + s[-6:] if len(s) > 24 else s


# ==================== token ====================
def make_token(union_id, open_id):
    raw = json.dumps({'unionid': union_id, 'openid': open_id}, separators=(',', ':'))
    return base64.b64encode(raw.encode('utf-8')).decode()


def decode_token(tok):
    try:
        t = tok.strip()
        t += '=' * (-len(t) % 4)
        d = json.loads(base64.urlsafe_b64decode(t).decode('utf-8'))
        return d.get('unionid') or d.get('unionId'), d.get('openid') or d.get('openId')
    except Exception:
        return None, None


def parse_entries():
    raw = (os.environ.get(ENV_NAME) or '').strip()
    if not raw:
        return []
    out = []
    for line in re.split(r'[&\n]+', raw):
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split('#')]
        name = parts[0] if len(parts) >= 2 and parts[0] else '账号%d' % (len(out) + 1)
        if len(parts) >= 3:
            union_id, open_id = parts[1], parts[2]
            tok = make_token(union_id, open_id)
        elif len(parts) == 2:
            v = parts[1]
            union_id, open_id = decode_token(v)
            tok = v if union_id else ''
        else:
            continue
        if not tok:
            continue
        out.append({'name': name, 'token': tok, 'union_id': union_id, 'open_id': open_id})
    return out


# ==================== HTTP ====================
def headers(acc):
    return {
        'Content-Type': 'application/json',
        'User-Agent': UA,
        'miniappToken': acc['token'],
        'Referer': 'https://servicewechat.com/%s/98/page-frame.html' % APP_ID,
    }


def api(acc, path, body=None, timeout=20):
    """小程序主接口"""
    try:
        r = requests.post(BASE + 'miniapp/' + path, headers=headers(acc),
                          data=json.dumps(body or {}).encode('utf-8'), timeout=timeout)
        return r.json()
    except Exception as e:
        return {'code': '-999', 'msg': '请求异常: %s' % str(e)[:120]}


# ==================== 游戏 ====================
def get_snake_token(acc):
    """用 miniappToken 换取 snakeToken（游戏专用，有效期 7 天）"""
    try:
        r = requests.get(BASE + 'miniapp/snakeGameUrl', headers=headers(acc), timeout=20)
        url = (r.json().get('rows') or '')
        if 'snakeToken=' in url:
            return url.split('snakeToken=')[-1], url
    except Exception:
        pass
    return '', ''


def game_headers(tok, url):
    return {
        'Content-Type': 'application/json',
        'snakeToken': tok,
        'Origin': 'https://q.hcszyx.cn',
        'Referer': url,
        'User-Agent': UA,
    }


def g_get(h, path, q=''):
    try:
        r = requests.get(BASE + 'snakegame/' + path + q, headers=h, timeout=20)
        return r.json()
    except Exception as e:
        return {'code': '-999', 'msg': str(e)[:80]}


def g_post(h, path, body=None):
    try:
        r = requests.post(BASE + 'snakegame/' + path, headers=h,
                          data=json.dumps(body or {}).encode(), timeout=20)
        return r.json()
    except Exception as e:
        return {'code': '-999', 'msg': str(e)[:80]}


def run_game(acc, rounds):
    """玩贪吃蛇刷和币
       核心：每局消耗 5 和币，达标分数返还更多和币
       返回 (局数, 总奖励, 最终和币)
    """
    tok, url = get_snake_token(acc)
    if not tok:
        log('   ⚠️ 获取 snakeToken 失败')
        return 0, 0, None, 0
    h = game_headers(tok, url)

    info = g_get(h, 'userInfo').get('rows') or {}
    coin = info.get('totalIntegral') or 0
    played = info.get('todayGameTimes') or 0
    left = info.get('todayGameSurplusTimes')
    log('   💰 当前和币 %s ｜ 今日已玩 %s 次 ｜ 剩余 %s 次' % (coin, played, left))

    if coin < 5:
        log('   ℹ️ 和币不足 5，无法开局')
        return 0, 0, coin, 0

    done = 0
    total = 0
    free_used = 0
    for i in range(rounds):
        # 先查免费机会（签到送的，不扣和币）
        free = False
        try:
            fc = g_post(h, 'checkFreeChance')
            free = str(fc.get('rows')).lower() in ('true', '1')
        except Exception:
            pass

        if free:
            log('      🎁 检测到免费机会，本局不扣和币')
        elif coin < 5:
            log('   ℹ️ 和币不足且无免费机会，停止（%s）' % coin)
            break

        jj = g_post(h, 'joinGame')
        if str(jj.get('code')) != '200':
            log('   ❌ 开局失败：%s' % (jj.get('msg') or jj.get('code')))
            break
        biz = (jj.get('rows') or {}).get('businessId')
        if not biz:
            log('   ❌ 没拿到 businessId')
            break
        if free:
            free_used += 1
        else:
            coin -= 5

        # 模拟真实游戏时长：分数越高耗时越长（游戏60秒上限）
        # 1500~2000 分大约对应 25~55 秒
        score = random.randint(SCORE_MIN, SCORE_MAX)
        elapsed = int(25 + (score - 1500) / 500.0 * 30 + random.uniform(-5, 5))
        elapsed = max(15, min(58, elapsed))
        log('      ⏳ 模拟游戏时长 %ds' % elapsed)
        time.sleep(elapsed)
        res = g_post(h, 'finishGame', {'score': score, 'businessId': biz})
        rows = res.get('rows') or {}
        if str(res.get('code')) == '200':
            rew = rows.get('rewardIntegral') or 0
            total += rew
            done += 1
            coin = (rows.get('totalIntegral') or coin) + rew   # 响应里是扣币后的值，奖励需另加
            log('      第%d局 %d分 → +%d和币（余额 %s）' % (i + 1, score, rew, coin))
        else:
            log('      第%d局结算失败：%s' % (i + 1, res.get('msg')))
            break
        time.sleep(random.uniform(3.0, 8.0))   # 每局间隔随机，避免固定节奏

    return done, total, coin, free_used


# ==================== 业务 ====================
def check_sign(acc):
    j = api(acc, 'checkSignIn')
    return bool(j.get('rows')) if str(j.get('code')) == '200' else None


def do_sign(acc):
    return api(acc, 'singIn')


def get_info(acc):
    j = api(acc, 'personal/info')
    return (j.get('rows') or {}) if str(j.get('code')) == '200' else {}


def run_account(acc):
    out = {'name': acc['name'], 'ok': False, 'already': False, 'error': None,
           'integral': None, 'games': 0, 'game_reward': 0, 'coin': None}

    log('👤 账号：%s' % acc['name'])
    log('   unionId：%s' % mask(acc.get('union_id') or '-'))
    log('')

    # 1. 校验
    log('🔐 [1/4] 校验登录态')
    info = get_info(acc)
    if not info:
        out['error'] = 'Token 无效或接口异常'
        log('   ❌ %s' % out['error'])
        return out
    log('   ✅ 登录有效 ｜ 昵称 %s' % (info.get('nickname') or '-'))
    log('')

    # 2. 签到
    # 注意：checkSignIn 和 singIn 的判断标准不一致
    #   checkSignIn 返回 false 不代表没签到（实测签到后仍可能 false）
    # 因此直接调 singIn，以其返回为准
    log('📅 [2/4] 每日签到')
    j = do_sign(acc)
    code = str(j.get('code'))
    msg = j.get('msg') or ''
    if code == '200':
        out['ok'] = True
        log('   ✅ 签到成功')
    elif '已签' in msg or '重复' in msg:
        out['ok'] = True
        out['already'] = True
        log('   ℹ️ %s' % msg)
    else:
        out['error'] = msg or ('code=%s' % code)
        log('   ❌ 签到失败：%s' % out['error'])
    time.sleep(1.5)
    log('')

    # 3. 贪吃蛇
    log('🎮 [3/4] 贪吃蛇游戏')
    if not PLAY_GAME:
        log('   ⏭️ 已通过 DZB_GAME=0 跳过')
    else:
        gd, gr, coin, fu = run_game(acc, GAME_ROUNDS)
        out['free_used'] = fu
        out['games'] = gd
        out['game_reward'] = gr
        out['coin'] = coin
        if gd:
            log('   ✅ 共 %d 局，累计 +%d 和币（余额 %s）' % (gd, gr, coin))
    log('')

    # 4. 复查
    log('📊 [4/4] 复查')
    time.sleep(1.0)
    info2 = get_info(acc)
    if info2:
        out['integral'] = info2.get('totalIntegral')
        log('   💰 积分：%s' % out['integral'])
    return out


# ==================== 推送（青龙老本子格式） ====================
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
    lines = ['【刁嘴巴会员中心 · 签到】']
    for r in results:
        if r.get('error'):
            lines.append('❌ %s：%s' % (r['name'], r['error']))
            continue
        lines.append('%s  ✅ %s' % (r['name'], '今日已签' if r.get('already') else '签到成功'))
        if r.get('games'):
            lines.append('   🎮 游戏 %d 局 +%d和币' % (r['games'], r.get('game_reward') or 0))
        if r.get('coin') is not None:
            lines.append('   💰 和币：%s' % r['coin'])
        elif r.get('integral') is not None:
            lines.append('   💰 积分：%s' % r['integral'])
    return '\n'.join(lines)


# ==================== 主流程 ====================
def main():
    global _ACC
    if not ENABLED:
        log('⏸️ 已通过 DZB_ENABLED 关闭')
        return

    entries = parse_entries()
    if not entries:
        log('未设置或格式错误：%s = 备注#miniappToken' % ENV_NAME)
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
