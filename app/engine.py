"""Deterministic calculation. All astronomical comparisons use UTC+08 clocks."""
import calendar
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
from typing import Literal, Optional
from zoneinfo import ZoneInfo, reset_tzpath

from lunar_python import Lunar, Solar
from pydantic import BaseModel, Field

STEMS = "甲乙丙丁戊己庚辛壬癸"
BRANCHES = "子丑寅卯辰巳午未申酉戌亥"
ELEMENTS = "木火土金水"
BRANCH_ELEMENTS = "水土木木土火火土金金土水"
HIDDEN = dict(zip(BRANCHES, ["癸", "己癸辛", "甲丙戊", "乙", "戊乙癸", "丙庚戊", "丁己", "己丁乙", "庚壬戊", "辛", "戊辛丁", "壬甲"]))
GODS = [("比肩", "劫财"), ("食神", "伤官"), ("偏财", "正财"), ("七杀", "正官"), ("偏印", "正印")]
reset_tzpath(())
ZONE = ZoneInfo.no_cache("Asia/Shanghai")
STANDARD = timezone(timedelta(hours=8))
PROFILE = "standard-23-minute-qiyun-v0.1"


class BirthInput(BaseModel):
    calendar: Literal["solar", "lunar"] = "solar"
    year: int = Field(ge=1970, le=2099)
    month: int = Field(ge=1, le=12)
    day: int = Field(ge=1, le=31)
    leap: bool = False
    time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    clock: Literal["historical", "standard"] = "historical"
    fold: Optional[Literal[0, 1]] = None
    gender: Optional[Literal[0, 1]] = None
    selected_year: int = Field(default=2026, ge=1970, le=2099)


