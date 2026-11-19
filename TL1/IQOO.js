/*
 * ==============================================
 * vx小程序 iQOO 社区 签到＋日常任务
 *
 * 【环境变量】
 * 变量名：iqoo_gpt
 * 格式：备注#accessToken
 * 示例：糖果块儿#eyJ0eXAiOiJKV1Qi...
 * 多账号：& 或换行分隔
 *
 * 可选变量
 * IQOO_Create：自动发帖开关，true 开启，默认 false
 * TEXTURL：随机文案接口，用于自动发帖内容
 *
 * 【定时】
 *  cron 25 7,16 * * * 时间自行修改
 *   ==============================================
 */
let $;
try {
    const { Env } = require("../tools/env.js");
    $ = new Env("iqoo社区");
} catch (e) {
    // tools/env.js 不存在时的精简降级实现
    $ = {
        userIdx: 0,
        userList: [],
        log: (...args) => console.log(...args),
        wait: (ms) => new Promise((r) => setTimeout(r, ms)),
        checkEnv(ckName) {
            const val = String(process.env[ckName] || "").trim();
            if (!val) {
                console.log(`缺少环境变量 ${ckName}，退出`);
                process.exit(0);
            }
            this.userList = val.split(/\n|&/).map((s) => s.trim()).filter(Boolean);
        },
        done() {
            console.log("全部任务执行完毕");
            process.exit(0);
        },
    };
}
const axios = require("axios");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const ckName = "iqoo_gpt";
const MINI_APP_ID = "wxcf4266fbc9463132";
const PAGE_VERSION = "263"; // 抓包实抓 referer 版本
const X_VERSION = "80800"; // 抓包实抓 x-version
const API_BASE = "https://bbs-api.iqoo.com/api/";
const TOKEN_CACHE_FILE = path.join(__dirname, "iqoo_token_cache.json");
const SIGN_SECRET = "2618194b0ebb620055e19cf9811d3c13"; // 已用抓包基准验证
const USER_AGENT =
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf254101e) XWEB/18151";

// ===== 风控延时配置（毫秒，区间随机），请勿调小 =====
const DELAY_REQ = [3000, 6000]; // 每次API请求前（同一帖子连做多动作，放慢防风控）
const DELAY_ACTION = [4000, 8000]; // 切换帖子/任务动作之间
const DELAY_ACCOUNT = [5000, 8000]; // 账号之间
const CACHE_VERIFY_SKIP_HOURS = 6; // 缓存新鲜期内跳过token校验请求
const TOKEN_SAFE_HOURS = 24; // token临期阈值，提前一天判定为临期

// 任务配置
const IQOO_Create = process.env.IQOO_Create === "true";
const TEXTURL = process.env.TEXTURL || "https://api.btstu.cn/yan/api.php";

// 可自动完成的任务白名单
const AUTO_TASKS = {
    newbie: ["首次发表评论", "认证机型"],
    daily: ["发表帖子", "发表评论", "浏览帖子", "点赞帖子", "分享帖子"],
};

// 评论内容池（随机取用，避免雷同文案）
const COMMENT_POOL = [
    "看起来不错", "支持一下", "不错不错", "学到了", "赞一个",
    "路过支持", "这个可以", "mark一下", "很有参考价值", "感谢分享",
    "真不错", "好耶", "学习了", "收藏了", "厉害了",
];

