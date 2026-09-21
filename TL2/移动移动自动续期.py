#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
移动云盘自动签到 v5.0.6

包含以下功能:
1. 每日自动签到 (签到/抽奖/摇一摇/新版云朵领取)
2. 多账号并发支持 (环境变量 & 分隔)
3. 云朵中心新版任务自动处理 (上传/分享/AI相机/月任务补传等)
4. 临时文件智能清理与详细日志推送

更新说明:

### 20260531
v5.0.6:
- 修复云朵中心任务列表（重构为 POST JSON 模式）。

### 20260503
v5.0.5:
- 修复分享文件任务流程。
- 新增去体验AI相机任务。
- 新增五一福利任务。

### 20260425
v5.0.4:
- 整合 Authorization 自动刷新。
- 新增账号级 deviceId/token 缓存。
- 优化 JWT 失败重试、设备信息与启动提示。

### 20260420
v5.0.3:
- 适配新版签到链路，修复旧接口报错。
- 接入云朵中心新版任务。
- 新增 AI 相机、上传清理、分享文件每日自动化与月上传补传能力。
- 优化启动与任务日志输出。

配置说明:
变量名: ydyp
赋值方式:
1. 抓包 authTokenRefresh.do 请求头 Authorization 值。
2. 填入青龙环境变量，格式：Authorization值#手机号 (多账号用 & 分隔)。

变量名: ydyp_device_id 或 YDYP_DEVICE_ID
用途: 签到和领取云朵优先使用的 deviceId

抓包方式:
1. 打开移动云盘 APP 进入云朵中心。
2. 搜索 startSignIn、receiveV2 或 infoV3 请求。
3. 复制请求头 deviceId 值，或复制 Cookie 中 .thumbcache_* 的原值。
4. 填入 ydyp_device_id；脚本会自动兼容请求头值和 .thumbcache_* 原值。

deviceId 配置说明:
1. 已填写 ydyp_device_id 或 YDYP_DEVICE_ID 时，优先使用环境变量。
2. 未填写环境变量时，脚本会按手机号读取或创建 ydyp_device_ids.json 账号记录。
3. 缓存中已填写 deviceId 时优先复用；缓存为空时自动生成并保存 deviceId。

⚠️ 依赖安装:
pip3 install requests pycryptodome

定时规则建议 (Cron):
0 0 8,16,20 * * * (每日早中晚执行)

Author: YaoHuo8648
Email: zheyizzf@188.com
Update: 2026.05.31
"""

import base64
import hashlib
import json
import os
import random
import re
import time
import uuid
from datetime import datetime, timezone, timedelta
from os import path
from pathlib import Path
from urllib.parse import unquote

import requests

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
except ImportError:
    AES = None
    pad = None

SCRIPT_VERSION = '5.0.6'

TOKEN_STORAGE_FILENAME = 'ydyp_token_storage.json'
DEVICE_ID_STORAGE_FILENAME = 'ydyp_device_ids.json'  # 简易 deviceId 存储：手机号 -> deviceId

# ⭐ 统一设备信息：iPhone 16 Pro + iOS 18_7 + 版本 12.5.4（抓包真实值）
ua = ''
market_ua = ''
cloud_file_dummy_content = b'0'
cloud_file_dummy_hash = hashlib.sha256(cloud_file_dummy_content).hexdigest()
TOKEN_VALID_TIME = 21600000
TOKEN_REFRESH_ADVANCE = 24 * 60 * 60 * 1000
TOKEN_REFRESH_COOLDOWN = 864000
REFRESH_TOKEN_AES_KEY = 'c7lXOigXahPnTViq'
AI_TOOL_ACCOUNT_AES_KEY = 'xuL97!x7GGxG%8V4'
AI_TOOL_ACCOUNT_AES_IV = '5OuCxk4XNu0NA*%x'
TOKEN_EXPIRE_SECONDS_FALLBACK = 2592000

err_accounts = ''  # 异常账号
all_logs = ''      # 所有用户的详细运行日志 (原 err_message)
user_amount = ''   # 用户云朵·数量
GLOBAL_DEBUG = False