def ten_god(day_stem: str, stem: str) -> str:
    d, s = STEMS.index(day_stem), STEMS.index(stem)
    return GODS[(s // 2 - d // 2) % 5][int(d % 2 != s % 2)]


def add_calendar(dt: datetime, years=0, months=0) -> datetime:
    # Apply years before months, preserving the specified month-end policy.
    y = dt.year + years
    dt = dt.replace(year=y, day=min(dt.day, calendar.monthrange(y, dt.month)[1]))
    n = dt.year * 12 + dt.month - 1 + months
    y, m = n // 12, n % 12 + 1
    return dt.replace(year=y, month=m, day=min(dt.day, calendar.monthrange(y, m)[1]))


def normalize(birth: BirthInput):
    h, minute = map(int, birth.time.split(":"))
    if birth.calendar == "lunar":
        lunar = Lunar.fromYmdHms(birth.year, -birth.month if birth.leap else birth.month, birth.day, h, minute, 0)
        solar = lunar.getSolar()
        wall = datetime(solar.getYear(), solar.getMonth(), solar.getDay(), h, minute)
        check = solar.getLunar()
        if (check.getYear(), check.getMonth(), check.getDay()) != (birth.year, -birth.month if birth.leap else birth.month, birth.day):
            raise ValueError("农历日期或闰月不存在，请核对。")
    else:
        if birth.leap:
            raise ValueError("公历输入不能选择农历闰月。")
        wall = datetime(birth.year, birth.month, birth.day, h, minute)
    if wall.date() > datetime.now(ZONE).date():
        raise ValueError("出生日期不能晚于今天。")
    zone = ZONE if birth.clock == "historical" else STANDARD
    valid = []
    for fold in (0, 1):
        candidate = wall.replace(tzinfo=zone, fold=fold)
        back = candidate.astimezone(timezone.utc).astimezone(zone)
        if back.replace(tzinfo=None) == wall and all(candidate.utcoffset() != v.utcoffset() for v in valid):
            valid.append(candidate)
    if not valid:
        raise ValueError("这个钟表时刻落在夏令时跳过的区间，请核对出生记录。")
    if len(valid) > 1 and birth.fold is None:
        raise ValueError("该钟表时刻因夏令时结束出现两次，请在时间设置中选择第一次或第二次。")
    aware = next((v for v in valid if v.fold == birth.fold), valid[0])
    standard = aware.astimezone(STANDARD)
    warnings = []
    if aware.utcoffset() != STANDARD.utcoffset(None):
        warnings.append("已将历史夏令时换算为东八区标准时间；节气仍按实际时刻比较。")
    if birth.gender is None:
        warnings.append("未提供传统排运参数，本次不计算大运方向与起运。")
    return wall, standard, warnings


def solar_of(dt):
    return Solar.fromYmdHms(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)


def lichun(year):
    term = Solar.fromYmdHms(year, 6, 1, 12, 0, 0).getLunar().getJieQiTable()["立春"]
    return term.toYmdHms() + "+08:00"


def calculate(birth: BirthInput):
    wall, standard, warnings = normalize(birth)
    lunar = solar_of(standard).getLunar()
    eight = lunar.getEightChar()
    eight.setSect(1)
    pairs = [eight.getYear(), eight.getMonth(), eight.getDay(), eight.getTime()]
    day_stem = pairs[2][0]
    pillars, facts = [], []
    for index, (label, pair) in enumerate(zip(["年柱", "月柱", "日柱", "时柱"], pairs)):
        stem, branch = pair
        god = ten_god(day_stem, stem)
        pillars.append({"label": label, "ganzhi": pair, "stem_element": ELEMENTS[STEMS.index(stem) // 2],
                        "branch_element": BRANCH_ELEMENTS[BRANCHES.index(branch)],
                        "ten_god": "日主" if index == 2 else god,
                        "hidden": [{"stem": s, "ten_god": ten_god(day_stem, s)} for s in HIDDEN[branch]]})
        facts.append({"id": "pillar_" + str(index), "text": label + "为" + pair + ("，天干十神为" + god if index != 2 else "，日主为" + stem), "source": "lunar-python 1.4.9 / " + PROFILE})
    counts = {e: 0 for e in ELEMENTS}
    for p in pillars:
        counts[p["stem_element"]] += 1
        counts[p["branch_element"]] += 1
    facts.append({"id": "element_counts", "text": "天干与地支主气计数：" + "、".join(e + str(n) for e, n in counts.items()) + "；不代表五行强弱。", "source": "明确计数口径"})
    luck = None
    if birth.gender is not None:
        yun = eight.getYun(birth.gender, 2)
        start = add_calendar(standard, yun.getStartYear(), yun.getStartMonth()) + timedelta(days=yun.getStartDay(), hours=yun.getStartHour())
        periods = []
        for i, item in enumerate(yun.getDaYun(9)[1:]):
            periods.append({"ganzhi": item.getGanZhi(), "start": add_calendar(start, i * 10).isoformat(), "end": add_calendar(start, (i + 1) * 10).isoformat()})
        luck = {"direction": "顺行" if yun.isForward() else "逆行", "start": start.isoformat(),
                "nominal_age": {"years": yun.getStartYear(), "months": yun.getStartMonth(), "days": yun.getStartDay(), "hours": yun.getStartHour()}, "periods": periods}
        facts.append({"id": "luck_start", "text": "大运" + luck["direction"] + "，首运自" + start.isoformat() + "开始。", "source": "三日折一年，分钟分解与历法加法"})
    year = birth.selected_year
    annual_pair = STEMS[(year - 4) % 10] + BRANCHES[(year - 4) % 12]
    annual = {"year": year, "ganzhi": annual_pair, "start": lichun(year), "end": lichun(year + 1), "ten_god": ten_god(day_stem, annual_pair[0])}
    facts.append({"id": "annual", "text": str(year) + "流年为" + annual_pair + "，以当年立春至次年立春为界，天干十神为" + annual["ten_god"], "source": "立春换年 / 相对原局日主"})
    return {"input": birth.model_dump(), "wall_time": wall.isoformat(), "standard_time": standard.isoformat(),
            "lunar_date": lunar.toString(), "profile": PROFILE, "engine": "lunar-python " + version("lunar-python"),
            "tzdata": version("tzdata"), "pillars": pillars, "elements": counts, "luck": luck, "annual": annual,
            "facts": facts, "warnings": warnings,
            "limitations": ["v0.1仅支持准确到分钟的出生时间；未知时刻、太阳时与多流派对照尚未实现。", "历史钟表时间采用Asia/Shanghai规则，不适用于新疆地方时间等其他钟表口径。", "计算一致性不等于命理预测有效性。"]}
