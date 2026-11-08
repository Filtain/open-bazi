"""Synthetic fixtures only. No real provider calls or personal birth data."""
import asyncio
import copy
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import main
from app.analysis import annual_evidence, relations
from app.engine import BirthInput
from app.sop import NATAL, question_years, validate_report

BASE = {"year": 1988, "month": 2, "day": 15, "time": "23:30", "gender": 1, "selected_year": 2026}


def section_fixture(spec):
    return {"sections": [{"id": identifier, "judgment": "测试判断", "basis": "测试依据", "conditions": "测试成立条件", "advice": "测试建议", "fact_ids": facts, "rule_ids": rules}
                         for identifier, _, facts, rules in spec]}


@pytest.fixture
def client(monkeypatch):
    main.REPORTS.clear()
    main.active_config = main.Connection(provider="custom", base_url="https://example.com/v1", model="synthetic-model", api_key="test-private-key")
    with TestClient(main.app, base_url="http://127.0.0.1:8765") as c:
        c.headers["x-local-token"] = c.get("/api/bootstrap").json()["token"]
        yield c
    main.active_config = None
    main.REPORTS.clear()


def provider_fixture(calls):
    async def fake(config, messages, limit, json_mode=False):
        context = json.loads(messages[-1]["content"])
        calls.append(context)
        assert "api_key" not in context and "input" not in context
        if "required_sections" in context:
            spec = [(s["id"], s["title"], s["required_fact_ids"], s["required_rule_ids"]) for s in context["required_sections"]]
            return json.dumps(section_fixture(spec)), {"total_tokens": 12}
        year = context["scope_years"][0]
        return json.dumps({"answer": f"{year}年测试回答", "basis": "合参原局和给定流年", "conditions": "以原局判断成立为前提", "advice": "核对实际情况", "fact_ids": [f"year_{year}", "pillar_2"], "rule_ids": ["R3", "R10"]}), {"total_tokens": 7}
    return fake


def complete_report(client):
    natal = client.post("/api/report", json=BASE)
    assert natal.status_code == 200, natal.text
    identifier = natal.json()["id"]
    future = client.post(f"/api/report/{identifier}/outlook", json={})
    assert future.status_code == 200, future.text
    return future.json()


def test_rule_evidence_contains_month_root_exposure_and_not_a_strength_score():
    result = main.compute(BirthInput(**BASE))
    facts = {f["id"]: f for f in result["facts"]}
    assert "金为囚" in facts["natal_season"]["text"]
    assert facts["natal_roots"]["data"] == [{"pillar": "日柱", "branch": "丑", "stem": "辛", "rank": "非本气", "ten_god": "比肩"}]
    assert result["analysis"]["pattern"]["candidate"] == "月令本气正财取格线索"
    assert result["analysis"]["pattern"]["exposed"][0]["stem"] == "甲"
    assert not any("score" in key for key in result["analysis"])
    ids = [f["id"] for f in result["facts"]]
    assert len(ids) == len(set(ids))


def test_relations_keep_positions_and_require_distinct_self_punishment():
    node = lambda i, pair: {"id": str(i), "label": str(i), "ganzhi": pair}
    assert relations([node(1, "戊辰")]) == []
    result = relations([node(1, "戊辰"), node(2, "甲辰")])
    assert [r["kind"] for r in result] == ["自刑配对"]
    nodes = [node(1, "庚申"), node(2, "甲子"), node(3, "戊辰")]
    assert any(r["kind"] == "三合齐支" and len(r["positions"]) == 3 for r in relations(nodes))
    assert not any(r["kind"] == "三合齐支" for r in relations(nodes[:2]))
    assert relations(nodes, ["nonexistent"]) == []


def test_annual_splits_actual_qiyun_and_does_not_use_first_luck_early():
    chart = main.compute(BirthInput(year=2022, month=3, day=9, time="20:51", gender=1))
    result = annual_evidence(chart, 2030)["data"]
    segments = result["segments"]
    assert len(segments) == 2
    assert segments[0]["luck_id"] is None
    assert "尚未起运" in segments[0]["status"]
    assert segments[0]["end"] == segments[1]["start"] == "2030-12-12T06:51:00+08:00"
    assert segments[1]["luck_id"] == "luck_0"
    assert segments[0]["start"] == result["start"] and segments[-1]["end"] == result["end"]
    later = annual_evidence(chart, 2040)["data"]["segments"]
    assert [s["luck_id"] for s in later] == ["luck_0", "luck_1"]
    assert all(set(r["positions"]) >= {"year_2040", s["luck_id"]} for s in later for r in s["relations"])