function readTokenCache() {
    try {
        if (!fs.existsSync(TOKEN_CACHE_FILE)) return {};
        return JSON.parse(fs.readFileSync(TOKEN_CACHE_FILE, "utf8")) || {};
    } catch (e) {
        return {};
    }
}
function writeTokenCache(cache) {
    try {
        fs.writeFileSync(TOKEN_CACHE_FILE, JSON.stringify(cache, null, 2), "utf8");
    } catch (e) {
        $.log(`写入token缓存失败: ${e.message || e}`);
    }
}
function shortToken(token = "") {
    const value = String(token || "").replace(/^Bearer\s+/i, "");
    return value ? `${value.slice(0, 6)}***${value.slice(-6)}` : "";
}
function isSuccess(result) {
    return Number(result?.Code) === 0;
}
function isTokenError(message) {
    return /-4011|401|403|token|登录|授权|请先登录|跳转到登录页|访客没有权限/i.test(String(message || ""));
}
function maskName(value = "") {
    const text = String(value || "");
    return text.length > 10 ? `${text.slice(0, 4)}***${text.slice(-3)}` : text;
}
function rand(min, max) {
    return Math.floor(min + Math.random() * (max - min));
}
function sleepRange(range) {
    return $.wait(rand(range[0], range[1]));
}
// 解析 JWT payload 里的过期时间（秒级时间戳），解析失败返回 0
// 从 JWT payload 里取 sub（即 userId）
function decodeJwtSub(token = "") {
    try {
        const payload = String(token).replace(/^Bearer\s+/i, "").split(".")[1];
        const json = JSON.parse(Buffer.from(payload, "base64").toString("utf8"));
        return String(json.sub || json.userId || json.uid || "");
    } catch (e) {
        return "";
    }
}
function decodeJwtExp(token = "") {
    try {
        const payload = String(token).replace(/^Bearer\s+/i, "").split(".")[1];
        const json = JSON.parse(Buffer.from(payload, "base64").toString("utf8"));
        return Number(json.exp) || 0;
    } catch (e) {
        return 0;
    }
}
function pickComment() {
    return COMMENT_POOL[rand(0, COMMENT_POOL.length)];
}

// ==================== 推送 ====================
const NOTIFY_TITLE = "iQOO社区 🌟";
const { execSync } = require("child_process");

function notifyPython(title, content, timeout = 60) {
    // 把 python 代码写到临时文件，避免 shell 转义问题
    const os = require("os");
    const pyPath = require("path").join(os.tmpdir(), "iqoo_notify.py");
    const pyCode = [
        "import sys, os, json",
        "title = sys.argv[1]",
        "content = sys.argv[2]",
        "def _try():",
        "    try:",
        "        from notify import send",
        "        send(title, content)",
        "        return True",
        "    except ImportError:",
        "        pass",
        "    except Exception as e:",
        "        sys.stderr.write('import-err:%s\\n' % e)",
        "    for p in ('/ql/scripts/notify.py', '/ql/data/scripts/notify.py'):",
        "        if not os.path.exists(p):",
        "            continue",
        "        try:",
        "            import importlib.util",
        "            sp = importlib.util.spec_from_file_location('qlnotify', p)",
        "            m = importlib.util.module_from_spec(sp)",
        "            sp.loader.exec_module(m)",
        "            m.send(title, content)",
        "            return True",
        "        except Exception as e:",
        "            sys.stderr.write('path-err(%s):%s\\n' % (p, e))",
        "    return False",
        "sys.exit(0 if _try() else 2)",
    ].join("\n");

    try {
        require("fs").writeFileSync(pyPath, pyCode, "utf8");
    } catch (e) {
        $.log(`⚠️ 写临时脚本失败: ${e.message}`);
        return false;
    }

    try {
        execSync(`python3 ${JSON.stringify(pyPath)} ${JSON.stringify(title)} ${JSON.stringify(content)}`, {
            timeout: timeout * 1000,
            stdio: ["ignore", "pipe", "pipe"],
        });
        $.log("✅ 推送已发送");
        return true;
    } catch (e) {
        if (e.killed || e.signal === "SIGTERM") {
            $.log(`⚠️ 推送超时（${timeout}s），已放弃`);
        } else {
            const err = String(e.stderr || e.message || e).replace(/\n/g, " ").slice(0, 140);
            $.log(`⚠️ 推送未发送: ${err}`);
        }
        return false;
    }
}

function pad(s, w) {
    s = String(s);
    let len = 0;
    for (const ch of s) len += ch.charCodeAt(0) > 127 ? 2 : 1;
    return s + " ".repeat(Math.max(0, w - len));
}

