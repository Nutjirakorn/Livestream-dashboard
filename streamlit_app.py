"""
Supersports Live Schedule & Performance dashboard (Streamlit).

Reads three Google Sheets directly and refreshes automatically:
  1. Shopee_Live_Tracking            -> sheet "Report - By Live"
  2. TikTok - Jazz Working File      -> sheets "Daily Live Calendar" and "Live Raw Data"
  3. Supersports TT Brief-Supersports Performance
                                     -> sheet "Live Template(NEW)" and every monthly "<Mon>[YY] Perf" sheet
                                        (or a "Live Raw Data" sheet, if one is added later)

Setup: see the README section at the bottom of this file.
"""

import re
import datetime as dt
from calendar import monthrange

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------
SHEET_IDS = {
    "shopee": "1RpP3HY6yTYnFqxFpF0NhwBRa70hCbynroZddm17dLIo",
    "tiktok": "1yJU84We3_yzPScbhKMv3pjcaiuv8IzWhHlgcD3Z-jO4",
    "perf": "1b0olOO85X6zsK2WCW1XeV2XhC5tq8IJmZNLZpUWiuEw",
}
REFRESH_MINUTES = 5          # how often the app re-reads the sheets
SHOPEE_COLOR = "#EE4D2D"
TIKTOK_COLOR = "#1F9FB4"
TYPE_COLORS = {"Agency": "#6C5CE7", "Internal MCs": "#00A86B", "KOLs": "#F5A623"}
TYPES = ["Agency", "Internal MCs", "KOLs"]

st.set_page_config(page_title="Supersports Live Dashboard", page_icon="📺", layout="wide")