def test_missing_gender_prebirth_and_supported_window_limit():
    chart = main.compute(BirthInput(**{**BASE, "gender": None, "selected_year": 2099}))
    assert chart["analysis"]["year_range"] == [2099]
    assert chart["analysis"]["luck"][0]["id"] == "luck_missing"
    assert "大运未知" in annual_evidence(chart, 2026)["data"]["segments"][0]["status"]
    assert "出生前" in annual_evidence(chart, 1980)["data"]["segments"][0]["status"]


@pytest.mark.parametrize("question,expected", [
    ("2028到2030年工作如何", [2028, 2029, 2030]),
    ("未来三年的事业", [2026, 2027, 2028]),
    ("今年和明年", [2026, 2027]), ("命格如何", [2026, 2027]),
    ("2035年会怎样", [2035]), ("2029-2031年", [2029, 2030, 2031])])
def test_question_time_scope(question, expected):
    assert question_years(question, [2026, 2027], datetime(2026, 11, 8)) == expected


@pytest.mark.parametrize("question", ["未来十一年", "未来11年", "1969年如何", "2030至2020年", "2026至2040年"])
def test_out_of_scope_questions(question):
    # Unsupported Chinese counts should be rejected, not silently treated as the default.
    with pytest.raises(ValueError):
        question_years(question, [2026], datetime(2026, 11, 8))


@pytest.mark.parametrize("mutation", ["missing_section", "wrong_order", "invented_id", "missing_evidence", "empty_judgment", "wrong_rule", "wrong_year"])
def test_strict_sop_validation(client, monkeypatch, mutation):
    async def invalid(config, messages, *args):
        context = json.loads(messages[-1]["content"])
        spec = [(s["id"], s["title"], s["required_fact_ids"], s["required_rule_ids"]) for s in context["required_sections"]]
        data = copy.deepcopy(section_fixture(spec))
        if mutation == "missing_section": data["sections"].pop()
        if mutation == "wrong_order": data["sections"].reverse()
        if mutation == "invented_id": data["sections"][0]["fact_ids"].append("fake_fact")
        if mutation == "missing_evidence": data["sections"][1]["fact_ids"] = ["pillar_0"]
        if mutation == "empty_judgment": data["sections"][0]["judgment"] = " "
        if mutation == "wrong_rule": data["sections"][0]["rule_ids"] = ["fake_rule"]
        if mutation == "wrong_year": data["sections"][0]["id"] = "year_2090"
        return json.dumps(data), {}
    monkeypatch.setattr(main, "completion", invalid)
    response = client.post("/api/report", json=BASE)
    assert response.status_code == 502
    assert main.REPORTS == {}
    assert "test-private-key" not in response.text


def test_report_chat_complete_flow_reuses_natal_and_server_owned_history(client, monkeypatch):
    calls = []
    monkeypatch.setattr(main, "completion", provider_fixture(calls))
    report = complete_report(client)
    assert report["status"] == "complete"
    assert len(report["sections"]) == 20  # Six natal + eight luck + five years + summary.
    assert report["usage"]["total_tokens"] == 24
    assert calls[1]["natal_report"][0]["id"] == "overview"
    identifier = report["id"]
    again = client.post(f"/api/report/{identifier}/outlook", json={})
    assert again.status_code == 200 and len(calls) == 2
    response = client.post(f"/api/report/{identifier}/chat", json={"question": "2035年事业如何？"})
    assert response.status_code == 200, response.text
    assert response.json()["scope_years"] == [2035]
    assert "year_2035" in [f["id"] for f in calls[-1]["facts"]]
    assert "year_2026" not in [f["id"] for f in calls[-1]["facts"]]
    client.post(f"/api/report/{identifier}/chat", json={"question": "命格的判断依据呢？"})
    assert len(calls[-1]["conversation"]) == 1
    assert calls[-1]["scope_years"] == [2035]
    assert calls[-1]["conversation"][0]["question"] == "2035年事业如何？"
    forged = client.post(f"/api/report/{identifier}/chat", json={"question": "问", "facts": [{"id": "invented"}]})
    assert forged.status_code == 422
    assert client.post(f"/api/report/{identifier}/clear-chat", json={}).status_code == 200
    assert main.REPORTS[identifier]["history"] == []


