/**
 * 平安 i 动 · 签到 + 饮食打卡 + litta 训练课程（YYB 全自动）
 *
 * 【变量】
 *   yyb_gpt = YYB地址#ref        多账号 & 或换行
 *
 * 【流程】
 *   1. YYB 取 wx.login code（appid wx340f915763f3ed2b）
 *   2. RSA 加密 → 平台 login → AES 解密拿 authToken/signKey/degradeToken/userInfo
 *   3. 6 头签名 → 换 i动商户 openId/openToken
 *   4. 服务端时间校准 → 会话等级检测
 *   5. 步数签到（先查状态，签完领奖）
 *   6. 饮食打卡（uploadText → batchRecordFoodLog → claimReward 三步）
 *   7. litta 训练课程（上报课时长 ≥180s → 领 10 健康币）
 *   8. 余额汇总 + 推送
 *
 * 【litta lk-sign 算法】（参考 YYB-GO-Script-i/平安i运动code8.2）
 *   SHA1(sortedLkHeaders + "secret=jqwei2uhEbcvaxDoi8hHs19&data=" + JSON.stringify(body)).toUpperCase()
 *   lk-ssotoken 不参与签名（签名前删除、签名后恢复）
 *
 * 【推送】企业微信 QYWX_KEY，60 秒超时
 */

const crypto = require('crypto')
const https = require('https')
const http = require('http')
process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0'   // 允许自签证书（YYB 内网）
const fs = require('fs')
const path = require('path')

// ============ 常量 ============
const MINI_APPID = 'wx340f915763f3ed2b'
const MERCHANT_CODE = 'b25d710fdfa2494ebb2cb74e90df72e7'
const AES_LOGIN_KEY = '123456789aasdfgh'
const RSA_PUB = '-----BEGIN PUBLIC KEY-----\n' +
  'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAnu5GnR0rVJgYmBlTLit8cSAs1BljgtlW20wV7wf5rYVYZKHCbq/L82NQ7Wga5bS6HUn4Jm30CJQL4z4SIDhkStSWrMdhdtSrR2WiP26xrYUa/B1+u/y5JaYWJje94TAKeHBtJITNfjG4BGLmZiSZImv3BksxmGGp2Tx/E6ClGislCmhmAOjEed+vl42CaJZ9WANK2owJ/zaEub1d4LyNo+BfEyewxI9UcelA10pFe7mV93VuQFA4W0caKFyOeJzhPwU54rMQmsheNGd0wF6NkWKoUvWa5LTetonrdd6HGN92B+KOt1I3D7I8SXaqcN4KIQKhSd0jRiqFkHw3GPwBOQIDAQAB\n' +
  '-----END PUBLIC KEY-----'

const PLATFORM_HOST = 'platform.lifeapp.pingan.com.cn'
const INCUBATOR_HOST = 'incubator.lifeapp.pingan.com.cn'

// litta（第三方课程平台）
const LITTA_HOST = 'api.litta.cn'
const LITTA_SECRET = 'jqwei2uhEbcvaxDoi8hHs19'
const LITTA_BASE = '/lens/conduct/h5/rpc/v2'
const TRAIN_CLASS_ID = (process.env.PAYD_CLASS_ID || '2')
const TRAIN_DURATION_TARGET = Number(process.env.PAYD_TARGET_SEC) || 185
const TRAIN_INTERVAL = Number(process.env.PAYD_TRAIN_INTERVAL) || 5000

const UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) AppleWebKit/605.1.15 ' +
  '(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.76(0x18004c3a) NetType/WIFI Language/zh_CN miniProgram/' + MINI_APPID

const CACHE_FILE = path.join(__dirname, 'idong_cache.json')

let TIME_OFFSET = 0
const nowMs = () => Date.now() + TIME_OFFSET

// ============ 工具 ============
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const rand = (a, b) => a + Math.floor(Math.random() * (b - a + 1))
const genHex16 = () => Array.from({ length: 16 }, () => '0123456789abcdef'[Math.floor(Math.random() * 16)]).join('')
const nowTime = () => new Date(Date.now() + 8 * 3600e3).toISOString().substr(0, 19).replace('T', ' ')
const today = () => new Date(nowMs() + 8 * 3600e3).toISOString().substr(0, 10)

function toQuery(o) {
  return Object.keys(o || {}).map((k) =>
    `${encodeURIComponent(k)}=${encodeURIComponent(o[k] == null ? '' : o[k])}`).join('&')
}

function req(url, { method = 'GET', headers = {}, body = null, timeout = 25000 } = {}) {
  return new Promise((resolve, reject) => {
    const u = new URL(url)
    const h = Object.assign({}, headers)
    if (body) h['Content-Length'] = Buffer.byteLength(body)
    const isHttps = u.protocol === 'https:'
    const lib = isHttps ? require('https') : require('http')
    const r = lib.request({
      hostname: u.hostname,
      port: u.port || (isHttps ? 443 : 80),
      path: u.pathname + u.search,
      method, headers: h, timeout,
      rejectUnauthorized: false,   // ★ YYB 常用自签证书
      servername: u.hostname
    }, (res) => {
      const chunks = []
      res.on('data', (c) => chunks.push(c))
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks).toString('utf8') }))
    })
    r.on('error', reject)
    r.on('timeout', () => r.destroy(new Error('请求超时')))
    if (body) r.write(body)
    r.end()
  })
}

function loadCache() {
  try { return JSON.parse(fs.readFileSync(CACHE_FILE, 'utf8')) } catch (e) { return {} }
}
function saveCache(c) {
  try { fs.writeFileSync(CACHE_FILE, JSON.stringify(c, null, 2)) } catch (e) { }
}

