/*
VX小程序 君品荟（原来的习酒花园） - 登录，签到，查询，酒谷之旅农场
      种高粱,收获，浇水，答题，每日任务
      保留了以前的ocr滑块验证，不知道是否需要
      OCR_SERVER=http://92.18.0.173:7777（IP自行更改）
cron: 45 8 * * * （每天四次差不多自己修改）

变量名：yyb_gpt
变量值：YYB服务地址:端口#id序号，多账号用 & 或换行分隔
  例: yyb_gpt=http://92.18.0.173:8000#1
  id序号 = YYB Go 账号列表里的账号 id(数字), 每段自带服务地址, 免token

默认酒兑换积分关闭；如果需要兑换；就设置JUNPINHUI_EXCHANGE=true

依赖变量：地块(可选)
依赖模块: axios, crypto-js
*/

// ====================== 替换掉缺失的 env.js 依赖 ======================
const $ = {
  name: "君品荟",
  userIdx: 1,
  userList: [],
  userCount: 0,
  remarkMap: {},
  log: (msg) => {
    const text = String(msg).replace(/账号\[(\d+)\]/g, (_, idx) => $.remarkMap[idx] || `账号[${idx}]`);
    console.log(text);
  },
  wait: (min, max) => new Promise(resolve => setTimeout(resolve, Math.floor(Math.random() * (max - min + 1) + min))),
  checkEnv: (ckName) => {
    let envVal = process.env[ckName];
    if (!envVal) {
      console.log(`❌ 未找到环境变量: ${ckName}，请先在青龙中添加变量。`);
      return;
    }
    // 支持按换行符、& 或 @ 分割多账号
    let splitted = envVal.split(/&|\n|@/).map(v => v.trim()).filter(v => v);
    $.userList = splitted;
    $.userCount = splitted.length;
    console.log(`✅ 共找到 ${$.userCount} 个账号配置`);
  },
  done: () => console.log("\n🎉 所有任务执行结束")
};
// =======================================================================

const axios = require("axios");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

let sendNotify = null;
for (const notifyPath of ["./sendNotify", "/ql/data/scripts/sendNotify", "/ql/scripts/sendNotify"]) {
  try {
    sendNotify = require(notifyPath).sendNotify;
    break;
  } catch {}
}
const notifyMsgs = [];

const ckName = "yyb_gpt";
const MINI_APP_ID = "wx8d41cdc44c8aeaab";
const APP_VERSION = "1.7";
const FM_BASE = "https://fm.exijiu.com";
const GARDEN_BASE = "https://apimallwm.exijiu.com";
const TOKEN_CACHE_FILE = path.join(__dirname, "yyb_gpt_token_cache.json");
const ENABLE_EXCHANGE = String(process.env.XiJiu_Exchange || process.env.JUNPINHUI_EXCHANGE || "").toLowerCase() === "true";

// === YYB Go 服务地址随账号串配置(ip:端口#id序号), 免token, 无需全局 wx_server_url/wx_auth ===

// ================= 新增滑块验证依赖全局配置 =================
const OCR_SERVER = process.env.OCR_SERVER || 'https://ddddocr.xzxxn7.live';

// 随机生成虚拟IP
const generateRandomIP = () => {
    const part1 = Math.floor(Math.random() * 255) + 1;
    const part2 = Math.floor(Math.random() * 255);
    const part3 = Math.floor(Math.random() * 255);
    const part4 = Math.floor(Math.random() * 255);
    return `${part1}.${part2}.${part3}.${part4}`;
};

// AES 加密函数
function aesEncrypt(e, n) {
    const cryptoJS = require('crypto-js');
    var t = cryptoJS.enc.Utf8.parse(n),
        i = cryptoJS.enc.Utf8.parse(JSON.stringify(e)),
        r = cryptoJS.AES.encrypt(i, t, {
            mode: cryptoJS.mode.ECB,
            padding: cryptoJS.pad.Pkcs7
        });
    return cryptoJS.enc.Base64.stringify(r.ciphertext);
}

// 滑块验证坐标请求
const slidePost = async (body, randomIP) => {
    try {
        const response = await axios.post(`${OCR_SERVER}/capcode`, body, {
            headers: {
                'Content-Type': 'application/json',
                'X-Forwarded-For': randomIP,
                'X-Real-IP': randomIP
            }
        });
        return response.data;
    } catch (error) {
        $.log(`滑块验证请求失败: ${error.message}`);
        throw error;
    }
};
// =========================================================

function readCache() {
  try {
    if (!fs.existsSync(TOKEN_CACHE_FILE)) return {};
    return JSON.parse(fs.readFileSync(TOKEN_CACHE_FILE, "utf8")) || {};
  } catch {
    return {};
  }
}

function writeCache(cache) {
  try {
    fs.writeFileSync(TOKEN_CACHE_FILE, JSON.stringify(cache, null, 2), "utf8");
  } catch (e) {
    $.log(`token缓存写入失败: ${e.message || e}`);
  }
}

function md5(text) {
  return crypto.createHash("md5").update(String(text)).digest("hex");
}

function mask(value = "") {
  value = String(value);
  if (!value) return "";
  if (value.length <= 12) return `${value.slice(0, 3)}***`;
  return `${value.slice(0, 6)}***${value.slice(-6)}`;
}

function todayString() {
  return new Date(Date.now() + 8 * 3600 * 1000).toISOString().split("T")[0];
}

