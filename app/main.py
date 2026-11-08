import asyncio
import json
import os
import secrets
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_core import PydanticCustomError
from starlette.concurrency import run_in_threadpool
from zoneinfo import reset_tzpath

# Use the installed tzdata package on every OS instead of differing system databases.
reset_tzpath(())
from .engine import BirthInput, calculate  # noqa: E402

ROOT = Path(__file__).parent
PORT = int(os.environ.get("OPEN_BAZI_PORT", "8765"))
TOKEN = secrets.token_urlsafe(32)
PROVIDERS = {
    "deepseek": {"name": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "deepseek-flash", "docs": "https://platform.deepseek.com/api_keys"},
    "qwen": {"name": "通义千问 · 北京", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus", "docs": "https://bailian.console.aliyun.com/"},
    "custom": {"name": "自定义兼容接口", "base_url": "", "model": "", "docs": ""},
}
RULES = [
    {"id": "R1", "title": "十神是相对关系", "text": "十神由其他天干与原局日主的五行生克和阴阳关系确定。日主自身标为日主。十神名称不直接等于现实职业、财富或亲属事件。", "source": "本项目算法说明：十神映射表"},
    {"id": "R2", "title": "计数不等于旺衰", "text": "当前五行数量只计算四个天干和四个地支主气，不纳入藏干权重，不能仅凭数量判定旺衰、喜忌或补什么。", "source": "本项目算法说明：计数口径"},
    {"id": "R3", "title": "流年的时间边界", "text": "流年以立春时刻换年，与公历元旦、春节不同。十神始终相对出生盘日主计算。", "source": "本项目算法说明：立春换年"},
    {"id": "R4", "title": "大运是传统时间分段", "text": "按选定传统参数决定顺逆；三日折一年，间隔按分钟分解，首运开始后每十个公历年为一段。此分段本身不能证实事件预测。", "source": "本项目算法说明：起运与大运"},
]
app = FastAPI(title="Open Bazi", docs_url=None, redoc_url=None, openapi_url=None)
active_config = None
configuration_epoch = 0
generation_lock = asyncio.Lock()
KEY_ERRORS = {
    "key_missing": "请粘贴供应商平台创建时提供的完整 API Key。",
    "key_masked": "密钥含星号或省略号，可能是打码后的显示内容。请复制完整密钥，或新建一枚后立即复制。",
    "key_format": "密钥包含空白、不可见字符或非英文字符，请重新复制完整 API Key；不要粘贴登录密码或接口网址。",
    "key_url": "这里需要填写 API Key，不是接口网址。请从供应商的 API keys 页面复制密钥。",
}


class Connection(BaseModel):
    provider: Literal["deepseek", "qwen", "custom"]
    base_url: str = Field(max_length=500)
    model: str = Field(min_length=1, max_length=150, pattern=r"^[\w./:@+-]+$")
    api_key: SecretStr = Field(max_length=4000)

    @model_validator(mode="after")
    def valid_endpoint(self):
        key = self.api_key.get_secret_value()
        # Normalize only obvious copy wrappers; never remove characters inside a key.
        for _ in range(2):
            key = key.strip(" \t\r\n\u200b\ufeff")
            if len(key) >= 2 and (key[0], key[-1]) in (("'", "'"), ('"', '"')):
                key = key[1:-1].strip()
            if key.lower().startswith(("bearer ", "bearer\t")):
                key = key[6:].strip()
        if not key and self.provider != "custom":
            raise PydanticCustomError("key_missing", KEY_ERRORS["key_missing"])
        if self.provider != "custom" and any(mark in key for mark in ("*", "…", "...")):
            raise PydanticCustomError("key_masked", KEY_ERRORS["key_masked"])
        if key.startswith(("http://", "https://")):
            raise PydanticCustomError("key_url", KEY_ERRORS["key_url"])
        if any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise PydanticCustomError("key_format", KEY_ERRORS["key_format"])
        self.api_key = SecretStr(key)
        self.base_url = self.base_url.strip().rstrip("/")
        if self.base_url.endswith("/chat/completions"):
            self.base_url = self.base_url[:-len("/chat/completions")]
        u = urlsplit(self.base_url)
        if not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError("请填写不含账号、查询参数的 API Base URL。")
        if u.scheme != "https" and not (u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost", "::1")):
            raise ValueError("远程 API 必须使用 HTTPS；HTTP 仅允许本机地址。")
        if self.provider != "custom" and self.base_url != PROVIDERS[self.provider]["base_url"]:
            raise ValueError("预置供应商地址不可修改，请选择自定义接口。")
        return self


@app.middleware("http")
async def local_boundary(request: Request, call_next):
    allowed = {"127.0.0.1:" + str(PORT), "localhost:" + str(PORT), "[::1]:" + str(PORT)}
    host = request.headers.get("host", "")
    if host not in allowed:
        return JSONResponse({"detail": "此演示版仅允许本机访问。"}, status_code=403)
    if request.method not in ("GET", "HEAD"):
        if request.headers.get("origin") not in (None, "http://" + host):
            return JSONResponse({"detail": "不允许跨站请求。"}, status_code=403)
        if not secrets.compare_digest(request.headers.get("x-local-token", ""), TOKEN):
            return JSONResponse({"detail": "本机会话已失效，请刷新页面。"}, status_code=403)
        if len(await request.body()) > 16384:
            return JSONResponse({"detail": "请求过大。"}, status_code=413)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    # Do not return invalid input objects, which could contain an API key.
    message = next((KEY_ERRORS[e["type"]] for e in error.errors() if e["type"] in KEY_ERRORS),
                   "输入格式不正确，请核对日期、时间或连接设置。")
    return JSONResponse({"detail": message}, status_code=422)


def compute(birth):
    try:
        return calculate(birth)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception:
        raise HTTPException(422, "日期无法转换，请检查农历闰月和每月实际天数。") from None


async def completion(config, messages, limit, json_mode=False):
    payload = {"model": config.model, "messages": messages, "max_tokens": limit, "stream": False}
    if config.provider == "deepseek":
        payload["thinking"] = {"type": "disabled"}
    if config.provider == "qwen":
        payload["enable_thinking"] = False
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Content-Type": "application/json"}
    key = config.api_key.get_secret_value().strip()
    if key:
        headers["Authorization"] = "Bearer " + key
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10), follow_redirects=False, trust_env=False) as client:
            response = await client.post(config.base_url + "/chat/completions", headers=headers, json=payload)
        if response.status_code != 200:
            authentication = {
                "deepseek": "DeepSeek 接口返回认证失败（HTTP 401）。无需选择地域。请在官方 API keys 页面新建密钥并复制完整值后重试；不要使用登录密码或打码后的内容。",
                "qwen": "通义千问接口返回认证失败（HTTP 401）。请核对完整密钥，并确认它来自北京地域；其他地域或专属地址请使用自定义接口。",
                "custom": "自定义接口返回认证失败（HTTP 401）。请核对该接口对应的密钥与认证方式。",
            }
            message = {401: authentication[config.provider], 402: "供应商返回余额不足（HTTP 402），请在对应 API 平台检查余额。", 403: "供应商拒绝访问（HTTP 403），请检查账号与模型权限。", 404: "接口地址或模型名称不存在（HTTP 404）。", 429: "供应商限流或额度不足（HTTP 429），请稍后重试。"}.get(response.status_code, "供应商请求失败（HTTP " + str(response.status_code) + "），请检查配置后重试。")
            raise HTTPException(502, message)
        data = response.json()
        choice = data["choices"][0]
        content = choice["message"]["content"]
        if choice.get("finish_reason") not in (None, "stop") or not isinstance(content, str) or not content.strip():
            raise HTTPException(502, "模型未返回完整内容，请检查模型与输出限制。")
        usage = data.get("usage") or {}
        safe_usage = {k: v for k, v in usage.items() if k in ("prompt_tokens", "completion_tokens", "total_tokens") and type(v) is int}
        return content, safe_usage
    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(504, "模型请求超时。未自动重试，避免重复消耗额度。") from None
    except Exception:
        raise HTTPException(502, "连接失败或响应不符合兼容协议。请核对地址、网络与模型。") from None


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/bootstrap")
def bootstrap():
    return {"token": TOKEN, "providers": PROVIDERS, "connected": active_config is not None,
            "model": active_config.model if active_config else None, "rules": RULES}