// ============ 加解密 ============
function rsaEncrypt(plain) {
  return crypto.publicEncrypt({ key: RSA_PUB, padding: crypto.constants.RSA_PKCS1_PADDING },
    Buffer.from(plain)).toString('base64')
}
function aesDecrypt(cipherB64, key, iv) {
  const k = Buffer.from(key, 'utf8').slice(0, 16)
  const v = Buffer.from(iv, 'utf8').slice(0, 16)
  const d = crypto.createDecipheriv('aes-128-cbc', k, v)
  return Buffer.concat([d.update(Buffer.from(cipherB64, 'base64')), d.final()]).toString('utf8')
}
function aesEncryptB64(plain, key, iv) {
  // key 截断到 16 字节（degradeToken 可能超长）
  const k = Buffer.from(key, 'utf8').slice(0, 16)
  const v = Buffer.from(iv, 'utf8').slice(0, 16)
  const c = crypto.createCipheriv('aes-128-cbc', k, v)
  return Buffer.concat([c.update(Buffer.from(plain, 'utf8')), c.final()]).toString('base64')
}
function sha256Base64(s) {
  return crypto.createHash('sha256').update(s, 'utf8').digest('base64')
}
/** X-Sign：url(去协议) + & + 排序后 k=v& + &reqBody= + body + &signKey= + key */
function platformSign(signKey, urlNoProto, reqBody, hdrs) {
  const kv = Object.keys(hdrs).sort().map((k) => `${k}=${hdrs[k]}`).join('&')
  const s = `${urlNoProto}&${kv}&reqBody=${reqBody || ''}&signKey=${signKey || ''}`
  return sha256Base64(s)
}
function encryptedUserId(degradeToken, userInfo) {
  return aesEncryptB64(`${userInfo}_${genHex16()}`, degradeToken, AES_LOGIN_KEY)
}

// ============ YYB 取码 ============
async function yybGetCode(yybUrl, ref) {
  let last = ''
  for (let i = 0; i < 3; i++) {
    try {
      const r = await req(`${String(yybUrl).replace(/\/+$/, '')}/wxapp/getCode`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ref: String(ref), app_id: MINI_APPID }),
        timeout: 40000
      })
      const j = JSON.parse(r.body)
      if (j.code === 0 || j.code === '0') {
        const inner = j.result || (j.data && j.data.result) || j.data
        const code = inner && inner.code
        if (code) return code
        last = 'YYB 未返回 code'
      } else {
        last = String(j.msg || j.error || '').slice(0, 100)
        // 熔断
        const d = j.data || {}
        if (d.circuit_open) last = `熔断 ${d.retry_after || '?'}s`
      }
    } catch (e) {
      last = String(e.message || e).slice(0, 100)
    }
    if (i < 2) {
      const wait = /429|熔断|circuit/.test(last) ? 35000 : 8000
      console.log(`   ⏳ YYB 取码失败(${last})，${wait / 1000} 秒后重试`)
      await sleep(wait)
    }
  }
  throw new Error('YYB 取码失败: ' + last)
}

// ============ 登录 ============
async function platformLogin(wxCode) {
  const randomKey = genHex16()
  // ★ 原文是 `${wxCode}+${randomKey}`，中间有加号
  const rsaResult = rsaEncrypt(`${wxCode}+${randomKey}`)
  const body = toQuery({ rsaResult, keyVersion: '2026' })
  const r = await req(`https://${PLATFORM_HOST}/user-session/external/wxMiniProgram/login`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'X-AppId': MINI_APPID,
      'X-Timestamp': Date.now().toString(),
      'X-B3-TraceId': genHex16(),
      'X-ReqID': genHex16(),
      'User-Agent': UA
    },
    body
  })
  const j = JSON.parse(r.body)
  if (j.CODE !== '00' || !j.DATA || !j.DATA.tokenAesResult) {
    throw new Error('平台登录失败: ' + r.body.slice(0, 200))
  }
  // ★ 原文：aesDecrypt(tokenAesResult, randomKey, AES_LOGIN_KEY) —— key=randomKey, iv=AES_LOGIN_KEY
  const plain = aesDecrypt(j.DATA.tokenAesResult, randomKey, AES_LOGIN_KEY)
  const parts = plain.split('#')
  const authToken = parts[0]
  const signKey = parts[1]
  const degradeToken = parts[2]
  const userInfo = parts.slice(3).join('#')
  if (!authToken || !signKey || !degradeToken || !userInfo) throw new Error('token 解密失败')
  return { authToken, signKey, degradeToken, userInfo }
}

// ============ 平安平台 POST ============
async function platformPost(tk, host, apiPath, params) {
  const body = params === undefined || params === null ? ''
    : (typeof params === 'string' ? params : toQuery(params))
  const v = {
    'X-AppId': MINI_APPID,
    'X-Timestamp': String(nowMs()),
    'X-B3-TraceId': genHex16(),
    'X-ReqID': genHex16(),
    'X-Token': tk.authToken,
    'X-EncryptedUserID': encryptedUserId(tk.degradeToken, tk.userInfo)
  }
  v['X-Sign'] = platformSign(tk.signKey, `${host}${apiPath}`, body, v)
  const headers = Object.assign({
    'Content-Type': 'application/x-www-form-urlencoded',
    charset: 'utf-8',
    Referer: `https://servicewechat.com/${MINI_APPID}/52/page-frame.html`,
    'X-OS-Type': '04', 'X-Source': '3', 'X-CV': '60800', 'User-Agent': UA
  }, v)
  const r = await req(`https://${host}${apiPath}`, { method: 'POST', headers, body })
  let j
  try { j = JSON.parse(r.body) } catch (e) { throw new Error(`${apiPath} 响应异常: ${r.body.slice(0, 120)}`) }
  if (j.CODE !== '00') {
    const err = new Error(`${apiPath} 失败: ${j.MSG || ''} ${JSON.stringify(j).slice(0, 200)}`)
    err.code = j.CODE
    err.raw = j
    throw err
  }
  return j.DATA
}

// JSON body 版本
async function platformPostJson(tk, host, apiPath, obj) {
  const body = JSON.stringify(obj || {})
  const v = {
    'X-AppId': MINI_APPID,
    'X-Timestamp': String(nowMs()),
    'X-B3-TraceId': genHex16(),
    'X-ReqID': genHex16(),
    'X-Token': tk.authToken,
    'X-EncryptedUserID': encryptedUserId(tk.degradeToken, tk.userInfo)
  }
  v['X-Sign'] = platformSign(tk.signKey, `${host}${apiPath}`, body, v)
  const headers = Object.assign({
    'Content-Type': 'application/json', charset: 'utf-8',
    Referer: `https://servicewechat.com/${MINI_APPID}/52/page-frame.html`,
    'X-OS-Type': '04', 'X-Source': '3', 'X-CV': '60800', 'User-Agent': UA
  }, v)
  const r = await req(`https://${host}${apiPath}`, { method: 'POST', headers, body })
  const j = JSON.parse(r.body)
  if (j.CODE !== '00') {
    const err = new Error(`${apiPath} 失败: ${j.MSG || ''} ${JSON.stringify(j).slice(0, 200)}`)
    err.code = j.CODE; err.raw = j
    throw err
  }
  return j.DATA
}

