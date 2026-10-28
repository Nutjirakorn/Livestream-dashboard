"""
Supersports Live Schedule & Performance dashboard (Streamlit).

Reads the Shopee and TikTok Google Sheets directly, applies the same mapping as the
claude.ai dashboard, and reloads automatically when any sheet is edited.

Setup: see README.md (service account in .streamlit/secrets.toml, share the sheets with it).
"""
from __future__ import annotations

import datetime as dt
import re
import time
from zoneinfo import ZoneInfo

import gspread
import pandas as pd
import plotly.express as px
import streamlit as st
from google.oauth2.service_account import Credentials

try:  # optional: re-runs the page on a timer so new sheet data shows up by itself
    from streamlit_autorefresh import st_autorefresh
except ImportError:  # pragma: no cover
    st_autorefresh = None

# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------
SHOPEE_ID = "1RpP3HY6yTYnFqxFpF0NhwBRa70hCbynroZddm17dLIo"       # Shopee_Live_Tracking
TT_SUPERSPORTS_ID = "1yJU84We3_yzPScbhKMv3pjcaiuv8IzWhHlgcD3Z-jO4"  # TikTok - Jazz Working File
TT_PERFORMANCE_ID = "1b0olOO85X6zsK2WCW1XeV2XhC5tq8IJmZNLZpUWiuEw"  # Supersports TT Brief-Supersports Performance

SHOPEE_SHEET = "Report - By Live"
TT_CALENDAR_SHEET = "Daily Live Calendar"
TT_RAW_SHEET = "Live Raw Data"
TT_PERF_TEMPLATE_SHEET = "Live Template(NEW)"
TT_PERF_RAW_SHEET = "Live Raw Data"  # used if it is ever added to the Performance file

CHECK_EVERY_SECONDS = 60          # how often the page checks whether a sheet changed
FALLBACK_RELOAD_MINUTES = 10      # reload interval if the change check is not available
TZ = ZoneInfo("Asia/Bangkok")

SH_COLOR, TT_COLOR = "#EE4D2D", "#1F9FB4"
TYPE_COLORS = {"Agency": "#7A5AF8", "Internal MCs": "#16A34A", "KOLs": "#F59E0B"}
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]

st.set_page_config(page_title="Supersports Live Dashboard", page_icon="📺", layout="wide")

# ----------------------------------------------------------------------------
# Google Sheets access
# ----------------------------------------------------------------------------
@st.cache_resource
def gclient() -> gspread.Client:
    creds = Credentials.from_service_account_info(dict(st.secrets["gcp_service_account"]), scopes=SCOPES)
    return gspread.authorize(creds)