def test_future_failure_is_resumable_and_chat_waits(client, monkeypatch):
    calls = []
    fake = provider_fixture(calls)
    monkeypatch.setattr(main, "completion", fake)
    identifier = client.post("/api/report", json=BASE).json()["id"]
    async def broken(*args, **kwargs): return "{}", {}
    monkeypatch.setattr(main, "completion", broken)
    assert client.post(f"/api/report/{identifier}/outlook", json={}).status_code == 502
    assert main.REPORTS[identifier]["status"] == "natal_ready"
    assert len(main.REPORTS[identifier]["natal"]) == 6
    assert client.post(f"/api/report/{identifier}/chat", json={"question": "你好"}).status_code == 409
    monkeypatch.setattr(main, "completion", fake)
    assert client.post(f"/api/report/{identifier}/outlook", json={}).status_code == 200
    assert len(calls) == 2


def test_invalid_chat_never_enters_history(client, monkeypatch):
    monkeypatch.setattr(main, "completion", provider_fixture([]))
    identifier = complete_report(client)["id"]
    async def bad(*args, **kwargs):
        return json.dumps({"answer": "2035年测试", "basis": "错误", "conditions": "条件", "advice": "建议", "fact_ids": ["pillar_2"], "rule_ids": ["R10"]}), {}
    monkeypatch.setattr(main, "completion", bad)
    assert client.post(f"/api/report/{identifier}/chat", json={"question": "2035年呢"}).status_code == 502
    assert main.REPORTS[identifier]["history"] == []


def test_disconnect_during_report_prevents_storage_and_releases_lock(monkeypatch):
    main.REPORTS.clear()
    async def scenario():
        started, finish = asyncio.Event(), asyncio.Event()
        main.active_config = main.Connection(provider="custom", base_url="https://example.com", model="test", api_key="fake")
        async def delayed(*args, **kwargs):
            started.set(); await finish.wait()
            return json.dumps(section_fixture(NATAL)), {}
        monkeypatch.setattr(main, "completion", delayed)
        task = asyncio.create_task(main.report(BirthInput(**BASE)))
        await started.wait(); main.disconnect(); finish.set()
        with pytest.raises(main.HTTPException) as exc:
            await task
        assert exc.value.status_code == 409
        assert not main.generation_lock.locked() and not main.REPORTS
    asyncio.run(scenario())


def test_expired_report_does_not_call_provider(client, monkeypatch):
    monkeypatch.setattr(main, "completion", provider_fixture([]))
    identifier = complete_report(client)["id"]
    main.REPORTS[identifier]["touched"] -= main.REPORT_TTL + 1
    assert client.post(f"/api/report/{identifier}/chat", json={"question": "问"}).status_code == 404


def test_year_evidence_cannot_be_swapped_between_annual_sections():
    from app.sop import outlook_spec
    chart = main.compute(BirthInput(**BASE))
    spec = outlook_spec(chart)
    data = copy.deepcopy(section_fixture(spec))
    section = next(s for s in data['sections'] if s['id'] == 'year_2026')
    section['fact_ids'].append('year_2027')
    with pytest.raises(ValueError):
        validate_report(json.dumps(data), chart, spec)


def test_money_is_not_automatically_parsed_as_a_year():
    assert question_years('每月5000元收入，2028元开销', [2026]) == [2026]


def test_bounded_reports_and_chat_history(client, monkeypatch):
    monkeypatch.setattr(main, 'completion', provider_fixture([]))
    first = complete_report(client)['id']
    for _ in range(main.REPORT_LIMIT):
        assert client.post('/api/report', json=BASE).status_code == 200
    assert len(main.REPORTS) == main.REPORT_LIMIT and first not in main.REPORTS
    identifier = next(iter(main.REPORTS))
    record = main.REPORTS[identifier]
    record['status'] = 'complete'
    record['history'] = [{}] * 20
    assert client.post(f'/api/report/{identifier}/chat', json={'question': '继续'}).status_code == 409