// ============ 服务端时间校准 ============
async function syncServerTime(tk) {
  try {
    const body = ''
    const v = {
      'X-AppId': MINI_APPID, 'X-Timestamp': String(Date.now()),
      'X-B3-TraceId': genHex16(), 'X-ReqID': genHex16(),
      'X-Token': tk.authToken,
      'X-EncryptedUserID': encryptedUserId(tk.degradeToken, tk.userInfo)
    }
    const p = '/health-core/ledong/home/getMyPageInfo'
    v['X-Sign'] = platformSign(tk.signKey, `${INCUBATOR_HOST}${p}`, body, v)
    const r = await req(`https://${INCUBATOR_HOST}${p}`, {
      method: 'POST',
      headers: Object.assign({ 'Content-Type': 'application/x-www-form-urlencoded', charset: 'utf-8', 'X-OS-Type': '04', 'X-Source': '3', 'X-CV': '60800', 'User-Agent': UA }, v),
      body
    })
    const dt = r.headers['date']
    if (dt) {
      const server = new Date(dt).getTime()
      const off = server - Date.now()
      if (Math.abs(off) > 1000) { TIME_OFFSET = off; return off }
    }
    return 0
  } catch (e) { return 0 }
}

// ============ 会话等级 ============
function sessionLevel(tk) {
  if (!tk.degradeToken || tk.degradeToken.length < 20) return { level: 'DEGRADED', degraded: true }
  if (!tk.signKey || tk.signKey.length < 10) return { level: 'DEGRADED', degraded: true }
  return { level: 'FULL', degraded: false }
}

// ============ 商户 openId ============
async function getMerchantOpenId(tk) {
  const d = await platformPost(tk, PLATFORM_HOST, '/open-platform/wxMiniProgram/merchant/getOpenId',
    { merchantCode: MERCHANT_CODE })
  return d || {}
}

// ============ 步数签到 ============
async function queryStepPage(tk) {
  try {
    const d = await platformPost(tk, INCUBATOR_HOST,
      '/health-core/ledong/healthStep/queryWxStepPageInfo', { displayChannel: 'MINI_PROGRAM' })
    return d || {}
  } catch (e) { return null }
}

function todaySignedInfo(page) {
  const p = (page && page.userTaskPeriod) || {}
  const recs = (page && page.dayPrizeMap) || {}
  return {
    consecutive: p.consecutiveCount,
    status: p.status,
    signed: String(p.status || '').toUpperCase() === 'SIGNED' || p.signToday === true,
    raw: p
  }
}

async function claimRewards(tk, ids) {
  const list = (ids || []).filter(Boolean)
  if (!list.length) return false
  const d = await platformPostJson(tk, INCUBATOR_HOST,
    '/health-core/ledong/equityTask/claimRewards', { rewardIds: list })
  return !(d && Number(d.failCount) > 0)
}

async function doStepSign(tk) {
  const page = await queryStepPage(tk)
  const info = todaySignedInfo(page)
  if (info.signed) {
    return { state: 'already', msg: `今日已签到（连续 ${info.consecutive || '?'} 天）` }
  }

  const d = await platformPost(tk, INCUBATOR_HOST,
    '/health-core/ledong/healthStep/step/completeJgjStepSignInTask', null)
  const recs = (d && d.userRewardRecords) || []
  const pend = recs.filter((x) => String(x.rewardStatus || '').toUpperCase() === 'PENDING')
  const ids = pend.map((x) => x.rewardId || x.id).filter(Boolean)
  const total = pend.reduce((s, x) => s + (Number(x.rewardValue) || 0), 0)

  if (!ids.length) {
    return { state: 'ok', msg: '签到成功（无待领奖励）', gain: 0 }
  }
  await sleep(rand(1200, 3500))
  const done = await claimRewards(tk, ids)
  return {
    state: done ? 'ok' : 'partial',
    msg: done ? `签到成功并领奖 +${total} 币` : `签到成功但领奖失败（待领 ${total} 币）`,
    gain: done ? total : 0
  }
}

// ============ 饮食打卡 ============
const FOODS = [
  '全麦面包一片', '无糖酸奶200ml', '水煮蛋两个', '苹果一个', '鸡胸肉100克',
  '糙米饭一碗', '西兰花150克', '牛奶250ml', '香蕉一根', '燕麦粥一碗',
  '清蒸鱼100克', '番茄两个', '豆浆300ml', '坚果一小把', '紫薯100克'
]
const MEALS = ['nightSnack', 'afternoonSnack', 'breakfast', 'lunch', 'dinner']

function pickFood() {
  return {
    food: FOODS[Math.floor(Math.random() * FOODS.length)],
    meal: MEALS[0]  // nightSnack（抓包验证过的值）
  }
}

/**
 * 饮食打卡（实测链路 2026-11-15）：
 *   ① uploadText             只分析，返回 analysisId
 *   ② getLittaDietResult     查分析结果（CODE=41019105 表示"分析中"，需轮询）
 *   ③ batchRecordFoodLog     JSON 写入，返回 rewards[].rewardId
 *   ④ equityTask/claimRewards 领奖
 */
