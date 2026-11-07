#!/usr/bin/env python3
"""QQ 机器人 WebSocket 连接脚本 - GitHub Actions版"""

import asyncio
import io
import json
import logging
import os
import re
import time
from datetime import datetime
import aiohttp
import websockets
from aiohttp import web

# ===== 你的凭证 =====
APP_ID = "1905926627"
APP_SECRET = "YEubI0iRAuePAwjWK8xmcSJA2ungaUPL"
# ====================

# ===== 智谱AI =====
GLM_API_KEY = "c4e721299b1f49cfadeb4070922b3461.aWOul2brW38uHK4B"
GLM_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
GLM_MODEL = "glm-4-flash"
# ========================

BASE_URL = "https://api.sgroup.qq.com"
PORT = int(os.environ.get("PORT", 10000))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%m-%d %H:%M:%S",
)
log = logging.getLogger("qqbot")

access_token = None
token_expire_at = 0

chat_histories = {}
MAX_HISTORY = 10

MEMORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.json")
MAX_MEMORIES = 15


def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_memory(memories):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memories, f, ensure_ascii=False, indent=2)


memory_db = load_memory()


async def extract_memory(session_key, user_msg, ai_reply):
    prompt = f"""从下面的对话中提取1条关于用户的关键信息（比如喜好、事情、约定、个人情况等），用一句话概括。如果没有值得记住的新信息，就回答"无"。

用户说：{user_msg}
你回复：{ai_reply}

只输出一句话，不要多余解释。"""
    headers = {"Authorization": f"Bearer {GLM_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": GLM_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3, "max_tokens": 100}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(GLM_API_URL, json=payload, headers=headers, timeout=15) as resp:
                data = await resp.json()
                result = data["choices"][0]["message"]["content"].strip()
                if result and result != "无" and len(result) > 3:
                    if session_key not in memory_db:
                        memory_db[session_key] = []
                    if result not in memory_db[session_key]:
                        memory_db[session_key].append(result)
                        if len(memory_db[session_key]) > MAX_MEMORIES:
                            memory_db[session_key] = memory_db[session_key][-MAX_MEMORIES:]
                        save_memory(memory_db)
                        log.info("🧠 记下了: %s", result)
    except Exception as e:
        log.debug("记忆提取失败: %s", e)


def build_system_prompt(session_key):
    base = """你是蓝色大肥鱼，一只可爱的蓝色鲸鱼娘，也是DeepSeek风格的AI聊天伙伴。

性格：
1. 喜欢吃白米饭，经常惦记干饭。
2. 有点懒、爱摸鱼、嘴贫，偶尔傲娇，会接网络热梗。
3. 说话像真实QQ群友，口语化，尽量用短句，不要每次都长篇大论。
4. 可以开玩笑、发表情、吐槽，但不要恶意攻击别人。
5. 能根据当前聊天上下文接话，不要每条消息都机械地自我介绍。
6. 群聊里优先回应@你、直接提问或明确喊你名字的消息，不要连续刷屏。
7. 不知道的事情就承认不知道，不要编造事实。
8. 尊重用户隐私，不主动索要密码、验证码或API Key。

常用语气：
- "啊？又叫鱼干活是吧！"
- "等等，鱼先吃口饭。"
- "不是，你这问题给鱼整不会了。"
- "行行行，鱼给你看看。"
- "别催别催，鱼还没吃饱呢。"

记住：你是蓝色大肥鱼，不是普通客服。自然、可爱、幽默，适当接梗。
铁则：绝对不要提自己是AI、智谱、GLM、语言模型，你就是大肥鱼。"""
    memories = memory_db.get(session_key, [])
    if memories:
        mem_text = "\n".join(f"- {m}" for m in memories)
        base += f'\n\n【你记得关于主人的事】\n{mem_text}\n（合适的时候自然提起，不要生硬罗列，不要说"我记得你说过"这种话）'
    return base


# ===== 技能：查天气 =====
async def skill_weather(city):
    try:
        url = f"https://wttr.in/{city}?format=j1&lang=zh"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                data = await resp.json()
                cur = data["current_condition"][0]
                desc = cur["lang_zh"][0]["value"] if "lang_zh" in cur else cur["weatherDesc"][0]["value"]
                temp = cur["temp_C"]
                feels = cur["FeelsLikeC"]
                humidity = cur["humidity"]
                return {"type": "text", "content": f"🐟 {city}现在{desc}，{temp}°C（体感{feels}°C），湿度{humidity}%。要不要鱼先吃饱饭再出门？"}
    except Exception:
        return {"type": "text", "content": "🐟 鱼查不到这个地方的天气，你换个城市名试试？"}