function buildReport(results) {
    if (!results.length) return "";
    const lines = ["【iQOO 社区】"];
    let total = 0;
    for (const r of results) {
        if (r.error) {
            lines.push(`❌  ${pad(r.name, 12)} ${String(r.error).slice(0, 20)}`);
            continue;
        }
        const mark = r.sign === "ok" ? "✅" : r.sign === "already" ? "·" : "❌";
        let line = `${mark}  ${pad(r.name, 12)}`;
        if (r.endScore !== null && r.endScore !== undefined) {
            line += `酷币 ${r.endScore}`;
            const gain = (r.endScore || 0) - (r.startScore || 0);
            if (gain > 0) {
                line += `  (+${gain})`;
                total += gain;
            }
        } else {
            line += "酷币 —";
        }
        lines.push(line);
    }
    lines.push("");
    lines.push(total > 0 ? `📊 今日 +${total} 酷币` : "📊 今日无新增");
    return lines.join("\n");
}

class Task {
    constructor(env) {
        this.index = ++$.userIdx; // 账号编号从1开始
        // 环境变量格式：备注#accessToken（也可 备注#openid#accessToken，openid 仅用于日志显示）
        const raw = String(env || "").trim();
        const parts = raw.split("#").map((x) => x.trim());
        // token 一定以 eyJ 开头（JWT），据此定位
        const tokIdx = parts.findIndex((x) => /^eyJ/i.test(x.replace(/^Bearer\s+/i, "")));
        let remark = "";
        let openid = "";
        let accessToken = "";
        if (tokIdx >= 0) {
            remark = parts.slice(0, tokIdx).filter((x) => !/^o[A-Za-z0-9_-]{15,}$/.test(x)).join("#");
            if (tokIdx >= 2) openid = parts[tokIdx - 1];
            accessToken = parts.slice(tokIdx).join("#");
        } else if (parts.length >= 2) {
            remark = parts[0];
            accessToken = parts.slice(1).join("#");
        } else {
            accessToken = parts[0];
        }
        this.openid = openid || "";
        this.remark = remark || "";
        this.configToken = (accessToken || "").replace(/^Bearer\s+/i, "");
        this.tag = this.remark || (this.openid ? `openid:${this.openid.slice(0, 8)}***` : "未命名");
        this.token = "";
        this.refreshToken = "";
        this.userId = "";
        this.userInfo = {};
        this.threads = []; // [{threadId, postId}]
        this.scoreRule = {};
        this.todayProgress = null;
        this.startScore = 0;
        this.endScore = 0;
        this.sign = "-"; // ok / already / fail
        // visitor 是设备标识，与 openid 无必然关系（抓包已证）
        // 有 openid 用它算，没有就基于 token 生成一次稳定值
        const seed = this.openid || this.configToken || String(Date.now());
        this.visitor = crypto.createHash("md5").update(seed).digest("hex");
    }

    async run() {
        $.log(`\n══════════ 账号${this.index}[${this.tag}] 开始 ══════════`);
        if (!this.configToken) {
            $.log(`❌ 变量缺少 accessToken（格式：备注#token）`);
            return;
        }
        this.token = this.configToken;
        this.exp = decodeJwtExp(this.token);
        // userId 从 JWT 的 sub 字段取（抓包验证过）
        if (!this.userId) {
            this.userId = decodeJwtSub(this.token);
            if (this.userId) $.log(`userId: ${this.userId}（来自 token）`);
        }
        if (this.exp) {
            const left = Math.floor((this.exp * 1000 - Date.now()) / 86400000);
            if (left < 0) {
                $.log(`❌ accessToken 已过期（${new Date(this.exp * 1000).toLocaleDateString("zh-CN")}），请更新变量`);
                return;
            }
            $.log(`token ${shortToken(this.token)} ｜ 剩余 ${left} 天`);
        }

        await this.keepAlive();
        await this.userInfoRequest();
        this.startScore = this.getScore();
        await this.getTreadList();
        await this.getScoreRule();
        await this.getTodayProgress();
        await this.signIn();
        await this.getDrawNum();
        await this.doNewbieTasks();
        await this.doDailyTasks();
        await sleepRange(DELAY_ACTION);
        await this.userInfoRequest();
        this.endScore = this.getScore();
        this.printScoreSummary();
    }