async function doDietCheck(tk) {
  const date = today()
  const { food, meal } = pickFood()
  const notes = []

  // ① 分析文本
  let analysisId = null
  try {
    const r1 = await platformPost(tk, INCUBATOR_HOST, '/health-core/healthDietV2/uploadText', {
      dietLog: food, useDate: date, mealType: meal
    })
    analysisId = r1 && r1.analysisId
    if (!analysisId) return { state: 'fail', msg: '第①步未返回 analysisId' }
    notes.push('分析已提交')
  } catch (e) {
    return { state: 'fail', msg: `第①步 uploadText 失败: ${String(e.message).slice(0, 60)}` }
  }

  // ② 轮询分析结果（最多 8 次，每次 3 秒）
  let parsed = null
  for (let i = 0; i < 8; i++) {
    await sleep(i === 0 ? 1500 : 3000)
    try {
      const r2 = await platformPost(tk, INCUBATOR_HOST,
        '/health-core/healthDietV2/getLittaDietResult', { analysisId })
      if (r2 && Array.isArray(r2.foods) && r2.foods.length) { parsed = r2; break }
    } catch (e) {
      // CODE=41019105 会抛错，继续轮询
      if (!/41019105|分析中/.test(String(e.message))) {
        return { state: 'fail', msg: `第②步查分析结果失败: ${String(e.message).slice(0, 60)}` }
      }
    }
  }
  if (!parsed) return { state: 'fail', msg: '食材分析超时（8 次轮询未出结果）' }
  notes.push(`解析 ${parsed.foods.length} 项`)

  // ③ 写入饮食记录
  let rewards = []
  try {
    const payload = {
      useDate: date,
      mealType: parsed.mealType || meal,
      analysisId,
      foods: parsed.foods.map((x) => ({
        foodName: x.foodName,
        eatAmount: String(x.eatAmount || '1.0'),
        unit: x.unit || '份',
        carbs: String(x.carbs || '0'),
        protein: String(x.protein || '0'),
        fat: String(x.fat || '0'),
        kcal: String(x.kcal || '0')
      }))
    }
    const r3 = await platformPostJson(tk, INCUBATOR_HOST,
      '/health-core/healthDietV2/batchRecordFoodLog', payload)
    rewards = (r3 && r3.rewards) || []
    notes.push(`写入 ${r3 && r3.recordCount ? r3.recordCount : 1} 条`)
  } catch (e) {
    return { state: 'fail', msg: `第③步 batchRecordFoodLog 失败: ${String(e.message).slice(0, 80)}` }
  }

  // ④ 领奖
  const pend = rewards.filter((x) => String(x.rewardStatus || '').toUpperCase() === 'PENDING')
  const ids = pend.map((x) => x.rewardId).filter(Boolean)
  const total = pend.reduce((s, x) => s + (Number(x.rewardValue) || 0), 0)
  let rewardMsg = ''
  if (ids.length) {
    await sleep(rand(1200, 2500))
    try {
      const done = await claimRewards(tk, ids)
      rewardMsg = done ? ` +${total}币` : ` 待领${total}币(领奖失败)`
    } catch (e) {
      rewardMsg = ` 领奖异常(${String(e.message).slice(0, 30)})`
    }
  } else {
    rewardMsg = ' 无待领奖励'
  }

  // ⑤ 复核
  await sleep(rand(800, 1500))
  let verified = ''
  try {
    const log = await platformPost(tk, INCUBATOR_HOST, '/health-core/healthDietV2/getLog', { useDate: date })
    const diet = (log && log.diet) || []
    const issued = log && log.checkInRewardIssuedToday
    verified = diet.length ? ` 记录${diet.length}条` : ' ⚠️记录为空'
    if (issued !== undefined) verified += issued ? ' 奖励已发' : ' 奖励未发'
  } catch (e) { }

  return { state: 'ok', msg: `饮食打卡「${food}」${rewardMsg}${verified}`, notes: notes.join(' / ') }
}

// ============ litta 训练课程（参数全部来自参考脚本实测）============
/** lk-sign: SHA1(排序后 lk-* 头 + "secret=xxx&data=" + JSON.stringify(data)).toUpperCase()
 *  参与签名的头：所有 lk- 开头，排除 lk-ssotoken 和 lk-sign 本身 */
function generateLkSign(headers, data) {
  const lkKeys = Object.keys(headers)
    .filter((k) => /^lk-/.test(k) && k !== 'lk-ssotoken' && k !== 'lk-sign')
    .sort()
  let str = ''
  for (const k of lkKeys) str += `${k}=${headers[k]}&`
  str += `secret=${LITTA_SECRET}&data=${JSON.stringify(data)}`
  return crypto.createHash('sha1').update(str, 'utf8').digest('hex').toUpperCase()
}

async function littaApiCall(rpcMethod, data, ssotoken) {
  const ts = Date.now().toString()
  const headers = {
    'Content-Type': 'application/json',
    'lk-appkey': 'wxProgram',
    'lk-appv': '5.2.1',
    'lk-mpkey': 'live',
    'lk-protocol': '1.0.2',
    'lk-tenantid': '3',
    'lk-timestamp': ts,
    'lt-request-id': ts.substring(0, 16),
    'charset': 'utf-8'
  }
  if (ssotoken) headers['lk-ssotoken'] = ssotoken
  headers['lk-sign'] = generateLkSign(headers, data)
  try {
    const r = await req(`https://${LITTA_HOST}${LITTA_BASE}/${rpcMethod}`, {
      method: 'POST', headers, body: JSON.stringify(data)
    })
    try { return JSON.parse(r.body) } catch (e) { return { _raw: r.body.slice(0, 200), _status: r.status } }
  } catch (e) {
    return { _error: String(e.message) }
  }
}

/** 课程详情：courseId → {courseIdent, courseType, courseDuration} */
async function getCalculatorTrainDetail(userId, ssotoken, courseId) {
  const res = await littaApiCall(
    'com.litta.ptrainer.api.front.calculator.TrainingCalculatorApi/getCalculatorTrainDetail',
    {
      requestData: {
        application: 'lk-live-applet', platform: 'WeChat', userId,
        aiUserSettingsModel: { trainingMode: 8 },
        courseId: String(courseId),
        passingParamsModel: { fromUserAdjustMode: false },
        channelCode: '4773379059'
      }, userId: ''
    }, ssotoken)
  if (res.code !== 0 || !res.data || !res.data.calculatorCourseInfoModel) {
    throw new Error(`getCalculatorTrainDetail 失败: ${res.msg || JSON.stringify(res).slice(0, 120)}`)
  }
  const m = res.data.calculatorCourseInfoModel
  let courseDuration = 0
  const st = res.data.aiUserSettingsModel
  if (st && st.optionTrainingModeList) {
    const mode = st.optionTrainingModeList.find((x) => String(x.id) === String(st.selectedTrainingMode))
    if (mode && mode.value) courseDuration = Number(mode.value) * 60
  }
  return { courseIdent: m.courseIdent, courseType: m.courseType, courseDuration }
}

/** 按目标时长生成 recordDuration 递增序列（真实抓包序列等比归一化） */
function buildDurationSequence(target) {
  const baseSeq = [1, 3, 4, 7, 11, 31, 36, 40, 60, 69, 73, 98, 110, 114, 139, 151, 155, 180, 182, 183]
  const seq = []
  let prev = 0
  for (const b of baseSeq) {
    const v = Math.round(b * target / 183)
    if (v > prev) { seq.push(v); prev = v }
  }
  if (seq[seq.length - 1] !== target) seq.push(target)
  return seq
}

/** 平安 getUserPlanV2：查体态计划任务，找未完成的 LITTA 任务拿 classId */
async function getUserPlanV2(tk) {
  const d = await platformPostJson(tk, INCUBATOR_HOST,
    '/health-core/healthPlan/getUserPlanV2', { displayChannel: 'MINI_PROGRAM' })
  return d
}