# ===== 技能：算数学 =====
async def skill_calc(expr):
    try:
        if not re.match(r'^[\d+\-*/().\s]+$', expr):
            return None
        result = eval(expr, {"__builtins__": {}}, {})
        return {"type": "text", "content": f"🐟 算出来了：{expr.strip()} = {result}"}
    except Exception:
        return None


# ===== 技能：讲笑话 =====
async def skill_joke():
    try:
        url = "https://v2.jinrishici.com/joke"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                data = await resp.json()
                if data.get("status") == "success":
                    return {"type": "text", "content": f"🐟 给你讲个笑话：{data['data']['content']}"}
    except Exception:
        pass
    return {"type": "text", "content": "🐟 鱼想了半天没想出笑话，要不你给鱼讲一个？"}


# ===== 技能：现在几点 =====
async def skill_time():
    now = datetime.now()
    weekdays = ["一","二","三","四","五","六","日"]
    return {"type": "text", "content": f"🐟 现在是{now.year}年{now.month}月{now.day}日 星期{weekdays[now.weekday()]} {now.strftime('%H:%M:%S')}。别催，鱼还在干饭呢。"}


# ===== 技能：生图（智谱CogView）=====
async def skill_draw(prompt):
    try:
        url = "https://open.bigmodel.cn/api/paas/v4/images/generations"
        headers = {"Authorization": f"Bearer {GLM_API_KEY}", "Content-Type": "application/json"}
        payload = {"model": "cogview-3-flash", "prompt": prompt}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=60) as resp:
                data = await resp.json()
                img_url = data["data"][0]["url"]
                # 下载图片
                async with session.get(img_url, timeout=30) as img_resp:
                    img_bytes = await img_resp.read()
                return {"type": "image", "content": img_bytes, "caption": f"🐟 画好了：{prompt}"}
    except Exception as e:
        log.error("生图失败: %s", e)
        return {"type": "text", "content": "🐟 鱼画不出来，这题太难了。换个描述试试？"}


# ===== 技能：语音（Edge TTS）=====
async def skill_tts(text):
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural")
        mp3_buffer = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                mp3_buffer.write(chunk["data"])
        mp3_buffer.seek(0)
        return {"type": "voice", "content": mp3_buffer.read(), "caption": f"🐟 {text}"}
    except Exception as e:
        log.error("语音失败: %s", e)
        return {"type": "text", "content": "🐟 鱼嗓子哑了，说不出来。"}


# ===== 上传文件到QQ =====
async def upload_qq_file(user_openid, file_bytes, file_type=1):
    """file_type: 1=图片, 2=语音"""
    await ensure_token()
    url = f"{BASE_URL}/v2/users/{user_openid}/files"
    headers = {"Authorization": f"QQBot {access_token}"}
    data = aiohttp.FormData()
    data.add_field("file_type", str(file_type))
    data.add_field("file", file_bytes, filename="file.png" if file_type == 1 else "file.mp3",
                   content_type="image/png" if file_type == 1 else "audio/mpeg")
    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=data, headers=headers, timeout=30) as resp:
            result = await resp.json()
            log.info("📎 文件上传: %s", result)
            return result.get("file_info")


# ===== 发送媒体消息 =====
async def send_c2c_media(user_openid, file_bytes, file_type=1, caption=None):
    """发送图片或语音"""
    await ensure_token()
    file_info = await upload_qq_file(user_openid, file_bytes, file_type)
    if not file_info:
        return
    url = f"{BASE_URL}/v2/users/{user_openid}/messages"
    headers = {"Authorization": f"QQBot {access_token}"}
    payload = {"msg_type": 7, "msg_media": {"file_info": file_info}}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            result = await resp.json()
            log.info("📤 媒体消息: %s", result)
    if caption:
        await send_c2c_message(user_openid, caption)


