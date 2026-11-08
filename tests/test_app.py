import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from app import main
from app.engine import BirthInput, calculate, ten_god, add_calendar

BASE = {"year": 1988, "month": 2, "day": 15, "time": "23:30", "gender": 1, "selected_year": 2026}


@pytest.fixture
def client():
    main.active_config = None
    main.REPORTS.clear()
    with TestClient(main.app, base_url="http://127.0.0.1:8765") as c:
        c.headers["x-local-token"] = c.get("/api/bootstrap").json()["token"]
        yield c
    main.active_config = None


def test_known_chart_and_day_boundary():
    result = calculate(BirthInput(**BASE))
    assert [p["ganzhi"] for p in result["pillars"]] == ["戊辰", "甲寅", "辛丑", "戊子"]
    earlier = calculate(BirthInput(**{**BASE, "time": "22:59"}))
    assert earlier["pillars"][2]["ganzhi"] == "庚子"
    assert sum(result["elements"].values()) == 8


def test_dst_is_removed_once():
    result = calculate(BirthInput(year=1991, month=8, day=13, time="17:20"))
    assert result["standard_time"] == "1991-08-13T16:20:00+08:00"
    assert result["pillars"][3]["ganzhi"].endswith("申")
    fixed = calculate(BirthInput(year=1991, month=8, day=13, time="17:20", clock="standard"))
    assert fixed["standard_time"] == "1991-08-13T17:20:00+08:00"


def test_dst_gap_and_fold(client):
    data = {**BASE, "year": 1991, "month": 4, "day": 14, "time": "02:30"}
    assert client.post("/api/chart", json=data).status_code == 422
    data.update(month=9, day=15, time="01:30")
    assert client.post("/api/chart", json=data).status_code == 422
    times = [client.post("/api/chart", json={**data, "fold": f}).json()["standard_time"] for f in (0, 1)]
    assert times[0] != times[1]


def test_qiyun_and_contiguous_periods():
    result = calculate(BirthInput(year=2022, month=3, day=9, time="20:51", gender=1))
    assert result["luck"]["start"] == "2030-12-12T06:51:00+08:00"
    periods = result["luck"]["periods"]
    assert all(a["end"] == b["start"] for a, b in zip(periods, periods[1:]))


def test_month_end_policy():
    assert add_calendar(datetime(2024, 2, 29), years=1).date().isoformat() == "2025-02-28"
    assert add_calendar(datetime(2024, 1, 31), months=1).day == 29


def test_ten_god_mapping():
    assert [ten_god("甲", s) for s in "甲乙丙丁戊己庚辛壬癸"] == ["比肩", "劫财", "食神", "伤官", "偏财", "正财", "七杀", "正官", "偏印", "正印"]


def test_lunar_conversion(client):
    r = client.post("/api/chart", json={**BASE, "calendar": "lunar", "year": 2024, "month": 1, "day": 1, "time": "12:00"})
    assert r.status_code == 200
    assert r.json()["wall_time"].startswith("2024-02-10")
    assert client.post("/api/chart", json={**BASE, "calendar": "lunar", "year": 2024, "leap": True}).status_code == 422


@pytest.mark.parametrize("patch", [{"month": 2, "day": 30}, {"time": "24:00"}, {"year": 2099}, {"leap": True}, {"time": ""}])
def test_invalid_inputs(client, patch):
    assert client.post("/api/chart", json={**BASE, **patch}).status_code == 422


def test_missing_gender_does_not_guess_direction(client):
    result = client.post("/api/chart", json={**BASE, "gender": None}).json()
    assert result["luck"] is None


def test_lichun_boundary():
    boundary = calculate(BirthInput(**BASE))["annual"]["start"]
    dt = datetime.fromisoformat(boundary)
    # Inputs have minute precision. Choose one minute on either side of the astronomical boundary.
    from datetime import timedelta
    values = []
    for moment in (dt - timedelta(minutes=1), dt + timedelta(minutes=1)):
        c = calculate(BirthInput(year=moment.year, month=moment.month, day=moment.day, time=moment.strftime("%H:%M")))
        values.append(c["pillars"][0]["ganzhi"])
    assert values == ["乙巳", "丙午"]


def test_local_security(client):
    assert client.post("/api/chart", json=BASE, headers={"origin": "https://attacker.example"}).status_code == 403
    assert client.post("/api/chart", json=BASE, headers={"x-local-token": "bad"}).status_code == 403
    assert client.get("/api/bootstrap", headers={"host": "attacker.example"}).status_code == 403
    assert client.get("/").headers["cache-control"] == "no-store"