    // ==================== 缓存管理 ====================
    isTokenFresh(cached) {
        // 条件：JWT 未临期 且 缓存记录 6 小时内使用过 → 完全跳过网络校验
        const now = Math.floor(Date.now() / 1000);
        const exp = this.exp || 0;
        const updatedAt = new Date(cached.updatedAt || 0).getTime() / 1000 || 0;
        return exp - now > TOKEN_SAFE_HOURS * 3600 && now - updatedAt < CACHE_VERIFY_SKIP_HOURS * 3600;
    }
    getCachedToken() {
        const cache = readTokenCache();
        return cache[this.openid] || null;
    }
    saveCachedToken() {
        // 本版不使用缓存文件，token 由变量提供
        return;
        /* eslint-disable no-unreachable */
        if (!this.token) return;
        const cache = readTokenCache();
        cache[this.openid] = {
            remark: this.remark,
            accessToken: this.token,
            refreshToken: this.refreshToken,
            userId: this.userId,
            visitor: this.visitor,
            nickname: this.userInfo?.nickname || this.userInfo?.username || "",
            exp: this.exp || decodeJwtExp(this.token),
            updatedAt: new Date().toISOString(),
        };
        writeTokenCache(cache);
    }
    removeCachedToken() {
        const cache = readTokenCache();
        if (cache[this.openid]) {
            delete cache[this.openid];
            writeTokenCache(cache);
        }
        this.token = "";
        this.refreshToken = "";
        this.userId = "";
        this.exp = 0;
        this.userInfo = {};
    }
    applyToken(data = {}) {
        this.token = data.accessToken || data.token || "";
        this.refreshToken = data.refreshToken || "";
        this.userId = data.userId || data.uid || data.userInfo?.userId || data.userInfo?.uid || "";
        this.userInfo = data.userInfo || data.user || {};
        this.visitor = data.visitor || this.visitor;
        this.exp = decodeJwtExp(this.token);
    }

    // ==================== 签名与请求 ====================
    // GET参数按字典序排序，保证签名一致（已用抓包基准验证）
    queryString(data = {}) {
        const list = [];
        const sortedKeys = Object.keys(data || {}).sort();
        for (const key of sortedKeys) {
            const value = data[key];
            if (value === undefined || value === null) continue;
            list.push(`${encodeURIComponent(key)}=${encodeURIComponent(value)}`);
        }
        return list.join("&");
    }
    getSign(method, apiPath, data = {}) {
        const upperMethod = method.toUpperCase();
        const timestamp = Math.floor(Date.now() / 1000);
        const query = upperMethod === "GET" ? this.queryString(data) : "";
        const body = upperMethod === "GET" ? "" : JSON.stringify(data || {});
        const raw = `${upperMethod}&/api/${apiPath}&${query}&${body}&appid=1002&timestamp=${timestamp}`;
        const signature = crypto.createHmac("sha256", SIGN_SECRET).update(raw).digest("base64");
        return `IQOO-HMAC-SHA256 appid=1002,timestamp=${timestamp},signature=${signature}`;
    }
    headers(method, apiPath, data, extra = {}) {
        return {
            Authorization: this.token ? `Bearer ${this.token}` : "Bearer",
            "X-Visitor": this.visitor,
            "X-Platform": "mini",
            "X-Version": X_VERSION,
            SIGN: this.getSign(method, apiPath, data),
            "Content-Nonce": "",
            "Xweb_Xhr": "1",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            Referer: `https://servicewechat.com/${MINI_APP_ID}/${PAGE_VERSION}/page-frame.html`,
            ...extra,
        };
    }
    async request(apiPath, data = {}, options = {}) {
        const method = (options.method || "POST").toUpperCase();
        // 风控：每次请求前随机延时
        await sleepRange(DELAY_REQ);
        const req = {
            method,
            url: new URL(apiPath, API_BASE).toString(),
            headers: this.headers(method, apiPath, data, options.headers || {}),
            timeout: 20000,
            validateStatus: () => true,
        };
        if (method === "GET") req.params = data;
        else req.data = data || {};
        const { data: result, status } = await axios.request(req);
        if (status !== 200) throw new Error(`HTTP ${status}: ${JSON.stringify(result)}`);
        if (!isSuccess(result)) throw new Error(`${result?.Code ?? ""} ${result?.Message || JSON.stringify(result)}`.trim());
        return result;
    }

