/*
 * 同程旅行学生卡现金签到 - 
 * 入口：#小程序://同程旅行/同程旅行/8sTpq4PySoZRSio
 * 变量名: tcqd_gpt
 * 抓包：wx.17u.cn域名下cookie
 * 格式: 备注#openid#unionid#sectoken
 * 定时：cron 16 6,16 * * * 每天两次以免遗漏
 */

const axios = require('axios');

let sendNotify = null;
try {
    sendNotify = require('./sendNotify');
} catch (e) {
    console.log('⚠️ 未找到 sendNotify.js，仅控制台日志输出');
}

const log = msg => console.log(`[${new Date().toLocaleTimeString()}] ${msg}`);

function getProgressBar(days) {
    const total = 35;
    const count = Math.min(Math.floor((days / total) * 10), 10);
    return "■".repeat(count) + "□".repeat(10 - count) + ` (${days}/${total}天)`;
}

function getNextGoal(days) {
    const stages = [
        { d: 3, m: "1.88-7.88" },
        { d: 7, m: "3.88-17.88" },
        { d: 15, m: "8.88-38.88" },
        { d: 20, m: "10.88-58.88" },
        { d: 35, m: "18.88-88.88" }
    ];
    for (let s of stages) {
        if (days < s.d) return `下一目标: ${s.d}天档 (${s.m}元) | 差 ${s.d - days} 天`;
    }
    return "🏆 已达成最高档位奖励！";
}

async function runTask(acc) {
    let detail = `【账号：${acc.remark}】\n`;
    log(`开始处理: ${acc.remark}`);
    
    const headers = {
        'Host': 'wx.17u.cn',
        'Content-Type': 'application/json;charset=UTF-8',
        'User-Agent': "Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.61(0x18003d39) NetType/WIFI Language/zh_CN miniProgram/wx336dcaf6a1ecf632",
        'openId': acc.openid,
        'userKey': acc.unionid,
        'TC-USER-TOKEN': acc.token,
        'TC-PLATFORM-CODE': 'WX_MP',
        'TC-OS-TYPE': '1'  // 🛠️ 补上这个关键校验，模拟iOS系统
    };

    try {
        const info = await axios.post('https://wx.17u.cn/platformflowpool/signTask/getTaskInfo', 
            { "actId": "sign:2026:0758" }, { headers, timeout: 10000 });

        if (info.data.code === 0) {
            const data = info.data.data;
            const days = data.calendarInfo.continueSignCount;
            const amount = data.helpDetail.totalAmount;
            const isSigned = data.calendarInfo.isTodayVisit === 1;

            log(`   ∟ 连签: ${days}天 | 累计: ${amount}元 | 状态: ${isSigned ? '已签' : '待签'}`);
            
            detail += `📊 连签进度: ${getProgressBar(days)}\n`;
            detail += `💰 累计现金: ${amount} 元\n`;
            detail += `🚩 ${getNextGoal(days)}\n`;

            if (isSigned) {
                detail += `✅ 今日状态: 已在卡位，稳起速中\n`;
            } else {
                const sign = await axios.post('https://wx.17u.cn/platformflowpool/signTask/actionVisit', 
                    { "userId": "SignTask_aa365011f44f4dba95d08ba0777d2145" }, { headers });
                
                if (sign.data.code === 0) {
                    log(`   ∟ 打卡成功！`);
                    detail += `✅ 今日状态: 打卡成功！\n`;
                } else {
                    log(`   ∟ 打卡失败: ${sign.data.msg}`);
                    detail += `❌ 打卡失败: ${sign.data.msg}\n`;
                }
            }
        } else {
            log(`   ∟ 接口报错: ${info.data.msg}`);
            detail += `❌ 查询失败: ${info.data.msg}\n`;
        }
    } catch (e) {
        log(`   ∟ 网络异常: ${e.message}`);
        detail += `💥 异常: ${e.message}\n`;
    }
    return detail;
}

async function main() {
    const env = process.env.tcqd_gpt;
    if (!env) return log("❌ 未找到变量 tcqd_gpt");

    const accounts = env.split('&').map(item => {
        const p = item.split('#');
        return {
            remark: p[0] || "未命名",
            openid: p[1] || "",
            unionid: p[2] || "",
            token: p[3] || ""
        };
    }).filter(a => a.openid && a.token);

    log(`🚀 详情全开版(校验补全)启动 | 账号: ${accounts.length}`);
    let finalReport = "";

    for (let acc of accounts) {
        finalReport += await runTask(acc) + "--------------------\n";
        await new Promise(r => setTimeout(r, 2000));
    }

    if (sendNotify && finalReport) {
        await sendNotify.sendNotify("同程现金💵连签🙋‍♀️", finalReport);
    }
    log("✨ 任务结束");
}

main();