# ----------------------------------------------------------------------------
# Parsing helpers
# ----------------------------------------------------------------------------
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def num(s):
    s = str(s or "").replace(",", "").replace("฿", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def hms_to_min(s):
    """'2:00:21' or '18:00' -> minutes."""
    m = re.match(r"^\s*(\d+):(\d+)(?::(\d+))?\s*$", str(s or ""))
    if not m:
        return 0.0
    return int(m[1]) * 60 + int(m[2]) + int(m[3] or 0) / 60


def clock_to_min(s):
    """'6:49 PM' / '2PM' / '18:30' -> minutes after midnight (or None)."""
    s = str(s or "").strip()
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?(?::\d{2})?\s*(AM|PM)$", s, re.I)
    if m:
        h = int(m[1]) % 12 + (12 if m[3].upper() == "PM" else 0)
        return h * 60 + int(m[2] or 0)
    m = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?$", s)
    if m:
        return int(m[1]) * 60 + int(m[2])
    return None


def fmt_clock(s):
    v = clock_to_min(s)
    return "" if v is None else f"{v // 60:02d}:{v % 60:02d}"


def clean(s):
    return re.sub("nsq", "Aloshop", str(s or "").strip(), flags=re.I)


def type_of(operate):
    o = operate.lower()
    if "agency" in o or "aloshop" in o:
        return "Agency"
    if "kol" in o:
        return "KOLs"
    return "Internal MCs"


def find_header(rows, *must):
    """Index of the first row that contains every name in `must`."""
    for i, r in enumerate(rows[:50]):
        cells = [str(c).strip() for c in r]
        if all(any(c == m or c.startswith(m) for c in cells) for m in must):
            return i
    raise ValueError(f"Header row with {must} not found")


def col(header, name, exact=True):
    cells = [str(c).strip() for c in header]
    for i, c in enumerate(cells):
        if (c == name) if exact else c.lower().startswith(name.lower()):
            return i
    raise ValueError(f"Column '{name}' not found")


def cell(r, i):
    return r[i] if i is not None and i < len(r) else ""


def parse_date(s, year=None):
    s = str(s or "").strip()
    for f in ("%d-%b-%Y", "%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(s, f).date()
        except ValueError:
            pass
    m = re.match(r"^(\d{1,2})-([A-Za-z]+)$", s)          # '2-Jan' + year column
    if m and year:
        return dt.date(int(year), MONTHS[m[2][:3].lower()], int(m[1]))
    return None


# ----------------------------------------------------------------------------
# Builders: raw sheet values -> tidy tables
# ----------------------------------------------------------------------------
SHOPEE_OPERATE = {"agency(aloshop)": "Agency (Aloshop)"}
SHOPEE_ACCOUNT = {"SSP Official": "Supersports", "SSP Livestream": "SSP Livestream"}
TT_OWN = {"supersports": "Supersports", "supersportsstore": "Supersports",
          "supersportsfootball": "Supersports Football", "supersportsmahachai": "Supersports Mahachai"}


def tt_operate(mc):
    mc = clean(mc)
    return "Agency (Aloshop)" if mc.lower().startswith("agency") else mc


def build_shopee(rows):
    h = find_header(rows, "Live Operate", "Live Account", "Duration Plan")
    hd = rows[h]
    c = {k: col(hd, k) for k in ["Year", "Date", "Live Operate", "Live Account", "Live Starts",
                                  "Live Ends", "Duration Plan", "Actual Duration"]}
    c["GMV"] = col(hd, "GMV", exact=False)
    sched, perf = [], []
    for r in rows[h + 1:]:
        d = parse_date(cell(r, c["Date"]), cell(r, c["Year"]))
        op_raw = cell(r, c["Live Operate"]).strip()
        if not d or not op_raw:
            continue
        op = SHOPEE_OPERATE.get(op_raw.lower(), clean(op_raw))
        ac = SHOPEE_ACCOUNT.get(cell(r, c["Live Account"]).strip(), clean(cell(r, c["Live Account"])))
        ty = type_of(op)
        base = dict(date=d, channel="Shopee", account=ac, operate=op, type=ty)
        sched.append({**base, "start": fmt_clock(cell(r, c["Live Starts"])), "end": fmt_clock(cell(r, c["Live Ends"])),
                      "plan_hrs": hms_to_min(cell(r, c["Duration Plan"])) / 60})
        gmv, dur = num(cell(r, c["GMV"])), hms_to_min(cell(r, c["Actual Duration"]))
        if gmv > 0 or dur > 0:
            perf.append({**base, "gmv": gmv, "hrs": dur / 60})
    return sched, perf


def build_tiktok_calendar(rows):
    h = find_header(rows, "Live Account", "MC", "Live Start")
    hd = rows[h]
    c = {k: col(hd, k) for k in ["Date", "Live Account", "MC", "Live Start", "Live End", "Duration"]}
    out = []
    for r in rows[h + 1:]:
        d = parse_date(cell(r, c["Date"]))
        acc = cell(r, c["Live Account"]).strip()
        if not d or not acc:
            continue
        mc = cell(r, c["MC"]).strip()
        if mc.upper() == "KOL" or acc.lower() not in TT_OWN:
            ac, op, ty = "KOLs", acc, "KOLs"
        else:
            ac, op = TT_OWN[acc.lower()], tt_operate(mc)
            ty = type_of(op)
        s, e = clock_to_min(cell(r, c["Live Start"])), clock_to_min(cell(r, c["Live End"]))
        pm = hms_to_min(cell(r, c["Duration"]))
        if not pm and s is not None and e is not None:
            pm = (e - s) % 1440
        out.append(dict(date=d, channel="TikTok", account=ac, operate=op, type=ty,
                        start=fmt_clock(cell(r, c["Live Start"])), end=fmt_clock(cell(r, c["Live End"])),
                        plan_hrs=pm / 60))
    return out


def build_tiktok_raw(rows, account_override=None):
    h = find_header(rows, "Creator ID", "Nickname")
    hd = rows[h]
    c = {"nick": col(hd, "Nickname"), "rev": col(hd, "Revenue", exact=False),
         "kol": col(hd, "KOL Name"), "dur": col(hd, "Duration(minute)"), "date": col(hd, "Date")}
    out = []
    for r in rows[h + 1:]:
        nick, d = cell(r, c["nick"]).strip(), parse_date(cell(r, c["date"]))
        if not nick or not d:
            continue
        kn = clean(cell(r, c["kol"]))
        if account_override:
            ac, op = account_override, tt_operate(kn) if kn else "Other operators"
        elif nick.lower() == "supersportsstore":
            ac, op = "Supersports", ("Agency (Aloshop)" if "agency" in kn.lower() else "Internal Live")
        elif nick.lower() in TT_OWN:
            ac, op = TT_OWN[nick.lower()], "Internal Live"
        else:
            ac, op = "KOLs", (kn if kn and kn != "Internal Live" else nick)
        ty = "KOLs" if ac == "KOLs" else type_of(op)
        out.append(dict(date=d, channel="TikTok", account=ac, operate=op, type=ty,
                        gmv=num(cell(r, c["rev"])), hrs=num(cell(r, c["dur"])) / 60))
    return out


def build_perf_template(rows):
    h = find_header(rows, "Date", "Live Operate", "Live Starts")
    hd = rows[h]
    c = {"date": col(hd, "Date"), "op": col(hd, "Live Operate"), "s": col(hd, "Live Starts")}
    try:
        c["e"] = next(i for i, x in enumerate(hd) if "live ends" in str(x).lower())
    except StopIteration:
        c["e"] = None
    try:
        c["dur"] = col(hd, "Duration Plan")
    except ValueError:
        c["dur"] = None
    out = []
    for r in rows[h + 1:]:
        d = parse_date(cell(r, c["date"]))
        op_raw = cell(r, c["op"]).strip()
        if not d or not op_raw:
            continue
        op = "Agency (Aloshop)" if "aloshop" in op_raw.lower() or "agency" in op_raw.lower() else clean(op_raw)
        s, e = clock_to_min(cell(r, c["s"])), clock_to_min(cell(r, c["e"]))
        pm = hms_to_min(cell(r, c["dur"])) if c["dur"] is not None else 0
        if not pm and s is not None and e is not None:
            pm = (e - s) % 1440 or 1440
        out.append(dict(date=d, channel="TikTok", account="Supersports Performance", operate=op, type=type_of(op),
                        start=fmt_clock(cell(r, c["s"])), end=fmt_clock(cell(r, c["e"])), plan_hrs=pm / 60))
    return out


PERF_SHEET_RE = re.compile(r"^([A-Za-z]{3,9})\s*'?(\d{2})?\s+Perf$", re.I)


def perf_sheet_month(title, default_year=2025):
    m = PERF_SHEET_RE.match(title.strip())
    if not m or m[1][:3].lower() not in MONTHS:
        return None
    return (2000 + int(m[2])) if m[2] else default_year, MONTHS[m[1][:3].lower()]
def gs_client():
    import gspread
    from google.oauth2.service_account import Credentials
    info = service_account_info()
    if "private_key" in info and isinstance(info["private_key"], str):
        # Fix backslash-n string replacements safely
        pk = info["private_key"].replace("\\n", "\n")
        # Ensure header and footer are clean
        info["private_key"] = pk
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    return gspread.authorize(creds)


def build_perf_monthly(title, rows):
    """A '<Mon>[YY] Perf' sheet: daily Total Shop vs Aloshop GMV and hours (Date block)."""
    ym = perf_sheet_month(title)
    if not ym:
        return []
    year, month = ym
    di = next((i for i, r in enumerate(rows) if r and str(r[0]).strip() == "Date"), None)
    if di is None:
        return []
    # The two summary blocks each end in a "Total" row: [label, hours, GMV, ...]
    totals = [r for r in rows[:di] if r and str(r[0]).strip() == "Total"]
    cols = (3, 15, 16, 28)                                       # default layout
    if len(totals) >= 2 and di >= 1:
        tr = rows[di - 1]                                        # column totals of the Date block
        g1 = _match_col(tr, num(totals[0][2]))
        h1 = _match_col(tr, num(totals[0][1]), (g1 or 0) + 1)
        g2 = _match_col(tr, num(totals[1][2]), (h1 or 0) + 1)
        h2 = _match_col(tr, num(totals[1][1]), (g2 or 0) + 1)
        if None not in (g1, h1, g2, h2):
            cols = (g1, h1, g2, h2)
    gi, hi, ai, ahi = cols
    out = []
    for r in rows[di + 1:]:
        m = re.match(r"^(\d{1,2})-[A-Za-z]+", str(cell(r, 0)).strip())
        if not m:
            continue
        day = int(m[1])
        if day > monthrange(year, month)[1]:
            continue
        d = dt.date(year, month, day)
        g, h, a, ah = num(cell(r, gi)), num(cell(r, hi)), num(cell(r, ai)), num(cell(r, ahi))
        base = dict(date=d, channel="TikTok", account="Supersports Performance")
        if a or ah:
            out.append({**base, "operate": "Agency (Aloshop)", "type": "Agency", "gmv": a, "hrs": ah})
        if (g - a) > 0 or (h - ah) > 0:
            out.append({**base, "operate": "Other operators", "type": "Internal MCs",
                        "gmv": max(g - a, 0), "hrs": max(h - ah, 0)})
    return out


def build_all(shopee_rows, cal_rows, raw_rows, tpl_rows, perf_sheets, perf_raw_rows=None):
    sched, perf = build_shopee(shopee_rows)
    sched += build_tiktok_calendar(cal_rows)
    perf += build_tiktok_raw(raw_rows)
    if tpl_rows:
        sched += build_perf_template(tpl_rows)
    if perf_raw_rows:                       # a "Live Raw Data" sheet in the Performance file wins
        perf += build_tiktok_raw(perf_raw_rows, account_override="Supersports Performance")
    else:
        for title, rows in perf_sheets.items():
            perf += build_perf_monthly(title, rows)
    S = pd.DataFrame(sched, columns=["date", "channel", "account", "operate", "type", "start", "end", "plan_hrs"])
    P = pd.DataFrame(perf, columns=["date", "channel", "account", "operate", "type", "gmv", "hrs"])
    for df in (S, P):
        df["date"] = pd.to_datetime(df["date"])
    return S, P


# ----------------------------------------------------------------------------
# Google Sheets loading (cached; re-read every REFRESH_MINUTES)
# ----------------------------------------------------------------------------
class SetupError(Exception):
    """A problem with the Google key setup, shown to the user as setup steps."""


def _secrets_dict():
    try:
        return {k: st.secrets[k] for k in st.secrets.keys()}
    except Exception:          # no secrets.toml anywhere
        return {}


def service_account_info():
    """Find the service account key. Accepts, in order:
    1. [gcp_service_account] section in secrets (TOML keys)
    2. gcp_service_account = '''{ ...JSON... }''' (the JSON key pasted as one string)
    3. [connections.gsheets] section (st-gsheets-connection style)
    4. the key's fields at the top level of secrets (pasted without a section header)
    5. a service_account.json file next to this app, or GOOGLE_APPLICATION_CREDENTIALS
    """
    import json
    import os
    from pathlib import Path

    sec = _secrets_dict()
    v = sec.get("gcp_service_account")
    if v is not None:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError as e:
                raise SetupError("gcp_service_account is text but not valid JSON") from e
        return dict(v)
    conn = sec.get("connections")
    if conn is not None and "gsheets" in conn:
        return {k: val for k, val in dict(conn["gsheets"]).items() if k not in ("spreadsheet", "worksheet")}
    if "private_key" in sec and "client_email" in sec:
        return {k: sec[k] for k in sec if not hasattr(sec[k], "keys")}
    for p in [Path(__file__).with_name("service_account.json"), Path(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", ""))]:
        if p.name and p.is_file():
            return json.loads(p.read_text())
    found = ", ".join(sorted(sec)) or "nothing"
    raise SetupError(
        f"No Google service account key found (secrets contain: {found}). Streamlit reads secrets only from "
        f"`.streamlit/secrets.toml` in the folder you run `streamlit run` from (now: `{Path.cwd()}`), from "
        "`~/.streamlit/secrets.toml`, or from the Secrets box on Streamlit Community Cloud.")


@st.cache_resource
def gs_client():
    import gspread
    from google.oauth2.service_account import Credentials
    info = service_account_info()
    if "private_key" in info:      # TOML editors sometimes turn \n into real line breaks or keep a literal "\\n"
        info["private_key"] = info["private_key"].replace("\\n", "\n")
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    return gspread.authorize(creds)


def _values(book, title):
    return book.worksheet(title).get_all_values()


@st.cache_data(ttl=REFRESH_MINUTES * 60, show_spinner="Reading Google Sheets…")
def load_data():
    gc = gs_client()
    shopee = gc.open_by_key(SHEET_IDS["shopee"])
    tiktok = gc.open_by_key(SHEET_IDS["tiktok"])
    pbook = gc.open_by_key(SHEET_IDS["perf"])

    titles = [ws.title for ws in pbook.worksheets()]
    perf_titles = [t for t in titles if perf_sheet_month(t)]
    perf_sheets = {}
    if perf_titles:                                   # one API call for all monthly sheets
        res = pbook.values_batch_get([f"'{t}'" for t in perf_titles])
        for t, vr in zip(perf_titles, res.get("valueRanges", [])):
            perf_sheets[t] = vr.get("values", [])

    S, P = build_all(
        shopee_rows=_values(shopee, "Report - By Live"),
        cal_rows=_values(tiktok, "Daily Live Calendar"),
        raw_rows=_values(tiktok, "Live Raw Data"),
        tpl_rows=_values(pbook, "Live Template(NEW)") if "Live Template(NEW)" in titles else [],
        perf_sheets=perf_sheets,
        perf_raw_rows=_values(pbook, "Live Raw Data") if "Live Raw Data" in titles else None,
    )
    return S, P, dt.datetime.now()


# ----------------------------------------------------------------------------
# Period helpers
# ----------------------------------------------------------------------------
def month_end(d):
    return d.replace(day=monthrange(d.year, d.month)[1])


def add_months(d, n, to_end=False):
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    last = monthrange(y, m)[1]
    return dt.date(y, m, last if to_end else min(d.day, last))


def shift_window(s, e, n):
    return add_months(s, n), add_months(e, n, to_end=(e == month_end(e)))


def preset_range(p, today):
    first = today.replace(day=1)
    q0 = dt.date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
    return {
        "This month": (first, month_end(first)),
        "Last month": (add_months(first, -1), month_end(add_months(first, -1))),
        "Next month": (add_months(first, 1), month_end(add_months(first, 1))),
        "This quarter": (q0, month_end(add_months(q0, 2))),
        "Last quarter": (add_months(q0, -3), month_end(add_months(q0, -1))),
        "This year": (dt.date(today.year, 1, 1), dt.date(today.year, 12, 31)),
        "Last year": (dt.date(today.year - 1, 1, 1), dt.date(today.year - 1, 12, 31)),
    }[p]


def fmt_range(s, e):
    return f"{s:%b %-d} to {e:%b %-d, %Y}" if s.year == e.year else f"{s:%b %-d, %Y} to {e:%b %-d, %Y}"


def pct(a, b):
    return None if not b else a / b - 1


def fmt_pct(v):
    return "n/a" if v is None else f"{v:+.1%}"


def thb(v):
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"฿{v / div:,.2f}{suf}"
    return f"฿{v:,.0f}"


def between(df, s, e):
    return df[(df["date"] >= pd.Timestamp(s)) & (df["date"] <= pd.Timestamp(e))]


# ----------------------------------------------------------------------------
# Table builders
# ----------------------------------------------------------------------------
SCHED_ROWS = [  # (label, level, filter)
    ("Total 2 channels", 0, lambda d: d),
    ("Shopee", 1, lambda d: d[d.channel == "Shopee"]),
    ("Supersports account", 2, lambda d: d[(d.channel == "Shopee") & (d.account == "Supersports")]),
    ("Internal MCs", 3, lambda d: d[(d.channel == "Shopee") & (d.account == "Supersports") & (d.type == "Internal MCs")]),
    ("Agency (Aloshop)", 3, lambda d: d[(d.channel == "Shopee") & (d.account == "Supersports") & (d.type == "Agency")]),
    ("KOLs (Shopee KOL)", 3, lambda d: d[(d.channel == "Shopee") & (d.account == "Supersports") & (d.type == "KOLs")]),
    ("SSP Livestream account (Agency)", 2, lambda d: d[(d.channel == "Shopee") & (d.account == "SSP Livestream")]),
    ("TikTok", 1, lambda d: d[d.channel == "TikTok"]),
    ("Supersports account", 2, lambda d: d[(d.channel == "TikTok") & (d.account == "Supersports")]),
    ("Internal MCs", 3, lambda d: d[(d.channel == "TikTok") & (d.account == "Supersports") & (d.type == "Internal MCs")]),
    ("Agency (Aloshop)", 3, lambda d: d[(d.channel == "TikTok") & (d.account == "Supersports") & (d.type == "Agency")]),
    ("Supersports Performance account", 2, lambda d: d[(d.channel == "TikTok") & (d.account == "Supersports Performance")]),
    ("KOLs accounts", 2, lambda d: d[(d.channel == "TikTok") & (d.account == "KOLs")]),
    ("Other own accounts (Football, Mahachai)", 2,
     lambda d: d[(d.channel == "TikTok") & d.account.isin(["Supersports Football", "Supersports Mahachai"])]),
]

PERF_ROWS = [
    ("Total 2 channels", 0, lambda d: d),
    ("Shopee", 1, lambda d: d[d.channel == "Shopee"]),
    ("Internal MCs", 2, lambda d: d[(d.channel == "Shopee") & (d.type == "Internal MCs")]),
    ("Agency", 2, lambda d: d[(d.channel == "Shopee") & (d.type == "Agency")]),
    ("KOLs", 2, lambda d: d[(d.channel == "Shopee") & (d.type == "KOLs")]),
    ("TikTok", 1, lambda d: d[d.channel == "TikTok"]),
    ("Agency", 2, lambda d: d[(d.channel == "TikTok") & (d.type == "Agency")]),
    ("Internal MCs", 2, lambda d: d[(d.channel == "TikTok") & (d.type == "Internal MCs")]),
    ("KOLs", 2, lambda d: d[(d.channel == "TikTok") & (d.type == "KOLs")]),
]
INDENT = {0: "", 1: "", 2: " ", 3: "  "}


def schedule_table(d):
    total_hrs = d.plan_hrs.sum()
    out = []
    for label, lvl, f in SCHED_ROWS:
        x = f(d)
        n, h = len(x), x.plan_hrs.sum()
        out.append({"Channel / account / live operate": INDENT[lvl] + label,
                    "Lives": n, "Planned hrs": round(h, 1), "Live days": x.date.nunique(),
                    "Avg hrs per live": round(h / n, 1) if n else None,
                    "Share of hrs": f"{h / total_hrs:.1%}" if total_hrs and n else "-", "_lvl": lvl})
    return pd.DataFrame(out)


def perf_table(cur, lm, ly, field, label):
    tot = cur[field].sum()
    out = []
    for name, lvl, f in PERF_ROWS:
        v, a, b = f(cur)[field].sum(), f(lm)[field].sum(), f(ly)[field].sum()
        r = 0 if field == "gmv" else 1
        out.append({"Live operate": INDENT[lvl] + name, label: round(v, r),
                    "Share": f"{v / tot:.1%}" if tot and v else "-",
                    "Last month": round(a, r), "%CHG MoM": fmt_pct(pct(v, a)),
                    "Last year": round(b, r), "%CHG YoY": fmt_pct(pct(v, b)), "_lvl": lvl})
    return pd.DataFrame(out)


def style_table(df, num_cols, decimals=0):
    lvl = df["_lvl"]
    show = df.drop(columns="_lvl")

    def row_style(r):
        l = lvl.loc[r.name]
        if l == 0:
            return ["font-weight:700; background-color:rgba(127,127,127,.12)"] * len(r)
        if l == 1:
            return ["font-weight:700"] * len(r)
        return [""] * len(r)

    def chg_color(v):
        if isinstance(v, str) and v.startswith("+"):
            return "color:#1a9e5a"
        if isinstance(v, str) and v.startswith("-"):
            return "color:#d64545"
        return ""

    sty = show.style.apply(row_style, axis=1).format(
        {c: (f"{{:,.{decimals}f}}" if c not in ("Lives", "Live days") else "{:,.0f}") for c in num_cols},
        na_rep="-")
    chg_cols = [c for c in show.columns if c.startswith("%CHG")]
    if chg_cols:
        sty = sty.map(chg_color, subset=chg_cols)
    return sty


def stacked_bars(df, value_col, s, e, split, yfmt, title_unit):
    days = (e - s).days + 1
    freq, label = ("MS", "month") if days > 120 else ("W-MON", "week") if days > 45 else ("D", "day")
    d = df.copy()
    if freq == "MS":
        d["bucket"] = d.date.dt.to_period("M").dt.start_time
    elif freq == "W-MON":
        d["bucket"] = d.date - pd.to_timedelta(d.date.dt.weekday, unit="D")
    else:
        d["bucket"] = d.date
    keys = ["Shopee", "TikTok"] if split == "Channel" else TYPES
    colors = {"Shopee": SHOPEE_COLOR, "TikTok": TIKTOK_COLOR, **TYPE_COLORS}
    by = "channel" if split == "Channel" else "type"
    piv = d.pivot_table(index="bucket", columns=by, values=value_col, aggfunc="sum", fill_value=0)
    fig = go.Figure()
    for k in keys:
        if k in piv.columns:
            fig.add_bar(x=piv.index, y=piv[k], name=k, marker_color=colors[k],
                        hovertemplate=f"%{{x|%b %-d, %Y}}<br>{k}: %{{y:{yfmt}}}{title_unit}<extra></extra>")
    fig.update_layout(barmode="stack", height=320, margin=dict(l=10, r=10, t=10, b=10),
                      legend=dict(orientation="h", y=1.1), bargap=0.2)
    fig.update_yaxes(tickformat=yfmt, gridcolor="rgba(127,127,127,.2)")
    return fig, label


def calendar_html(d, s, e, max_months=3):
    by = d.groupby(d.date.dt.date).agg(
        sh=("channel", lambda x: (x == "Shopee").sum()), tt=("channel", lambda x: (x == "TikTok").sum()),
        h=("plan_hrs", "sum"))
    html = ["<div style='display:flex;flex-direction:column;gap:14px;font-size:12px'>"]
    m = s.replace(day=1)
    shown = 0
    while m <= e and shown < max_months:
        html.append(f"<div><div style='font-weight:600;margin-bottom:4px'>{m:%B %Y}</div>"
                    "<div style='display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:3px'>")
        html += [f"<div style='text-align:center;opacity:.6;font-size:11px'>{w}</div>"
                 for w in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]]
        html += ["<div></div>"] * m.weekday()
        for day in range(1, monthrange(m.year, m.month)[1] + 1):
            dd = m.replace(day=day)
            out = dd < s or dd > e
            o = by.loc[dd] if dd in by.index else None
            body = f"<span style='opacity:.6'>{day}</span>"
            if o is not None:
                if o.sh:
                    body += f"<span><b style='color:{SHOPEE_COLOR}'>■</b> {int(o.sh)} Shopee</span>"
                if o.tt:
                    body += f"<span><b style='color:{TIKTOK_COLOR}'>■</b> {int(o.tt)} TikTok</span>"
                body += f"<span style='margin-top:auto;opacity:.6'>{o.h:,.1f}h</span>"
            html.append("<div style='border:1px solid rgba(127,127,127,.3);border-radius:4px;padding:3px 4px;"
                        f"min-height:56px;display:flex;flex-direction:column;gap:1px;opacity:{.35 if out else 1};"
                        f"{'border-style:dashed;' if o is None else ''}'>{body}</div>")
        html.append("</div></div>")
        m = add_months(m, 1)
        shown += 1
    if m <= e:
        html.append("<div style='opacity:.6'>The calendar shows the first 3 months of the period.</div>")
    html.append("</div>")
    return "".join(html)


# ----------------------------------------------------------------------------
# App
# ----------------------------------------------------------------------------
st.title("Supersports Live Schedule & Performance")

try:
    S_all, P_all, loaded_at = load_data()
except SetupError as exc:
    st.error(f"**Google key not set up.** {exc}")
    st.markdown(
        "Add this to `.streamlit/secrets.toml` (note the folder name starts with a dot and the file must not "
        "end in `.example`), filling in the values from your service account JSON key file:")
    st.code('[gcp_service_account]\ntype = "service_account"\nproject_id = "..."\nprivate_key_id = "..."\n'
            'private_key = "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n"\n'
            'client_email = "...@....iam.gserviceaccount.com"\nclient_id = "..."\n'
            'token_uri = "https://oauth2.googleapis.com/token"', language="toml")
    st.markdown("Or paste the whole JSON file as one value:")
    st.code("gcp_service_account = '''\n{ paste the JSON key file here }\n'''", language="toml")
    st.markdown("Or save the JSON key file as `service_account.json` next to `streamlit_app.py`. "
                "On Streamlit Community Cloud, paste the TOML into **App settings > Secrets**, then reboot the app.")
    st.stop()
except Exception as exc:  # show a readable message instead of a stack trace
    import gspread
    if isinstance(exc, gspread.exceptions.SpreadsheetNotFound) or "403" in str(exc) or "PERMISSION" in str(exc).upper():
        try:
            email = service_account_info().get("client_email", "the service account email")
        except Exception:
            email = "the service account email"
        st.error(f"**No access to a Google Sheet.** Share all 3 sheets with `{email}` as Viewer, and make sure "
                 "the Google Sheets API is turned on in that Google Cloud project.")
    elif isinstance(exc, gspread.exceptions.WorksheetNotFound):
        st.error(f"**A sheet tab is missing:** {exc}. Check the tab names: 'Report - By Live', "
                 "'Daily Live Calendar', 'Live Raw Data', 'Live Template(NEW)'.")
    else:
        st.error(f"Couldn't read the Google Sheets: {type(exc).__name__}: {exc}")
    st.stop()

# ---- Sidebar filters
today = dt.date.today()
with st.sidebar:
    st.header("Filters")
    preset = st.selectbox("Period", ["This month", "Last month", "Next month", "This quarter", "Last quarter",
                                     "This year", "Last year", "Custom dates"])
    if preset == "Custom dates":
        ps, pe = preset_range("This month", today)
        picked = st.date_input("From / To", (ps, pe))
        start, end = (picked if isinstance(picked, (list, tuple)) and len(picked) == 2 else (ps, pe))
    else:
        start, end = preset_range(preset, today)
    channel = st.selectbox("Channel", ["All (TikTok + Shopee)", "TikTok", "Shopee"])
    typ = st.selectbox("Type", ["All types"] + TYPES)
    split = st.radio("Split charts by", ["Channel", "Type"], horizontal=True)
    st.divider()
    if st.button("Refresh data now", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.caption(f"Data read {loaded_at:%b %-d, %Y %H:%M}. Re-reads Google Sheets every {REFRESH_MINUTES} min.")


@st.fragment(run_every=f"{REFRESH_MINUTES}m")
def dashboard(start, end, channel, typ, split):
    S_all, P_all, loaded_at = load_data()          # cached; re-read after REFRESH_MINUTES

    def keep(df):
        if channel != "All (TikTok + Shopee)":
            df = df[df.channel == channel]
        if typ != "All types":
            df = df[df.type == typ]
        return df

    S, P = keep(S_all), keep(P_all)
    last = P_all.loc[P_all.gmv > 0, "date"].max()
    last = last.date() if pd.notna(last) else end
    cur_e = min(end, last)
    empty = cur_e < start
    lm_s, lm_e = shift_window(start, cur_e, -1)
    ly_s, ly_e = shift_window(start, cur_e, -12)
    filt = f"{channel if channel != 'All (TikTok + Shopee)' else 'TikTok + Shopee'} · {typ}"

    sched = between(S, start, end)
    cur = between(P, start, cur_e) if not empty else P.iloc[0:0]
    lm, ly = between(P, lm_s, lm_e), between(P, ly_s, ly_e)

    st.markdown(
        f"**{fmt_range(start, end)}**: **{len(sched):,}** lives planned (**{sched.plan_hrs.sum():,.1f}** hrs). "
        f"Results to **{last:%b %-d, %Y}**: GMV **{thb(cur.gmv.sum())}**, "
        f"**{fmt_pct(pct(cur.gmv.sum(), lm.gmv.sum()))}** vs last month and "
        f"**{fmt_pct(pct(cur.gmv.sum(), ly.gmv.sum()))}** vs last year.")

    tab_a, tab_b = st.tabs(["A · Live Schedule", "B · Live Performance"])

    # ============================ A: LIVE SCHEDULE ============================
    with tab_a:
        with st.container(border=True):
            st.subheader("Live schedule summary")
            st.caption(f"{fmt_range(start, end)} · {filt}")
            c1, c2, c3, c4 = st.columns(4)
            sh, tt = sched[sched.channel == "Shopee"], sched[sched.channel == "TikTok"]
            tot_h = sched.plan_hrs.sum()
            c1.metric("Total lives planned", f"{len(sched):,}",
                      f"{tot_h:,.1f} hrs · {sched.date.nunique()} live days", delta_color="off")
            c2.metric("Shopee", f"{len(sh):,}", f"{sh.plan_hrs.sum():,.1f} hrs planned", delta_color="off")
            c3.metric("TikTok", f"{len(tt):,}", f"{tt.plan_hrs.sum():,.1f} hrs planned", delta_color="off")
            shares = " · ".join(f"{sched[sched.type == t].plan_hrs.sum() / tot_h:.0%}" if tot_h else "-" for t in TYPES)
            c4.metric("Agency · Internal MCs · KOLs", shares, "share of planned hours", delta_color="off")
            st.dataframe(style_table(schedule_table(sched), ["Lives", "Planned hrs", "Live days", "Avg hrs per live"], 1),
                         hide_index=True, width="stretch")

        a1, a2 = st.columns(2)
        with a1, st.container(border=True):
            st.subheader("Live plan by channel")
            fig, unit = stacked_bars(sched, "plan_hrs", start, end, split, ",.1f", " hrs")
            st.caption(f"{fmt_range(start, end)} · planned live hours per {unit}")
            st.plotly_chart(fig, width="stretch", key="sched_chart")
        with a2, st.container(border=True):
            st.subheader("Live calendar")
            st.caption("Lives per day by channel, planned hours at the bottom of each day")
            st.markdown(calendar_html(sched, start, end), unsafe_allow_html=True)

        with st.container(border=True):
            st.subheader("Schedule by live operate")
            g = (sched.groupby(["operate", "type", "channel", "account"], as_index=False)
                 .agg(Lives=("date", "size"), **{"Planned hrs": ("plan_hrs", "sum")})
                 .sort_values("Planned hrs", ascending=False))
            g["Share of hrs"] = (g["Planned hrs"] / tot_h).map("{:.1%}".format) if tot_h else "-"
            g.columns = ["Live operate", "Type", "Channel", "Account", "Lives", "Planned hrs", "Share of hrs"]
            st.dataframe(g.style.format({"Planned hrs": "{:,.1f}"}), hide_index=True, width="stretch")

        with st.container(border=True):
            st.subheader("Daily live plan")
            q = st.text_input("Search date, operate or account", key="q")
            lst = sched.sort_values(["date", "start"]).assign(
                Date=lambda x: x.date.dt.strftime("%a %b %-d, %Y"),
                Time=lambda x: x.start + " - " + x.end)
            lst = lst[["Date", "Time", "channel", "account", "operate", "type", "plan_hrs"]]
            lst.columns = ["Date", "Time", "Channel", "Account", "Live operate", "Type", "Planned hrs"]
            if q:
                lst = lst[lst.apply(lambda r: q.lower() in " ".join(map(str, r.values)).lower(), axis=1)]
            st.dataframe(lst.style.format({"Planned hrs": "{:,.1f}"}), hide_index=True,
                         width="stretch", height=420)

    # ========================== B: LIVE PERFORMANCE ===========================
    with tab_b:
        cur_label = f"No results yet for {fmt_range(start, end)}" if empty else fmt_range(start, cur_e)
        with st.container(border=True):
            st.subheader("Live performance summary")
            st.caption(f"{cur_label} · {filt}")
            cols = st.columns(5)

            def tile(c, label, a, b_lm, b_ly, fmt):
                c.metric(label, fmt(a), f"MoM {fmt_pct(pct(a, b_lm))} · YoY {fmt_pct(pct(a, b_ly))}",
                         delta_color="off")

            for c, label, ch in [(cols[0], "GMV, 2 channels", None), (cols[1], "Shopee GMV", "Shopee"),
                                 (cols[2], "TikTok GMV", "TikTok")]:
                f = (lambda d: d) if ch is None else (lambda d, ch=ch: d[d.channel == ch])
                tile(c, label, f(cur).gmv.sum(), f(lm).gmv.sum(), f(ly).gmv.sum(), thb)
            tile(cols[3], "Live duration (hrs)", cur.hrs.sum(), lm.hrs.sum(), ly.hrs.sum(), lambda v: f"{v:,.1f}")
            gph = lambda d: d.gmv.sum() / d.hrs.sum() if d.hrs.sum() else 0
            tile(cols[4], "GMV per live hour", gph(cur), gph(lm), gph(ly), thb)
            st.caption(f"Compared with the same days last month ({fmt_range(lm_s, lm_e)}) and last year "
                       f"({fmt_range(ly_s, ly_e)}). The current period stops at the latest date with results.")

        st.markdown("#### 1 · GMV")
        b1, b2 = st.columns(2)
        with b1, st.container(border=True):
            st.subheader("GMV by date")
            fig, unit = stacked_bars(cur, "gmv", start, cur_e, split, ",.0f", " THB")
            st.caption(f"{cur_label} · GMV (THB) per {unit}")
            st.plotly_chart(fig, width="stretch", key="gmv_chart")
        with b2, st.container(border=True):
            st.subheader("GMV by live operate")
            st.caption(cur_label)
            t = perf_table(cur, lm, ly, "gmv", "GMV (THB)")
            st.dataframe(style_table(t, ["GMV (THB)", "Last month", "Last year"], 0), hide_index=True,
                         width="stretch")

        st.markdown("#### 2 · Duration")
        b3, b4 = st.columns(2)
        with b3, st.container(border=True):
            st.subheader("Live duration by date")
            fig, unit = stacked_bars(cur, "hrs", start, cur_e, split, ",.1f", " hrs")
            st.caption(f"{cur_label} · actual live hours per {unit}")
            st.plotly_chart(fig, width="stretch", key="dur_chart")
        with b4, st.container(border=True):
            st.subheader("Duration by live operate")
            st.caption(cur_label)
            t = perf_table(cur, lm, ly, "hrs", "Hours")
            st.dataframe(style_table(t, ["Hours", "Last month", "Last year"], 1), hide_index=True,
                         width="stretch")

        st.markdown("#### 3 · TikTok by account")
        with st.container(border=True):
            st.subheader("TikTok GMV and duration by account")
            st.caption(cur_label)
            acc_rows = [("TikTok", 1, lambda d: d[d.channel == "TikTok"]),
                        ("Supersports account", 2, lambda d: d[(d.channel == "TikTok") & (d.account == "Supersports")]),
                        ("Supersports Performance account", 2,
                         lambda d: d[(d.channel == "TikTok") & (d.account == "Supersports Performance")]),
                        ("KOLs accounts", 2, lambda d: d[(d.channel == "TikTok") & (d.account == "KOLs")]),
                        ("Other own accounts (Football, Mahachai)", 2,
                         lambda d: d[(d.channel == "TikTok") & d.account.isin(["Supersports Football", "Supersports Mahachai"])])]
            acc = []
            for name, lvl, f in acc_rows:
                c0, c1, c2, allr = f(cur), f(lm), f(ly), f(P)
                lastd = allr.loc[(allr.gmv > 0) | (allr.hrs > 0), "date"].max()
                acc.append({"Account": INDENT[lvl] + name, "GMV (THB)": round(c0.gmv.sum()),
                            "GMV %CHG MoM": fmt_pct(pct(c0.gmv.sum(), c1.gmv.sum())),
                            "GMV %CHG YoY": fmt_pct(pct(c0.gmv.sum(), c2.gmv.sum())),
                            "Hours": round(c0.hrs.sum(), 1),
                            "Hrs %CHG MoM": fmt_pct(pct(c0.hrs.sum(), c1.hrs.sum())),
                            "Hrs %CHG YoY": fmt_pct(pct(c0.hrs.sum(), c2.hrs.sum())),
                            "Results up to": f"{lastd:%b %-d, %Y}" if pd.notna(lastd) else "No results", "_lvl": lvl})
            st.dataframe(style_table(pd.DataFrame(acc), ["GMV (THB)", "Hours"], 0).format({"Hours": "{:,.1f}"}),
                         hide_index=True, width="stretch")
            st.caption("'Results up to' shows the latest date with results for each account, so a gap in a source "
                       "sheet (for example missing monthly Perf sheets) is easy to spot.")

        with st.container(border=True):
            st.subheader("Performance by live operate (detail)")
            key = ["operate", "type", "channel", "account"]
            agg = lambda d: d.groupby(key)[["gmv", "hrs"]].sum()
            det = agg(cur).join(agg(lm), rsuffix="_lm", how="left").join(agg(ly), rsuffix="_ly", how="left").fillna(0)
            det = det[(det.gmv > 0) | (det.hrs > 0)].sort_values("gmv", ascending=False).reset_index()
            out = pd.DataFrame({
                "Live operate": det.operate, "Type": det.type, "Channel": det.channel, "Account": det.account,
                "GMV (THB)": det.gmv.round(0),
                "GMV %CHG MoM": [fmt_pct(pct(a, b)) for a, b in zip(det.gmv, det.gmv_lm)],
                "GMV %CHG YoY": [fmt_pct(pct(a, b)) for a, b in zip(det.gmv, det.gmv_ly)],
                "Hours": det.hrs.round(1),
                "Hrs %CHG MoM": [fmt_pct(pct(a, b)) for a, b in zip(det.hrs, det.hrs_lm)],
                "Hrs %CHG YoY": [fmt_pct(pct(a, b)) for a, b in zip(det.hrs, det.hrs_ly)],
                "GMV per hour": [round(a / b) if b else None for a, b in zip(det.gmv, det.hrs)],
            })
            color = lambda v: ("color:#1a9e5a" if str(v).startswith("+") else
                               "color:#d64545" if str(v).startswith("-") else "")
            st.dataframe(out.style.format({"GMV (THB)": "{:,.0f}", "Hours": "{:,.1f}", "GMV per hour": "{:,.0f}"},
                                          na_rep="-").map(color, subset=[c for c in out.columns if "%CHG" in c]),
                         hide_index=True, width="stretch", height=420)
            st.caption("Supersports Performance results come from its monthly Perf sheets (or a Live Raw Data "
                       "sheet in that file, if one is added). Aloshop hours count as Agency; the rest of that "
                       "account counts as Internal MCs ('Other operators').")


dashboard(start, end, channel, typ, split)


# ----------------------------------------------------------------------------
# README
# ----------------------------------------------------------------------------
# 1) requirements.txt
#       streamlit>=1.48
#       pandas
#       plotly
#       gspread>=6
#       google-auth
#
# 2) Google service account (one time)
#    - Google Cloud Console > create a project > enable "Google Sheets API".
#    - IAM & Admin > Service accounts > create one > Keys > Add key > JSON. Download the file.
#    - Share each of the 3 Google Sheets with the service account's email
#      (xxx@yyy.iam.gserviceaccount.com) as Viewer.
#
# 3) Secrets: put the JSON key into .streamlit/secrets.toml (local) or the app's
#    Secrets box on Streamlit Community Cloud:
#
#       [gcp_service_account]
#       type = "service_account"
#       project_id = "..."
#       private_key_id = "..."
#       private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
#       client_email = "...@....iam.gserviceaccount.com"
#       client_id = "..."
#       auth_uri = "https://accounts.google.com/o/oauth2/auth"
#       token_uri = "https://oauth2.googleapis.com/token"
#       auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
#       client_x509_cert_url = "..."
#
# 4) Run:  streamlit run streamlit_app.py
#
# Auto update: data is cached for REFRESH_MINUTES and the dashboard re-runs on that
# interval, so edits in Google Sheets appear within ~5 minutes. "Refresh data now"
# in the sidebar reloads immediately.