    // ==================== 登录 ====================
    // 本版不依赖任何外部登录服务：缓存失效时直接提示手工抓包
    async loginByWxCode() {
        $.log("缓存已失效，需要手动更新 accessToken（脚本不再自动登录）");
        $.log("获取方法：");
        $.log("  1) 手机打开「iQOO社区」小程序，抓包一次");
        $.log("  2) 找 POST https://bbs-api.iqoo.com/api/v3/users/vivo/mini 的响应");
        $.log("  3) 复制 Data.accessToken，写入缓存文件 iqoo_token_cache.json");
        $.log("     格式：{ \"<openid>\": { \"accessToken\": \"eyJ...\", \"userId\": \"\", \"visitor\": \"\" } }");
        $.log("  4) 或直接删掉缓存文件，重新运行并在提示时按说明填");
        this.loginFailed = true;
    }

    async checkToken() {
        if (!this.token) {
            return false;
        }
        if (!this.userId) {
            // userId 未提供时，从任意一次用户接口自动推断（v3/user 不带参数）
            try {
                const r = await this.request("v3/user", {}, { method: "GET" });
                const d = r?.Data || {};
                this.userId = d.id || d.userId || d.uid || "";
                this.userInfo = d;
                if (this.userId) {
                    $.log(`自动获取 userId: ${this.userId}`);
                    return true;
                }
            } catch (e) {
                $.log(`自动获取 userId 失败: ${String(e.message || e).slice(0, 60)}`);
                return false;
            }
        }
        for (let retry = 0; retry < 2; retry++) {
            try {
                await this.request("v3/user", { userId: this.userId }, { method: "GET" });
                // 校验通过顺手刷新缓存时间戳
                this.saveCachedToken();
                return true;
            } catch (e) {
                const message = String(e.message || e);
                if (isTokenError(message)) break;
                if (retry < 1) {
                    $.log(`校验令牌网络异常，重试中`);
                    await $.wait(1000);
                }
            }
        }
        $.log(`缓存令牌失效，尝试免费续期`);
        const refreshSuccess = await this.refreshAccessToken();
        if (refreshSuccess) {
            try {
                await this.request("v3/user", { userId: this.userId }, { method: "GET" });
                return true;
            } catch (e) {
                $.log(`续期后校验仍失败`);
            }
        }
        return false;
    }
    // 抓包确认 refreshToken 为空串，此接口仅作兜底
    async refreshAccessToken() {
        if (!this.refreshToken) return false;
        try {
            const result = await this.request("v3/users/token/refresh", { refreshToken: this.refreshToken }, { headers: { Authorization: "Bearer" } });
            const data = result?.Data || {};
            if (!data.accessToken) throw new Error("刷新未返回新令牌");
            this.token = data.accessToken;
            this.refreshToken = data.refreshToken || this.refreshToken;
            this.exp = decodeJwtExp(this.token);
            this.saveCachedToken();
            $.log(`Token 刷新成功: ${shortToken(this.token)}`);
            return true;
        } catch (e) {
            $.log(`Token 刷新失败: ${e.message || e}`);
            return false;
        }
    }

