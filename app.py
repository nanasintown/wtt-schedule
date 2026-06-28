from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import json
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from streamlit_autorefresh import st_autorefresh


DEFAULT_URL = (
    "https://www.worldtabletennis.com/eventInfo?"
    "selectedTab=Matches&innerselectedTab=Scheduled&eventId=3242"
)
TIMEZONES = {
    "Helsinki": "Europe/Helsinki",
    "Vietnam": "Asia/Ho_Chi_Minh",
    "Korea": "Asia/Seoul",
}
DATE_RE = re.compile(
    r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s+"
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(?P<month>[A-Za-z]+)\s+(?P<year>\d{4})$",
    re.IGNORECASE,
)
DATE_TIME_RE = re.compile(
    r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s+"
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(?P<month>[A-Za-z]+)\s+(?P<year>\d{4})\s*[-–—]\s*"
    r"(?P<time>\d{1,2}:\d{2})$",
    re.IGNORECASE,
)
TIME_RE = re.compile(r"^(?P<time>\d{1,2}:\d{2})(?:\s+(?P<rest>.*))?$")
WANG_RE = re.compile(r"\bWang\s+Chuqin\b", re.IGNORECASE)
SUN_RE = re.compile(r"\bSun\s+Yingsha\b", re.IGNORECASE)


def _clean_lines(body: str) -> list[str]:
    return [line.strip() for line in body.splitlines() if line.strip()]


def _parse_date(line: str) -> datetime | None:
    match = DATE_RE.match(line)
    if not match:
        return None
    return _build_date(match["day"], match["month"], match["year"])


def _build_date(day: str, month: str, year: str) -> datetime:
    date_text = f"{day} {month} {year}"
    for month_format in ("%B", "%b"):
        try:
            return datetime.strptime(date_text, f"%d {month_format} %Y")
        except ValueError:
            continue
    raise ValueError(f"Không thể đọc ngày thi đấu: {date_text}")


def _extract_players(lines: list[str]) -> tuple[str, str]:
    metadata_prefixes = (
        "start list",
        "scheduled",
        "match centre",
        "filter",
        "download",
    )
    vs_opponent = ""
    for line in lines:
        lower_line = line.lower()
        if " vs " in lower_line:
            _, right = re.split(r"\s+vs\.?\s+", line, maxsplit=1, flags=re.I)
            vs_opponent = right.strip()
            continue
        if (
            "singles -" in lower_line
            or "doubles -" in lower_line
            or lower_line.startswith("winner of")
            or lower_line.startswith(metadata_prefixes)
        ):
            continue
    combined = " ".join(lines)
    if WANG_RE.search(combined) and SUN_RE.search(combined):
        return "WANG Chuqin / SUN Yingsha", vs_opponent

    category_index = next(
        (
            index
            for index, line in enumerate(lines)
            if (
                "singles" in line.lower()
                or "doubles" in line.lower()
                or "msingles" in line.lower()
                or "wsingles" in line.lower()
                or "xdoubles" in line.lower()
            )
        ),
        None,
    )
    if category_index is None:
        target_match = WANG_RE.search(combined) or SUN_RE.search(combined)
        return (target_match.group(0), vs_opponent) if target_match else ("", "")

    participants: list[str] = []
    for line in lines[category_index + 1 :]:
        lower_line = line.lower()
        if (
            lower_line.startswith("table")
            or "convention center" in lower_line
        ):
            break
        if (
            lower_line.startswith(metadata_prefixes)
            or lower_line.startswith("winner of")
            or "round of" in lower_line
            or re.search(r"\btt[a-z]+", lower_line)
            or re.search(r"\b(r\d+|qf|sf|f)-", lower_line)
        ):
            continue
        if line and line not in participants:
            participants.append(re.sub(r"\s*\(\d+\)$", "", line).strip())
    if len(participants) >= 2:
        return participants[0], participants[1]
    target_match = WANG_RE.search(combined) or SUN_RE.search(combined)
    if target_match:
        return target_match.group(0), vs_opponent
    return "", ""


def _round_label(details: str) -> str:
    patterns = [
        (r"round\s+of\s+(\d+)", lambda match: f"Vòng {match.group(1)}"),
        (r"\bR(128|64|32|16|8|4|2)\b", lambda match: f"Vòng {match.group(1)}"),
        (r"quarter[- ]?final", lambda _: "Tứ kết"),
        (r"semi[- ]?final", lambda _: "Bán kết"),
        (r"final", lambda _: "Chung kết"),
    ]
    for pattern, formatter in patterns:
        match = re.search(pattern, details, re.IGNORECASE)
        if match:
            return formatter(match)
    return ""


def _format_time(value: datetime, timezone_name: str) -> str:
    local_time = value.astimezone(ZoneInfo(timezone_name))
    hour = local_time.hour % 12 or 12
    period = "sáng" if local_time.hour < 12 else "chiều" if local_time.hour < 18 else "tối"
    return f"{local_time:%d/%m/%Y} · {hour:02d}:{local_time.minute:02d} {period}"