// ============ YYB Go 取码/加密能力 (免token) ============
// 响应 {code:0, msg, data:{openid, account, result}}, 业务结果在 data.result 深处
function yybExtract(obj, keys) {
  // 在 obj 深处找第一个命中的字符串/数值字段(跳过根节点顶层业务码 code:0)
  const stack = [];
  if (obj && typeof obj === "object") {
    for (const k of Object.keys(obj)) if (obj[k] && typeof obj[k] === "object") stack.push(obj[k]);
  }
  // 根节点若 code 是字符串也认
  for (const k of keys) if (obj && typeof obj[k] === "string" && obj[k]) return obj[k];
  while (stack.length) {
    const cur = stack.pop();
    if (!cur || typeof cur !== "object") continue;
    for (const k of keys) {
      if (typeof cur[k] === "string" && cur[k]) return cur[k];
    }
    for (const k of Object.keys(cur)) if (cur[k] && typeof cur[k] === "object") stack.push(cur[k]);
  }
  return undefined;
}
function yybExtractObj(obj, keys) {
  const stack = [obj];
  while (stack.length) {
    const cur = stack.pop();
    if (!cur || typeof cur !== "object") continue;
    for (const k of keys) {
      if (cur[k] && typeof cur[k] === "object") return cur[k];
    }
    for (const k of Object.keys(cur)) if (cur[k] && typeof cur[k] === "object") stack.push(cur[k]);
  }
  return {};
}
// 单入口 POST: 3xx/401/非JSON/业务失败报清晰诊断
async function yybCall(base, entry, body) {
  let res;
  try {
    res = await axios.post(base + entry, body, {
      headers: { "Content-Type": "application/json", "User-Agent": "Mozilla/5.0" },
      timeout: 30000,
      maxRedirects: 0,
      validateStatus: () => true,
    });
  } catch (e) {
    const er = e.response;
    if (er && er.status >= 300 && er.status < 400) {
      const l = er.headers && er.headers.location;
      throw new Error(`${entry} ${er.status} -> 重定向到: ${l || "(无Location)"} — 经代理/网关被拦, 改用内网地址或放行端口`);
    }
    throw new Error(`YYB Go 请求失败: ${e.message}`);
  }
  const st = res.status, d = res.data, loc = res.headers && res.headers.location;
  if (st >= 300 && st < 400) throw new Error(`${entry} ${st} -> 重定向到: ${loc || "(无Location)"} — 经代理/网关被拦, 改用内网地址或放行端口`);
  if (st === 401) throw new Error(`${entry} 401 协议令牌缺失或错误(YYB Go 开了 YYB_PROTOCOL_TOKEN)`);
  if (!d || typeof d !== "object") throw new Error(`${entry} 响应非JSON(status=${st}): ${JSON.stringify(d).slice(0, 200)}`);
  if (d.code !== undefined && d.code !== 0) throw new Error(`${entry} 失败: ${d.msg || d.message || JSON.stringify(d).slice(0, 200)}`);
  return d;
}
// 双入口取 code: /wx/code -> /wxapp/getCode
async function yybGetCode(baseUrl, ref) {
  const b = String(baseUrl || "").replace(/\/+$/, "");
  if (!b) throw new Error("自动登录需要在 yyb_gpt 账号串里配置 YYB Go 地址(ip:端口#id序号)");
  let lastErr;
  for (const entry of ["/wx/code", "/wxapp/getCode"]) {
    try {
      const d = await yybCall(b, entry, { ref: String(ref), app_id: MINI_APP_ID });
      const code = yybExtract(d, ["code", "wxcode", "open_code"]);
      if (code && typeof code === "string" && code.length >= 6) return code;
      lastErr = new Error(`${entry} 未取到code(检查id序号对应的YYB账号是否存在): ${JSON.stringify(d).slice(0, 200)}`);
    } catch (e) {
      const msg = String(e.message || e);
      if (msg.includes("401") || msg.includes("重定向")) throw e;
      lastErr = e;
    }
  }
  throw lastErr;
}
// ---- YYB Go 把二进制字段序列化成 {"__bytes__": "<hex>"}; 递归解包 protobuf 找 key/iv/version ----
function parseVarint(buf, i) {
  let r = 0, s = 0;
  while (i < buf.length) {
    const x = buf[i++];
    r |= (x & 0x7f) << s;
    if (!(x & 0x80)) return [r, i];
    s += 7;
  }
  return [r, i];
}
function parseProto(buf) {
  // 返回 {fieldNum: [bytes|number, ...]}
  const out = {};
  let i = 0;
  while (i < buf.length) {
    let key; [key, i] = parseVarint(buf, i);
    const fn = key >> 3, wt = key & 7;
    if (!fn) break;
    if (wt === 0) { let v; [v, i] = parseVarint(buf, i); (out[fn] = out[fn] || []).push(v); }
    else if (wt === 2) {
      let ln; [ln, i] = parseVarint(buf, i);
      const v = buf.slice(i, i + ln); i += ln;
      (out[fn] = out[fn] || []).push(v);
    }
    else if (wt === 5) { (out[fn] = out[fn] || []).push(buf.slice(i, i + 4)); i += 4; }
    else if (wt === 1) { (out[fn] = out[fn] || []).push(buf.slice(i, i + 8)); i += 8; }
    else break;
  }
  return out;
}
// 把 YYB Go result(含 __bytes__)铺平成所有字节块/标量的列表
function flattenResult(node, out) {
  if (node === null || node === undefined) return out;
  if (Buffer.isBuffer(node)) { out.blobs.push(node); return out; }
  if (typeof node === "string") { out.strings.push(node); return out; }
  if (typeof node === "number") { out.scalars.push(node); return out; }
  if (typeof node !== "object") return out;
  if (typeof node.__bytes__ === "string") {
    const buf = Buffer.from(node.__bytes__, "hex");
    out.blobs.push(buf);
    try { flattenResult(parseProto(buf), out); } catch (e) {}
    return out;
  }
  if (Array.isArray(node)) { for (const v of node) flattenResult(v, out); return out; }
  for (const k of Object.keys(node)) flattenResult(node[k], out);
  return out;
}
// 从铺平结果里按启发式挑 16字节 encryptKey / 16字节 iv / 小整数 version
function pickKeyIvVersion(result) {
  const flat = { blobs: [], scalars: [], strings: [] };
  flattenResult(result, flat);
  // 16 字节块 = key/iv 候选(也可能解出来的子消息恰好16字节, 但优先长度精确匹配)
  const b16 = flat.blobs.filter((b) => b.length === 16).map((b) => b.toString("utf8"));
  // 兼容 hex/base64 字符串型 key(旧 smallcat 形态)
  const s16 = flat.strings.filter((s) => s.length === 16);
  const keys = b16.concat(s16);
  // version 候选: 小正整数
  const vers = flat.scalars.filter((v) => Number.isInteger(v) && v >= 0 && v < 100000);
  if (keys.length >= 2) return { encryptKey: keys[0], iv: keys[1], version: vers.length ? vers[vers.length - 1] : 1 };
  return null;
}
// /wx/encryptkey: 兼容小猫{encryptKey,iv,version}解码形态 与 YYB Go 原始 __bytes__ protobuf 形态
async function yybGetEncryptKey(baseUrl, ref) {
  const b = String(baseUrl || "").replace(/\/+$/, "");
  if (!b) throw new Error("自动登录需要在 yyb_gpt 账号串里配置 YYB Go 地址(ip:端口#id序号)");
  // 官方 YYB-Go protocol-api.md 确切格式: payload = { api_name, data, env }
  // 操作名 webapi_getuserencryptkey(getLatestUserKey 的 operateWxData 协议名)
  // 成功结果含 encrypt_key/iv/version/expire_in; result.data 可能是 JSON 字符串需再解析
  const OPS = "webapi_getuserencryptkey";
  const payload = { api_name: OPS, data: {}, env: 1 };
  const attempts = ["/wx/getlatestuserkey", "/wx/encryptkey"];
  let lastRaw = "";
  for (const entry of attempts) {
    let d;
    try {
      d = await yybCall(b, entry, { ref: String(ref), app_id: MINI_APP_ID, payload });
    } catch (e) {
      const msg = String(e.message || e);
      if (msg.includes("401") || msg.includes("重定向")) throw e;
      lastRaw = msg;
      continue;
    }
    // result.data 可能是 JSON 字符串, 递归摊平; 兼容 named 字段与 protobuf __bytes__
    const parsed = deepParseResult(d.data || d);
    lastRaw = JSON.stringify(parsed).slice(0, 300);
    if (/invalid\s*(request|api_name|api)/i.test(lastRaw)) continue;
    // 内层 err_no 业务状态(0=成功), 外层 code=0 只代表转发完成
    const errNo = findField(parsed.objs, ["err_no", "errNo", "errcode", "ret"]);
    if (errNo !== undefined && Number(errNo) !== 0) { lastRaw = `err_no=${errNo} ${lastRaw}`; continue; }
    // 1) 具名字段: 在含 encrypt_key 的同一对象里取 iv/version(避免跨对象错配)
    const keyObj = parsed.objs.find((o) => (o.encrypt_key || o.encryptKey) && o.iv !== undefined);
    if (keyObj) {
      const encryptKey = keyObj.encrypt_key || keyObj.encryptKey;
      const iv = keyObj.iv;
      const version = keyObj.version;
      if (encryptKey && iv && version !== undefined) return { encryptKey, iv, version: Number(version) };
    }
    // 2) protobuf __bytes__ 形态: 启发式提取(传原始节点给 flattenResult 摊平)
    const picked = pickKeyIvVersion(d.data || d);
    if (picked && picked.encryptKey && picked.iv) return picked;
  }
  throw new Error(`YYB Go encryptkey 无法提取 key/iv/version(末次result: ${lastRaw})`);
}
// 在对象数组里找字段(浅找, 用于 err_no 等单值)
function findField(objs, keys) {
  for (const o of objs) {
    if (!o || typeof o !== "object") continue;
    for (const k of keys) if (o[k] !== undefined && o[k] !== null) return o[k];
  }
  return undefined;
}
// 递归摊平 result: 把 JSON 字符串再解析, 收集所有对象
function deepParseResult(node, out) {
  out = out || { objs: [], strs: [], nums: [] };
  if (node === null || node === undefined) return out;
  if (typeof node === "string") {
    out.strs.push(node);
    if (/^[\[{]/.test(node.trim())) { try { deepParseResult(JSON.parse(node), out); } catch (e) {} }
    return out;
  }
  if (typeof node === "number") { out.nums.push(node); return out; }
  if (Buffer.isBuffer(node)) return out;
  if (typeof node !== "object") return out;
  out.objs.push(node);
  if (typeof node.__bytes__ === "string") {
    try { deepParseResult(parseProto(Buffer.from(node.__bytes__, "hex")), out); } catch (e) {}
  }
  for (const k of Object.keys(node)) deepParseResult(node[k], out);
  return out;
}

function findCachedAccountByRemark(remark) {
  const cache = readCache();
  return Object.values(cache).find((item) => item && item.remark === remark && (item.openid || item.token)) || null;
}

function parseAccount(raw) {
  const text = String(raw || "").trim();
  if (!text) return { remark: "", openid: "", token: "", yybUrl: "" };

  // 自动登录: YYB服务地址#id序号 (按最后一个#切分, 免备注/免token)
  const auto = /^(https?:\/\/[^#\s]+)#(.+)$/.exec(text);
  if (auto) {
    return { remark: `YYB#${auto[2].trim()}`, openid: auto[2].trim(), token: "", yybUrl: auto[1].trim() };
  }

  if (text.startsWith("{")) {
    try {
      const data = JSON.parse(text);
      return {
        remark: data.remark || data.name || data.nickname || "",
        openid: data.openid || data.openId || data.account || "",
        token: data.token || data.accessToken || "",
      };
    } catch {}
  }

  if (text.includes("#")) {
    const [remark, ...rest] = text.split("#");
    return { remark: remark.trim(), openid: rest.join("#").trim(), token: "", yybUrl: "" };
  }

  if (text.includes("|")) {
    const [openid, ...rest] = text.split("|");
    return { remark: "", openid: openid.trim(), token: rest.join("|").trim(), yybUrl: "" };
  }

  if (text.length > 40 && !text.startsWith("o")) return { remark: "", openid: "", token: text, yybUrl: "" };

  const cached = findCachedAccountByRemark(text);
  if (cached) {
    return { remark: text, openid: cached.openid || "", token: cached.token || "", yybUrl: "" };
  }

  return { remark: "", openid: text, token: "", yybUrl: "" };
}

function headers(token = "") {
  return {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 MicroMessenger MiniProgramEnv/Windows",
    Referer: `https://servicewechat.com/${MINI_APP_ID}/269/page-frame.html`,
    AppID: MINI_APP_ID,
    "App-Version": APP_VERSION,
    Authorization: `Basic ${Buffer.from("wechat:wechat_secret").toString("base64")}`,
    ...(token ? { "X-Access-Token": token } : {}),
  };
}

function shortJson(value, limit = 180) {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if (!text) return "";
  return text.length > limit ? `${text.slice(0, limit)}...` : text;
}

function okCode(res) {
  return String(res?.code) === "10000" || res?.success === true || Number(res?.err) === 0;
}

function assertOk(res, action) {
  if (!res || !okCode(res)) {
    const detail = res?.message || res?.msg || res?.errMsg || shortJson(res, 500);
    const errCode = res?.code ?? res?.err ?? "";
    throw new Error(`${action}失败: ${errCode !== "" && errCode !== 0 && errCode !== "0" ? `[${errCode}] ` : ""}${detail}`);
  }
  return res.data;
}

async function request(method, base, urlPath, { token = "", data = null, params = null } = {}) {
  const res = await axios({
    method,
    url: `${base}${urlPath}`,
    data,
    params,
    timeout: 20000,
    validateStatus: () => true,
    headers: headers(token),
  });
  return res.data;
}

function aesCbcPkcs7Hex(text, key, iv) {
  const keyBuf = Buffer.from(String(key), "utf8");
  const ivBuf = Buffer.from(String(iv), "utf8");
  const algo = { 16: "aes-128-cbc", 24: "aes-192-cbc", 32: "aes-256-cbc" }[keyBuf.length];
  if (!algo) throw new Error(`encrypt_key长度异常: ${keyBuf.length}`);
  if (ivBuf.length !== 16) throw new Error(`iv长度异常: ${ivBuf.length}`);
  const cipher = crypto.createCipheriv(algo, keyBuf, ivBuf);
  return cipher.update(text, "utf8", "hex") + cipher.final("hex");
}

function listify(value) {
  if (Array.isArray(value)) return value;
  if (!value || typeof value !== "object") return [];
  for (const key of ["list", "data", "records", "items", "rows"]) {
    if (Array.isArray(value[key])) return value[key];
  }
  return [];
}

function pickId(item = {}) {
  for (const key of ["id", "sorghum_id", "sorghumId", "member_sorghum_id", "memberSorghumId", "land_id", "landId"]) {
    if (item[key] !== undefined && item[key] !== null && item[key] !== "") return item[key];
  }
  return "";
}

function landNo(item = {}) {
  return item.serial_number ?? item.serialNumber ?? pickId(item) ?? "?";
}

function landStatus(item = {}) {
  const value = Number(item.status ?? -1);
  return Number.isFinite(value) ? value : -1;
}

function landStatusText(item = {}) {
  const status = landStatus(item);
  // 抓包[16]/[63]实锤：status=2 成熟可收获，status=10 刚种下成长中（crop_time 未到）
  const map = { "-1": "未解锁", 0: "空地", 1: "成长中", 2: "可收获", 10: "成长中", 11: "成长中" };
  return map[status] || `状态${status}`;
}

function cropName(item = {}) {
  const map = { 1: "高粱", 2: "小麦" };
  return map[String(item.type ?? "1")] || "未知作物";
}

function isPlantable(item = {}) {
  return pickId(item) && landStatus(item) === 0;
}

function isGrowing(item = {}) {
  const status = landStatus(item);
  return pickId(item) && status > 0 && status !== 2;
}

function isHarvestable(item = {}) {
  return pickId(item) && landStatus(item) === 2;
}

function isCompleted(task = {}) {
  return Number(task.is_complete ?? task.isComplete ?? task.complete ?? task.status_complete ?? 0) === 1;
}

class Task {
  constructor(raw) {
    this.index = $.userIdx++;
    const account = parseAccount(raw);
    this.remark = account.remark || `账号${this.index}`;
    $.remarkMap = $.remarkMap || {};
    $.remarkMap[this.index] = this.remark;
    this.openid = account.openid;
    this.yybUrl = account.yybUrl || "";
    this.token = account.token || "";
    this.member = {};
    this.lastLands = [];
    this.exchangeSummary = "";
    this.summaryLines = [];
    this.cacheKey = this.yybUrl ? `${this.yybUrl}#${this.openid}` : (this.openid || (this.remark ? `remark_${this.remark}` : "") || (this.token ? md5(this.token).slice(0, 16) : `account_${this.index}`));
    this.cacheLoginLogged = false;
    this.encryptKeyCache = null;
    this.syncingSessionKey = false;
  }

  getCached() {
    return readCache()[this.cacheKey] || {};
  }

  saveCache(extra = {}) {
    const cache = readCache();
    cache[this.cacheKey] = {
      ...(cache[this.cacheKey] || {}),
      remark: this.remark,
      openid: this.openid || this.getCached().openid || "",
      ...(this.token ? { token: this.token } : {}),
      ...extra,
      updatedAt: new Date().toISOString(),
    };
    writeCache(cache);
  }

  removeToken() {
    const cache = readCache();
    if (cache[this.cacheKey]) {
      delete cache[this.cacheKey].token;
      writeCache(cache);
    }
    this.token = ""; 
  }

  async getWxCode() {
    if (!this.openid) throw new Error("缺少 id序号，无法自动登录");
    return await yybGetCode(this.yybUrl, this.openid);
  }

  async syncSessionKey() {
    if (this.syncingSessionKey) return;
    this.syncingSessionKey = true;
    try {
      await $.wait(5000, 5000); 
      
      const newCode = await this.getWxCode(); 
      const data = assertOk(
        await request("get", GARDEN_BASE, "/garden/wechat/saveSessionKey", {
          token: this.token,
          params: { code: newCode },
        }),
        "同步微信会话"
      );
      this.saveCache({ sessionSyncedDate: todayString() });
      $.log(`✅ 账号[${this.index}] 微信会话已同步`);
      return data;
    } catch (e) {
      $.log(`⚠️ 账号[${this.index}] 微信会话同步失败: ${e.message}`);
    } finally {
      this.syncingSessionKey = false;
    }
  }

  async login() {
    $.log(`⏳ 账号[${this.index}] 正在登录...`);
    const code = await this.getWxCode();
    const data = assertOk(
      await request("post", FM_BASE, "/api/v2/login/wxMiniSilentLogin", {
        data: { code },
      }),
      "静默登录"
    );
    if (!data?.token) throw new Error(`静默登录未返回 token: ${JSON.stringify(data)}`);
    this.token = data.token;
    this.saveCache({
      unionId: data.unionId || "",
      phone: data.phone || "",
      mainOpenId: data.openId || "",
    });
    $.log(`✅ 账号[${this.index}] 登录成功`);

    await this.syncSessionKey();
  }

  async ensureLogin() {
    const cached = this.getCached();
    if (!this.openid && cached.openid) this.openid = cached.openid;
    if (!this.token) this.token = cached.token || "";
    if (this.token) {
      if (!this.cacheLoginLogged) {
        $.log(`✅ 账号[${this.index}] 使用缓存登录`);
        this.cacheLoginLogged = true;
      }
      if (!this.syncingSessionKey && cached.sessionSyncedDate !== todayString()) {
        $.log(`账号[${this.index}] 缓存登录微信会话今日未同步，正在同步`);
        await this.syncSessionKey();
      }
      return;
    }
    await this.login();
  }

  async ensureValidLogin() {
    await this.ensureLogin();
    if (!this.token) return;
    try {
      const res = await request("get", GARDEN_BASE, "/garden/Gardenmemberinfo/getMemberInfo", { token: this.token });
      const msg = `${res?.message || ""}${res?.msg || ""}`;
      if (!okCode(res) && /登录|授权|token|Token|未认证|失效/.test(msg)) {
        throw new Error(msg || "缓存登录失效");
      }
      if (!okCode(res)) throw new Error(res?.message || res?.msg || "缓存登录校验失败");
      this.member = res.data || {};
      return;
    } catch (e) {
      $.log(`账号[${this.index}] 缓存登录失效，重新登录`);
      this.removeToken();
      await this.login();
    }
  }

  async withRelogin(fn) {
    await this.ensureLogin();
    let res = await fn();
    const msg = `${res?.message || ""}${res?.msg || ""}`;
    if (!okCode(res) && this.openid && /登录|授权|token|Token|未认证|失效/.test(msg)) {
      $.log(`账号[${this.index}] 登录状态失效，重新登录`);
      this.removeToken();
      await this.login();
      res = await fn();
    }
    return res;
  }

  async gardenGet(urlPath, params = {}) {
    const res = await this.withRelogin(() =>
      request("get", GARDEN_BASE, urlPath, { token: this.token, params })
    );
    return assertOk(res, urlPath);
  }

  async gardenPost(urlPath, data = {}) {
    const res = await this.withRelogin(() =>
      request("post", GARDEN_BASE, urlPath, { token: this.token, data })
    );
    return assertOk(res, urlPath);
  }

  async getEncryptKey() {
    if (this.encryptKeyCache) {
      return this.encryptKeyCache;
    }
    if (!this.openid) throw new Error("缺少 id序号，无法生成微信 encryptData");
    const info = await yybGetEncryptKey(this.yybUrl, this.openid);
    $.log(`✅ 账号[${this.index}] 加密参数就绪`);
    this.encryptKeyCache = info;
    return this.encryptKeyCache;
  }

  async encryptData(data = {}) {
    const payload = data && typeof data === "object" ? { ...data } : {};
    const key = await this.getEncryptKey();
    payload.ts = Date.now();
    payload.encryptData = aesCbcPkcs7Hex(JSON.stringify(payload), key.encryptKey, key.iv);
    payload.version = key.version;
    return payload;
  }

  async encryptedPost(urlPath, data = {}) {
    return this.gardenPost(urlPath, await this.encryptData(data));
  }

  async encryptedGet(urlPath, data = {}) {
    return this.gardenGet(urlPath, await this.encryptData(data));
  }

  async encryptedPostWithSessionRetry(urlPath, data = {}) {
    try {
      return await this.encryptedPost(urlPath, data);
    } catch (e) {
      const msg = String(e.message || e);
      const sessionError = /系统异常|SessionKey|sessionKey|用户信息异常|重新打开|重新进入|5001/.test(msg);
      // "获取失败"=业务层拒绝，实测为服务端会话绑定陈旧（长期无真实app活动），重同步后即可恢复
      const businessDenied = /获取失败/.test(msg);
      if (!sessionError && !businessDenied) throw e;
      $.log(`账号[${this.index}] ${urlPath}被拒(${businessDenied ? "业务" : "会话"})，刷新微信会话后重试: ${msg}`);
      this.encryptKeyCache = null;
      await this.syncSessionKey();
      return await this.encryptedPost(urlPath, data);
    }
  }

  async checkSessionKey() {
    try {
      const res = await this.gardenGet("/garden/wechat/checkSessionKey");
      $.log(`✅ 账号[${this.index}] 微信会话校验通过`);
      return res;
    } catch (e) {
      $.log(`⚠️ 账号[${this.index}] 微信会话校验异常: ${e.message || e}，尝试重新同步会话`);
      this.encryptKeyCache = null;
      await this.syncSessionKey();
      return null;
    }
  }

  async queryMember(shouldLog = true) {
    try {
      const info = await this.gardenGet("/garden/Gardenmemberinfo/getMemberInfo");
      this.member = info || {};
      this.saveCache({
        memberId: info?.member_id || "",
        nickName: info?.nick_name || "",
        integration: info?.integration || "",
      });
      if (shouldLog) {
        $.log(
          `账号[${this.index}] 会员: ${info?.nick_name || mask(info?.member_id || "")}，积分${info?.integration ?? "未知"}，水滴${info?.water ?? 0}，有机肥${info?.manure ?? 0}，高粱${info?.sorghum ?? 0}，小麦${info?.wheat ?? 0}，酒曲${info?.wine_yeast ?? 0}，酒滴${info?.wine ?? 0}`
        );
      }
      return info || {};
    } catch (e) {
      // 花园通道(getMemberInfo)依赖 encryptkey, 可能失败; 兜底走 FM 通道查基本信息
      if (shouldLog) $.log(`账号[${this.index}] 花园会员查询失败(${String(e.message || e).slice(0, 40)}), 改用FM通道兜底`);
      return await this.queryMemberFm(shouldLog);
    }
  }

  // FM 通道会员信息兜底(queryById + balance), 不依赖 encryptkey
  async queryMemberFm(shouldLog = true) {
    try {
      const [profile, bal] = await Promise.all([
        this.fmGetNoThrow("/api/customer/queryById/token", { channel: "h5" }),
        this.fmGetNoThrow("/api/customer/balance"),
      ]);
      const nick = profile?.nickname || "";
      const money = bal?.money ?? "";
      this.member = { ...(this.member || {}), nick_name: nick, money };
      if (nick) this.saveCache({ nickName: nick });
      if (shouldLog) {
        $.log(`账号[${this.index}] 会员(FM兜底): ${nick || mask(this.openid)}${money !== "" ? `，余额${money}` : ""}`);
      }
      return this.member;
    } catch (e) {
      if (shouldLog) $.log(`账号[${this.index}] FM会员兜底失败: ${String(e.message || e).slice(0, 40)}`);
      return {};
    }
  }

  async verifyCaptcha() {
    try {
        const randomIP = generateRandomIP();
        $.log(`账号[${this.index}] 准备获取滑块验证码 (虚拟IP: ${randomIP})...`);
        
        const captchaRes = await axios.post(`${FM_BASE}/api/captcha/get`, { captchaType: "blockPuzzle" }, {
            headers: {
                ...headers(this.token),
                "X-Forwarded-For": randomIP,
                "X-Real-IP": randomIP
            }
        });
        
        const captcha = captchaRes.data;
        if (!captcha.success) {
            throw new Error(`获取滑块验证码失败: ${captcha.message}`);
        }

        const slideResponse = await slidePost({
            slidingImage: captcha.data.repData.jigsawImageBase64,
            backImage: captcha.data.repData.originalImageBase64
        }, randomIP);

        const point = aesEncrypt({ x: slideResponse.result, y: 5 }, captcha.data.repData.secretKey);
        
        const checkRes = await axios.post(`${FM_BASE}/api/captcha/check`, {
            captchaType: "blockPuzzle",
            pointJson: point,
            token: captcha.data.repData.token
        }, {
            headers: {
                ...headers(this.token),
                "X-Forwarded-For": randomIP,
                "X-Real-IP": randomIP
            }
        });

        if (checkRes.data.success) {
            $.log(`✅ 账号[${this.index}] 滑块验证通过!`);
            return true;
        } else {
            throw new Error(`滑块验证结果失败: ${checkRes.data.message}`);
        }
    } catch (error) {
        $.log(`⚠️ 账号[${this.index}] 滑块验证异常 (可能不影响后续操作): ${error.message}`);
        return false;
    }
  }

  async dailySign() {
    // 对齐小程序真实签到流程: checkTodaySignIn 预检 → fillSignIn → getRewards
    const already = await this.checkTodaySignIn();
    if (already) {
      $.log(`✅ 账号[${this.index}] 今日已签到，跳过`);
      await this.claimSignRewards();
      return;
    }
    const fn = async () => {
      const code = await this.getWxCode();
      const res = await axios({
        method: "post",
        url: `${FM_BASE}/api/customer/daily/fillSignIn`,
        data: { code: code, channelCode: "xj_mall_wx_applet" },
        timeout: 20000,
        validateStatus: () => true,
        headers: {
          "Content-Type": "application/json; charset=utf-8",
          "User-Agent": "Mozilla/5.0 (Linux; Android 14; 2106118C Build/UKQ1.231003.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/146.0.7680.178 Mobile Safari/537.36 XWEB/1460217 MMWEBSDK/20250904 MMWEBID/1162 MicroMessenger/8.0.64.2940(0x28004033) WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64 MiniProgramEnv/android",
          "Referer": `https://servicewechat.com/${MINI_APP_ID}/218/page-frame.html`,
          "AppID": MINI_APP_ID,
          "App-Version": APP_VERSION,
          "Authorization": "Basic d2VjaGF0OndlY2hhdF9zZWNyZXQ=",
          "X-Access-Token": this.token,
          "Accept-Encoding": "gzip, deflate, br"
        }
      });
      return assertOk(res.data, "每日签到");
    };

    try {
      const data = await this.withRelogin(fn);
      const point = data && (data.pointValue ?? data.point ?? "");
      $.log(`✅ 账号[${this.index}] 签到完成${point !== "" ? ` +${point}积分` : ""}`);
      await this.claimSignRewards();
    } catch (e) {
      $.log(`账号[${this.index}] 签到失败: ${e.message || e}`);
    }
  }

  // 今日是否已签(预检, 避免重复签到被风控)
  async checkTodaySignIn() {
    try {
      const res = await this.fmGetNoThrow("/api/customer/daily/checkTodaySignIn");
      // 返回布尔 或 {data:bool}
      return res === true || res === "true" || res?.isSign === true || res?.data === true;
    } catch (e) {
      return false; // 预检失败不阻断, 继续走签到
    }
  }

  // 领取连签奖励(7/15/30天), best-effort
  async claimSignRewards() {
    try {
      const data = await this.fmGetNoThrow("/api/customer/daily/getRewards");
      if (!data) return;
      const parts = [];
      if (data.signIn7) parts.push(`连签7天+${data.signIn7}`);
      if (data.signIn15) parts.push("连签15天奖励");
      if (data.signIn30) parts.push(`连签30天+${data.signIn30}`);
      $.log(parts.length ? `✅ 账号[${this.index}] ${parts.join(" ")}` : `账号[${this.index}] 暂无可领连签奖励`);
    } catch (e) {
      $.log(`账号[${this.index}] 领取连签奖励跳过: ${e.message || e}`);
    }
  }

  // 通用 GET, 不 assertOk(用于预检/奖励等非强依赖接口), 返回 data 或 null
  async fmGetNoThrow(urlPath, params = {}) {
    try {
      const res = await request("get", FM_BASE, urlPath, { token: this.token, params });
      return okCode(res) ? res.data : null;
    } catch (e) {
      return null;
    }
  }

  async getActiveWineRecord() {
    try {
      const data = await this.gardenGet("/garden/gardenmemberwine/index");
      const records = listify(data);
      const cache = this.getCached();
      const activeRecord = records.find(r => {
        if (String(r.id || "") === String(cache.last_harvested_wine_id || "")) return false;
        return r.status === 3 || r.status === 2 || r.status === 1 || r.status === 4 || r.crop_time;
      });
      if (activeRecord) {
        return activeRecord;
      }
    } catch (e) {}
    return null;
  }

  async makeYeast() {
    let cache = this.getCached();
    let wheatPlantedCount = cache.wheatPlantedCount || 0;
    let wheatStockRaw = this.member.wheat ?? this.member.wheat_volumn ?? this.member.wheatVolumn;
    let wheatStock = wheatStockRaw === undefined ? null : Number(wheatStockRaw || 0);

    if (wheatStock !== null && wheatStock < 100) {
      $.log(`账号[${this.index}] 小麦不足 (${wheatStock} < 100)，跳过制曲`);
      return false;
    }

    if (wheatStock === null && wheatPlantedCount <= 0) {
      $.log(`账号[${this.index}] 当前未记录小麦成熟来源，跳过制曲操作`);
      return false;
    }

    try {
      const res = await this.withRelogin(async () => {
        const response = await axios({
          method: "post",
          url: `${GARDEN_BASE}/garden/wheat/makeWineYeast`,
          data: "volumn=100",
          headers: {
            ...headers(this.token),
            "Content-Type": "application/x-www-form-urlencoded"
          }
        });
        return response.data;
      });
      const data = assertOk(res, "制曲");
      
      const nextWheatPlantedCount = Math.max(0, wheatPlantedCount - 1);
      this.saveCache({ wheatPlantedCount: nextWheatPlantedCount });
      await this.queryMember(false);
      $.log(`账号[${this.index}] 制曲成功: 消耗小麦 ${Math.abs(data?.consume_wheat || 0)}, 获得酒曲 ${data?.get_wine_yeast || 0}。剩余可制曲次数: ${nextWheatPlantedCount}`);
      return true;
    } catch (e) {
      $.log(`账号[${this.index}] 制曲失败: ${e.message || e}`);
      return false;
    }
  }

  async makeWine(isRetry = false) {
    try {
      const res = await this.withRelogin(async () => {
        const response = await axios({
          method: "post",
          url: `${GARDEN_BASE}/garden/gardenmemberwine/makeWine`,
          data: "volumn=200",
          headers: {
            ...headers(this.token),
            "Content-Type": "application/x-www-form-urlencoded"
          }
        });
        return response.data;
      });
      const data = assertOk(res, "制酒(酿酒)");
      
      if (data && data.make_wine_record) {
        const record = data.make_wine_record;
        $.log(`账号[${this.index}] 制酒开始，单号: ${record.id}，预计收取时间: ${record.crop_time}`);
        this.saveCache({
          current_wine_id: record.id,
          wine_crop_time: record.crop_time
        });
        return true;
      }
    } catch (e) {
      const errorMsg = String(e.message || e);
      $.log(`账号[${this.index}] 制酒(酿酒)失败: ${errorMsg}`);
      
      if (errorMsg.includes("正在制酒中")) {
        $.log(`⚠️ 警告: 账号[${this.index}] 本地丢失了酿造流水号。为了恢复自动收取，请前往小程序【手动收一次酒】，重置脚本循环！`);
      }

      if (errorMsg.includes("曲块不足") && !isRetry) {
        $.log(`账号[${this.index}] 检测到曲块不足，触发自动制曲...`);
        let yeastSuccess = await this.makeYeast();
        
        // 只有制曲成功（即有小麦种植次数并成功消耗）才重试酿酒
        if (yeastSuccess) {
            $.log(`账号[${this.index}] 等待 5 秒后重试酿酒...`);
            await $.wait(5000, 5000);
            return await this.makeWine(true); 
        } else {
            $.log(`账号[${this.index}] 缺乏小麦种植记录，无法自动制曲，中止本次酿酒重试。`);
        }
      }

      return false;
    }
  }

  async harvestWine(id) {
    try {
      const data = await this.gardenGet("/garden/gardenmemberwine/harvestWine", { id: id });
      this.saveCache({ last_harvested_wine_id: id });
      $.log(`账号[${this.index}] 收酒成功: 获得酒滴 ${data?.wine || 0}`);
      return true;
    } catch (e) {
      const msg = String(e.message || e);
      if (msg.includes("已经收取") || msg.includes("重复操作")) {
        this.saveCache({ last_harvested_wine_id: id });
        $.log(`账号[${this.index}] 酒已收取，清理本地酿造缓存`);
        return true;
      }
      $.log(`账号[${this.index}] 收酒失败(ID:${id}): ${msg}`);
      return false;
    }
  }

  async queryFriendToken() {
    try {
      const data = await this.gardenGet("/garden/friends/addFriendToken");
      if (data && typeof data === "object") {
        data.friend_id = this.member?.member_id || this.openid || "";
      }
      this.saveCache({ friendToken: data || "" });
      return data;
    } catch (e) {
      return null;
    }
  }

  async exchangeWine() {
    if (!ENABLE_EXCHANGE) return false;
    await this.queryMember();
    const beforeWine = Number(this.member.wine || 0);
    if (beforeWine <= 0) {
      $.log(`账号[${this.index}] 暂无可兑换酒滴`);
      return false;
    }

    const logExchangeResult = async () => {
      await this.queryMember(false);
      const afterWine = Number(this.member.wine || 0);
      const usedWine = Math.max(0, beforeWine - afterWine) || beforeWine;
      this.exchangeSummary = `酒滴兑换：消耗${usedWine}酒滴，获得${usedWine}积分`;
      $.log(`✅ 账号[${this.index}] ${this.exchangeSummary}`);
    };

    try {
      await this.encryptedGet("/garden/Gardenjifenshop/exchange", { wine: beforeWine });
      await logExchangeResult();
      return true;
    } catch (e) {
      const msg = e.message || String(e);
      if (/重新进入|SessionKey|session/i.test(msg)) {
        try {
          $.log(`账号[${this.index}] 兑换需要刷新微信会话，正在重试`);
          this.encryptKeyCache = null;
          await this.syncSessionKey();
          await this.encryptedGet("/garden/Gardenjifenshop/exchange", { wine: beforeWine });
          await logExchangeResult();
          return true;
        } catch (retryErr) {
          $.log(`账号[${this.index}] 酒滴兑换失败: ${retryErr.message || retryErr}`);
          return false;
        }
      }
      $.log(`账号[${this.index}] 酒滴兑换失败: ${msg}`);
      return false;
    }
  }

  async queryWineStatus() {
    let records = [];
    try {
      const data = await this.gardenGet("/garden/gardenmemberwine/index");
      records = listify(data);
      if (records.length) {
        const summary = records.map(item => {
          const volumn = item.crrent_volumn ?? item.current_volumn ?? item.volumn ?? 0;
          const cropTime = item.crop_time || "未知";
          return `酒*${volumn} 收获时间：${cropTime}`;
        }).join("；");
        $.log(`账号[${this.index}] 酿酒状态：${summary}`);
      } else {
        $.log(`账号[${this.index}] 酿酒状态：暂无正在酿造`);
      }
    } catch (e) {
      $.log(`账号[${this.index}] 酿酒状态查询失败: ${e.message || e}`);
    }

    await this.queryMember();
    $.log(`账号[${this.index}] 酒滴数量：${this.member.wine ?? 0}，积分：${this.member.integration ?? "未知"}`);
    return records;
  }

  async runWineAutomation() {
    $.log(`\n账号[${this.index}] 开始酒坊酿造逻辑 (先查状态/收酒，再按库存制酒)...`);
    let cache = this.getCached();
    let wineId = cache.current_wine_id;
    let cropTime = cache.wine_crop_time;

    const activeRecord = await this.getActiveWineRecord();
    if (activeRecord && activeRecord.id) {
      wineId = activeRecord.id;
      cropTime = activeRecord.crop_time;
      $.log(`账号[${this.index}] 同步到进行中的酿造: ID ${wineId}, 预计收取 ${cropTime || "未知"}`);
      this.saveCache({ current_wine_id: wineId, wine_crop_time: cropTime || null });
    } else if (wineId) {
      $.log(`账号[${this.index}] 服务器未查询到进行中的酿造，清理本地旧缓存`);
      wineId = null;
      cropTime = null;
      this.saveCache({ current_wine_id: null, wine_crop_time: null });
    }

    if (wineId && cropTime) {
      const now = new Date().getTime();
      const targetTime = new Date(cropTime.replace(/-/g, "/")).getTime(); 

      if (now >= targetTime) {
        $.log(`账号[${this.index}] 酒已酿熟，开始收酒 (ID: ${wineId})`);
        const success = await this.harvestWine(wineId);
        
        if (success) {
          this.saveCache({ current_wine_id: null, wine_crop_time: null });
          wineId = null;
          cropTime = null;
          await this.queryMember(false);
          await $.wait(3000, 5000);
        } else {
          return;
        }
      } else {
        $.log(`账号[${this.index}] 酿酒进行中，未到收取时间: ${cropTime}`);
        return;
      }
    } else if (wineId) {
      $.log(`账号[${this.index}] 已有酿造记录但缺少成熟时间，本次只同步状态，不重复操作`);
      return;
    }

    const currentSorghum = Number(this.member.sorghum || 0);
    if (currentSorghum < 200) {
      $.log(`账号[${this.index}] 高粱不足 (${currentSorghum} < 200)，本次不新开制酒`);
      return; 
    }

    let currentYeast = Number(this.member.wine_yeast ?? this.member.wineYeast ?? 0);
    if (currentYeast <= 0) {
      $.log(`账号[${this.index}] 酒曲不足，先尝试制曲`);
      const yeastSuccess = await this.makeYeast();
      if (!yeastSuccess) {
        $.log(`账号[${this.index}] 无法补充酒曲，本次不新开制酒`);
        return;
      }
      currentYeast = Number(this.member.wine_yeast ?? this.member.wineYeast ?? 0);
      if (currentYeast <= 0) {
        $.log(`账号[${this.index}] 制曲后仍未检测到酒曲，本次不新开制酒`);
        return;
      }
    }

    $.log(`账号[${this.index}] 酒坊空闲且高粱/酒曲充足，开始新的制酒`);
    await this.makeWine();
  }

  async queryFarm() {
    const data = await this.gardenGet("/garden/sorghum/index");
    const lands = listify(data);
    this.lastLands = lands;
    const summary = lands
      .map((v) => `#${v.serial_number ?? v.id ?? "?"}:${landStatusText(v)}`)
      .join(" ");
    $.log(`账号[${this.index}] 地块: ${lands.length || 0}块 ${summary}`.trim());
    return lands;
  }

  landPayload(land) {
    // 抓包[28]实锤：真实收获请求明文仅 {"id":1141456}，多发字段会导致业务校验失败("获取失败")
    return { id: pickId(land) };
  }

  async harvestFarm(lands) {
    const candidates = lands.filter(isHarvestable);
    if (!candidates.length) return false;

    let acted = false;
    try {
      await this.encryptedGet("/garden/Sorghum/harvestAll");
      $.log(`✅ 账号[${this.index}] 一键收获完成`);
      return true;
    } catch (e) {
      $.log(`账号[${this.index}] 一键收获失败，尝试单块收获: ${e.message || e}`);
    }

    for (const land of candidates) {
      try {
        await this.encryptedPostWithSessionRetry("/garden/sorghum/harvest", this.landPayload(land));
        $.log(`✅ 账号[${this.index}] 地块${landNo(land)} 收获完成`);
        acted = true;
      } catch (e) {
        $.log(`账号[${this.index}] 收获失败[${landNo(land)}]: ${e.message || e}`);
      }
      await $.wait(20000, 25000);
    }
    return acted;
  }

  async seedFarm(lands) {
    const seedCount = Number(this.member.sorghum || 0);
    if (seedCount <= 0) {
      $.log(`账号[${this.index}] 无可用高粱，跳过种植`);
      return false;
    }
    const candidates = lands.filter(isPlantable);
    if (!candidates.length) {
      $.log(`账号[${this.index}] 未识别到可种植地块`);
      return false;
    }
    let acted = false;
    const limit = Math.min(seedCount, candidates.length);
    for (let i = 0; i < limit; i++) {
      const land = candidates[i];
      try {
        await this.encryptedPost("/garden/sorghum/seed", { id: pickId(land), type: 1 });
        $.log(`✅ 账号[${this.index}] 地块${landNo(land)} 种植完成`);
        acted = true;
      } catch (e) {
        $.log(`账号[${this.index}] 种植失败[${landNo(land)}]: ${e.message || e}`);
      }
      await $.wait(20000, 25000);
    }
    return acted;
  }

  async waterFarm(lands) {
    const waterCount = Number(this.member.water || 0);
    if (waterCount <= 0) {
      $.log(`账号[${this.index}] 无可用水滴，跳过浇水`);
      return false;
    }
    const candidates = lands.filter(isGrowing);
    if (!candidates.length) {
      $.log(`账号[${this.index}] 未识别到可浇水地块`);
      return false;
    }
    let acted = false;
    const limit = Math.min(waterCount, candidates.length);
    for (let i = 0; i < limit; i++) {
      const land = candidates[i];
      try {
        await this.encryptedPostWithSessionRetry("/garden/sorghum/watering", this.landPayload(land));
        $.log(`✅ 账号[${this.index}] 地块${landNo(land)} 浇水完成`);
        acted = true;
      } catch (e) {
        $.log(`账号[${this.index}] 浇水失败[${landNo(land)}]: ${e.message || e}`);
      }
      await $.wait(20000, 25000);
    }
    return acted;
  }

  async manureFarm(lands) {
    const manureCount = Number(this.member.manure || 0);
    if (manureCount <= 0) {
      $.log(`账号[${this.index}] 无可用有机肥，跳过施肥/养护`);
      return false;
    }
    const candidates = lands.filter(isGrowing);
    if (!candidates.length) {
      $.log(`账号[${this.index}] 未识别到可施肥/养护地块`);
      return false;
    }
    let acted = false;
    const limit = Math.min(manureCount, candidates.length);
    for (let i = 0; i < limit; i++) {
      const land = candidates[i];
      try {
        await this.encryptedPostWithSessionRetry("/garden/sorghum/manuring", this.landPayload(land));
        $.log(`✅ 账号[${this.index}] 地块${landNo(land)} 施肥完成`);
        acted = true;
      } catch (e) {
        $.log(`账号[${this.index}] 施肥/养护失败[${landNo(land)}]: ${e.message || e}`);
      }
      await $.wait(20000, 25000);
    }
    return acted;
  }

  async runFarmAutomation() {
    let cropType = [{"1":"高粱"},{"2":"小麦"}];
    
    // 读取青龙环境变量控制地块数量
    let landLimitStr = process.env['地块'];
    let landLimit = parseInt(landLimitStr);
    if (isNaN(landLimit) || landLimit <= 0) {
      landLimit = 1; // 默认只操作第 1 块地
    }

    $.log(`\n账号[${this.index}] 开始农场植物状态逻辑 (计划操作前 ${landLimit} 块地)`);
    await this.queryMember();
    let lands = await this.queryFarm();
    
    if (!lands || lands.length === 0) {
      $.log(`账号[${this.index}] 未查询到任何土地`);
      return true;
    }

    // 根据环境变量截取需要操作的地块
    let targetLands = lands.slice(0, landLimit);

    for (let i = 0; i < targetLands.length; i++) {
      let land = targetLands[i];
      let landId = pickId(land);

      $.log(`\n--- 开始处理第 ${i + 1} 块地 (ID: ${landId}) ---`);

      if (land.status == -1) {
        $.log(`账号[${this.index}] 第${i + 1}块地(${landId})：未解锁`);
        $.log(`账号[${this.index}] 开始解锁第${i + 1}块土地`);
        try {
          await this.encryptedPostWithSessionRetry(`/garden/sorghum/extend`, { serial_number: land.serial_number ?? land.id });
          $.log(`账号[${this.index}] 解锁成功`);
          lands = await this.queryFarm();
          land = lands.find(l => pickId(l) == landId) || lands[i];
        } catch (e) {
          $.log(`账号[${this.index}] 解锁失败: ${e.message || e}`);
          continue; 
        }
      }

      if (land.status != -1) {
        $.log(`账号[${this.index}] 第${i + 1}块地(${landId})：已解锁`);
        let typeStr = land.type !== undefined ? String(land.type) : "1";
        let nameObj = cropType.find(item => typeStr in item);
        let name = nameObj ? nameObj[typeStr] : "未知作物";

        $.log(`账号[${this.index}] 种植状态：${name}*${land.volumn || 0} 收获时间：${land.crop_time || '未知'}`);

        const plantCropWithRotation = async () => {
          let cache = this.getCached();
          let sorghumCount = cache.sorghumPlantCount || 0;
          let wheatPlantedCount = cache.wheatPlantedCount || 0;
          let sorghumStock = Number(this.member.sorghum || 0);
          let wheatStockRaw = this.member.wheat ?? this.member.wheat_volumn ?? this.member.wheatVolumn;
          let yeastStockRaw = this.member.wine_yeast ?? this.member.wineYeast;
          let wheatStock = wheatStockRaw === undefined ? null : Number(wheatStockRaw || 0);
          let yeastStock = yeastStockRaw === undefined ? null : Number(yeastStockRaw || 0);
          let nextType = 1;
          let reason = `按20:1轮换，已连种高粱 ${sorghumCount} 次`;

          if (yeastStock !== null && yeastStock <= 0 && (wheatStock === null || wheatStock < 100) && wheatPlantedCount <= 0) {
            nextType = 2;
            reason = `酒曲不足，小麦库存${wheatStock === null ? "未知" : wheatStock}，优先补小麦`;
          } else if (sorghumStock < 200) {
            nextType = 1;
            reason = `高粱库存不足200，当前${sorghumStock}，优先补高粱`;
          } else if (sorghumCount >= 20) {
            nextType = 2;
            reason = `已连种高粱 ${sorghumCount} 次，按比例补小麦`;
          }

          let nextName = nextType === 1 ? "高粱" : "小麦";

          $.log(`账号[${this.index}] 准备种植: ${nextName} (${reason})`);

          try {
            await this.encryptedPostWithSessionRetry(`/garden/sorghum/seed`, { id: landId, type: nextType });
            $.log(`账号[${this.index}] 种植 ${nextName} 成功`);
            
            if (nextType === 1) {
              this.saveCache({ sorghumPlantCount: sorghumCount + 1 });
            } else {
              this.saveCache({ 
                  sorghumPlantCount: 0,
                  wheatPlantedCount: wheatPlantedCount + 1
              });
              $.log(`账号[${this.index}] 记录：种了一次小麦，获得 1 次制曲资格 (当前累计: ${wheatPlantedCount + 1} 次)`);
            }
          } catch (e) { 
            $.log(`账号[${this.index}] 种植失败: ${e.message || e}`); 
          }
        };

        const harvestAndPlant = async () => {
          $.log(`账号[${this.index}] ${name}已成熟，开始收获`);
          let harvestPayload = null;
          try {
            const latestLands = await this.queryFarm();
            const latestLand = latestLands.find(l => pickId(l) == landId) || land;
            harvestPayload = this.landPayload(latestLand);
            $.log(`账号[${this.index}] 收获请求: 地块=${landId} status=${landStatus(latestLand)} payload=${shortJson(harvestPayload, 150)}`);
            // "获取失败"等业务拒绝由 encryptedPostWithSessionRetry 统一刷新会话重试
            await this.encryptedPostWithSessionRetry(`/garden/sorghum/harvest`, harvestPayload);
            $.log(`账号[${this.index}] 收获成功`);
            $.log(`账号[${this.index}] 开始种植新一轮作物`);
            await this.queryMember();
            await $.wait(20000, 25000);
            await plantCropWithRotation();
            // 种下后立即浇水，不等下次运行
            await $.wait(2000, 3000);
            await waterLoop(land);
          } catch (e) {
            $.log(`账号[${this.index}] 收获/种植失败: ${e.message || e} (payload: ${shortJson(harvestPayload, 150)})`);
          }
        };

        const waterLoop = async (targetLand) => {
          let code = 0, retry = 0;
          let matureHint = false;
          while (code == 0 && retry < 3) {
            await this.queryMember();
            if (Number(this.member.water || 0) <= 0) {
              $.log(`账号[${this.index}] 水滴不足，跳过浇水`);
              break;
            }
            try {
              await this.encryptedPostWithSessionRetry(`/garden/sorghum/watering`, this.landPayload(targetLand));
              $.log(`账号[${this.index}] 浇水成功`);
              retry++;
              await $.wait(2000, 3000);
            } catch (e) {
              let errorMsg = String(e.message || e);
              $.log(`账号[${this.index}] 浇水结束: ${errorMsg}`);
              code = 1;
              if (errorMsg.includes("仅允许为成长中的高粱浇水") || errorMsg.includes("成熟")) {
                matureHint = true;
              }
            }
          }
          return matureHint;
        };

        if (land.status == 0) {
          $.log(`账号[${this.index}] 第${i + 1}块地已清空，开始播种`);
          await plantCropWithRotation();
          // 种下后立即浇水，不等下次运行
          await $.wait(2000, 3000);
          await waterLoop(land);
        } else if (land.status == 2) {
          await harvestAndPlant();
        } else {
          const matureAfterWater = await waterLoop(land);

          if (matureAfterWater) {
             $.log(`账号[${this.index}] 识别到植物已成熟，立刻进行收获与种植操作！`);
             await harvestAndPlant();
             continue;
          }

          let code = 0, retry = 0;
          let isMature = false;
          while (code == 0 && retry < 3) {
            await this.queryMember();
            if (Number(this.member.manure || 0) <= 0) {
              $.log(`账号[${this.index}] 有机肥不足，跳过施肥`);
              break;
            }
            try {
              await this.encryptedPostWithSessionRetry(`/garden/sorghum/manuring`, this.landPayload(land));
              $.log(`账号[${this.index}] 施肥成功`);
              retry++;
              await $.wait(2000, 3000);
            } catch (e) {
              let errorMsg = String(e.message || e);
              $.log(`账号[${this.index}] 施肥结束: ${errorMsg}`);
              code = 1;
              if (errorMsg.includes("成熟")) {
                isMature = true;
              }
            }
          }

          if (isMature) {
             $.log(`账号[${this.index}] 识别到植物已成熟，立刻进行收获与种植操作！`);
             await harvestAndPlant();
          }
        }
      }
    }
    return true;
  }

  async queryTasks() {
    const data = await this.gardenGet("/garden/tasks/index");
    const tasks = listify(data);
    if (!tasks.length) {
      $.log(`账号[${this.index}] 未查询到任务列表`);
      return [];
    }
    $.log(
      `账号[${this.index}] 任务状态：${tasks
        .map((t) => `${isCompleted(t) ? "✅" : "○"}${t.name || "未知任务"}`)
        .join("，")}`
    );
    return tasks;
  }

  async doShareTask() {
    for (let i = 1; i <= 3; i++) {
      let done = false;
      for (let attempt = 1; attempt <= 2 && !done; attempt++) {
        try {
          await this.encryptedPost("/garden/gardenmemberinfo/dailyShare");
          done = true;
          $.log(`✅ 账号[${this.index}] 分享任务 ${i}/3 完成`);
          try {
            await this.gardenGet("/garden/friends/addFriendToken");
          } catch {}
        } catch (e) {
          $.log(`账号[${this.index}] 分享任务 ${i}/3 失败(第${attempt}次): ${e.message || e}`);
          if (attempt === 1) {
            this.encryptKeyCache = null;
            await this.syncSessionKey();
            await $.wait(3000, 5000);
          }
        }
      }

      if (i < 3) {
        const delayMs = Math.floor(Math.random() * (15000 - 5000 + 1) + 5000);
        await $.wait(delayMs, delayMs);
      }
    }
  }

  async doQuestionTask() {
    let questions = [];
    for (let attempt = 1; attempt <= 2; attempt++) {
      try {
        questions = listify(await this.gardenGet("/garden/Gardenquestiontask/index"));
        break;
      } catch (e) {
        $.log(`账号[${this.index}] 获取题目失败(第${attempt}次): ${e.message || e}`);
        if (attempt === 1) {
          this.encryptKeyCache = null;
          await this.syncSessionKey();
          await $.wait(3000, 5000);
        } else {
          return;
        }
      }
    }
    if (!questions.length) {
      $.log(`账号[${this.index}] 每日一答无题目`);
      return;
    }
    for (const q of questions) {
      const id = q.id;
      const answer = q.answer;
      if (!id || !answer) continue;
      try {
        await this.encryptedPost("/garden/Gardenquestiontask/answerResultsJph", {
          itemid: id,
          selected: answer,
        });
        $.log(`✅ 账号[${this.index}] 每日一答完成: ${shortJson(q.title || id, 40)}`);
      } catch (e) {
        $.log(`账号[${this.index}] 每日一答提交失败: ${e.message || e}`);
      }
      await $.wait(20000, 25000);
    }
  }

  async doRealityTask() {
    try {
      try {
        await this.gardenGet("/garden/notice/realScene");
      } catch {}
      await this.gardenGet("/garden/realscene/reward");
      $.log(`✅ 账号[${this.index}] 实景相册任务完成`);
    } catch (e) {
      $.log(`账号[${this.index}] 实景相册任务失败: ${e.message || e}`);
    }
  }

  async doCompleteInfoTask() {
    try {
      await this.gardenGet("/garden/tasks/checkCompleteMemberInfo");
      $.log(`✅ 账号[${this.index}] 完善信息任务已检查`);
    } catch (e) {
      $.log(`账号[${this.index}] 完善信息任务失败: ${e.message || e}`);
    }
  }

  async doSubscribePrize() {
    try {
      await this.gardenGet("/garden/tasks/getSubscribePrize");
      $.log(`✅ 账号[${this.index}] 订阅奖励已领取`);
    } catch (e) {
      $.log(`账号[${this.index}] 订阅奖励失败: ${e.message || e}`);
    }
  }

  async doTasks(tasks) {
    const pending = tasks.filter((task) => !isCompleted(task));
    if (!pending.length) {
      $.log(`账号[${this.index}] 暂无未完成任务`);
      return; 
    }
    for (const task of pending) {
      const code = task.code || "";
      if (code === "answer_survey") await this.doQuestionTask();
      else if (code === "garden_share") await this.doShareTask();
      else if (code === "view_organic_sorghum") await this.doRealityTask();
      else if (code === "complete_member_info") await this.doCompleteInfoTask();
      else if (/subscribe/i.test(code)) await this.doSubscribePrize();
      else $.log(`账号[${this.index}] 未适配任务: ${task.name || code || task.id}`);
      await $.wait(20000, 25000);
    }
  }

  buildNotifyMessage() {
    const m = this.member || {};
    const lands = this.lastLands || [];
    const landLines = lands.length
      ? lands.map((land) => {
          const name = landStatus(land) <= 0 ? "未种植" : `${cropName(land)}*${land.volumn || 0}`;
          const cropTime = land.crop_time ? `，收获时间:${land.crop_time}` : "";
          return `  地块${land.serial_number ?? land.id ?? "?"}: ${landStatusText(land)}，${name}${cropTime}`;
        })
      : ["  未查询到土地"];

    const exchangeLines = this.exchangeSummary ? [this.exchangeSummary] : [];

    return [
      `【${this.remark}】`,
      `账号: ${mask(this.openid || this.cacheKey)}`,
      `昵称: ${m.nick_name || m.nickname || "未知"}`,
      `会员ID: ${m.member_id || "未知"}`,
      `积分: ${m.integration ?? "未知"}`,
      `水滴: ${m.water ?? 0}，有机肥: ${m.manure ?? 0}，高粱: ${m.sorghum ?? 0}，小麦: ${m.wheat ?? 0}，酒曲: ${m.wine_yeast ?? 0}，酒滴: ${m.wine ?? 0}`,
      ...exchangeLines,
      `土地: ${lands.length}块`,
      ...landLines,
    ].join("\n");
  }

  async run() {
    $.log(`\n========== ${this.remark} ==========`);
    
    await this.ensureValidLogin();
    await this.queryMember();

    // 对齐小程序真实加载顺序: checkSessionKey -> sorghum/index -> getMemberInfo -> dailySign -> tasks
    await this.checkSessionKey();
    try {
      await this.queryFarm();
    } catch (e) {
      $.log(`账号[${this.index}] 预热地块查询失败: ${e.message || e}`);
    }
    
    const todayDate = new Date(Date.now() + 8 * 3600 * 1000).toISOString().split('T')[0];
    const cacheData = this.getCached();

    if (cacheData.lastSignDate === todayDate) {
      $.log(`✅ 账号[${this.index}] 今日已签到`);
    } else {
      const randomDelay = Math.floor(Math.random() * 5000) + 10000;
      await $.wait(randomDelay, randomDelay);
      
      await this.verifyCaptcha();
      await $.wait(2000, 5000); 

      await this.dailySign();
      
      this.saveCache({ lastSignDate: todayDate });
      
      await $.wait(20000, 25000);
    }

    const tasks = await this.queryTasks();
    await this.doTasks(tasks);

    await this.queryFriendToken();

    await this.queryWineStatus();
    await this.runWineAutomation();
    await this.exchangeWine();

    await this.runFarmAutomation();

    await this.queryMember();
    await this.queryFarm();
    notifyMsgs.push(this.buildNotifyMessage());
  }
}

!(async () => {
  $.checkEnv(ckName);
  if (!$.userCount) return;
  for (let i = 0; i < $.userList.length; i++) {
    const account = $.userList[i];
    const task = new Task(account);
    try {
      await task.run();
    } catch (e) {
      $.log(`账号执行失败: ${e.message || e}`);
      notifyMsgs.push(`【${task.remark}】\n执行失败: ${e.message || e}`);
    }
    
    // 如果不是最后一个账号，则随机等待 10-20 秒
    if (i < $.userList.length - 1) {
      const waitTime = Math.floor(Math.random() * 11) + 10;
      $.log(`等待 ${waitTime} 秒后执行下个账号`);
      await $.wait(waitTime * 1000, waitTime * 1000);
    }
  }
})()
  .catch((e) => {
    $.log(`脚本异常: ${e.message || e}`);
    notifyMsgs.push(`脚本异常: ${e.message || e}`);
  })
  .finally(async () => {
    if (notifyMsgs.length) {
      const content = notifyMsgs.join("\n\n");
      let pushed = false;

      // ① 企业微信优先（与其它脚本统一）
      const qywxKey = String(process.env.QYWX_KEY || "").trim();
      if (qywxKey) {
        try {
          // 企业微信文本上限 2048 字节，按 3500 字符切段
          const text = `${$.name}\n${content}`;
          for (let i = 0; i < text.length; i += 3500) {
            await require("axios").post(
              `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=${qywxKey}`,
              { msgtype: "text", text: { content: text.slice(i, i + 3500) } },
              { timeout: 15000 }
            );
          }
          $.log("✅ 企业微信推送成功");
          pushed = true;
        } catch (e) {
          $.log(`⚠️ 企业微信推送失败: ${e.message || e}`);
        }
      }

      // ② 回落青龙 sendNotify
      if (!pushed) {
        if (sendNotify) {
          try {
            await sendNotify($.name, content);
            $.log("✅ 青龙推送已发送");
            pushed = true;
          } catch (e) {
            $.log(`青龙推送失败: ${e.message || e}`);
          }
        } else {
          $.log("未配置 QYWX_KEY，也没有青龙 sendNotify，跳过推送");
        }
      }

      // ③ 都没成功就打日志，至少青龙里能看见
      if (!pushed) $.log(`\n${content}`);
    }
    $.done && $.done();
  });
