#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
====================================================================
  APP：芳华未来 · 自动签到 & 刷视频领芳华币（RSA + AES 混合加密已完全逆向）

【使用方法】
  1. 抓包获取 AppToken（Stream 抓 api.cdwjyyh.com 任意请求的 AppToken 头）
  2. 青龙环境变量：fh_gpt = 备注#AppToken，多账号&分割或换行
     也可在token后面#deviceId，不加脚本自动生成
  3. cron 0 12 * * * 一天一次足够
  4. 每次运行自动刷35个视频，数量可在变量自行修改
  
【环境变量】
  fh_gpt    必填，AppToken
  fh_times  可选，刷视频轮数，默认 35

====================================================================
"""

import base64
import json
import os
import random
import string
import sys
import time

import requests
from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import pad

# ==================== 配置区 ====================

# ---- 多账号 ----
# 青龙变量 fh_gpt 支持多行（或 & 分隔），每行一个账号：
#   备注#token                      → deviceId 自动生成并缓存
#   备注#token#deviceId             → 手动指定 deviceId
#   也可以不写备注，直接 token
ENV_TOKEN = 'fh_gpt'
DEVICE_CACHE = 'fh_devices.json'   # deviceId 缓存文件
TIMES = int(os.environ.get('fh_times') or 35)   # 刷视频轮数
DELAY_MIN = 65                # 两次领取最小间隔（秒）
DELAY_MAX = 110               # 最大间隔
PUSH_ENABLE = True            # 推送开关

BASE_URL = 'https://api.cdwjyyh.com'
DEVICE_ID = 'C54306CA-E831-42AF-903E-14BAD17F287E'
APP_VERSION = '1.8.2'
APP_VERSION_CODE = '1821'
UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) '
      'AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 ')
IV = b'\x00' * 16   # 兼容旧引用，实际加解密用 key 作 IV
AES_CHARS = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'

# ---- RSA 密钥对（从 App 二进制提取）----
PUBLIC_KEY_B64 = (
    'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAwVjN8e7S9Ygg2jzc+laQEYD1YxSRppwU'
    'l1fEjfpV8CF/KvQ5IgTcyUYDe3O7/41+i7HjX2ZuwDXPOhhoVy6oD2e/NS/+XmUYLt9aEzo+erb'
    'q2+uxjwK93t0akM5C9xZDa4Ji0M5ICfZMx8pt56fTIIi5m8C3s7fhh8RSVUp78XK054ZweW25Xe3'
    'tQICF6UuuqMAESfTGfhP591hEikbJTxUhXfRywjarlwziZyP9waZYu8D0QA7Z84xaDPU1h3kgxb6'
    'Gt5DUAdCOg0dMxuiC24glnUET9yzHa3bIglZMMxpBiGI+B9jDYjKa03IF1NfsQn8eN1n+JlHyeM'
    'XtITrgqQIDAQAB'
)
PRIVATE_KEY_B64 = (
    'MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDBWM3x7tL1iCDaPNz6VpARgPVj'
    'FJGmnBSXV8SN+lXwIX8q9DkiBNzJRgN7c7v/jX6LseNfZm7ANc86GGhXLqgPZ781L/5eZRgu31oT'
    'Oj56turb67GPAr3e3RqQzkL3FkNrgmLQzkgJ9kzHym3np9MgiLmbwLezt+GHxFJVSnvxcrTnhnB5'
    'bbld7e1AgIXpS66owARJ9MZ+E/n3WESKRslPFSFd9HLCNquXDOJnI/3Bpli7wPRADtnzjFoM9TWH'
    'eSDFvoa3kNQB0I6DR0zG6ILbiCWdQRP3LMdrdsiCVkwzGkGIYj4H2MNiMprTcgXU1+xCfx43Wf4m'
    'UfJ4xe0hOuCpAgMBAAECggEAdMfOnHJDuUmfjjF0xz/BhND/ZfjmgFuFlGPOtHKftYqF5MveNk35'
    'jRhcwhQFWTV9WaL4UobsHexiXhSf8QidObDQLK/wU9N759O/9B0Z38Tb1jll5ZsiU5n4kb4DdHpd'
    '/nGifbwahundNk9uUp1rSBtNAGZGjqZh8j8B+8IhWpOA1090lPiqcbnCMueSVF3VghNPAYBYE/Vp'
    'S1zQnkx54FiS/ojvhZNmW9rSnXtci3fiQkLOg2GHI5ZTIxbFzOVb1F+TTGxtHcwOddOXz6DuaQmy'
    'sXEmavcw7PrmeibWhc/JggBiBBcYLEUbnDdYIwnPmP+ymaQfxYUv+wQ/fjvgAQKBgQDqBGY8/pMT'
    'ngWRnipAS0ciI2to17oor1ovutAjMEEHXmHFeKVCh3NFkd0xscUF4wqqkZm8VdRz9QEANlfRgy/C'
    'RSPTHGxZcBwjwdgr0f946XL5E2RGfNChWjECTSCxxHKktfuIrjDR1bkDIWwYgpGUncnAL8crn+Ios'
    'qlo4YeTSQKBgQDTgl+olhYL6rg3VeiNqbWi30w3+Xn8QOBNBnpKXBxdsUD/CBqIFyYJnvG3y2yq'
    'bNv0JwQijxC7o7VsF72eJYij3zYSufrsU6nIfilMMFpBIy5zJARiGEev3ugbIQyE09BIeizxVmOs'
    'Z6exJFhej4UipTr7xTOqulmBVtjg8omCYQKBgEcDBL83hQvr5Ma2Vx3heflrBBnxdIUKCPT43FYB'
    'O4pv4n1YydUxYxJWW+fLiPzrU35E5oDXDrwNObuFwgpKo8Bw2JkkQ+Cz+2YCWYWamMppFMFuV/xn'
    'vatowfxvyR8IfL1sl6J3MUtLbnP7vWCGpoSRiPovxWGAh9FPvcacwVY5AoGAOjvfEo+gKk/JwJKK'
    'oNZlCB7q4U5y450JJKvv56FMvg8bkhwtEeMtueBlNPFxTcsDFEnZvZoeRUthnA09S9mRsWy3ephy'
    'Gbc/O9BglnWJo/2HwHPeMRP2SNnalf2XcMrQwePBlADxGHrBlOgo3IAva8aKYt98xjjgg9fhhq3A'
    'ZoECgYEA14vIL+vdzsvgIMT1mNRqpDOTNEh9STOFPI2qD+UR0GMcPyoqsMb6ySkgPw+Evrx3W+SZ'
    'ASAFDxTFcIWQ2Ok3ZKzq9nZMNbSerd+lQ7KUmunBORVGatuE1etOWIeXl63G05Rz31ElZBxi03g9'
    '/FdPp5ImE+NFdpN3pOvjTddv7KM='
)

_PUB = RSA.importKey(base64.b64decode(PUBLIC_KEY_B64))
_PRIV = RSA.importKey(base64.b64decode(PRIVATE_KEY_B64))
_CIPHER_PUB = PKCS1_v1_5.new(_PUB)
_CIPHER_PRIV = PKCS1_v1_5.new(_PRIV)


# ==================== 加解密 ====================

def rand_nonce(n=11):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=n))


def rand_aes_key(n=16):
    return ''.join(random.choices(AES_CHARS, k=n))


def aes_encrypt(plain: bytes, key: bytes) -> str:
    """AES-CBC/PKCS7，**IV = aesKey 本身**（已逆向确认）"""
    return base64.b64encode(
        AES.new(key, AES.MODE_CBC, key).encrypt(pad(plain, 16))
    ).decode()


def aes_decrypt(b64: str, key: bytes) -> bytes:
    """解密并提取 JSON，IV = aesKey"""
    raw = AES.new(key, AES.MODE_CBC, key).decrypt(base64.b64decode(b64))
    dec = json.JSONDecoder()
    for i, ch in enumerate(raw):
        if ch != 0x7b:
            continue
        try:
            text = raw[i:].decode('utf-8', 'ignore')
            _obj, end = dec.raw_decode(text)
            return text[:end].encode('utf-8')
        except Exception:
            continue
    raise ValueError('解密失败：未找到 JSON')


def build_sign(aes_key: str, ts: str) -> str:
    return base64.b64encode(
        _CIPHER_PUB.encrypt(f'timestamp={ts}&aesKey={aes_key}'.encode())
    ).decode()


def decrypt_response(j: dict) -> dict:
    key = _CIPHER_PRIV.decrypt(base64.b64decode(j['encryptedKey']), None)
    return json.loads(aes_decrypt(j['encryptedData'], key))


# ==================== 推送 ====================

def load_notify():
    cur = os.path.abspath(os.path.dirname(__file__))
    for f in (os.path.join(cur, 'notify.py'), '/ql/data/scripts/notify.py'):
        if os.path.exists(f):
            try:
                sys.path.insert(0, os.path.dirname(f))
                from notify import send
                return send
            except ImportError:
                continue
    return None


# ==================== 业务 ====================

class FangHua:
    def __init__(self, token, device_id=None, name=''):
        self.token = token.strip()
        self.name = name or self.token[:12]
        self.device_id = device_id or DEVICE_ID
        self.sess = requests.Session()
        self.today_gain = 0
        self.sign_ok = False
        self.balance = None
        self.start_balance = None
        self.user_id = self._parse_uid()

    def _parse_uid(self):
        try:
            p = self.token.split('.')[1]
            claims = json.loads(base64.urlsafe_b64decode(p + '=' * (-len(p) % 4)))
            return claims.get('sub')
        except Exception:
            return ''

    # ---- 底层请求 ----
    def _request(self, path, payload=None, method='POST'):
        aes_key = rand_aes_key(16)
        ts = str(int(time.time() * 1000))
        headers = {
            'AppToken': self.token,
            'X-Api-Nonce': rand_nonce(),
            'X-Api-DeviceId': self.device_id,
            'X-Api-Sign': build_sign(aes_key, ts),
            'X-Api-Timestamp': ts,
            'AppVersionCode': APP_VERSION_CODE,
            'AppVersion': APP_VERSION,
            'AppPlatform': 'ios',
            'User-Agent': UA,
            'Content-Type': 'text/plain',
        }
        body = ''
        if payload is not None:
            body = aes_encrypt(json.dumps(payload, separators=(',', ':')).encode(),
                               aes_key.encode())
        try:
            if method == 'GET':
                url = BASE_URL + path
                if payload:
                    from urllib.parse import urlencode
                    url += '?' + urlencode(payload)
                r = self.sess.get(url, headers=headers, timeout=20)
            else:
                r = self.sess.post(BASE_URL + path, headers=headers, data=body, timeout=20)
        except Exception as e:
            return {'code': -1, 'msg': f'请求异常: {e}'}

        txt = r.text.strip()
        if not txt.startswith('{'):
            return {'code': r.status_code, 'msg': txt[:80]}
        try:
            j = json.loads(txt)
        except ValueError:
            return {'code': -1, 'msg': '响应解析失败'}
        if 'encryptedData' in j and 'encryptedKey' in j:
            try:
                return decrypt_response(j)
            except Exception as e:
                return {'code': -1, 'msg': f'解密失败: {e}'}
        return j

    # ---- 查询 ----
    def get_balance(self):
        """从钱包接口读取余额"""
        r = self._request(f'/app/common/getWallet/{self.user_id}', None, 'GET')
        d = r.get('data') if isinstance(r.get('data'), dict) else r
        try:
            self.balance = int(d['integral'])
            return self.balance
        except Exception:
            return None

    def get_today_total(self):
        """统计今日获得的芳华币"""
        payload = {'pageNum': 1, 'pageSize': 50}
        r = self._request('/app/integral/getUserIntegralLogsList', payload, 'GET')
        try:
            rows = r['data']['list']
        except Exception:
            rows = r.get('list') or []
        today = time.strftime('%Y-%m-%d')
        total = 0
        for it in rows:
            t = str(it.get('createTime') or it.get('create_time') or '')
            if t.startswith(today) and int(it.get('integral') or 0) > 0:
                total += int(it.get('integral') or 0)
        return total

    # ---- 动作 ----
    def sign(self):
        r = self._request('/app/integral/sign', {})
        code = r.get('code')
        if code == 200:
            self.sign_ok = True
            return True, '签到成功'
        msg = r.get('msg') or r.get('message') or '未知错误'
        if '已签' in str(msg):
            self.sign_ok = True
            return True, '今日已签到'
        return False, msg

    def get_videos(self, size=10):
        """获取随机视频列表"""
        r = self._request('/app/video/getVideoList-new',
                          {'keyword': '', 'isRandom': 1, 'videoId': '',
                           'pageNum': 1, 'pageSize': size}, 'GET')
        try:
            return [str(v['id']) for v in r['data']['list']]
        except Exception:
            return []

    def track(self, vid, event):
        """播放上报：PLAY / PLAY_3S / COMPLETE"""
        return self._request('/app/video/track', {'videoId': str(vid), 'event': event})

    def watch_one(self, vid, seconds=12):
        """模拟完整观看一个视频并领取奖励"""
        self.track(vid, 'PLAY')
        time.sleep(random.uniform(2.5, 4.5))
        self.track(vid, 'PLAY_3S')
        time.sleep(seconds)
        self.track(vid, 'COMPLETE')
        r = self._request('/app/integral/addIntegral', {'type': 2, 'videoId': str(vid)})
        code = r.get('code')
        msg = r.get('msg') or r.get('message') or ''
        if code == 200:
            return True, msg or 'ok'
        if '频繁' in str(msg):
            return False, '限流'
        return False, msg or f'code={code}'

# ==================== 主流程 ====================

def load_device_cache():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), DEVICE_CACHE)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_device_cache(cache):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), DEVICE_CACHE)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def parse_accounts(raw):
    """解析 fh_gpt：每行一个账号，支持 备注#token[#deviceId]"""
    import uuid
    cache = load_device_cache()
    result = []
    lines = []
    for chunk in raw.replace('&', '\n').split('\n'):
        if chunk.strip():
            lines.append(chunk.strip())
    for line in lines:
        parts = [x.strip() for x in line.split('#') if x.strip()]
        token = ''
        name = ''
        did = ''
        for x in parts:
            if x.startswith('eyJ'):
                token = x
            elif len(x) >= 30 and x.count('-') == 4:
                did = x
            elif not name:
                name = x
        if not token:
            continue
        key = token[-20:]
        if not did:
            did = cache.get(key) or str(uuid.uuid4()).upper()
            cache[key] = did
        result.append({'name': name or token[:12], 'token': token, 'device_id': did})
    save_device_cache(cache)
    return result