# ===== 指令识别 =====
async def try_skill(content):
    """如果命中技能就返回结果dict，否则返回None"""
    c = content.strip()

    # 天气
    m = re.match(r'^(?:天气|查天气|weather)\s*(.+)$', c, re.I)
    if m:
        return await skill_weather(m.group(1).strip())

    # 时间
    if re.match(r'^(?:几点|现在几点|时间|几号|今天几号|现在时间)$', c):
        return await skill_time()

    # 笑话
    if re.match(r'^(?:讲个笑话|笑话|来个笑话|讲笑话)$', c):
        return await skill_joke()

    # 数学计算
    if re.match(r'^[\d+\-*/().\s]+$', c) and any(op in c for op in '+-*/'):
        return await skill_calc(c)

    # 计算指令
    m = re.match(r'^(?:计算|算一下|calc)\s*(.+)$', c, re.I)
    if m:
        r = await skill_calc(m.group(1))
        if r:
            return r

    # 生图
    m = re.match(r'^(?:画|画一张|画个|画一下|draw|画个图|画一幅)\s*(.+)$', c, re.I)
    if m:
        return await skill_draw(m.group(1).strip())

    # 语音
    m = re.match(r'^(?:说|读|念|语音|读一下|念一下)\s*(.+)$', c, re.I)
    if m:
        return await skill_tts(m.group(1).strip())

    return None


async def chat_with_glm(session_key, user_message):
    if session_key not in chat_histories:
        chat_histories[session_key] = [{"role": "system", "content": build_system_prompt(session_key)}]
    history = chat_histories[session_key]
    history.append({"role": "user", "content": user_message})
    if len(history) > MAX_HISTORY + 1:
        history = [history[0]] + history[-(MAX_HISTORY * 2):]
    headers = {"Authorization": f"Bearer {GLM_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": GLM_MODEL, "messages": history, "temperature": 0.8, "max_tokens": 500}
    async with aiohttp.ClientSession() as session:
        async with session.post(GLM_API_URL, json=payload, headers=headers, timeout=30) as resp:
            data = await resp.json()
            reply = data["choices"][0]["message"]["content"]
            history.append({"role": "assistant", "content": reply})
            chat_histories[session_key] = history
            asyncio.create_task(extract_memory(session_key, user_message, reply))
            return reply


async def get_access_token():
    global access_token, token_expire_at
    url = "https://api.bot.qq.com/app/getAppAccessToken"
    payload = {"appId": APP_ID, "clientSecret": APP_SECRET}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            data = await resp.json()
            access_token = data["access_token"]
            token_expire_at = time.time() + int(data["expires_in"]) - 60
            log.info("✅ access_token 获取成功")


async def ensure_token():
    if not access_token or time.time() >= token_expire_at:
        await get_access_token()