def test_connect_and_forget_key(client, monkeypatch):
    async def fake(config, messages, limit, json_mode=False):
        assert config.api_key.get_secret_value() == "test-secret-never-real"
        return "OK", {"total_tokens": 4}
    monkeypatch.setattr(main, "completion", fake)
    body = {"provider": "deepseek", "base_url": "https://api.deepseek.com", "model": "deepseek-flash", "api_key": "test-secret-never-real"}
    r = client.post("/api/connect", json=body)
    assert r.status_code == 200
    assert "test-secret-never-real" not in r.text + client.get("/api/bootstrap").text
    assert client.post("/api/disconnect", json={}).status_code == 200
    assert main.active_config is None


def test_reject_bad_endpoint_without_echoing_key(client):
    r = client.post("/api/connect", json={"provider": "custom", "base_url": "http://example.com", "model": "m", "api_key": "do-not-echo"})
    assert r.status_code == 422
    assert "do-not-echo" not in r.text


def test_disconnect_invalidates_inflight_connection(monkeypatch):
    import asyncio
    main.active_config = None
    config = main.Connection(provider="custom", base_url="https://example.com/v1", model="test", api_key="fake")
    async def scenario():
        started, finish = asyncio.Event(), asyncio.Event()
        async def delayed(*args, **kwargs):
            started.set()
            await finish.wait()
            return "OK", {}
        monkeypatch.setattr(main, "completion", delayed)
        task = asyncio.create_task(main.connect(config))
        await started.wait()
        main.disconnect()
        finish.set()
        with pytest.raises(main.HTTPException) as exc:
            await task
        assert exc.value.status_code == 409
        assert main.active_config is None
    asyncio.run(scenario())


def test_report_requires_connection(client):
    assert client.post("/api/report", json=BASE).status_code == 409



def test_provider_protocol_and_errors(monkeypatch):
    import asyncio
    real_client = main.httpx.AsyncClient
    captured = []
    def handler(request):
        captured.append(request)
        return main.httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "OK"}}], "usage": {"total_tokens": 5, "unexpected": "private"}})
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **kw: real_client(transport=main.httpx.MockTransport(handler), **kw))
    config = main.Connection(provider="qwen", base_url=main.PROVIDERS["qwen"]["base_url"], model="qwen-plus", api_key="fake")
    content, usage = asyncio.run(main.completion(config, [{"role": "user", "content": "test"}], 32))
    assert str(captured[0].url).endswith("/compatible-mode/v1/chat/completions")
    assert captured[0].headers["authorization"] == "Bearer fake"
    assert json.loads(captured[0].content)["enable_thinking"] is False
    assert content == "OK" and usage == {"total_tokens": 5}


@pytest.mark.parametrize("key", ['  sk-demo-only  ', 'Bearer sk-demo-only', '"sk-demo-only"', "'Bearer sk-demo-only'", '\ufeffsk-demo-only\u200b'])
def test_pasted_key_wrappers_are_removed(client, monkeypatch, key):
    async def fake(config, *args, **kwargs):
        assert config.api_key.get_secret_value() == "sk-demo-only"
        return "OK", {}
    monkeypatch.setattr(main, "completion", fake)
    r = client.post("/api/connect", json={"provider": "deepseek", "base_url": "https://api.deepseek.com", "model": "deepseek-flash", "api_key": key})
    assert r.status_code == 200
    assert "sk-demo-only" not in r.text


@pytest.mark.parametrize("key,expected", [('sk-demo****', '打码'), ('sk-demo…', '打码'), ('sk-demo only', '空白'), ('sk-demo\u200bonly', '不可见'), ('https://api.deepseek.com', '网址'), ('', '完整')])
def test_invalid_keys_rejected_before_network(client, monkeypatch, key, expected):
    async def never(*args, **kwargs):
        raise AssertionError("Invalid keys must not be sent to a supplier")
    monkeypatch.setattr(main, "completion", never)
    r = client.post("/api/connect", json={"provider": "deepseek", "base_url": "https://api.deepseek.com", "model": "deepseek-flash", "api_key": key})
    assert r.status_code == 422
    assert expected in r.json()["detail"]
    assert "sk-demo" not in r.text


@pytest.mark.parametrize("provider,code,expected", [("deepseek",401,"无需选择地域"), ("qwen",401,"北京地域"), ("custom",401,"认证方式"), ("deepseek",402,"余额不足")])
def test_authentication_errors_are_specific_and_private(client, monkeypatch, provider, code, expected):
    real_client = main.httpx.AsyncClient
    def handler(request):
        assert request.headers["authorization"] == "Bearer private-test-key"
        return main.httpx.Response(code, json={"error":{"message":"Echoed secret private-test-key", "code":"auth_error"}})
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **kw: real_client(transport=main.httpx.MockTransport(handler), **kw))
    url = main.PROVIDERS[provider]["base_url"] or "https://example.com/v1"
    r = client.post("/api/connect", json={"provider":provider,"base_url":url,"model":"test","api_key":"private-test-key"})
    assert r.status_code == 502
    assert expected in r.json()["detail"]
    assert str(code) in r.json()["detail"]
    assert "private-test-key" not in r.text