function findPendingPlanTask(planData) {
  if (!planData || !planData.taskInfo || !planData.taskInfo.taskList) return null
  for (const week of planData.taskInfo.taskList) {
    for (const t of (week.tasks || [])) {
      if (t.categoryType !== 'TASK' || t.eventType !== 'LITTA') continue
      if ((t.completionCount || 0) > 0) continue
      let ext = t.extInfo
      if (typeof ext === 'string') { try { ext = JSON.parse(ext) } catch (e) { ext = {} } }
      if (ext && ext.classId) {
        return { classId: String(ext.classId), taskName: t.displayTitle || t.taskName }
      }
    }
  }
  return null
}

function extractClaimablePlanRewards(planData) {
  const out = []
  if (!planData || !planData.taskInfo || !planData.taskInfo.taskList) return out
  for (const week of planData.taskInfo.taskList) {
    for (const t of (week.tasks || [])) {
      if (t.categoryType !== 'TASK' || !t.rewardId) continue
      if (String(t.rewardCoinStatus) === '1') {
        out.push({ rewardId: String(t.rewardId), name: t.displayTitle || t.taskName, value: t.rewardCoinValue })
      }
    }
  }
  return out
}

/** 平安 getClassAndRewardInfo：上报训练完成 */
async function getClassAndRewardInfo(tk, openId, businessId) {
  try {
    const d = await platformPostJson(tk, INCUBATOR_HOST,
      '/health-core/ledong/class/getClassAndRewardInfo',
      { openId, businessId, achievedFlag: false })
    return d || null
  } catch (e) {
    return null
  }
}

/**
 * 完整训练课程流程
 *   ① getMerchantOpenId → openId
 *   ② litta loginWithReg(openId) → userId + ssotoken
 *   ③ getCalculatorTrainDetail(classId) → courseIdent/courseType
 *   ④ appointClass → roomId（冲突则 cancelUserSelfTrainingCourse 后重试）
 *   ⑤ upClassRoom(roomId)
 *   ⑥ recordDuration × N（累计到 targetDuration）
 *   ⑦ downClassRoom → businessId
 *   ⑧ 平安 getClassAndRewardInfo → 触发发奖
 *   ⑨ getUserPlanV2 → 取 rewardId → claimReward
 */
async function doLittaTrain(tk, mo) {
  const notes = []
  const openId = (mo && mo.openId) || ''
  if (!openId) return { state: 'skip', msg: '缺少商户 openId' }

  // ① 找计划任务拿 classId
  let classId = TRAIN_CLASS_ID
  try {
    const plan = await getUserPlanV2(tk)
    const pend = findPendingPlanTask(plan)
    if (pend) {
      classId = pend.classId
      notes.push(`任务「${pend.taskName}」classId=${classId}`)
    } else {
      notes.push(`无待做课程任务，用默认 classId=${classId}`)
    }
  } catch (e) { notes.push('取计划失败，用默认课程') }

  // ② litta 登录
  const loginRes = await littaApiCall(
    'com.litta.user.api.front.UserExternalChannelMaApi/loginWithReg',
    { userId: '', requestData: { openId, application: 'lk-live-applet', platform: 'WeChat' } })
  if (loginRes.code !== 0 || !loginRes.data || !loginRes.data.token) {
    return { state: 'fail', msg: `litta 登录失败: ${loginRes.msg || JSON.stringify(loginRes).slice(0, 80)}`, notes: notes.join(' / ') }
  }
  const userId = loginRes.data.userId
  const ssotoken = loginRes.data.token
  notes.push(`litta userId=${userId}`)

  // ③ 课程详情
  const detail = await getCalculatorTrainDetail(userId, ssotoken, classId)
  const targetDuration = detail.courseDuration || TRAIN_DURATION_TARGET
  notes.push(`courseIdent=${String(detail.courseIdent).slice(0, 10)}… 目标 ${targetDuration}s`)

  // ④ 预约
  const appointReq = {
    application: 'lk-live-applet', platform: 'WeChat', userId,
    courseType: detail.courseType, courseIdent: detail.courseIdent,
    source: 1, operator: userId, trainingMode: '8', userCourseTrainingMethods: 3
  }
  const APPOINT_API = 'com.litta.ptrainer.api.front.calculator.TrainingCalculatorApi/appointClass'
  let appointRes = await littaApiCall(APPOINT_API, { requestData: appointReq, userId: '' }, ssotoken)
  if (appointRes.code !== 0 || !appointRes.data || !appointRes.data.roomId) {
    notes.push('预约冲突，结束遗留课程后重试')
    await littaApiCall('com.litta.ptrainer.api.front.SelfCourseApi/cancelUserSelfTrainingCourse',
      { requestData: {}, userId: '' }, ssotoken)
    await sleep(rand(1500, 3500))
    appointRes = await littaApiCall(APPOINT_API, { requestData: appointReq, userId: '' }, ssotoken)
  }
  if (appointRes.code !== 0 || !appointRes.data || !appointRes.data.roomId) {
    return { state: 'fail', msg: `预约课程失败: ${appointRes.msg || JSON.stringify(appointRes).slice(0, 80)}`, notes: notes.join(' / ') }
  }
  const roomId = String(appointRes.data.roomId)

  // ⑤ 进教室
  const upRes = await littaApiCall('com.litta.ptrainer.api.front.LiveRoomApi/upClassRoom',
    { requestData: { roomId, videoRecordMode: 1, application: 'lk-live-applet', platform: 'WeChat' }, userId: '' },
    ssotoken)
  if (upRes.code !== 0) {
    return { state: 'fail', msg: `进教室失败: ${upRes.msg || ''}`, notes: notes.join(' / ') }
  }

  // ⑥ 上报时长
  const seq = buildDurationSequence(targetDuration)
  let fails = 0
  for (let i = 0; i < seq.length; i++) {
    const r = await littaApiCall('com.litta.ptrainer.api.front.SelfCourseApi/recordDuration',
      { requestData: { roomId, duration: seq[i], application: 'lk-live-applet', platform: 'WeChat' }, userId: '' },
      ssotoken)
    if (r.code !== 0) fails++
    if (i < seq.length - 1) {
      await sleep(Math.round(TRAIN_INTERVAL * (0.7 + Math.random() * 0.7)))
    }
  }
  notes.push(`上报 ${seq.length} 次${fails ? `（${fails}次失败）` : ''}`)

  // ⑦ 下课
  const downRes = await littaApiCall('com.litta.ptrainer.api.front.LiveRoomApi/downClassRoom',
    {
      requestData: {
        classDuration: targetDuration, roomId, type: 2, videoRecordMode: 1,
        lastActionSnapshotComplete: false, classInTrainingMethods: 3,
        source: 'litta_plugin_pingan', application: 'lk-live-applet', platform: 'WeChat'
      }, userId: ''
    }, ssotoken)
  if (downRes.code !== 0) {
    return { state: 'fail', msg: `下课失败: ${downRes.msg || ''}`, notes: notes.join(' / ') }
  }
  const businessId = downRes.data && downRes.data.businessId
  notes.push(`businessId=${String(businessId).slice(0, 12)}…`)

  // ⑧ 平安上报完成
  await sleep(rand(1500, 3000))
  await getClassAndRewardInfo(tk, openId, businessId)

  // ⑨ 领奖
  await sleep(rand(1500, 3000))
  let gain = 0
  try {
    const plan2 = await getUserPlanV2(tk)
    const rewards = extractClaimablePlanRewards(plan2)
    if (rewards.length) {
      const ok = await claimRewards(tk, rewards.map((x) => x.rewardId))
      gain = rewards.reduce((s, x) => s + (Number(x.value) || 0), 0)
      notes.push(ok ? `领奖 +${gain}币` : '领奖失败')
    } else {
      notes.push('暂无可领奖励')
    }
  } catch (e) {
    notes.push(`领奖异常: ${String(e.message).slice(0, 30)}`)
  }

  return {
    state: 'ok',
    msg: `训练完成 ${targetDuration}s${gain ? ` +${gain}币` : ''}`,
    notes: notes.join(' / ')
  }
}