def _contains_wang_sun_pair(lines: list[str]) -> bool:
    combined = " ".join(lines)
    return bool(WANG_RE.search(combined) and SUN_RE.search(combined))


def parse_schedule(body: str, source_timezone: str) -> pd.DataFrame:
    lines = _clean_lines(body)
    rows: list[dict[str, str]] = []
    current_date: datetime | None = None

    for index, line in enumerate(lines):
        date_time_match = DATE_TIME_RE.match(line)
        if date_time_match:
            current_date = _build_date(
                date_time_match["day"],
                date_time_match["month"],
                date_time_match["year"],
            )
            time_value = date_time_match["time"]
        else:
            time_value = None
        parsed_date = _parse_date(line)
        if parsed_date and not date_time_match:
            current_date = parsed_date
            continue

        time_match = TIME_RE.match(line) if time_value is None else None
        if time_match:
            time_value = time_match["time"]
        if time_value is None or current_date is None:
            continue

        context: list[str] = []
        if time_match and time_match["rest"]:
            context.append(time_match["rest"])
        for following in lines[index + 1 : index + 61]:
            if (
                _parse_date(following)
                or DATE_TIME_RE.match(following)
                or TIME_RE.match(following)
            ):
                break
            context.append(following)

        player_1, player_2 = _extract_players(context)
        details = " | ".join(context)
        details_lower = details.lower()
        is_mixed_doubles = (
            "mixed doubles" in details_lower
            or "xdoubles" in details_lower
            or re.search(r"\bxd\b", details_lower)
        )
        is_women_singles = (
            "women" in details_lower
            or "wsingles" in details_lower
            or "ttewsingles" in details_lower
            or re.search(r"\bws\b", details_lower)
        )
        is_men_singles = (
            "men" in details_lower
            or "msingles" in details_lower
            or "ttemsingles" in details_lower
            or re.search(r"\bms\b", details_lower)
        )
        if is_mixed_doubles and _contains_wang_sun_pair(context):
            category = "Mixed Doubles"
        elif SUN_RE.search(details) and is_women_singles:
            category = "Women Singles"
        elif WANG_RE.search(details) and is_men_singles:
            category = "Men Singles"
        else:
            category = ""
        local_dt = datetime.strptime(
            f"{current_date:%Y-%m-%d} {time_value}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=ZoneInfo(source_timezone))
        rows.append(
            {
                "Date": current_date.strftime("%d/%m/%Y"),
                "Venue time": _format_time(local_dt, source_timezone),
                "Helsinki": _format_time(local_dt, TIMEZONES["Helsinki"]),
                "Vietnam": _format_time(local_dt, TIMEZONES["Vietnam"]),
                "Korea": _format_time(local_dt, TIMEZONES["Korea"]),
                "Match": (
                    f"{player_1} VS {player_2}"
                    if player_1 and player_2
                    else player_1
                ),
                "Details": details,
                "Category": category,
            }
        )

    return pd.DataFrame(
        rows,
        columns=[
            "Date",
            "Venue time",
            "Helsinki",
            "Vietnam",
            "Korea",
            "Match",
            "Details",
            "Category",
        ],
    )


def scrape_page(url: str) -> str:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox"],
        )

        page = browser.new_page(
            viewport={"width": 1440, "height": 1200}
        )

        responses = []

        def capture_response(response):
            try:
                content_type = response.headers.get("content-type", "")
                if "json" in content_type:
                    responses.append(response)
            except Exception:
                pass

        page.on("response", capture_response)

        try:
            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60_000,
            )

            # Give React time to start
            page.wait_for_timeout(3000)

            # Wait until WTT actually renders match content
            try:
                page.wait_for_function(
                    """
                    () => {
                        const text = document.body.innerText;
                        return (
                            text.includes("Wang") ||
                            text.includes("Sun") ||
                            text.includes("Scheduled") ||
                            text.includes("Matches")
                        );
                    }
                    """,
                    timeout=30000,
                )
            except PlaywrightTimeoutError:
                pass

            body = page.locator("body").inner_text()

            # Prefer rendered content if it contains real player data
            if (
                "Wang" in body
                or "SUN" in body
                or "Sun" in body
            ):
                return body


            # Otherwise try API responses
            for response in responses:
                try:
                    data = response.json()
                    text = json.dumps(data)

                    if (
                        "Wang" in text
                        or "Sun" in text
                        or "Chuqin" in text
                        or "Yingsha" in text
                    ):
                        return text

                except Exception:
                    continue

            # Debug information
            print("No WTT data found")
            print("Body length:", len(body))
            print(body[:500])

            return body

        finally:
            browser.close()

