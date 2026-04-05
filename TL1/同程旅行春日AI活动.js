/*
 * 使用说明：
 * 1.同程旅行春日AI活动
 * 2.入口：#小程序://同程旅行/DeepTrip/iMhugdyOwjcBjsu
 * 3. 环境变量名称：tclx_gpt
 * 4. 变量格式：备注#unionid#wechatSecToken#地区#cookie(多个账号之间用 & 符号分隔)
 * 3. 抓包关键点：目标域名：dtgw.ly.com
 * 4. 定时：cron 30 9 * * * (每天上午 9:30 运行)
 */
const axios = require('axios');
const crypto = require('crypto');
// --- 自动加载通知模块 ---
let sendNotify = null;
try {
    sendNotify = require('./sendNotify');
} catch (e) {
    console.log('⚠️ 未找到 sendNotify.js，仅控制台日志输出');
}
const log = msg => console.log(`[${new Date().toLocaleTimeString()}] ${msg}`);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const CONFIG = {
    ACTIVITY_ID: "2cade612be7e4840b9f7abe0c8844996", 
    PHASE_ID: "eff975a54c804d7096be81fab6dcb231",
    APPID: "wx1b9209e76e943ce6",
    UA: "Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.61(0x18003d39) NetType/WIFI Language/zh_CN miniProgram/wx336dcaf6a1ecf632"
};
function getHeaders(acc) {
    return {
        'Host': 'dtgw.ly.com',
        'appid': CONFIG.APPID,
        'wechatSecToken': acc.token.trim(),
        'unionid': acc.unionid.trim(),
        'dt-channel': 'H5',
        'platId': '10888',
        'business': '100072',
        'User-Agent': CONFIG.UA,
        'Content-Type': 'application/json;charset=UTF-8',
        'Referer': 'https://deeptrip.ly.com/',
        'Origin': 'https://deeptrip.ly.com',
        'Cookie': acc.cookie.trim(),
        'SessionToken': 'undefined',
        'dtSource': 'h5_wechat',
        'token': '629dc5bc574bd7001d858698',
        'Accept': 'application/json, text/plain, */*'
    };
}
async function runAccount(acc) {
    let detail = `【账号：${acc.remark}】\n`;
    log(`👤 [${acc.remark}] 开始执行深度任务...`);
    try {
        // 1. 获取活动详情
        const res = await axios.post('https://dtgw.ly.com/deeptrip/marketing/activity/common/detail', 
            {"activityType": "AI_SPRING_TRIP"}, { headers: getHeaders(acc), timeout: 10000 });
        if (res.data && res.data.code === "0") {
            const data = res.data.data;
            log(`   ✅ 登录成功！剩余抽奖次数: ${data.lotteryNum}`);
            // 2. 固定执行 2 次 AI 提问任务 (每日必做)
            const tasks = [
                data.dailyTaskSugs ? data.dailyTaskSugs[0] : "[DeepTrip_春日游] 帮我制定旅游计划",
                data.searchTicketTaskSugs ? data.searchTicketTaskSugs[0] : "[DeepTrip_春日游] 查一下机票"
            ];
            for (let [index, q] of tasks.entries()) {
                log(`   ∟ 执行 AI 任务 ${index + 1}: ${q.slice(0, 10)}...`);
                await axios.post('https://dtgw.ly.com/deeptrip/chat/hotel_chat', {
                    "q": q, "sid": crypto.randomUUID(),
                    "loc": { "address": acc.address, "city": "广州市", "province": "广东省" },
                    "flight_create_front_param": { "type": "springTravel" }
                }, { headers: { ...getHeaders(acc), 'version': '20260402' } });
                await sleep(4000); 
            }
            detail += `✨ 每日 2 次 AI 提问任务已完成\n`;
            // 3. 抽奖逻辑
            log(`   🎁 正在执行抽奖...`);
            let results = [];
            for (let i = 0; i < 5; i++) {
                const lRes = await axios.post('https://dtgw.ly.com/deeptrip/marketing/activity/qiushi-lottery/lottery', 
                    { "bizActivityId": CONFIG.ACTIVITY_ID, "phaseId": CONFIG.PHASE_ID }, 
                    { headers: getHeaders(acc) });
                
                if (lRes.data.code === "0") {
                    const prize = lRes.data.data.prizeTitle;
                    log(`   ∟ 抽到: ${prize}`);
                    results.push(prize);
                } else if (lRes.data.msg.includes("不足")) {
                    log(`   ∟ 抽奖结束: 次数已用完`);
                    break;
                }
                await sleep(3000);
            }
            detail += results.length > 0 ? `🎁 抽奖汇总: ${results.join('、')}\n` : `ℹ️ 今日暂无可用抽奖次数\n`;
        } else {
            log(`   ❌ 登录失败: ${res.data.msg}`);
            detail += `❌ 登录状态失效，请更新 Cookie\n`;
        }
    } catch (e) {
        log(`   💥 异常: ${e.message}`);
        detail += `💥 异常: 接口请求超时\n`;
    }
    return detail;
}
async function main() {
    const env = process.env.tclx_gpt;
    if (!env) return log("❌ 环境变量 tclx_gpt 没填！请参考开头说明配置。");
    const accounts = env.split('&').map(s => {
        const p = s.split('#');
        return { 
            remark: p[0], 
            unionid: p[1], 
            token: p[2], 
            address: p[3] || "广州市", 
            cookie: p[4] || "" 
        };
    }).filter(a => a.unionid && a.token);
    log(`🚀 Node.js 版春日游脚本启动 | 发现 ${accounts.length} 个账号`);
    let finalReport = "";
    for (let acc of accounts) {
        finalReport += await runAccount(acc) + "--------------------\n";
        await sleep(2000); 
    }
    if (sendNotify && finalReport) {
        await sendNotify.sendNotify("同程春日游 AI 任务简报", finalReport);
    }
    log("✨ 任务结束");
}
main();