// ============ 任务表 / 奖励汇总 ============
/** 任务列表 + 余额（主入口） */
async function getTaskListAndBalance(tk) {
  try {
    const d = await platformPost(tk, INCUBATOR_HOST,
      '/health-core/external/ledong/equity/getTaskListAndBalance',
      { displayChannel: 'MINI_PROGRAM' })
    return d || null
  } catch (e) { return null }
}

/** 首页任务列表（自由训练 FREE_EXERCISE 在这里） */
async function getHomeTaskList(tk) {
  try {
    const d = await platformPost(tk, INCUBATOR_HOST, '/health-core/ledong/homeV2/getTaskList',
      { displayChannel: 'MINI_PROGRAM', stepAuthorized: 'Y' })
    return d || null
  } catch (e) { return null }
}

async function getUserRewardsSummary(tk) {
  try {
    const d = await platformPost(tk, INCUBATOR_HOST,
      '/health-core/ledong/equity/getUserRewardsSummary', { displayChannel: 'MINI_PROGRAM' })
    return d || null
  } catch (e) { return null }
}

async function queryWxStepSignInStatus(tk) {
  try {
    const d = await platformPost(tk, INCUBATOR_HOST,
      '/health-core/ledong/healthStep/step/queryWxStepSignInStatus', { displayChannel: 'MINI_PROGRAM' })
    return d || null
  } catch (e) { return null }
}

async function queryAddWechatTask(tk) {
  try {
    const d = await platformPost(tk, INCUBATOR_HOST,
      '/health-core/ledong/healthStep/queryAddWechatTask', { displayChannel: 'MINI_PROGRAM' })
    return d || null
  } catch (e) { return null }
}

/** 单个领奖（训练课/盲盒用这个，不是批量那个） */
async function claimReward(tk, rewardId) {
  try {
    const d = await platformPostJson(tk, INCUBATOR_HOST,
      '/health-core/healthEquity/claimReward', { rewardId })
    return d || null
  } catch (e) { return null }
}

/** 场景奖励查询 + 自动领 PENDING（FREE_EXERCISE / DAILY_SPORT_BOX / 训练课） */
async function getSceneRewardInfo(tk, openId, businessId, eventType, label) {
  try {
    const d = await platformPostJson(tk, INCUBATOR_HOST,
      '/health-core/ledong/class/getClassAndRewardInfoV2',
      { openId, businessId, achievedFlag: false, eventType })
    const data = d || {}
    const pending = (data.rewardList || []).filter((x) =>
      String(x.rewardStatus || '').toUpperCase() === 'PENDING')
    const ids = pending.map((x) => x.id || x.rewardId).filter(Boolean)
    const total = pending.reduce((s, x) => s + (Number(x.rewardValue) || 0), 0)
    if (ids.length) {
      // 先用单个领奖接口逐个领，失败的再用批量兜底
      let claimed = 0
      for (const id of ids) {
        const r = await claimReward(tk, id)
        if (r) claimed++
        await sleep(rand(600, 1200))
      }
      if (claimed === 0) {
        const ok = await claimRewards(tk, ids)
        if (ok) claimed = ids.length
      }
      return { status: data.status, claimed, total }
    }
    return { status: data.status, claimed: 0, total }
  } catch (e) {
    return { status: '', claimed: 0, total: 0, err: String(e.message).slice(0, 40) }
  }
}

// ============ 运动盲盒 / 自由训练 ============
/** 开盲盒：返回 {classId, name, duration(分钟)} */
async function openSportBox(tk) {
  try {
    const d = await platformPost(tk, INCUBATOR_HOST, '/health-core/dailySportBox/random', null)
    if (!d || !d.classId) return null
    return {
      classId: String(d.classId),
      name: d.className || '盲盒课程',
      duration: Number(d.duration) || 0,
      reward: d.rewardValue
    }
  } catch (e) { return null }
}

async function getFreeExerciseClass(tk) {
  try {
    const d = await platformPostJson(tk, INCUBATOR_HOST,
      '/health-core/external/healthService/class/list',
      { pageNo: 1, pageSize: 10, category: 'FEATURED', difficulties: [], durationRanges: [] })
    const list = (d && d.list) || []
    const c = list.find((x) => x && x.classId)
    return c ? { classId: String(c.classId), name: c.className || '自由训练课程' } : null
  } catch (e) { return null }
}

/**
 * 内容课通用流程（自由训练 / 运动盲盒）
 *   loginWithReg(platform: litta_plugin) → startContentClass → reportContentClassProgress × N → finishContentClass
 */