@st.cache_data(
    ttl=15 * 60,
    show_spinner=False
)
def load_schedule(
    url: str,
    source_timezone: str,
    cache_version: str = "korea-time-v3",
):
    del cache_version

    df = parse_schedule(
        scrape_page(url),
        source_timezone,
    )

    if df.empty:
        raise RuntimeError(
            "WTT returned no matches. Not caching empty result."
        )

    return df

def _matches_for_player(schedule: pd.DataFrame, category: str) -> pd.DataFrame:
    matches = schedule[schedule["Category"] == category].copy()
    if category == "Men Singles":
        return matches[matches["Details"].str.contains(WANG_RE, na=False)]
    if category == "Women Singles":
        return matches[matches["Details"].str.contains(SUN_RE, na=False)]
    return matches[
        matches["Details"].apply(
            lambda details: _contains_wang_sun_pair(details.split(" | "))
        )
    ]


def _render_match(match: pd.Series) -> None:
    matchup = match["Match"] or "Thông tin người chơi đang được cập nhật"
    round_label = _round_label(match["Details"])
    display_match = f"{round_label}: {matchup}" if round_label else matchup
    korea_time = match.get("Korea", "Đang cập nhật")
    st.markdown(
        f"""
        <div class="match-card">
          <div class="match-date">{match['Date']}</div>
          <div class="match-players">{display_match}</div>
          <div class="match-time"><span>🇫🇮 {match['Helsinki']}</span><span>🇻🇳 {match['Vietnam']}</span><span>🇰🇷 {korea_time}</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.set_page_config(
    page_title="WTT US Smash 2026 - Lịch thi đấu",
    page_icon="🏓",
    layout="wide"
)

st.markdown(
    """
    <style>
    .block-container {
        max-width: 1500px;
        padding-top: 2.5rem;
    }

    .event-title {
        border: 2px solid var(--text-color);
        color: var(--text-color);
        border-radius: 26px;
        padding: 1rem;
        text-align: center;
        font-size: 2rem;
        font-weight: 700;
        margin: 0 auto 2.5rem;
        max-width: 650px;
    }

    .section-title {
        color: var(--text-color);
        text-align: center;
        font-size: 1.35rem;
        font-weight: 700;
        margin-bottom: 1rem;
    }

    .match-card {
        border: 1px solid var(--secondary-background-color);
        border-radius: 14px;
        padding: .8rem;
        margin-bottom: .8rem;
        background: var(--background-color);
        color: var(--text-color);
        box-shadow: 0 1px 4px rgba(0,0,0,.06);
    }

    .match-players {
        color: var(--text-color);
        font-weight: 650;
        line-height: 1.3;
        min-height: 2.6em;
    }

    .match-date {
        color: var(--text-color);
        opacity: 0.65;
        font-size: .85rem;
        margin-bottom: .35rem;
    }

    .match-time {
        color: var(--text-color);
        display: flex;
        flex-direction: column;
        gap: .35rem;
        font-size: 1.15rem;
        font-weight: 650;
        line-height: 1.35;
        margin-top: .8rem;
    }

    .updated {
        color: var(--text-color);
        opacity: 0.65;
        text-align: center;
        font-size: .85rem;
        margin-bottom: 1.5rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
st.markdown('<div class="event-title">WTT US Smash 2026</div>', unsafe_allow_html=True)
st_autorefresh(interval=12 * 60 * 60 * 1000, key="wtt-12-hour-refresh")
url = DEFAULT_URL
source_timezone = "America/Los_Angeles"

try:
    with st.spinner("Đang cập nhật lịch thi đấu…"):
        schedule = load_schedule(url, source_timezone)
except Exception as error:
    st.error("Không thể cập nhật lịch thi đấu WTT.")
    st.caption(f"Chi tiết lỗi: {error}")
    st.stop()

schedule = schedule[schedule["Category"].isin(["Men Singles", "Women Singles", "Mixed Doubles"])]
if schedule.empty:
    st.write("Rows scraped:", len(schedule))
    st.write(schedule.head(10))
    st.warning(
        "Hiện chưa có trận đấu của Wang Chuqin hoặc Sun Yingsha, hoặc WTT chưa công bố lịch thi đấu."
    )
    st.stop()

st.markdown('<div class="updated">Lịch thi đấu · tự cập nhật mỗi 12 giờ</div>', unsafe_allow_html=True)
columns = st.columns(3)
for column, title, category in zip(
    columns,
    ["Đơn nam", "Đơn nữ", "Đôi nam nữ"],
    ["Men Singles", "Women Singles", "Mixed Doubles"],
):
    with column:
        st.markdown(f'<div class="section-title">{title}</div>', unsafe_allow_html=True)
        matches = _matches_for_player(schedule, category)
        if matches.empty:
            st.caption("Chưa có trận đấu")
        else:
            for _, match in matches.iterrows():
                _render_match(match)