def run_account(acc, idx, total, times):
    print('\n' + '=' * 52)
    print(f'账号 {idx}/{total}: {acc["name"]}')
    print('=' * 52)

    app = FangHua(acc['token'], acc['device_id'], acc['name'])
    print(f'deviceId: {app.device_id}')

    start = app.get_balance()
    if start is None:
        print('❌ 无法获取余额，AppToken 可能已失效')
        return None
    print(f'起始余额: {start}')

    print('\n[签到]')
    ok, msg = app.sign()
    print(f'  {"✅" if ok else "❌"} {msg}')

    print(f'\n[刷视频] 计划 {times} 个')
    ok_cnt = 0
    vids = app.get_videos(10)
    if not vids:
        print('  ❌ 获取视频列表失败')
    idx_v = 0
    for i in range(times):
        if idx_v >= len(vids):
            vids = app.get_videos(10)
            idx_v = 0
            if not vids:
                break
            random.shuffle(vids)
        vid = vids[idx_v]
        idx_v += 1
        t = time.strftime('%H:%M:%S')
        ok, msg = app.watch_one(vid)
        if ok:
            ok_cnt += 1
            print(f'  [{t}] {i+1}/{times} ✅ {msg}')
        elif msg == '限流':
            print(f'  [{t}] {i+1}/{times} ⏸ 限流，等 60 秒')
            time.sleep(60)
        else:
            print(f'  [{t}] {i+1}/{times} ❌ {msg}')
        if i < times - 1:
            time.sleep(random.uniform(2, 6))

    end = app.get_balance()
    today = app.get_today_total()
    gain = (end - start) if (end and start) else 0

    print('\n' + '-' * 52)
    print(f'签到: {"成功" if app.sign_ok else "失败"}')
    print(f'领取: {ok_cnt}/{times} 次')
    print(f'余额: {start} → {end}  (+{gain})')
    print(f'今日累计: {today}')
    print('-' * 52)

    return {'name': acc['name'], 'sign': app.sign_ok, 'ok': ok_cnt,
            'times': times, 'start': start, 'end': end,
            'gain': gain, 'today': today}


