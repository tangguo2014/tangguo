#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
脚本名称: 钻石矿场每日全自动任务，信息自动脱敏
链接地址: https://tj.hqlmapp.com/plugin.php?id=orange_down&x=164370
功能实现: 签到 -> 收益领取 -> 雇佣矿工 -> 资产核对 -> 推送
环境变量: zskc_gpt = 备注#账号#密码 (多账号支持换行或 & 分隔，无备注时可直接传 账号#密码)
定时建议: cron 20 8,20 * * * (每天二次自行修改)
安装依赖：requests
"""

import os
import re
import sys
import time
import requests

try:
    from notify import send
except ImportError:
    def send(title, content):
        pass

HOST_URL = "https://tj.hqlmapp.com/"
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 13; V2244A Build/TP1A.220624.014; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/120.0.0.0 "
    "Mobile Safari/537.36"
)
REQUEST_TIMEOUT = 25
MAX_HIRE_ROUNDS = 400  

def print_log(msg):
    """格式化标准日志输出"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)

def desensitize_phone(phone_str):
    """手机号脱敏展示"""
    if len(phone_str) >= 7:
        return f"{phone_str[:3]}****{phone_str[-4:]}"
    return phone_str

class DiamondMiner:
    def __init__(self, tag, account, password):
        self.tag = tag.strip()
        self.phone = account.strip()
        self.password = password.strip()
        self.log_prefix = f"[{self.tag}]"
        
        # 初始化持久化会话
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Referer": HOST_URL + "portal.php",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        
        # 记录运行数据以供推送
        self.status_sign = "未签到"
        self.status_claim = "未领取"
        self.daily_production = "未统计"
        self.hire_result = "无操作"
        self.workers = 0
        self.coins = 0
        self.diamonds = 0

    @staticmethod
    def _decode_response(resp):
        """站点采用GBK编码，统一容错解码"""
        return resp.content.decode("gb18030", errors="ignore")

    def _get_page(self, params):
        """通用 GET 请求封装"""
        resp = self.session.get(HOST_URL + "plugin.php", params=params, timeout=REQUEST_TIMEOUT)
        return self._decode_response(resp)

    def _post_page(self, params, data):
        """通用 POST 请求封装"""
        resp = self.session.post(HOST_URL + "plugin.php", params=params, data=data, timeout=REQUEST_TIMEOUT)
        return self._decode_response(resp)

    @staticmethod
    def _extract_formhash(html):
        """从页面源码中解析 formhash 参数"""
        match = re.search(r"formhash=([0-9a-f]{8})", html)
        return match.group(1) if match else ""

    @staticmethod
    def _extract_system_message(html):
        """从 Discuz! 提示框中提取响应文案"""
        patterns = (
            r"<div class=\"toast\">(.*?)</div>",
            r"showerror\('(.*?)'\)",
            r"showright\('(.*?)'\)"
        )
        for pattern in patterns:
            match = re.search(pattern, html, re.S)
            if match:
                return re.sub(r"<[^>]+>", "", match.group(1)).strip()
        return ""

    @staticmethod
    def _find_prev_number(html, keyword):
        """定位标签文本前方的数值内容"""
        pos = html.find(keyword)
        if pos < 0:
            return None
        nums = re.findall(r">\s*([\d.]+)\s*<", html[:pos])
        if not nums:
            return None
        latest = nums[-1]
        return float(latest) if "." in latest else int(latest)

    def login(self):
        """执行短信/密码登录"""
        try:
            login_page = self._get_page({"id": "jzsjiale_sms:mobile", "act": "login"})
            fh = self._extract_formhash(login_page)
            if not fh:
                print_log(f"{self.log_prefix} 登录失败: 无法获取页面的安全哈希校验码 (formhash)")
                self.status_sign = "登录异常 (formhash获取失败)"
                return False

            post_params = {
                "id": "jzsjiale_sms:mobile",
                "act": "phonemimalogin",
                "phoneloginsubmit": "yes",
                "formhash": fh
            }
            post_data = {
                "phone": self.phone,
                "phone_password": self.password,
                "cookietime": "2592000",
                "referer": HOST_URL + "portal.php"
            }
            body = self._post_page(post_params, post_data)
            
            if "loginsuccess" in body:
                print_log(f"{self.log_prefix} 用户身份认证通过，登录成功")
                return True
                
            err_match = re.search(r'"msg":"(.*?)"', body) or re.search(r'"data":"(.*?)"', body)
            err_msg = err_match.group(1) if err_match else "账号密码有误或被拦截"
            print_log(f"{self.log_prefix} 登录受阻: {err_msg}")
            self.status_sign = f"登录失败 ({err_msg})"
            return False

        except Exception as err:
            print_log(f"{self.log_prefix} 请求登录接口发生网络异常: {err}")
            self.status_sign = "登录接口网络异常"
            return False

    def fetch_home_overview(self):
        """同步首页矿工数及当日产出明细"""
        try:
            html = self._get_page({"id": "gbank:front"})
            worker_match = re.search(r"您当前拥有矿工数量.*?Money\">\s*([\d.]+)", html, re.S)
            if worker_match:
                self.workers = worker_match.group(1)

            data_groups = re.findall(r'<ul class="item">\s*(.*?)</ul>', html, re.S)
            if len(data_groups) >= 2:
                prod_nums = re.findall(r">\s*([\d.]+)\s*<", data_groups[-1])
                if len(prod_nums) >= 3:
                    self.daily_production = f"共 {prod_nums[0]} (金币 {prod_nums[1]} + 钻石 {prod_nums[2]})"
                    print_log(f"{self.log_prefix} 当前拥有矿工: {self.workers} 个 | 今日已产出: {self.daily_production}")
                    return

            print_log(f"{self.log_prefix} 当前拥有矿工: {self.workers} 个")
        except Exception as err:
            print_log(f"{self.log_prefix} 获取首页矿区概况异常: {err}")

    def query_wallet_assets(self):
        """查询金币、钻石及矿工数量，并补充 formhash"""
        try:
            html = self._get_page({"id": "gbank:front", "mod": "index", "act": "hmoney", "do": "asc"})
            w = self._find_prev_number(html, "已有矿工")
            c = self._find_prev_number(html, "我的金币")
            d = self._find_prev_number(html, "我的钻石")
            if w is not None:
                self.workers = w
            if c is not None:
                self.coins = c
            if d is not None:
                self.diamonds = d
            return w, c, d, self._extract_formhash(html)
        except Exception as err:
            print_log(f"{self.log_prefix} 查询矿区资产异常: {err}")
            return None, None, None, ""

    def sign_in(self):
        """执行每日签到打卡"""
        try:
            html = self._get_page({"id": "gsignin:index"})
            if "btn_visited" in html or "已签到" in html:
                days = self._find_prev_number(html, "累计签到")
                day_text = f"累计 {days} 天" if days is not None else "已打卡"
                self.status_sign = f"今日已签到 ({day_text})"
                print_log(f"{self.log_prefix} 每日签到: {self.status_sign}")
                return

            fh = self._extract_formhash(html) or self.query_wallet_assets()[3]
            resp = self._get_page({
                "id": "gsignin:index",
                "action": "signin",
                "formhash": fh,
                "inajax": "1",
                "ajaxtarget": "show_toast"
            })
            msg = self._extract_system_message(resp) or "打卡成功"
            self.status_sign = msg
            print_log(f"{self.log_prefix} 每日签到: {msg}")
        except Exception as err:
            self.status_sign = f"签到失败 ({err})"
            print_log(f"{self.log_prefix} 执行签到请求时发生异常: {err}")

    def claim_earnings(self):
        """收取今日已产出的利息与收益"""
        try:
            resp = self._get_page({
                "id": "gbank:front",
                "mod": "index",
                "act": "interest"
            })
            msg = self._extract_system_message(resp) or "已领取完毕"
            self.status_claim = msg
            print_log(f"{self.log_prefix} 矿场收益: {msg}")
        except Exception as err:
            self.status_claim = f"领取失败 ({err})"
            print_log(f"{self.log_prefix} 领取矿场收益请求异常: {err}")

    def _hire_request(self, hire_amount, formhash):
        """向服务端提交单次雇佣数据包"""
        return self._post_page(
            {"id": "gbank:front", "mod": "index", "act": "hmoney", "do": "asc", "formhash": formhash},
            {"money": str(hire_amount)}
        )

    def hire_workers_auto(self):
        """
        自动满额雇佣矿工 (1 金币 = 1 矿工)
        自适应收敛单次投递上限，避免超出服务端单次限制，分批将全部金币转化为矿工
        """
        try:
            _, available_coins, _, fh = self.query_wallet_assets()
        except Exception as err:
            print_log(f"{self.log_prefix} 读取雇佣资产失败: {err}")
            self.hire_result = "查询金币异常"
            return

        coins = int(available_coins or 0)
        if coins <= 0:
            print_log(f"{self.log_prefix} 当前账户可雇佣金币为 0，无需提交")
            self.hire_result = "金币为0无需雇佣"
            return

        if not fh:
            print_log(f"{self.log_prefix} 雇佣中断: 未能捕获到有效的 formhash")
            self.hire_result = "缺少安全令牌"
            return

        # 阶段 1: 动态探测单次提交额度上限 (二分自适应收敛)
        test_amount = coins
        while test_amount >= 1:
            try:
                resp = self._hire_request(test_amount, fh)
            except Exception as err:
                print_log(f"{self.log_prefix} 雇佣额度试探请求失败: {err}")
                self.hire_result = "网络试探失败"
                return

            if "showright(" in resp and "showerror(" not in resp:
                break

            reason = self._extract_system_message(resp)
            if any(k in reason for k in ("不能超过", "超出", "上限")):
                test_amount //= 2
                continue
            
            print_log(f"{self.log_prefix} 服务端拒绝雇佣请求: {reason or '未知规则限制'}")
            self.hire_result = f"服务端拒绝 ({reason})"
            return

        if test_amount < 1:
            print_log(f"{self.log_prefix} 雇佣步长收敛到 0，跳过本轮投递")
            self.hire_result = "单次限额低于1"
            return

        # 阶段 2: 循环批次提交，彻底清空金币
        total_hired = test_amount
        stalled_counter = 0

        max_loops = min(coins // test_amount + 2, MAX_HIRE_ROUNDS)
        for _ in range(max_loops):
            try:
                _, current_coins, _, fh = self.query_wallet_assets()
            except Exception:
                break

            current_int = int(current_coins or 0)
            if current_int <= 0 or not fh:
                break

            batch = min(current_int, test_amount)
            try:
                resp = self._hire_request(batch, fh)
            except Exception:
                break

            if not ("showright(" in resp and "showerror(" not in resp):
                break

            total_hired += batch

            # 校验金币是否有实际扣除，连续无扣除则熔断防止死循环
            after_coins = self.query_wallet_assets()[1]
            if after_coins is not None and int(after_coins or 0) >= current_int:
                stalled_counter += 1
                if stalled_counter >= 2:
                    print_log(f"{self.log_prefix} 检测到金币余额未扣除，终止多余重试")
                    break
            else:
                stalled_counter = 0
            
            time.sleep(1)

        final_remain = self.query_wallet_assets()[1]
        self.hire_result = f"+{total_hired} 个矿工 (消耗 {total_hired} 金币)"
        print_log(f"{self.log_prefix} 雇佣流程完成: 成功雇佣 {total_hired} 个矿工，账户剩余金币: {final_remain}")

    def execute_all(self):
        """执行单一账号的全部流程并构建汇报卡片"""
        print_log(f"========== 账号 [{self.tag}] 开始执行 ==========")
        
        # 1. 登录验证
        if not self.login():
            card = (
                f"👤 账号备注: {self.tag}\n"
                f"📱 绑定手机: {desensitize_phone(self.phone)}\n"
                f"❌ 运行状态: 登录失败，请检查账号密码"
            )
            print_log(f"========== 账号 [{self.tag}] 任务中断 ==========\n")
            return card

        # 2. 依次调度任务链路
        self.fetch_home_overview()
        self.sign_in()
        self.claim_earnings()
        self.hire_workers_auto()
        self.query_wallet_assets()  # 再次刷新获取最新资产
        
        print_log(f"========== 账号 [{self.tag}] 执行完毕 ==========\n")
        
        card = (
            f"👤 账号备注: {self.tag}\n"
            f"📱 绑定手机: {desensitize_phone(self.phone)}\n"
            f"📝 签到状态: {self.status_sign}\n"
            f"🎁 领取收益: {self.status_claim}\n"
            f"🌾 今日产量: {self.daily_production}\n"
            f"⛏️ 本轮雇佣: {self.hire_result}\n"
            f"💰 账户资产: 矿工 {self.workers} 个 | 金币 {self.coins} | 钻石 {self.diamonds}"
        )
        return card

def load_accounts_from_env():
    """解析 zskc_gpt 环境变量"""
    raw_env = os.getenv("zskc_gpt", "").replace("\r", "\n").strip()
    if not raw_env:
        return []

    account_list = []
    # 支持换行与 & 分隔多账号
    for item in re.split(r"[\n&]+", raw_env):
        item = item.strip()
        if not item or "#" not in item:
            continue
        parts = [p.strip() for p in item.split("#") if p.strip()]
        if len(parts) == 2:
            acc, pwd = parts
            tag = desensitize_phone(acc)
            account_list.append((tag, acc, pwd))
        elif len(parts) >= 3:
            tag, acc, pwd = parts[0], parts[1], parts[2]
            account_list.append((tag, acc, pwd))
            
    return account_list

def main():
    accounts = load_accounts_from_env()
    if not accounts:
        print_log("[!] 未检测到环境变量 zskc_gpt，请在青龙面板中添加环境变量:")
        print_log("    变量名: zskc_gpt")
        print_log("    变量值: 备注#账号#密码  (或 账号#密码，多个账号回车换行或使用 & 分隔)")
        return

    print_log(f"[*] 成功识别到 {len(accounts)} 个钻石矿场账号配置，开始批量执行...")
    report_cards = []

    for index, (tag, phone, pwd) in enumerate(accounts, start=1):
        if index > 1:
            time.sleep(3)
        try:
            worker = DiamondMiner(tag, phone, pwd)
            card = worker.execute_all()
            report_cards.append(card)
        except Exception as err:
            print_log(f"[!] 账号 [{tag}] 发生未捕获的运行异常: {err}")
            report_cards.append(
                f"👤 账号备注: {tag}\n"
                f"📱 绑定手机: {desensitize_phone(phone)}\n"
                f"❌ 运行异常: {err}"
            )

    # 聚合汇总并推送
    split_line = "\n" + "-" * 28 + "\n"
    final_content = split_line.join(report_cards)
    if report_cards:
        send("💎钻石矿场信息💎", final_content)

if __name__ == "__main__":
    main()