@app.post("/api/chart")
def chart(birth: BirthInput):
    return compute(birth)


@app.post("/api/connect")
async def connect(config: Connection):
    global active_config, configuration_epoch
    configuration_epoch += 1
    epoch = configuration_epoch
    _, usage = await completion(config, [{"role": "user", "content": "Reply only OK."}], 32)
    if epoch != configuration_epoch:
        raise HTTPException(409, "连接设置已更改，本次测试结果未保存。")
    active_config = config
    return {"connected": True, "model": config.model, "usage": usage}


@app.post("/api/disconnect")
def disconnect():
    global active_config, configuration_epoch
    configuration_epoch += 1
    active_config = None
    return {"connected": False}


@app.post("/api/report")
async def report(birth: BirthInput):
    config = active_config
    if config is None:
        raise HTTPException(409, "请先连接自己的模型 API。离线排盘无需连接。")
    if generation_lock.locked():
        raise HTTPException(429, "已有解读正在生成，请稍后再试。")
    async with generation_lock:
        result = await run_in_threadpool(compute, birth)
        system = "你是八字文化学习助手。只解释给定facts和rules，不作事件、健康、财富或婚姻预测，不推断强弱喜忌，不编造古籍原文。输出中文JSON，结构为{\"sections\":[{\"title\":\"标题\",\"text\":\"简明解释\",\"fact_ids\":[\"事实ID\"],\"rule_ids\":[\"规则ID\"]}]}。写3至5节，每节最多300字，每节必须引用实际存在且相关的事实和规则。不要输出Markdown或HTML。缺失信息请明确说明。"
        content, usage = await completion(config, [{"role": "system", "content": system}, {"role": "user", "content": json.dumps({"facts": result["facts"], "rules": RULES}, ensure_ascii=False)}], 2400, True)
        try:
            data = json.loads(content)
            sections = data["sections"]
            assert isinstance(sections, list) and 1 <= len(sections) <= 5
            fact_ids = {f["id"] for f in result["facts"]}
            rule_ids = {r["id"] for r in RULES}
            cleaned = []
            for section in sections:
                assert isinstance(section["title"], str) and 0 < len(section["title"]) <= 60
                assert isinstance(section["text"], str) and 0 < len(section["text"]) <= 1200
                for key, allowed in (("fact_ids", fact_ids), ("rule_ids", rule_ids)):
                    assert isinstance(section[key], list) and 1 <= len(section[key]) <= 10
                    assert all(isinstance(v, str) and v in allowed for v in section[key])
                cleaned.append({k: section[k] for k in ("title", "text", "fact_ids", "rule_ids")})
        except (ValueError, KeyError, AssertionError, TypeError):
            raise HTTPException(502, "解读未通过格式或引用检查，已保留排盘。可手动重试，重试会再次调用供应商。") from None
        return {"sections": cleaned, "model": config.model, "usage": usage, "facts": result["facts"], "rules": RULES,
                "notice": "AI生成，仅核验输出结构和引用ID；尚未自动证明文字与依据一致。请结合依据阅读。"}


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