async function completeSceneExercise(tk, openId, courseId, scene, label, targetDuration, interval) {
  const notes = []
  const loginRes = await littaApiCall(
    'com.litta.user.api.front.UserExternalChannelMaApi/loginWithReg',
    {
      userId: '', requestData: {
        openId, channelCode: '4773379059',
        application: 'lk-live-applet', platform: 'litta_plugin'
      }
    })
  if (!loginRes || loginRes.code !== 0 || !loginRes.data || !loginRes.data.token) {
    return { state: 'fail', msg: `${label} litta 登录失败: ${(loginRes && loginRes.msg) || ''}`, notes: '' }
  }
  const userId = loginRes.data.userId
  const ssotoken = loginRes.data.token
  const common = { application: 'lk-live-applet', platform: 'litta_plugin' }

  const startRes = await littaApiCall(
    'com.litta.ptrainer.api.front.content.ContentClassApi/startContentClass',
    {
      requestData: Object.assign({}, common, {
        userId, openId, courseId: Number(courseId), channelCode: '4773379059',
        extraParams: encodeURIComponent(JSON.stringify({ scene }))
      })
    }, ssotoken)
  if (!startRes || startRes.code !== 0 || !startRes.data || !startRes.data.recordId) {
    return { state: 'fail', msg: `${label} 开始失败: ${(startRes && startRes.msg) || ''}`, notes: '' }
  }
  const recordId = startRes.data.recordId
  notes.push(`recordId=${String(recordId).slice(0, 10)}…`)

  const seq = buildDurationSequence(targetDuration)
  for (let i = 0; i < seq.length; i++) {
    const d = seq[i]
    const pr = await littaApiCall(
      'com.litta.ptrainer.api.front.content.ContentClassApi/reportContentClassProgress',
      {
        requestData: Object.assign({}, common, {
          recordId, userId, currentPosition: d - 1, trainingDuration: d,
          source: 'litta_plugin_pingan', sourceCourseType: 'RECORD_COURSE'
        })
      }, ssotoken)
    if (!pr || pr.code !== 0) {
      return { state: 'fail', msg: `${label} 上报 ${d}s 失败: ${(pr && pr.msg) || ''}`, notes: notes.join(' / ') }
    }
    if (i < seq.length - 1) {
      await sleep(Math.round(interval * (0.7 + Math.random() * 0.7)))
    }
  }
  notes.push(`上报 ${seq.length} 次`)

  const finishRes = await littaApiCall(
    'com.litta.ptrainer.api.front.content.ContentClassApi/finishContentClass',
    {
      requestData: Object.assign({}, common, {
        recordId, userId, courseId: Number(courseId), trainingDuration: targetDuration,
        finalPosition: targetDuration - 1, contentDuration: targetDuration, isCompleted: true,
        source: 'litta_plugin_pingan', sourceCourseType: 'RECORD_COURSE'
      })
    }, ssotoken)
  if (!finishRes || finishRes.code !== 0 || !finishRes.data || !finishRes.data.businessId) {
    return { state: 'fail', msg: `${label} 完成失败: ${(finishRes && finishRes.msg) || ''}`, notes: notes.join(' / ') }
  }
  const businessId = finishRes.data.businessId

  await sleep(rand(1500, 3000))
  const rw = await getSceneRewardInfo(tk, openId, businessId, scene, label)
  notes.push(rw.claimed ? `领奖 ${rw.claimed} 项 +${rw.total}币` : '无待领奖励')

  return {
    state: 'ok',
    msg: `${label}完成 ${targetDuration}s${rw.claimed ? ` +${rw.total}币` : ''}`,
    notes: notes.join(' / ')
  }
}

// ============ 余额 ============

async function getBalance(tk) {
  try {
    const d = await platformPost(tk, INCUBATOR_HOST, '/health-core/ledong/home/getMyPageInfo', {})
    return d || {}
  } catch (e) { return null }
}