def main():
    raw = (os.environ.get(ENV_TOKEN) or '').strip()
    if not raw:
        print(f'❌ 未设置环境变量 {ENV_TOKEN}')
        return

    accounts = parse_accounts(raw)
    if not accounts:
        print(f'❌ {ENV_TOKEN} 中没有找到有效 AppToken')
        return

    times = int(os.environ.get('fh_times') or 35)
    print('=' * 52)
    print(f'芳华未来 · 自动签到 & 刷视频')
    print(f'共 {len(accounts)} 个账号，每号 {times} 个视频')
    print('=' * 52)

    results = []
    for i, acc in enumerate(accounts, 1):
        try:
            r = run_account(acc, i, len(accounts), times)
            if r:
                results.append(r)
        except Exception as e:
            print(f'账号 {acc["name"]} 异常: {e}')
        if i < len(accounts):
            time.sleep(random.uniform(5, 15))

    # 汇总推送
    if results and PUSH_ENABLE:
        send = load_notify()
        if send:
            all_ok = all(r['sign'] and r['ok'] >= r['times'] for r in results)
            title = '芳华 · ' + ('全部完成' if all_ok else '部分完成')
            lines = []
            for r in results:
                lines.append(
                    f"{r['name']}: 签到{'✅' if r['sign'] else '❌'} "
                    f"视频{r['ok']}/{r['times']} "
                    f"+{r['gain']} (今日{r['today']}) 总{r['end']}"
                )
            try:
                send(title, '\n'.join(lines))
                print('\n推送已发送')
            except Exception as e:
                print(f'推送失败: {e}')


if __name__ == '__main__':
    main()
