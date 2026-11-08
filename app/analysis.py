"""Versioned, deterministic evidence for the traditional reading SOP.

These are structural observations, not a validated prediction model. Never
convert a matching 合/冲 or a count of elements into an automatic event verdict.
"""
from datetime import datetime
from itertools import combinations, product

from .engine import BRANCHES, ELEMENTS, HIDDEN, STEMS, lichun, ten_god

VERSION = "ziping-evidence-0.2.0"
PAIR_RULES = {
    "天干五合": (0, ("甲己", "乙庚", "丙辛", "丁壬", "戊癸")),
    "六合": (1, ("子丑", "寅亥", "卯戌", "辰酉", "巳申", "午未")),
    "六冲": (1, ("子午", "丑未", "寅申", "卯酉", "辰戌", "巳亥")),
    "六害": (1, ("子未", "丑午", "寅巳", "卯辰", "申亥", "酉戌")),
    "相刑配对": (1, ("寅巳", "巳申", "申寅", "丑戌", "戌未", "未丑", "子卯")),
}
TRIPLES = {"三合齐支": ("申子辰", "亥卯未", "寅午戌", "巳酉丑"),
           "三会齐支": ("寅卯辰", "巳午未", "申酉戌", "亥子丑")}
SEASONS = {"寅": ("春", "木"), "卯": ("春", "木"), "巳": ("夏", "火"), "午": ("夏", "火"),
           "申": ("秋", "金"), "酉": ("秋", "金"), "亥": ("冬", "水"), "子": ("冬", "水")}