    // ==================== 信息查询 ====================
    getScore() {
        const score = this.userInfo?.score ?? this.userInfo?.points ?? this.userInfo?.coolCoin;
        return isNaN(Number(score)) ? 0 : Number(score);
    }
    printScoreSummary() {
        const gain = this.endScore - this.startScore;
        const gainText = gain >= 0 ? `+${gain}` : `${gain}`;
        $.log(`══════════ 账号${this.index}[${this.tag}] 完成 ══════════`);
        $.log(`今日获得酷币: ${gainText} | 当前总酷币: ${this.endScore}`);
    }
    async userInfoRequest() {
        if (!this.token || !this.userId) {
            if (!this.userId) $.log(`⚠️ 缺少 userId，跳过用户信息查询`);
            return;
        }
        try {
            const result = await this.request("v3/user", { userId: this.userId }, { method: "GET" });
            this.userInfo = result?.Data || this.userInfo;
            if (!this.userId) {
                const d = this.userInfo || {};
                this.userId = d.id || d.userId || d.uid || "";
                if (this.userId) $.log(`自动获取 userId: ${this.userId}`);
            }
            const name = this.userInfo?.nickname || this.userInfo?.username || this.userId;
            const score = this.userInfo?.score ?? this.userInfo?.points ?? this.userInfo?.coolCoin ?? "未知";
            $.log(`用户:${maskName(name)} 酷币:${score}`);
        } catch (e) {
            const message = String(e.message || e);
            $.log(`获取用户信息失败:${message}`);
            if (isTokenError(message)) this.removeCachedToken();
        }
    }
    // 在线心跳（抓包中真实客户端每次启动都会调用）
    async keepAlive() {
        try {
            await this.request("v5/users/keep-alive", {});
        } catch (e) {
            // 心跳失败不影响任务
        }
    }
    // 抓包确认：任务规则接口已迁移到 v5/users/tasks，老接口作兜底
    async getScoreRule() {
        try {
            const result = await this.request("v5/users/tasks", {}, { method: "GET" });
            this.scoreRule = result?.Data || {};
        } catch (e) {
            try {
                const result = await this.request("v3/user.score.rule", {}, { method: "GET" });
                this.scoreRule = result?.Data || {};
            } catch (e2) {
                $.log(`获取任务规则失败:${e2.message || e2}`);
            }
        }
    }
    // 抓包新增：今日任务进度（含各任务完成数与上限，可精确计算剩余次数，避免多发请求）
    async getTodayProgress() {
        try {
            const result = await this.request("v5/users/tasks/today-progress", {}, { method: "GET" });
            this.todayProgress = result?.Data || null;
        } catch (e) {
            this.todayProgress = null;
        }
    }

    // ==================== 帖子数据 ====================
    async getTreadList() {
        try {
            const result = await this.request("v3/thread.list", {
                scope: 5,
                page: 1,
                perPage: 20, // 与抓包一致，多拿帖子供点赞/浏览/分享轮换
                "filter[sort]": 4,
                "filter[essence]": 1,
                sequence: 0,
            }, { method: "GET" });
            const list = result?.Data?.pageData || [];
            if (!list.length) throw new Error("帖子列表为空");
            this.threads = list
                .filter((t) => t.threadId && t.postId)
                .map((t) => ({ threadId: t.threadId, postId: t.postId }));
            $.log(`获取帖子列表成功，共${this.threads.length}篇可用`);
        } catch (e) {
            $.log(`获取帖子列表失败: ${e.message || e}`);
            $.log(`帖子相关任务将跳过`);
        }
    }

