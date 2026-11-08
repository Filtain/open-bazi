import asyncio
import json
import os
import re
import secrets
import time
from datetime import datetime
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
from .engine import BirthInput, STANDARD, calculate  # noqa: E402
from .analysis import enrich, annual_evidence
from .sop import RULES, VERSION as SOP_VERSION, NATAL, SYSTEM, outlook_spec, report_messages, validate_report, validate_reading, question_years

ROOT = Path(__file__).parent
PORT = int(os.environ.get("OPEN_BAZI_PORT", "8765"))
TOKEN = secrets.token_urlsafe(32)
PROVIDERS = {
    "deepseek": {"name": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "deepseek-flash", "docs": "https://platform.deepseek.com/api_keys"},
    "qwen": {"name": "通义千问 · 北京", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus", "docs": "https://bailian.console.aliyun.com/"},
    "custom": {"name": "自定义兼容接口", "base_url": "", "model": "", "docs": ""},
}
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
        return enrich(calculate(birth))
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


# Local memory only: bounded capacity and six-hour idle expiry. No API keys in records.
REPORTS = {}
REPORT_TTL = 6 * 3600
REPORT_LIMIT = 8


def prune_reports():
    now = time.monotonic()
    for key in list(REPORTS):
        if now - REPORTS[key]["touched"] > REPORT_TTL:
            del REPORTS[key]


def get_record(report_id):
    prune_reports()
    record = REPORTS.get(report_id)
    if record is None:
        raise HTTPException(404, "这份报告已过期或本地服务已重启，请重新生成报告。")
    record["touched"] = time.monotonic()
    return record


def view_record(record):
    return {"id": record["id"], "status": record["status"], "sop_version": SOP_VERSION,
            "sections": record["natal"] + record["outlook"], "model": record["model"],
            "usage": record["usage"], "models": record["models"], "facts": record["chart"]["facts"], "rules": RULES,
            "notice": "传统八字推演 · AI生成。已检查流程完整性和引用编号，文字判断仍需结合依据审阅，未来趋势不代表必然事件。"}


def require_connection():
    if active_config is None:
        raise HTTPException(409, "请先连接自己的模型 API。离线排盘无需连接。")
    if generation_lock.locked():
        raise HTTPException(429, "已有分析或回答正在生成，请完成后再试。")
    return active_config, configuration_epoch


def check_epoch(epoch):
    if epoch != configuration_epoch:
        raise HTTPException(409, "模型连接已改变，本次生成结果未保存。请使用当前连接重新生成。")


def parse_sections(content, result, spec):
    try:
        return validate_report(content, result, spec)
    except (ValueError, KeyError, TypeError):
        raise HTTPException(502, "模型输出未通过完整流程或引用检查，已保留现有结果。可手动重试，重试会调用供应商。") from None


def add_usage(a, b):
    return {key: a.get(key, 0) + b.get(key, 0) for key in set(a) | set(b)}


@app.post("/api/report")
async def report(birth: BirthInput):
    config, epoch = require_connection()
    async with generation_lock:
        result = await run_in_threadpool(compute, birth)
        content, usage = await completion(config, report_messages(result, NATAL), 6500, True)
        sections = parse_sections(content, result, NATAL)
        check_epoch(epoch)
        prune_reports()
        if len(REPORTS) >= REPORT_LIMIT:
            oldest = min(REPORTS, key=lambda key: REPORTS[key]["touched"])
            del REPORTS[oldest]
        identifier = secrets.token_urlsafe(24)
        record = {"id": identifier, "chart": result, "natal": sections, "outlook": [],
                  "status": "natal_ready", "model": config.model, "models": {"natal": config.model}, "usage": usage,
                  "history": [], "touched": time.monotonic()}
        REPORTS[identifier] = record
        return view_record(record)


@app.post("/api/report/{report_id}/outlook")
async def outlook(report_id: str):
    record = get_record(report_id)
    if record["status"] == "complete":
        return view_record(record)  # Safe retry without another paid call.
    config, epoch = require_connection()
    async with generation_lock:
        spec = outlook_spec(record["chart"])
        content, usage = await completion(config, report_messages(record["chart"], spec, record["natal"]), 10000, True)
        sections = parse_sections(content, record["chart"], spec)
        check_epoch(epoch)
        record.update(outlook=sections, status="complete", model=config.model,
                      usage=add_usage(record["usage"], usage), touched=time.monotonic())
        record["models"]["outlook"] = config.model
        return view_record(record)


class Question(BaseModel):
    # Never accept client-provided charts, histories, system prompts or report text.
    model_config = {"extra": "forbid"}
    question: str = Field(min_length=1, max_length=1500)


@app.post("/api/report/{report_id}/chat")
async def chat(report_id: str, request: Question):
    record = get_record(report_id)
    if record["status"] != "complete":
        raise HTTPException(409, "请先完成大运流年报告，再围绕完整分析追问。")
    question = request.question.strip()
    if not question:
        raise HTTPException(422, "请先输入想了解的问题。")
    if len(record["history"]) >= 20:
        raise HTTPException(409, "本次对话已达20轮，请导出后清空对话再继续。命盘与报告会保留。")
    try:
        previous_scope = record["history"][-1]["scope_years"] if record["history"] else record["chart"]["analysis"]["year_range"]
        years = question_years(question, previous_scope)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    config, epoch = require_connection()
    async with generation_lock:
        # Recompute requested years server-side, including years beyond the report window.
        year_facts = await run_in_threadpool(lambda: [annual_evidence(record["chart"], y) for y in years])
        facts = [f for f in record["chart"]["facts"] if not f["id"].startswith("year_")] + year_facts
        context = {"sop_version": SOP_VERSION, "today": datetime.now(STANDARD).date().isoformat(),
                   "scope_years": years, "scope_note": "明确年份已由程序提取。无明确年份时沿用上一轮范围，首次提问用报告窗口。若仍有歧义，先澄清，不强行对应。",
                   "rules": RULES, "facts": facts, "natal_report": record["natal"],
                   "outlook_report": record["outlook"],
                   "conversation": record["history"][-8:], "question": question,
                   "workflow": ["确认问题主题与时间", "复核原局与既有条件判断", "合参相应大运流年", "解释依据与反证", "回答并给行动建议"],
                   "output_schema": {"answer": "直接回答，涉及未来给出条件趋势", "basis": "相关原局及运年依据", "conditions": "限制、反证或澄清问题", "advice": "可执行建议", "fact_ids": ["至少一个相关事实ID；讨论某流年必须引用对应year_ID"], "rule_ids": ["必须包含R10和相关规则"]}}
        content, usage = await completion(config, [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)}], 3600, True)
        try:
            data = json.loads(content)
            answer = validate_reading(data, facts, required_rules=["R10"], chat=True)
            mentioned = {int(y) for y in re.findall(r"(?<!\d)(\d{4})年", " ".join(answer[k] for k in ("answer", "basis", "conditions", "advice")))}
            # Only computed annual scopes can be claimed as an annual analysis.
            for y in mentioned & set(years):
                if f"year_{y}" not in answer["fact_ids"]:
                    raise ValueError("missing annual reference")
        except (ValueError, KeyError, TypeError):
            raise HTTPException(502, "回答未通过格式或引用检查，未写入对话。可手动重试。") from None
        check_epoch(epoch)
        entry = {"question": question, "answer": answer, "scope_years": years,
                 "facts": [f for f in facts if f["id"] in answer["fact_ids"]], "model": config.model, "usage": usage}
        record["history"].append(entry)
        record["touched"] = time.monotonic()
        return entry


@app.post("/api/report/{report_id}/clear-chat")
async def clear_chat(report_id: str):
    record = get_record(report_id)
    if generation_lock.locked():
        raise HTTPException(409, "请等待正在生成的回答完成后再清空。")
    record["history"].clear()
    return {"cleared": True}


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