def element(stem):
    return ELEMENTS[STEMS.index(stem) // 2]


def relations(nodes, required=()):
    """Retain pillar positions. required selects interactions involving a transit."""
    found = []
    for a, b in combinations(nodes, 2):
        if required and not ({a["id"], b["id"]} & set(required)):
            continue
        for kind, (index, pairs) in PAIR_RULES.items():
            chars = a["ganzhi"][index] + b["ganzhi"][index]
            if chars in pairs or chars[::-1] in pairs:
                found.append({"kind": kind, "positions": [a["id"], b["id"]],
                              "text": f'{a["label"]}{a["ganzhi"]}与{b["label"]}{b["ganzhi"]}：{kind}'})
        if a["ganzhi"][1] == b["ganzhi"][1] and a["ganzhi"][1] in "辰午酉亥":
            found.append({"kind": "自刑配对", "positions": [a["id"], b["id"]],
                          "text": f'{a["label"]}与{b["label"]}重复出现{a["ganzhi"][1]}：自刑配对'})
    for kind, groups in TRIPLES.items():
        for group in groups:
            slots = [[n for n in nodes if n["ganzhi"][1] == branch] for branch in group]
            for match in product(*slots):
                ids = [n["id"] for n in match]
                if required and not set(ids) & set(required):
                    continue
                found.append({"kind": kind, "positions": ids,
                              "text": "、".join(n["label"] + n["ganzhi"] for n in match) + "：" + kind})
    return found


def natal_nodes(chart):
    return [{"id": f"pillar_{i}", "label": p["label"], "ganzhi": p["ganzhi"]}
            for i, p in enumerate(chart["pillars"])]


def describe_relations(items):
    return "；".join(r["text"] for r in items) or "本规则表未检出对应组合"


def fact(identifier, text, data=None):
    result = {"id": identifier, "text": text, "source": VERSION}
    if data is not None:
        result["data"] = data
    return result


def annual_evidence(chart, year):
    day = chart["pillars"][2]["ganzhi"][0]
    pair = STEMS[(year - 4) % 10] + BRANCHES[(year - 4) % 12]
    start, end = datetime.fromisoformat(lichun(year)), datetime.fromisoformat(lichun(year + 1))
    born = datetime.fromisoformat(chart["standard_time"])
    nodes = natal_nodes(chart)
    annual = {"id": f"year_{year}", "label": f"{year}流年", "ganzhi": pair}
    interactions = relations(nodes + [annual], [annual["id"]])
    segments = []
    boundaries = {start, end}
    if start < born < end:
        boundaries.add(born)
    periods = chart["luck"]["periods"] if chart["luck"] else []
    for p in periods:
        for edge in ("start", "end"):
            dt = datetime.fromisoformat(p[edge])
            if start < dt < end:
                boundaries.add(dt)
    boundaries = sorted(boundaries)
    for left, right in zip(boundaries, boundaries[1:]):
        match = next(((i, p) for i, p in enumerate(periods)
                      if datetime.fromisoformat(p["start"]) <= left < datetime.fromisoformat(p["end"])), None)
        segment = {"start": left.isoformat(), "end": right.isoformat(), "luck_id": None, "ganzhi": None, "relations": []}
        if right <= born:
            segment["status"] = "出生前，不作个人流年推演"
        elif match:
            i, p = match
            luck = {"id": f"luck_{i}", "label": "大运", "ganzhi": p["ganzhi"]}
            segment.update(luck_id=luck["id"], ganzhi=p["ganzhi"], status="大运覆盖",
                           relations=[r for r in relations(nodes + [luck, annual], [annual["id"]])
                                      if luck["id"] in r["positions"]])
        elif not chart["luck"]:
            segment["status"] = "未提供排运参数，大运未知"
        elif right <= datetime.fromisoformat(chart["luck"]["start"]):
            segment["status"] = "尚未起运，不使用第一步大运代替"
        else:
            segment["status"] = "超出已计算的八步大运范围"
        segments.append(segment)
    data = {"year": year, "ganzhi": pair, "start": start.isoformat(), "end": end.isoformat(),
            "ten_god": ten_god(day, pair[0]),
            "hidden": [{"stem": s, "ten_god": ten_god(day, s)} for s in HIDDEN[pair[1]]],
            "relations": interactions, "segments": segments}
    text = f'{year}流年{pair}，天干为{data["ten_god"]}；{data["start"]}至{data["end"]}（不含结束）。'
    text += "与原局关系：" + describe_relations(interactions) + "。分段："
    text += "；".join(f'{s["start"]}至{s["end"]}：{s["ganzhi"] or s["status"]}' for s in segments)
    return fact(annual["id"], text, data)


def enrich(chart):
    pillars = chart["pillars"]
    day = pillars[2]["ganzhi"][0]
    month = pillars[1]["ganzhi"][1]
    main = HIDDEN[month][0]
    god = ten_god(day, main)
    if month in SEASONS:
        season, dominant = SEASONS[month]
        delta = (ELEMENTS.index(element(day)) - ELEMENTS.index(dominant)) % 5
        state = ("旺", "相", "死", "囚", "休")[delta]
        season_text = f'{month}月属{season}，采用四时旺相休囚死口径：{dominant}当令，日主{element(day)}为{state}。'
    else:
        season_text = f'{month}月为四季土月，含杂气；本版不将全月一律视为土司令，不硬判日主季节强弱。'
    roots = [{"pillar": p["label"], "branch": p["ganzhi"][1], "stem": h["stem"],
              "rank": "本气" if j == 0 else "非本气", "ten_god": h["ten_god"]}
             for p in pillars for j, h in enumerate(p["hidden"]) if element(h["stem"]) == element(day)]
    stems = [{"pillar": p["label"], "stem": p["ganzhi"][0], "ten_god": p["ten_god"]}
             for i, p in enumerate(pillars) if i != 2]
    help_gods = {"比肩", "劫财", "正印", "偏印"}
    support = [s for s in stems if s["ten_god"] in help_gods]
    drain = [s for s in stems if s["ten_god"] not in help_gods]
    exposed = [{"stem": s, "ten_god": ten_god(day, s),
                "positions": [p["label"] for i, p in enumerate(pillars) if i != 2 and p["ganzhi"][0] == s]}
               for s in HIDDEN[month]]
    exposed = [s for s in exposed if s["positions"]]
    lu = dict(zip(STEMS, "寅卯巳午巳午申酉亥子"))
    blades = dict(zip("甲丙戊庚壬", "卯午午酉子"))
    if month == lu[day]:
        candidate = "建禄候选"
    elif blades.get(day) == month:
        candidate = "阳刃候选"
    else:
        candidate = f"月令本气{god}取格线索"
    pattern = {"candidate": candidate, "month_main_stem": main, "month_main_god": god, "exposed": exposed}
    relation_list = relations(natal_nodes(chart))
    root_text = "、".join(r["pillar"] + r["branch"] + "藏" + r["stem"] + "（" + r["rank"] + "）" for r in roots) or "未检出同五行藏干根"
    stem_text = lambda group: "、".join(s["pillar"] + s["stem"] + s["ten_god"] for s in group) or "无"
    observations = [
        fact("natal_season", "日主" + day + "为" + ("阳" if STEMS.index(day) % 2 == 0 else "阴") + element(day) + "。" + season_text + "月令本气" + main + "，相对日主为" + god + "。", {"month": month, "main": main, "god": god}),
        fact("natal_roots", "日主同五行通根：" + root_text + "。位置和本气层级已保留，不加权计分。", roots),
        fact("natal_support", "其他明干印比：" + stem_text(support) + "；食伤财官杀：" + stem_text(drain) + "。藏干另见四柱。", {"support": support, "drain": drain}),
        fact("natal_pattern", candidate + "。月令藏干在年、月、时干透出：" + ("、".join(s["stem"] + s["ten_god"] + "见" + "/".join(s["positions"]) for s in exposed) or "无") + "。候选不等于成格，须看制化与配合。", pattern),
        fact("natal_relations", describe_relations(relation_list) + "。仅记录结构，不自动合化、解除冲刑或判定吉凶。", relation_list),
    ]
    luck_facts = []
    if chart["luck"]:
        for i, p in enumerate(chart["luck"]["periods"]):
            n = {"id": f"luck_{i}", "label": f"第{i + 1}步大运", "ganzhi": p["ganzhi"]}
            interactions = relations(natal_nodes(chart) + [n], [n["id"]])
            data = {**p, "ten_god": ten_god(day, p["ganzhi"][0]),
                    "hidden": [{"stem": s, "ten_god": ten_god(day, s)} for s in HIDDEN[p["ganzhi"][1]]], "relations": interactions}
            luck_facts.append(fact(n["id"], f'{n["label"]}{p["ganzhi"]}，天干{data["ten_god"]}；{p["start"]}至{p["end"]}（不含结束）。' + describe_relations(interactions), data))
    else:
        luck_facts.append(fact("luck_missing", "未提供传统排运参数，不能判断大运顺逆、起运及运年叠加。"))
    years = list(range(chart["input"]["selected_year"], min(chart["input"]["selected_year"] + 5, 2100)))
    year_facts = [annual_evidence(chart, y) for y in years]
    chart["facts"] += observations + luck_facts + year_facts
    chart["analysis"] = {"version": VERSION, "observations": observations, "pattern": pattern,
                         "luck": luck_facts, "years": year_facts, "year_range": years}
    return chart