    // ==================== 任务执行 ====================
    async doNewbieTasks() {
        const newbieData = this.scoreRule?.newbieData || [];
        if (!newbieData.length) return;
        for (const task of newbieData) {
            const taskName = task.name;
            if (!AUTO_TASKS.newbie.includes(taskName)) continue;
            const [done, upper] = String(task.progress || "0/1").split("/").map((n) => parseInt(n) || 0);
            const remain = upper - done;
            $.log(`新手任务【${taskName}】进度: ${done}/${upper}`);
            if (remain <= 0) continue;
            if (taskName === "首次发表评论") {
                if (this.threads.length) {
                    await this.commonPost(this.threads[0].threadId);
                    await sleepRange(DELAY_ACTION);
                }
            } else if (taskName === "认证机型") {
                await this.setPhoneModel();
                await sleepRange(DELAY_ACTION);
            }
        }
    }
    async doDailyTasks() {
        const perDayData = this.scoreRule?.perDayData || [];
        if (!perDayData.length) {
            $.log(`未获取到每日任务列表`);
            return;
        }
        const progress = this.todayProgress || {};
        // 计算各任务剩余次数（today-progress 优先，抓包确认字段，更精确）
        const remainOf = (name) => {
            const task = perDayData.find((t) => t.access === name);
            if (!task) return 0;
            const upper = parseInt(task.upper_limit) || 0;
            const done = parseInt(task.isFinal) || 0;
            let remain = upper - done;
            if (name === "浏览帖子" && progress.viewUpperLimit) {
                remain = Math.max(0, progress.viewUpperLimit - (progress.viewCount || 0));
            } else if (name === "点赞帖子" && progress.likeUpperLimit) {
                remain = Math.max(0, progress.likeUpperLimit - (progress.likeCount || 0));
            } else if (name === "分享帖子" && progress.shareUpperLimit) {
                remain = Math.max(0, progress.shareUpperLimit - (progress.shareCount || 0));
            } else if (name === "发表评论" && progress.createPostUpperLimit) {
                remain = Math.max(0, progress.createPostUpperLimit - (progress.postCount || 0));
            }
            return Math.max(0, remain);
        };
        const needView = remainOf("浏览帖子");
        const needLike = remainOf("点赞帖子");
        const needShare = remainOf("分享帖子");
        const needComment = remainOf("发表评论");
        const needCreate = remainOf("发表帖子");
        $.log(`任务需求: 浏览${needView} 点赞${needLike} 分享${needShare} 评论${needComment} 发帖${needCreate}`);

        // 自动发帖（独立任务，先处理）
        if (needCreate > 0) {
            if (IQOO_Create) {
                for (let i = 0; i < needCreate; i++) {
                    const text = await this.textGet();
                    await this.createThread("日常分享", text);
                    await sleepRange(DELAY_ACTION);
                }
            } else {
                $.log(`未开启自动发帖，跳过`);
            }
        }

        // 模拟真实用户刷帖：按列表顺序逐帖浏览，浏览时顺手对该帖点赞/分享/评论
        // （真实用户看完一帖就会点赞收藏分享，而不是分开各刷各的）
        let v = 0, l = 0, s = 0, c = 0;
        for (const t of this.threads) {
            if (v >= needView && l >= needLike && s >= needShare && c >= needComment) break;
            await this.viewPost(t.threadId);
            v++;
            await sleepRange(DELAY_REQ);
            if (l < needLike) {
                await this.likePost(t.threadId, t.postId);
                l++;
                await sleepRange(DELAY_REQ);
            }
            if (s < needShare) {
                await this.sharePost(t.threadId);
                s++;
                await sleepRange(DELAY_REQ);
            }
            if (c < needComment) {
                await this.commonPost(t.threadId);
                c++;
                await sleepRange(DELAY_REQ);
            }
            await sleepRange(DELAY_ACTION);
        }
        // 配额未满足的兜底统计
        if (l < needLike || s < needShare || c < needComment) {
            $.log(`帖子不够用: 点赞${l}/${needLike} 分享${s}/${needShare} 评论${c}/${needComment}`);
        }
    }