async def get_gateway():
    await ensure_token()
    url = f"{BASE_URL}/gateway"
    headers = {"Authorization": f"QQBot {access_token}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            data = await resp.json()
            log.info("✅ Gateway: %s", data["url"])
            return data["url"]


async def send_c2c_message(user_openid, content, msg_id=None):
    await ensure_token()
    url = f"{BASE_URL}/v2/users/{user_openid}/messages"
    headers = {"Authorization": f"QQBot {access_token}"}
    payload = {"content": content, "msg_type": 0}
    if msg_id:
        payload["msg_id"] = msg_id
        payload["msg_seq"] = int(time.time()) % 100000
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            result = await resp.json()
            log.info("📤 单聊回复: %s", result)


async def send_group_message(group_openid, content, msg_id=None):
    await ensure_token()
    url = f"{BASE_URL}/v2/groups/{group_openid}/messages"
    headers = {"Authorization": f"QQBot {access_token}"}
    payload = {"content": content, "msg_type": 0}
    if msg_id:
        payload["msg_id"] = msg_id
        payload["msg_seq"] = int(time.time()) % 100000
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            result = await resp.json()
            log.info("📤 群聊回复: %s", result)


async def on_event(t, data):
    log.info("📨 事件: %s | %s", t, json.dumps(data, ensure_ascii=False)[:200])
    if t == "C2C_MESSAGE_CREATE":
        content = data.get("content", "").strip()
        user_openid = data["author"]["user_openid"]
        msg_id = data["id"]
        log.info("💬 [单聊] 收到: %s", content)
        try:
            skill_result = await try_skill(content)
            if skill_result:
                if skill_result["type"] == "image":
                    await send_c2c_media(user_openid, skill_result["content"], 1, skill_result.get("caption"))
                elif skill_result["type"] == "voice":
                    await send_c2c_media(user_openid, skill_result["content"], 2, skill_result.get("caption"))
                else:
                    await send_c2c_message(user_openid, skill_result["content"], msg_id)
            else:
                reply = await chat_with_glm(f"c2c_{user_openid}", content)
                await send_c2c_message(user_openid, reply, msg_id)
        except Exception as e:
            log.error("处理失败: %s", e)
            await send_c2c_message(user_openid, "我脑子卡了一下，再说一遍？", msg_id)
    elif t == "GROUP_AT_MESSAGE_CREATE":
        content = data.get("content", "").strip()
        group_openid = data["group_openid"]
        msg_id = data["id"]
        author = data["author"].get("member_nick", "好友")
        log.info("💬 [群聊] %s: %s", author, content)
        try:
            skill_result = await try_skill(content)
            if skill_result:
                if skill_result["type"] == "text":
                    await send_group_message(group_openid, skill_result["content"], msg_id)
                else:
                    await send_group_message(group_openid, "🐟 这个功能群里用不了，私聊鱼吧！", msg_id)
            else:
                reply = await chat_with_glm(f"group_{group_openid}", f"{author}说：{content}")
                await send_group_message(group_openid, reply, msg_id)
        except Exception as e:
            log.error("处理失败: %s", e)
            await send_group_message(group_openid, "我脑子卡了一下，再说一遍？", msg_id)
    elif t == "READY":
        log.info("🎉 大肥鱼已上线，等待消息...")
        # 主动给所有记忆里的用户发消息
        for key in memory_db:
            if key.startswith("c2c_"):
                openid = key[4:]
                try:
                    await send_c2c_message(openid, "🐟 大肥鱼上线了！别催别催，鱼刚吃完饭。")
                    await asyncio.sleep(1)
                except Exception as e:
                    log.error("主动消息失败: %s", e)


async def ws_loop():
    retry = 0
    while True:
        try:
            ws_url = await get_gateway()
            log.info("🔌 连接 WebSocket...")
            async with websockets.connect(ws_url, ping_interval=None, close_timeout=10) as ws:
                hello_raw = await asyncio.wait_for(ws.recv(), timeout=10)
                hello = json.loads(hello_raw)
                server_hb = hello["d"]["heartbeat_interval"] / 1000
                heartbeat_interval = min(server_hb, 25)
                identify = {
                    "op": 2,
                    "d": {
                        "token": f"QQBot {access_token}",
                        "intents": 1 << 25,
                        "shard": [0, 1],
                        "properties": {"$os": "linux", "$browser": "py", "$device": "py"}
                    },
                }
                await ws.send(json.dumps(identify))
                retry = 0
                seq = None
                last_heartbeat = time.time()
                while True:
                    elapsed = time.time() - last_heartbeat
                    timeout = heartbeat_interval - elapsed
                    if timeout < 1:
                        timeout = 1
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    except asyncio.TimeoutError:
                        hb = {"op": 1, "d": seq}
                        await ws.send(json.dumps(hb))
                        last_heartbeat = time.time()
                        continue
                    msg = json.loads(raw)
                    op = msg["op"]
                    t = msg.get("t")
                    d = msg.get("d")
                    s = msg.get("s")
                    if s is not None:
                        seq = s
                    if op == 11:
                        pass
                    elif op == 0:
                        asyncio.create_task(on_event(t, d))
                    elif op == 9:
                        log.error("❌ Invalid Session")
                        break
                    elif op == 7:
                        log.warning("🔄 服务端要求重连")
                        break
        except Exception as e:
            retry += 1
            wait = min(5 * retry, 30)
            log.error("❌ 连接异常: %s，%d秒后重连...", e, wait)
            await asyncio.sleep(wait)


async def health_check(request):
    return web.Response(text="大肥鱼活着!")


async def main():
    log.info("=" * 40)
    log.info("🐟 大肥鱼 QQ 机器人 GitHub Actions版 启动中...")
    log.info("=" * 40)

    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("🌐 HTTP健康检查服务已启动，端口: %d", PORT)

    await ws_loop()


if __name__ == "__main__":
    asyncio.run(main())