@st.cache_data(ttl=CHECK_EVERY_SECONDS, show_spinner=False)
def sheet_versions() -> tuple:
    """Last-edit time of each spreadsheet. A change here makes the data below reload."""
    out = []
    for fid in (SHOPEE_ID, TT_SUPERSPORTS_ID, TT_PERFORMANCE_ID):
        try:
            out.append(gclient().open_by_key(fid).get_lastUpdateTime())
        except Exception:
            out.append(int(time.time() // (FALLBACK_RELOAD_MINUTES * 60)))
    return tuple(out)


def batch_values(file_id: str, titles: list[str]) -> dict[str, list[list[str]]]:
    """Read several tabs of one spreadsheet in a single API call (values as shown in the sheet)."""
    sh = gclient().open_by_key(file_id)
    ranges = [f"'{t}'" for t in titles]
    res = sh.values_batch_get(ranges, params={"valueRenderOption": "FORMATTED_VALUE"})
    out = {}
    for t, vr in zip(titles, res.get("valueRanges", [])):
        out[t] = vr.get("values", [])
    return out


# ----------------------------------------------------------------------------
# Parsing helpers
# ----------------------------------------------------------------------------
MON = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def num(s) -> float:
    s = str(s or "").replace(",", "").replace("฿", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def hms_to_min(s) -> float:
    m = re.match(r"^(\d+):(\d+)(?::(\d+))?$", str(s or "").strip())
    return int(m[1]) * 60 + int(m[2]) + int(m[3] or 0) / 60 if m else 0.0


def clock_to_min(s):
    m = re.match(r"^(\d+)(?::(\d+))?\s*(AM|PM)$", str(s or "").strip(), re.I)
    if not m:
        return None
    return (int(m[1]) % 12 + (12 if m[3].upper() == "PM" else 0)) * 60 + int(m[2] or 0)


def clock(s) -> str:
    v = clock_to_min(s)
    return "" if v is None else f"{v // 60:02d}:{v % 60:02d}"


def to_aloshop(s: str) -> str:
    return re.sub("nsq", "Aloshop", s or "", flags=re.I)


def type_of(operate: str) -> str:
    o = operate.lower()
    if "agency" in o or "aloshop" in o:
        return "Agency"
    if "kol" in o:
        return "KOLs"
    return "Internal MCs"


def cell(row, i):
    return row[i].strip() if i is not None and i < len(row) else ""


def header_index(values, must: list[str], max_scan: int = 10):
    """Find the header row that contains every name in `must`; return (row_no, {name: col})."""
    for r, row in enumerate(values[:max_scan]):
        cells = [str(c).strip() for c in row]
        if all(any(c == m or c.startswith(m) for c in cells) for m in must):
            idx = {}
            for i, c in enumerate(cells):
                idx.setdefault(c, i)
                idx[c + "#last"] = i
            return r, idx
    raise ValueError(f"Header with {must} not found")


def find_col(idx: dict, name: str, last=False, startswith=False):
    if startswith:
        hits = [v for k, v in idx.items() if k.startswith(name) and not k.endswith("#last")]
        return (max(hits) if last else min(hits)) if hits else None
    return idx.get(name + "#last") if last else idx.get(name)


# ----------------------------------------------------------------------------
# Source readers
# ----------------------------------------------------------------------------
SH_OPERATE = {"Agency(aloshop)": "Agency (Aloshop)"}
SH_ACCOUNT = {"SSP Official": "Supersports"}
TT_ACCOUNT = {"supersports": "Supersports", "supersportsstore": "Supersports",
              "supersportsfootball": "Supersports Football", "supersportsmahachai": "Supersports Mahachai"}


def read_shopee(values):
    hr, ix = header_index(values, ["Year", "Date", "Live Operate", "Live Account", "Actual Duration"])
    c = lambda n, **k: find_col(ix, n, **k)
    cy, cd, cop, cac = c("Year"), c("Date"), c("Live Operate"), c("Live Account")
    cs, ce, cplan, cdur = c("Live Starts"), c("Live Ends"), c("Duration Plan"), c("Actual Duration")
    cg = c("GMV", startswith=True)
    sched, perf = [], []
    for row in values[hr + 1:]:
        y, d = cell(row, cy), cell(row, cd)
        if not y.isdigit() or "-" not in d:
            continue
        day, mon = d.split("-")[:2]
        try:
            date = dt.date(int(y), MON[mon[:3].lower()], int(day))
        except (KeyError, ValueError):
            continue
        raw_op = cell(row, cop)
        op = SH_OPERATE.get(raw_op, to_aloshop(raw_op))
        ac = SH_ACCOUNT.get(cell(row, cac), cell(row, cac))
        ty = type_of(raw_op)
        sched.append(dict(date=date, channel="Shopee", account=ac, operate=op, type=ty,
                          start=clock(cell(row, cs)), end=clock(cell(row, ce)), plan_hrs=hms_to_min(cell(row, cplan)) / 60))
        g, du = num(cell(row, cg)), hms_to_min(cell(row, cdur))
        if g > 0 or du > 0:
            perf.append(dict(date=date, channel="Shopee", account=ac, operate=op, type=ty, gmv=g, hrs=du / 60, lives=1))
    return sched, perf


def tt_operate(mc: str) -> str:
    return "Agency (Aloshop)" if mc.lower().startswith("agency") else to_aloshop(mc)


def read_tt_calendar(values):
    hr, ix = header_index(values, ["Date", "Live Account", "MC", "Live Start", "Live End", "Duration"])
    c = lambda n, **k: find_col(ix, n, **k)
    cd, cac, cmc, cs, ce, cdu = c("Date"), c("Live Account"), c("MC"), c("Live Start"), c("Live End"), c("Duration")
    out = []
    for row in values[hr + 1:]:
        try:
            date = dt.datetime.strptime(cell(row, cd), "%d-%b-%Y").date()
        except ValueError:
            continue
        acc, mc = cell(row, cac), cell(row, cmc)
        if not acc:
            continue
        if mc == "KOL" or acc not in TT_ACCOUNT:
            ac, op, ty = "KOLs", acc, "KOLs"
        else:
            ac, op = TT_ACCOUNT[acc], tt_operate(mc)
            ty = type_of(op)
        s, e = clock_to_min(cell(row, cs)), clock_to_min(cell(row, ce))
        plan = hms_to_min(cell(row, cdu))
        if not plan and s is not None and e is not None:
            plan = (e - s) % 1440
        out.append(dict(date=date, channel="TikTok", account=ac, operate=op, type=ty,
                        start=clock(cell(row, cs)), end=clock(cell(row, ce)), plan_hrs=plan / 60))
    return out


def read_tt_raw(values, account_override: str | None = None):
    hr, ix = header_index(values, ["Nickname", "Revenue", "KOL Name", "Duration(minute)"])
    c = lambda n, **k: find_col(ix, n, **k)
    cn, cg, ck, cdu = c("Nickname"), c("Revenue", startswith=True), c("KOL Name"), c("Duration(minute)")
    cd = c("Date", last=True)
    out = []
    for row in values[hr + 1:]:
        nick = cell(row, cn)
        if not nick:
            continue
        try:
            date = dt.datetime.strptime(cell(row, cd), "%m/%d/%Y").date()
        except ValueError:
            continue
        kn = cell(row, ck)
        if account_override:
            ac, op = account_override, ("Agency (Aloshop)" if re.search("agency|nsq|aloshop", kn, re.I) else (kn or "Other operators"))
        elif nick == "supersportsstore":
            ac, op = "Supersports", ("Agency (Aloshop)" if "agency" in kn.lower() else "Internal Live")
        elif nick in ("supersportsfootball", "supersportsmahachai"):
            ac, op = TT_ACCOUNT[nick], "Internal Live"
        else:
            ac, op = "KOLs", (kn if kn and kn != "Internal Live" else nick)
        ty = "KOLs" if ac == "KOLs" else type_of(op)
        out.append(dict(date=date, channel="TikTok", account=ac, operate=op, type=ty,
                        gmv=num(cell(row, cg)), hrs=num(cell(row, cdu)) / 60, lives=1))
    return out


def read_perf_template(values):
    hr, ix = header_index(values, ["Date", "Live Operate", "Live Starts", "Duration Plan"])
    c = lambda n: find_col(ix, n)
    cd, cop, cs, ce, cdu = c("Date"), c("Live Operate"), c("Live Starts"), c("formular Live Ends") or c("Live Ends"), c("Duration Plan")
    out = []
    for row in values[hr + 1:]:
        try:
            date = dt.datetime.strptime(cell(row, cd), "%d-%b-%Y").date()
        except ValueError:
            continue
        raw = cell(row, cop)
        if not raw:
            continue
        op = SH_OPERATE.get(raw, to_aloshop(raw))
        s, e = clock_to_min(cell(row, cs)), clock_to_min(cell(row, ce))
        plan = hms_to_min(cell(row, cdu)) or ((e - s) % 1440 if s is not None and e is not None else 0)
        out.append(dict(date=date, channel="TikTok", account="Supersports Performance", operate=op, type=type_of(raw),
                        start=clock(cell(row, cs)), end=clock(cell(row, ce)), plan_hrs=plan / 60))
    return out


PERF_TAB = re.compile(r"^(jan|feb|mar|apr|may|jun|june|jul|july|aug|sep|oct|nov|dec)(\d{2})?\s*perf$", re.I)


def perf_tab_month(title: str):
    m = PERF_TAB.match(title.strip())
    if not m:
        return None
    year = 2000 + int(m[2]) if m[2] else 2025  # tabs without a year (Apr Perf ... Dec Perf) are 2025
    return year, MON[m[1][:3].lower()]


def read_perf_month(values, year: int, month: int):
    """Daily Total Shop and Aloshop GMV/hours from one monthly Perf tab."""
    # find the "Date" cell whose next row starts with a day like "1-July"
    start = None
    for r, row in enumerate(values):
        for c0, v in enumerate(row[:3]):
            if v.strip() == "Date" and any(re.match(r"^\d{1,2}-[A-Za-z]+$", cell(nx, c0)) for nx in values[r + 1:r + 4]):
                start = (r, c0)
                break
        if start:
            break
    if not start:
        return []
    r0, c0 = start
    days = [row for row in values[r0 + 1:] if re.match(r"^\d{1,2}-[A-Za-z]+$", cell(row, c0))]
    g1 = c0 + 3  # Total Shop GMV
    # Aloshop GMV column: the one that equals Total Shop GMV on Aloshop-only days and never exceeds it
    best, best_hits = None, -1
    width = max((len(r) for r in days), default=0)
    for c in range(g1 + 5, width):
        hits, ok = 0, True
        for row in days:
            a, t = num(cell(row, c)), num(cell(row, g1))
            if a > t + 1:
                ok = False
                break
            hits += int(t > 0 and abs(a - t) < 1)
        if ok and hits > best_hits:
            best, best_hits = c, hits
    if best is None:
        return []
    h1, g2 = best - 1, best
    h2 = g2 + (h1 - g1)
    out = []
    for row in days:
        d = int(cell(row, c0).split("-")[0])
        try:
            date = dt.date(year, month, d)
        except ValueError:
            continue
        gt, ht, ga, ha = num(cell(row, g1)), num(cell(row, h1)), num(cell(row, g2)), num(cell(row, h2))
        if ga or ha:
            out.append(dict(date=date, channel="TikTok", account="Supersports Performance", operate="Agency (Aloshop)",
                            type="Agency", gmv=ga, hrs=ha, lives=None))
        if (gt - ga) or (ht - ha):
            out.append(dict(date=date, channel="TikTok", account="Supersports Performance", operate="Other operators",
                            type="Internal MCs", gmv=gt - ga, hrs=ht - ha, lives=None))
    return out


@st.cache_data(ttl=24 * 3600, show_spinner="Loading the latest data from Google Sheets…")
def load_all(versions: tuple):
    notes = []
    # Shopee
    shp = batch_values(SHOPEE_ID, [SHOPEE_SHEET])[SHOPEE_SHEET]
    sched, perf = read_shopee(shp)
    # TikTok Supersports + KOLs
    tt = batch_values(TT_SUPERSPORTS_ID, [TT_CALENDAR_SHEET, TT_RAW_SHEET])
    sched += read_tt_calendar(tt[TT_CALENDAR_SHEET])
    perf += read_tt_raw(tt[TT_RAW_SHEET])
    # TikTok Supersports Performance
    sh = gclient().open_by_key(TT_PERFORMANCE_ID)
    titles = [w.title for w in sh.worksheets()]
    tabs = [TT_PERF_TEMPLATE_SHEET] if TT_PERF_TEMPLATE_SHEET in titles else []
    has_raw = TT_PERF_RAW_SHEET in titles
    perf_tabs = [t for t in titles if perf_tab_month(t)]
    if has_raw:
        tabs.append(TT_PERF_RAW_SHEET)
    else:
        tabs += perf_tabs
    pv = batch_values(TT_PERFORMANCE_ID, tabs) if tabs else {}
    if TT_PERF_TEMPLATE_SHEET in pv:
        sched += read_perf_template(pv[TT_PERF_TEMPLATE_SHEET])
    if has_raw:
        perf += read_tt_raw(pv[TT_PERF_RAW_SHEET], account_override="Supersports Performance")
    else:
        notes.append("Supersports Performance has no 'Live Raw Data' tab, so its results come from the monthly Perf tabs "
                     "(Aloshop = Agency, the rest of the account = Internal MCs 'Other operators').")
        for t in perf_tabs:
            y, m = perf_tab_month(t)
            perf += read_perf_month(pv[t], y, m)
    s = pd.DataFrame(sched)
    p = pd.DataFrame(perf)
    s["date"] = pd.to_datetime(s["date"])
    p["date"] = pd.to_datetime(p["date"])
    return s, p, notes, dt.datetime.now(TZ)


# ----------------------------------------------------------------------------
# Periods and comparisons
# ----------------------------------------------------------------------------
def month_range(y, m):
    first = dt.date(y + (m - 1) // 12, (m - 1) % 12 + 1, 1)
    nxt = dt.date(first.year + first.month // 12, first.month % 12 + 1, 1)
    return first, nxt - dt.timedelta(days=1)


def preset_range(name: str):
    t = dt.datetime.now(TZ).date()
    q = (t.month - 1) // 3
    if name == "This month":
        return month_range(t.year, t.month)
    if name == "Last month":
        return month_range(t.year, t.month - 1)
    if name == "Next month":
        return month_range(t.year, t.month + 1)
    if name == "This quarter":
        return month_range(t.year, q * 3 + 1)[0], month_range(t.year, q * 3 + 3)[1]
    if name == "Last quarter":
        return month_range(t.year, q * 3 - 2)[0], month_range(t.year, q * 3)[1]
    if name == "This year":
        return dt.date(t.year, 1, 1), dt.date(t.year, 12, 31)
    if name == "Last year":
        return dt.date(t.year - 1, 1, 1), dt.date(t.year - 1, 12, 31)
    return None


def shift_months(d: dt.date, n: int, to_end=False) -> dt.date:
    first, last = month_range(d.year, d.month + n)
    return last if to_end else min(first.replace(day=d.day) if d.day <= last.day else last, last)


def is_month_end(d: dt.date) -> bool:
    return (d + dt.timedelta(days=1)).day == 1


def shift_window(s, e, n):
    return shift_months(s, n), shift_months(e, n, to_end=is_month_end(e))


def pct(a, b):
    return None if not b else a / b - 1


def fmt_pct(v):
    return "n/a" if v is None else f"{v:+.1%}"


def thb(v):
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(v) >= div:
            return f"฿{v / div:,.2f}{unit}"
    return f"฿{v:,.0f}"


def color_pct(v):
    if isinstance(v, str) and v.startswith("+"):
        return "color: #15803d"
    if isinstance(v, str) and v.startswith("-"):
        return "color: #b91c1c"
    return "color: #888"


# ----------------------------------------------------------------------------
# Page
# ----------------------------------------------------------------------------
if st_autorefresh:
    st_autorefresh(interval=CHECK_EVERY_SECONDS * 1000, key="auto_check")

st.markdown("""
<style>
.block-container {padding-top: 1.6rem;}
div[data-testid="stMetricValue"] {font-size: 1.6rem;}
</style>""", unsafe_allow_html=True)

st.title("Supersports Live Schedule & Performance")

try:
    sched_all, perf_all, notes, loaded_at = load_all(sheet_versions())
except Exception as exc:  # show a readable error instead of a stack trace
    st.error(f"Could not read the Google Sheets: {exc}. Check that each sheet is shared with the service account "
             f"in secrets.toml and that the tab names are unchanged.")
    st.stop()

# ---- Filters
f1, f2, f3, f4, f5 = st.columns([1.2, 1, 1, 1.2, 1])
presets = ["This month", "Last month", "Next month", "This quarter", "Last quarter", "This year", "Last year", "Custom dates"]
preset = f1.selectbox("Period", presets, index=0)
default = preset_range(preset) or preset_range("This month")
if preset == "Custom dates":
    rng = f2.date_input("From – To", value=default, format="YYYY-MM-DD")
    start, end = (rng if isinstance(rng, (list, tuple)) and len(rng) == 2 else default)
    f3.write("")
else:
    start, end = default
    f2.metric("From", start.strftime("%b %d, %Y"))
    f3.metric("To", end.strftime("%b %d, %Y"))
channel = f4.selectbox("Channel", ["All (TikTok + Shopee)", "TikTok", "Shopee"])
ttype = f5.selectbox("Type", ["All types", "Agency", "Internal MCs", "KOLs"])
split = st.radio("Split charts by", ["Channel", "Type"], horizontal=True)

c1, c2 = st.columns([4, 1])
c1.caption(f"Data loaded {loaded_at:%b %d, %Y %H:%M} (Bangkok). The page checks for sheet edits every "
           f"{CHECK_EVERY_SECONDS} seconds and reloads when a sheet changes.")
if c2.button("Refresh now"):
    st.cache_data.clear()
    st.rerun()


def keep(df: pd.DataFrame) -> pd.DataFrame:
    m = pd.Series(True, index=df.index)
    if channel != "All (TikTok + Shopee)":
        m &= df["channel"] == channel
    if ttype != "All types":
        m &= df["type"] == ttype
    return df[m]


def window(df, s, e):
    return keep(df[(df["date"] >= pd.Timestamp(s)) & (df["date"] <= pd.Timestamp(e))])


ctx_label = f"{start:%b %d, %Y} – {end:%b %d, %Y} · {channel.split(' (')[0] if channel.startswith('All') else channel} · {ttype}"

last_result = perf_all.loc[perf_all["gmv"] > 0, "date"].max().date()
pe = min(end, last_result)
cur_s, cur_e = start, pe
lm_s, lm_e = shift_window(cur_s, cur_e, -1)
ly_s, ly_e = shift_window(cur_s, cur_e, -12)
no_results = pe < start

S = window(sched_all, start, end)
P = window(perf_all, cur_s, cur_e) if not no_results else perf_all.iloc[0:0]
PLM = window(perf_all, lm_s, lm_e)
PLY = window(perf_all, ly_s, ly_e)


def bucket(df: pd.DataFrame, s, e) -> tuple[pd.DataFrame, str]:
    days = (e - s).days + 1
    unit = "month" if days > 120 else "week" if days > 45 else "day"
    d = df.copy()
    if unit == "month":
        d["bucket"] = d["date"].dt.to_period("M").dt.start_time
    elif unit == "week":
        d["bucket"] = d["date"] - pd.to_timedelta(d["date"].dt.weekday, unit="D")
    else:
        d["bucket"] = d["date"]
    return d, unit


def bar_chart(df, value, s, e, y_title, money=False):
    d, unit = bucket(df, s, e)
    key = "channel" if split == "Channel" else "type"
    g = d.groupby(["bucket", key], as_index=False)[value].sum()
    cmap = {"Shopee": SH_COLOR, "TikTok": TT_COLOR, **TYPE_COLORS}
    fig = px.bar(g, x="bucket", y=value, color=key, color_discrete_map=cmap,
                 labels={"bucket": unit.title(), value: y_title, key: ""})
    fig.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=0), legend=dict(orientation="h", y=1.1), bargap=0.2)
    fig.update_xaxes(range=[pd.Timestamp(s) - pd.Timedelta(days=0.6), pd.Timestamp(e) + pd.Timedelta(days=0.6)])
    if money:
        fig.update_yaxes(tickprefix="฿")
    st.plotly_chart(fig, width="stretch")
    return unit


tab_a, tab_b = st.tabs(["A · Live Schedule", "B · Live Performance"])

# ============================ A: LIVE SCHEDULE ============================
G = {
    "Total 2 channels": (0, lambda d: d.index == d.index),
    "Shopee": (1, lambda d: d.channel == "Shopee"),
    "Shopee · Supersports account": (2, lambda d: (d.channel == "Shopee") & (d.account == "Supersports")),
    "Shopee · Supersports · Internal MCs": (3, lambda d: (d.channel == "Shopee") & (d.account == "Supersports") & (d.type == "Internal MCs")),
    "Shopee · Supersports · Agency (Aloshop)": (3, lambda d: (d.channel == "Shopee") & (d.account == "Supersports") & (d.type == "Agency")),
    "Shopee · Supersports · KOLs": (3, lambda d: (d.channel == "Shopee") & (d.account == "Supersports") & (d.type == "KOLs")),
    "Shopee · SSP Livestream account": (2, lambda d: (d.channel == "Shopee") & (d.account == "SSP Livestream")),
    "TikTok": (1, lambda d: d.channel == "TikTok"),
    "TikTok · Supersports account": (2, lambda d: (d.channel == "TikTok") & (d.account == "Supersports")),
    "TikTok · Supersports · Internal MCs": (3, lambda d: (d.channel == "TikTok") & (d.account == "Supersports") & (d.type == "Internal MCs")),
    "TikTok · Supersports · Agency (Aloshop)": (3, lambda d: (d.channel == "TikTok") & (d.account == "Supersports") & (d.type == "Agency")),
    "TikTok · Supersports Performance account": (2, lambda d: (d.channel == "TikTok") & (d.account == "Supersports Performance")),
    "TikTok · KOLs accounts": (2, lambda d: (d.channel == "TikTok") & (d.account == "KOLs")),
    "TikTok · Other own accounts (Football, Mahachai)": (2, lambda d: (d.channel == "TikTok") & d.account.isin(["Supersports Football", "Supersports Mahachai"])),
}

with tab_a:
    st.subheader("Live schedule summary")
    st.caption(ctx_label)
    tot_h = S["plan_hrs"].sum()
    k = st.columns(4)
    k[0].metric("Total lives planned", f"{len(S):,}", help=f"{tot_h:,.1f} hrs · {S['date'].nunique()} live days")
    for col, ch in ((k[1], "Shopee"), (k[2], "TikTok")):
        d = S[S.channel == ch]
        col.metric(f"{ch} lives", f"{len(d):,}", f"{d['plan_hrs'].sum():,.1f} hrs planned", delta_color="off")
    shares = [S.loc[S.type == t, "plan_hrs"].sum() / tot_h if tot_h else 0 for t in ("Agency", "Internal MCs", "KOLs")]
    k[3].metric("Agency · Internal MCs · KOLs (share of hrs)", " · ".join(f"{v:.0%}" for v in shares))

    rows = []
    for label, (lvl, f) in G.items():
        d = S[f(S)]
        n, h = len(d), d["plan_hrs"].sum()
        rows.append({"Channel / account / live operate": ("    " * lvl) + label.split(" · ")[-1] if lvl else label,
                     "Lives": n, "Planned hrs": round(h, 1), "Live days": d["date"].nunique(),
                     "Avg hrs per live": round(h / n, 1) if n else None, "Share of hrs": f"{h / tot_h:.1%}" if tot_h and n else "–"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=36 * (len(rows) + 1) + 4)

    a1, a2 = st.columns(2)
    with a1:
        st.markdown("**Live plan by channel** (planned hours)")
        bar_chart(S, "plan_hrs", start, end, "Planned hrs")
    with a2:
        st.markdown("**Live calendar** (lives per day)")
        cal = S.groupby([S["date"].dt.date, "channel"]).size().unstack(fill_value=0)
        cal["Hours"] = S.groupby(S["date"].dt.date)["plan_hrs"].sum().round(1)
        cal.index = [d.strftime("%a %b %d") for d in cal.index]
        st.dataframe(cal, width="stretch", height=320)

    st.markdown("**Schedule by live operate**")
    op = (S.groupby(["operate", "type", "channel", "account"], as_index=False)
            .agg(Lives=("date", "size"), **{"Planned hrs": ("plan_hrs", "sum")})
            .sort_values("Planned hrs", ascending=False))
    op["Share of hrs"] = (op["Planned hrs"] / tot_h).map("{:.1%}".format) if tot_h else "–"
    op["Planned hrs"] = op["Planned hrs"].round(1)
    st.dataframe(op.rename(columns=str.title), hide_index=True, width="stretch")

    st.markdown("**Daily live plan**")
    q = st.text_input("Search date, operate or account", "")
    lst = S.sort_values(["date", "start"]).assign(Date=lambda d: d["date"].dt.strftime("%a %b %d, %Y"),
                                                   Time=lambda d: d["start"] + "–" + d["end"],
                                                   **{"Planned hrs": lambda d: d["plan_hrs"].round(1)})
    lst = lst[["Date", "Time", "channel", "account", "operate", "type", "Planned hrs"]].rename(columns=str.title)
    if q:
        lst = lst[lst.apply(lambda r: q.lower() in " ".join(map(str, r.values)).lower(), axis=1)]
    st.dataframe(lst, hide_index=True, width="stretch", height=420)

# ============================ B: LIVE PERFORMANCE ============================
PG = [
    ("Total 2 channels", lambda d: d.index == d.index),
    ("Shopee", lambda d: d.channel == "Shopee"),
    ("    Internal MCs", lambda d: (d.channel == "Shopee") & (d.type == "Internal MCs")),
    ("    Agency", lambda d: (d.channel == "Shopee") & (d.type == "Agency")),
    ("    KOLs", lambda d: (d.channel == "Shopee") & (d.type == "KOLs")),
    ("TikTok", lambda d: d.channel == "TikTok"),
    ("    Agency", lambda d: (d.channel == "TikTok") & (d.type == "Agency")),
    ("    Internal MCs", lambda d: (d.channel == "TikTok") & (d.type == "Internal MCs")),
    ("    KOLs", lambda d: (d.channel == "TikTok") & (d.type == "KOLs")),
]


def perf_table(field: str, label: str, decimals: int):
    tot = P[field].sum()
    rows = []
    for name, f in PG:
        v, lm, ly = P.loc[f(P), field].sum(), PLM.loc[f(PLM), field].sum(), PLY.loc[f(PLY), field].sum()
        r = lambda x: f"{x:,.{decimals}f}" if x else "–"
        rows.append({"Live operate": name, label: r(v), "Share": f"{v / tot:.1%}" if tot and v else "–",
                     "Last month": r(lm), "%CHG MoM": fmt_pct(pct(v, lm)),
                     "Last year": r(ly), "%CHG YoY": fmt_pct(pct(v, ly))})
    df = pd.DataFrame(rows)
    sty = df.style.map(color_pct, subset=["%CHG MoM", "%CHG YoY"]).apply(
        lambda r: ["font-weight: 700" if not r["Live operate"].startswith(" ") else "" for _ in r], axis=1)
    st.dataframe(sty, hide_index=True, width="stretch", height=36 * (len(rows) + 1) + 4)


with tab_b:
    st.subheader("Live performance summary")
    period_txt = "No results yet for this period" if no_results else f"{cur_s:%b %d} – {cur_e:%b %d, %Y}"
    st.caption(f"{period_txt} · compared with {lm_s:%b %d} – {lm_e:%b %d, %Y} (last month) and "
               f"{ly_s:%b %d} – {ly_e:%b %d, %Y} (last year). Results stop at {last_result:%b %d, %Y}.")

    def tile(col, title, cur, lm, ly, money=True, suffix=""):
        col.metric(title, thb(cur) if money else f"{cur:,.1f}{suffix}", f"MoM {fmt_pct(pct(cur, lm))}")
        col.caption(f"YoY {fmt_pct(pct(cur, ly))}")

    t = st.columns(5)
    tile(t[0], "GMV, 2 channels", P.gmv.sum(), PLM.gmv.sum(), PLY.gmv.sum())
    for col, ch in ((t[1], "Shopee"), (t[2], "TikTok")):
        tile(col, f"{ch} GMV", P.loc[P.channel == ch, "gmv"].sum(), PLM.loc[PLM.channel == ch, "gmv"].sum(), PLY.loc[PLY.channel == ch, "gmv"].sum())
    tile(t[3], "Live duration", P.hrs.sum(), PLM.hrs.sum(), PLY.hrs.sum(), money=False, suffix=" hrs")
    gph = lambda d: d.gmv.sum() / d.hrs.sum() if d.hrs.sum() else 0
    tile(t[4], "GMV per live hour", gph(P), gph(PLM), gph(PLY))

    st.markdown("### 1 · GMV")
    b1, b2 = st.columns(2)
    with b1:
        st.markdown("**GMV by date**")
        bar_chart(P, "gmv", cur_s, cur_e if not no_results else end, "GMV (THB)", money=True)
    with b2:
        st.markdown("**GMV by live operate**")
        perf_table("gmv", "GMV (THB)", 0)

    st.markdown("### 2 · Duration")
    b3, b4 = st.columns(2)
    with b3:
        st.markdown("**Live duration by date**")
        bar_chart(P, "hrs", cur_s, cur_e if not no_results else end, "Hours")
    with b4:
        st.markdown("**Duration by live operate**")
        perf_table("hrs", "Hours", 1)

    st.markdown("**Performance by live operate (detail)**")
    keys = ["operate", "type", "channel", "account"]
    cur = P.groupby(keys)[["gmv", "hrs"]].sum()
    lm = PLM.groupby(keys)[["gmv", "hrs"]].sum().add_suffix("_lm")
    ly = PLY.groupby(keys)[["gmv", "hrs"]].sum().add_suffix("_ly")
    det = cur.join(lm, how="left").join(ly, how="left").fillna(0).reset_index().sort_values("gmv", ascending=False)
    out = pd.DataFrame({
        "Live operate": det.operate, "Type": det.type, "Channel": det.channel, "Account": det.account,
        "GMV (THB)": det.gmv.map("{:,.0f}".format),
        "GMV %CHG MoM": [fmt_pct(pct(a, b)) for a, b in zip(det.gmv, det.gmv_lm)],
        "GMV %CHG YoY": [fmt_pct(pct(a, b)) for a, b in zip(det.gmv, det.gmv_ly)],
        "Hours": det.hrs.map("{:,.1f}".format),
        "Hours %CHG MoM": [fmt_pct(pct(a, b)) for a, b in zip(det.hrs, det.hrs_lm)],
        "Hours %CHG YoY": [fmt_pct(pct(a, b)) for a, b in zip(det.hrs, det.hrs_ly)],
        "GMV per hour": [f"{a / b:,.0f}" if b else "–" for a, b in zip(det.gmv, det.hrs)],
    })
    st.dataframe(out.style.map(color_pct, subset=[c for c in out.columns if "%CHG" in c]),
                 hide_index=True, width="stretch", height=420)

    for n in notes:
        st.caption(n)
    st.caption("Shopee duration is the actual live duration from 'Report - By Live'. TikTok duration is from 'Live Raw Data'. "
               "'NSQ' is shown as 'Aloshop'.")