    // ==================== 具体动作 ====================
    async setPhoneModel() {
        try {
            await this.request("v3/set/phone.model", {
                model: "V2218A",
                brand: "vivo",
                product: "iQOO 10 Pro",
                guideType: 1,
            });
            $.log(`机型认证成功`);
        } catch (e) {
            $.log(`机型认证失败:${e.message || e}`);
        }
    }
    async textGet() {
        try {
            const { data } = await axios.get(TEXTURL, { timeout: 10000 });
            return data?.trim() || "生活不止眼前的苟且，还有读不懂的诗和到不了的远方。";
        } catch (e) {
            return "生活不止眼前的苟且，还有读不懂的诗和到不了的远方。";
        }
    }
    async createThread(title, content) {
        try {
            const body = {
                title: title || "日常分享",
                categoryId: 27,
                content: { text: `<p>${content}</p>` },
                position: {},
                price: 0,
                freeWords: 0,
                attachmentPrice: 0,
                draft: 0,
                anonymous: 0,
                topicId: "",
                source: "",
                videoId: "",
            };
            await this.request("v3/thread.create", body);
            $.log(`发表帖子成功`);
        } catch (e) {
            $.log(`发表帖子失败:${e.message || e}`);
        }
    }
    async getDrawNum() {
        try {
            const result = await this.request("v3/today.draw.count", {}, { method: "GET" });
            if (Number(result?.Data?.count) === 0) {
                await this.draw();
            } else {
                $.log(`今日已抽奖`);
            }
        } catch (e) {
            $.log(`查询抽奖次数失败:${e.message || e}`);
        }
    }
    async draw() {
        try {
            const result = await this.request("v3/luck.draw", {});
            $.log(`抽奖成功 获得${result?.Data?.prize_name || "奖励"}`);
        } catch (e) {
            $.log(`抽奖失败:${e.message || e}`);
        }
    }
    // 抓包确认请求体为 {"from":""}
    async signIn() {
        if (this.todayProgress?.isSignedIn) {
            $.log(`今日已签到(today-progress确认)`);
            this.sign = "already";
            return;
        }
        try {
            const result = await this.request("v3/sign", { from: "" });
            this.sign = "ok";
            $.log(`签到成功: 已签${result?.Data?.serialDays}天 +${result?.Data?.score}酷币 当前${result?.Data?.scoreCount}`);
        } catch (e) {
            const message = String(e.message || e);
            if (/已签|重复|今日|13006/.test(message)) {
                $.log(`今日已签到`);
                this.sign = "already";
                return;
            }
            $.log(`签到失败:${message}`);
            if (isTokenError(message)) this.removeCachedToken();
        }
    }
    // 与抓包一致：仅点赞，likeOnly=false 时才会取消（补次数用）
    async likePost(threadId, postId, likeOnly = true) {
        try {
            await this.request("v3/posts.update", { id: threadId, postId, data: { attributes: { isLiked: true } } });
            $.log(`帖子点赞成功 threadId=${threadId}`);
        } catch (e) {
            const message = String(e.message || e);
            if (/已赞|重复/.test(message)) $.log(`帖子已点赞过，跳过`);
            else $.log(`帖子点赞失败:${message}`);
            return;
        }
        if (!likeOnly) {
            await sleepRange(DELAY_REQ);
            try {
                await this.request("v3/posts.update", { id: threadId, postId, data: { attributes: { isLiked: false } } });
                $.log(`帖子取消点赞成功`);
            } catch (e) {
                $.log(`帖子取消点赞失败:${e.message || e}`);
            }
        }
    }
    // 抓包确认请求体含 utm_source:"Mini"，threadId 为字符串
    async sharePost(threadId) {
        try {
            await this.request("v3/thread.share", { threadId: String(threadId), utm_source: "Mini" });
            $.log(`帖子分享成功 threadId=${threadId}`);
        } catch (e) {
            $.log(`帖子分享失败:${e.message || e}`);
        }
    }
    async viewPost(threadId) {
        try {
            await this.request("v3/view.count", { threadId, type: 0 }, { method: "GET" });
            $.log(`帖子浏览成功 threadId=${threadId}`);
        } catch (e) {
            $.log(`帖子浏览失败:${e.message || e}`);
        }
    }
    async commonPost(threadId) {
        try {
            const content = pickComment();
            await this.request("v3/posts.create", { id: threadId, type: 0, content, source: "", attachments: [] });
            $.log(`评论成功 threadId=${threadId} 内容:${content}`);
        } catch (e) {
            $.log(`评论失败:${e.message || e}`);
        }
    }
}

!(async () => {
    $.checkEnv(ckName);
    $.log(`iQOO社区共获取到${$.userList.length}个账号`);
    const results = [];
    let first = true;
    for (const user of $.userList) {
        if (!first) await sleepRange(DELAY_ACCOUNT); // 多账号错峰
        first = false;
        let task = null;
        try {
            task = new Task(user);
            await task.run();
            results.push({
                name: task.tag,
                sign: task.sign,
                startScore: task.startScore,
                endScore: task.endScore,
            });
        } catch (e) {
            $.log(`账号执行异常: ${e.message || e}`);
            results.push({
                name: task ? task.tag : "账号",
                error: String(e.message || e),
            });
        }
    }

    const text = buildReport(results);
    console.log("\n" + "=".repeat(50));
    console.log(text);
    console.log("=".repeat(50));
    if (!process.argv.includes("--no-push")) notifyPython(NOTIFY_TITLE, text);
})()
    .catch((e) => console.log(e))
    .finally(() => $.done());
