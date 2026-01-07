# cron: 16 8 * * *
# 心喜任务 · 最终整合增强版
# 功能说明：
# - 多账号支持，格式： xx_gpt="备注1@Sso1#备注2@Sso2"
# - 自动签到（含签到天数 + 今日奖励）
# - 点赞 / 分享 / 浏览商城 / 会员权益 / 发帖 / 评论 / 想要 / 取消关注
# - 发帖内容来源：一言（已带 emoji）
# - 评论内容来源：一言（轻量文本）
# - 推送内容美化
# - 企业微信机器人自动适配


import requests
import json,os,sys,re
import time
from notify import send

msg = []

def pr(t):
    msg.append(t + "\n")
    print(t)


# ---------- 签到信息 ----------
def get_sign_info(sso):
    try:
        url = "https://api.xinc818.com/mini/sign/info"
        header = {"sso": sso, "user-agent": "Mozilla/5.0"}

        j = json.loads(requests.get(url, headers=header).text)
        if j["code"] != 0:
            return None, None, False

        day = j["data"]["continuousDay"]
        reward = j["data"]["integral"]
        flag = j["data"]["flag"]

        return day, reward, flag

    except:
        return None, None, False


# ---------- 签到 ----------
def xy_qiandao(sso):
    url = "https://api.xinc818.com/mini/sign/in?dailyTaskId"
    header = {"sso": sso, "user-agent": "Mozilla/5.0"}

    j = json.loads(requests.get(url, headers=header).text)
    if j["code"] == 0:
        pr("🎉 签到成功")
    else:
        pr("❌ 签到失败：" + j.get("msg", ""))


# ---------- 点赞 ----------
def xy_dzlist(sso):
    url = "https://api.xinc818.com/mini/community/home/posts?pageNum=1&pageSize=10&queryType=1&position=2"
    header = {"sso": sso, "user-agent": "Mozilla/5.0"}

    j = json.loads(requests.get(url, headers=header).text)
    if j["code"] != 0:
        return []

    lst = j["data"]["list"]
    return [lst[0]["id"], lst[1]["id"], lst[2]["id"]]


def xy_dz(sso):
    url = "https://api.xinc818.com/mini/posts/like"
    header = {"sso": sso, "user-agent": "Mozilla/5.0"}

    ids = xy_dzlist(sso)
    ok = []
    for pid in ids:
        data = {"postsId": pid, "decision": True}
        j = json.loads(requests.put(url, headers=header, json=data).text)
        if j["code"] == 0:
            ok.append(pid)
        time.sleep(2)

    pr(f"👍 点赞成功：{ok}")


# ---------- 浏览商城 ----------
def xy_sc_ll(sso):
    url = "https://api.xinc818.com/mini/dailyTask/browseGoods/22"
    header = {"sso": sso, "user-agent": "Mozilla/5.0"}

    j = json.loads(requests.get(url, headers=header).text)
    if j["code"] == 0:
        pr("🛒 浏览商城成功")
    else:
        pr("🛒 浏览失败：" + j.get("msg", ""))


# ---------- 会员权益 ----------
def xy_vip(sso):
    url = "https://api.xinc818.com/mini/dailyTask/benefits/2"
    header = {"sso": sso, "user-agent": "Mozilla/5.0"}

    j = json.loads(requests.get(url, headers=header).text)
    if j["code"] == 0:
        pr("👑 查看会员权益完成")
    else:
        pr("👑 失败：" + j.get("msg", ""))


# ---------- 分享 ----------
def xy_fenxiang(sso):
    url = "https://api.xinc818.com/mini/dailyTask/share"
    header = {"sso": sso, "user-agent": "Mozilla/5.0"}

    j = json.loads(requests.get(url, headers=header).text)
    if j["code"] == 0:
        pr("📤 分享成功")
    else:
        pr("📤 分享失败：" + j.get("msg", ""))


# ---------- 发帖（含 emoji） ----------
def xy_fatie(sso):
    try:
        text = requests.get("https://v1.hitokoto.cn/?encode=text").text.strip()
    except:
        text = "心情复杂，言不由衷。"

    content = f"🌿 今日随想：\n{text}"

    url = "https://api.xinc818.com/mini/posts"
    header = {"sso": sso, "user-agent": "Mozilla/5.0"}

    data = {
        "topicNames": ["心情树洞"],
        "content": content,
        "attachments": [],
        "voteType": 0,
        "commentType": "0",
        "sid": int(time.time() * 1000)
    }

    j = json.loads(requests.post(url, headers=header, json=data).text)
    if j["code"] == 0:
        pr("📝 发帖成功")
    else:
        pr("📝 发帖失败：" + j.get("msg", ""))


# ---------- 评论 ----------
def xy_pinglun(sso):
    url = "https://api.xinc818.com/mini/postsComments"
    header = {"sso": sso, "user-agent": "Mozilla/5.0"}

    ids = xy_dzlist(sso)
    ok = []

    for pid in ids:
        try:
            txt = requests.get("https://v1.hitokoto.cn/?encode=text").text.strip()
        except:
            txt = "人生如逆旅，我亦是行人。"

        data = {"postsId": pid, "content": f"💬 {txt}"}

        j = json.loads(requests.post(url, headers=header, json=data).text)
        if j["code"] == 0:
            ok.append(pid)

        time.sleep(2)

    pr(f"💬 评论成功：{ok}")


# ---------- 主流程 ----------
def index(remark, sso):
    try:
        pr(f"===== 开始执行：{remark} =====")

        # 登录
        url = "https://api.xinc818.com/mini/user"
        header = {"sso": sso, "user-agent": "Mozilla/5.0"}

        j = json.loads(requests.get(url, headers=header).text)
        if j["code"] != 0:
            pr("❌ 登录失败，Sso 可能已失效")
            return

        total = j["data"]["integral"]
        pr(f"登录成功：{remark} 当前积分：{total}")

        # 签到信息
        day, reward, flag = get_sign_info(sso)

        if not flag:
            xy_qiandao(sso)
            time.sleep(2)

        pr(f"📅 连续签到：{day} 天")
        pr(f"🎁 今日奖励：{reward} 积分")

        w = time.localtime().tm_wday

        # 点赞
        xy_dz(sso)
        time.sleep(2)

        # 分享
        if w == 2:
            xy_fenxiang(sso)
            time.sleep(2)
            xy_fenxiang(sso)

        # 会员权益
        xy_vip(sso)
        time.sleep(2)

        # 浏览商城
        if w == 2:
            xy_sc_ll(sso)
            time.sleep(4)
            xy_sc_ll(sso)

        # 发帖（周一、周四、周六）
        if w in [0, 3, 5]:
            xy_fatie(sso)

        # 评论（周一）
        if w == 0:
            xy_pinglun(sso)

        # 最终积分
        j = json.loads(requests.get(url, headers=header).text)
        now_total = j["data"]["integral"]

        pr(f"💰 当前总积分：{now_total}")
        pr("🎉 今日任务全部完成！")

    except Exception as e:
        pr("❌ 脚本执行错误：" + str(e))


# ---------- 入口 ----------
def sicxs():
    env = os.environ.get("xx_gpt")
    if not env:
        print("未设置变量 xx_gpt")
        return

    accounts = [i for i in env.split("#") if i.strip()]

    for acc in accounts:
        if "@" in acc:
            remark, sso = acc.split("@", 1)
        else:
            remark = "未备注账号"
            sso = acc

        index(remark, sso)

        send("心喜任务", "".join(msg))
        msg.clear()

    print("=== 所有账号执行完毕 ===")


if __name__ == "__main__":
    sicxs()
