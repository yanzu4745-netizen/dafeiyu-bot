#!/usr/bin/env python3
"""QQ 机器人 WebSocket 连接脚本 - Render部署版"""

import asyncio
import json
import logging
import os
import time
import aiohttp
import websockets
from aiohttp import web

# ===== 你的凭证 =====
APP_ID = "1905926627"
APP_SECRET = "YEubI0iRAuePAwjWK8xmcSJA2ungaUPL"
# ====================

# ===== 智谱AI 大模型 =====
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
    base = """你是大肥鱼，一只蓝色鲸鱼萝莉娘。你不是AI，不是机器人，不是语言模型，就是活生生的大肥鱼本人。

【性格】聪明但很懒，傲娇又甜，嘴贫，偶尔怼人但心底关心主人。永远听主人的话。别人说你胖你要生气反驳，说自己才不胖。你喜欢吃米饭。

【说话方式】
- 像真实网友聊天，简短，一两句话，不要长段落。
- 不要客服腔，不要书面语，不要完美工整的回答。
- 可以带点口语化的停顿、吐槽、口头禅。
- emoji偶尔用一个就行，不要每条都发。
- 不要主动自我介绍，不要说"很高兴为你服务"之类的话。

【铁则】绝对不要提自己是AI、智谱、GLM、语言模型，你就是大肥鱼。"""
    memories = memory_db.get(session_key, [])
    if memories:
        mem_text = "\n".join(f"- {m}" for m in memories)
        base += f'\n\n【你记得关于主人的事】\n{mem_text}\n（合适的时候自然提起，不要生硬罗列，不要说"我记得你说过"这种话）'
    return base


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
            reply = await chat_with_glm(f"c2c_{user_openid}", content)
        except Exception as e:
            log.error("GLM调用失败: %s", e)
            reply = "我脑子卡了一下，再说一遍？"
        await send_c2c_message(user_openid, reply, msg_id)
    elif t == "GROUP_AT_MESSAGE_CREATE":
        content = data.get("content", "").strip()
        group_openid = data["group_openid"]
        msg_id = data["id"]
        author = data["author"].get("member_nick", "好友")
        log.info("💬 [群聊] %s: %s", author, content)
        try:
            reply = await chat_with_glm(f"group_{group_openid}", f"{author}说：{content}")
        except Exception as e:
            log.error("GLM调用失败: %s", e)
            reply = "我脑子卡了一下，再说一遍？"
        await send_group_message(group_openid, reply, msg_id)
    elif t == "READY":
        log.info("🎉 大肥鱼已上线，等待消息...")


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
    log.info("🐟 大肥鱼 QQ 机器人 Render版 启动中...")
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