// ============ 推送 ============
async function sendPush(title, content, timeout = 60) {
  console.log(`\n【通知】${title}\n${content}`)
  const key = (process.env.QYWX_KEY || '').trim()
  if (!key) { console.log('⚠️ 未配置 QYWX_KEY，跳过推送'); return }

  const doSend = async () => {
    const body = { msgtype: 'text', text: { content: `🔔 ${title}\n${'-'.repeat(20)}\n${content}` } }
    const r = await req(`https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=${key}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), timeout: 20000
    })
    const j = JSON.parse(r.body)
    if (j.errcode !== 0) throw new Error(j.errmsg)
    return true
  }

  try {
    const p = doSend()
    const t = new Promise((_, rej) => setTimeout(() => rej(new Error('推送超时')), timeout * 1000))
    await Promise.race([p, t])
    console.log('✅ 推送已发送')
  } catch (e) {
    console.log(`⚠️ 推送失败: ${String(e.message).slice(0, 60)}`)
  }
}

// ============ 账号解析 ============
function parseAccounts(raw) {
  const list = []
  const norm = String(raw || '')
    .replace(/＃/g, '#')          // 全角井号
    .replace(/\r/g, '')
    .replace(/\n/g, '&')
  norm.split('&').map((x) => x.trim()).filter(Boolean).forEach((seg) => {
    // 按 # 分段，找出 http 开头那段作为地址，紧随其后的数字作为 ref
    const parts = seg.split('#').map((x) => x.trim())
    const hi = parts.findIndex((p) => /^https?:/i.test(p))
    let url = '', ref = '', remark = ''
    if (hi >= 0) {
      url = parts[hi].replace(/\/+$/, '')
      ref = (parts[hi + 1] || '').replace(/[^0-9]/g, '')
      remark = (parts[0] && !/^https?:/i.test(parts[0])) ? parts[0] : ''
    } else {
      // 没有 http 前缀的简写：地址#ref 或 备注#地址#ref
      const i = seg.lastIndexOf('#')
      if (i > 0) {
        url = seg.slice(0, i).trim().replace(/\/+$/, '')
        ref = seg.slice(i + 1).replace(/[^0-9]/g, '')
      }
    }
    if (url && ref) {
      list.push({ remark: remark || ('账号' + ref), yybUrl: url, ref })
    } else {
      console.log(`   ⚠️ 账号串格式错误已跳过: ${seg.slice(0, 50)}`)
    }
  })
  return list
}

// ============ 主流程 ============
async function main() {
  const raw = (process.env.yyb_gpt || '').trim()
  if (!raw) { console.log('❌ 未设置 yyb_gpt，格式：YYB地址#ref'); return }
  const accounts = parseAccounts(raw)
  if (!accounts.length) { console.log('❌ yyb_gpt 格式错误'); return }

  console.log('='.repeat(52))
  console.log('🏃 平安 i 动 · 签到 + 饮食打卡 + 训练课程')
  console.log('='.repeat(52))
  console.log(`🕒 ${nowTime()} ｜ 小程序 ${MINI_APPID}`)
  console.log(`📦 账号数 ${accounts.length}`)
  console.log('='.repeat(52))

  const reports = []
  for (let i = 0; i < accounts.length; i++) {
    const acc = accounts[i]
    console.log(`\n[${i + 1}/${accounts.length}] ${acc.remark}`)
    const lines = [`👤 ${acc.remark}`]
    try {
      const code = await yybGetCode(acc.yybUrl, acc.ref)
      console.log('   ✅ YYB 取码成功')
      const tk = await platformLogin(code)
      console.log('   ✅ 平台登录成功')
      const mo = await getMerchantOpenId(tk)
      console.log(`   ✅ openId=${String(mo.openId || '').slice(0, 8)}…`)

      const off = await syncServerTime(tk)
      console.log(Math.abs(off) > 3000 ? `   ⏰ 校准 ${off}ms` : '   ⏰ 时钟正常')

      const lv = sessionLevel(tk)
      console.log(`   🪪 会话 ${lv.level}`)
      lines.push(`🪪 会话：${lv.level}`)
      await sleep(1200)

      // ① 步数签到
      try {
        const r = await doStepSign(tk)
        console.log('   📝 ' + r.msg)
        lines.push('📝 ' + r.msg)
      } catch (e) {
        console.log(`   ⚠️ 签到：${String(e.message).slice(0, 80)}`)
        lines.push('📝 签到：⚠️ ' + String(e.message).slice(0, 50))
      }
      await sleep(1800)

      // ② 饮食打卡
      try {
        const r = await doDietCheck(tk)
        console.log('   🍚 ' + r.msg + (r.notes ? `（${r.notes}）` : ''))
        lines.push('🍚 ' + r.msg)
      } catch (e) {
        console.log(`   ⚠️ 饮食：${String(e.message).slice(0, 80)}`)
        lines.push('🍚 饮食：⚠️ ' + String(e.message).slice(0, 50))
      }
      await sleep(1800)

      // ③ litta 训练（会话降级跳过）
      if (lv.degraded) {
        console.log('   ⏭️ 训练课程：会话降级，跳过')
        lines.push('🏃 训练：⏭️ 会话降级跳过')
      } else {
        try {
          const r = await doLittaTrain(tk, mo)
          console.log('   🏃 ' + r.msg)
          lines.push('🏃 ' + r.msg)
        } catch (e) {
          console.log(`   ⚠️ 训练：${String(e.message).slice(0, 80)}`)
          lines.push('🏃 训练：⚠️ ' + String(e.message).slice(0, 50))
        }
      }
      await sleep(1500)

      // ④ 运动盲盒
      if (lv.degraded) {
        console.log('   ⏭️ 运动盲盒：会话降级，跳过')
        lines.push('🎲 盲盒：⏭️ 会话降级跳过')
      } else if (String(process.env.PAYD_SKIP_BOX || '') === '1') {
        console.log('   ⏭️ 运动盲盒：PAYD_SKIP_BOX=1，跳过')
      } else {
        try {
          const box = await openSportBox(tk)
          if (!box) {
            console.log('   ⚠️ 盲盒：未开出课程')
            lines.push('🎲 盲盒：⚠️ 未开出课程')
          } else {
            console.log(`   🎲 开盲盒：${box.name}，${box.duration} 分钟`)
            const dur = Math.max(300, (box.duration || 0) * 60)
            const r = await completeSceneExercise(tk, String(mo.openId || ''), box.classId,
              'DAILY_SPORT_BOX', '运动盲盒', dur, 1000)
            console.log('   🎲 ' + r.msg + (r.notes ? `（${r.notes}）` : ''))
            lines.push('🎲 ' + r.msg)
          }
        } catch (e) {
          console.log(`   ⚠️ 盲盒：${String(e.message).slice(0, 80)}`)
          lines.push('🎲 盲盒：⚠️ ' + String(e.message).slice(0, 50))
        }
      }
      await sleep(1500)

      // ⑤ 自由训练
      if (lv.degraded) {
        console.log('   ⏭️ 自由训练：会话降级，跳过')
        lines.push('🏋️ 自由训练：⏭️ 会话降级跳过')
      } else if (String(process.env.PAYD_SKIP_FREE || '') === '1') {
        console.log('   ⏭️ 自由训练：PAYD_SKIP_FREE=1，跳过')
      } else {
        try {
          const fc = await getFreeExerciseClass(tk)
          if (!fc) {
            console.log('   ⚠️ 自由训练：无推荐课程')
            lines.push('🏋️ 自由训练：⚠️ 无课程')
          } else {
            console.log(`   🏋️ 自由训练：${fc.name}`)
            const dur = Math.max(185, Number(process.env.PAYD_FREE_SEC) || 185)
            const r = await completeSceneExercise(tk, String(mo.openId || ''), fc.classId,
              'FREE_EXERCISE', '自由训练', dur, 1000)
            console.log('   🏋️ ' + r.msg + (r.notes ? `（${r.notes}）` : ''))
            lines.push('🏋️ ' + r.msg)
          }
        } catch (e) {
          console.log(`   ⚠️ 自由训练：${String(e.message).slice(0, 80)}`)
          lines.push('🏋️ 自由训练：⚠️ ' + String(e.message).slice(0, 50))
        }
      }
      await sleep(1500)

      // ⑥ 余额
      const bal = await getBalance(tk)
      const b = bal && (bal.balance !== undefined ? bal.balance : bal.coin)
      if (b !== undefined) {
        console.log(`   💰 健康币 ${b}`)
        lines.push(`💰 健康币 ${b}`)
      }
      saveCache(Object.assign(loadCache(), { [acc.ref]: { time: nowTime() } }))
    } catch (e) {
      console.log(`   ❌ ${String(e.message).slice(0, 120)}`)
      lines.push('❌ ' + String(e.message).slice(0, 80))
    }
    reports.push(lines.join('\n'))
    if (i < accounts.length - 1) await sleep(rand(3000, 6000))
  }

  console.log(`\n全部完成 ${nowTime()}`)
  await sendPush('平安i动 · 每日任务', reports.join('\n\n'))
}

if (require.main === module) main().catch((e) => console.log('执行异常: ' + e.message))
module.exports = { platformLogin, getMerchantOpenId, doStepSign, doDietCheck, doLittaTrain }
