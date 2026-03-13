"""
WebUntis Lekse-varsler til iPhone via Pushover
"""

import requests
import json
import os
from datetime import datetime, timedelta
import hashlib
import base64

WEBUNTIS_SERVER     = os.environ.get("WEBUNTIS_SERVER", "")
WEBUNTIS_SCHOOL     = os.environ.get("WEBUNTIS_SCHOOL", "")
WEBUNTIS_USERNAME   = os.environ.get("WEBUNTIS_USERNAME", "")
WEBUNTIS_PASSWORD   = os.environ.get("WEBUNTIS_PASSWORD", "")
WEBUNTIS_ELEMENT_ID = os.environ.get("WEBUNTIS_ELEMENT_ID", "1859")
PUSHOVER_USER_KEY   = os.environ.get("PUSHOVER_USER_KEY", "")
PUSHOVER_APP_TOKEN  = os.environ.get("PUSHOVER_APP_TOKEN", "")

SEEN_HOMEWORK_FILE = "seen_homework.json"


def load_seen_homework():
    if os.path.exists(SEEN_HOMEWORK_FILE):
        with open(SEEN_HOMEWORK_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_seen_homework(seen: set):
    with open(SEEN_HOMEWORK_FILE, "w") as f:
        json.dump(list(seen), f)


def note_id(subject, text, date) -> str:
    key = f"{date}-{subject}-{str(text)[:80]}"
    return hashlib.md5(key.encode()).hexdigest()


def send_pushover(title: str, message: str):
    resp = requests.post("https://api.pushover.net/1/messages.json", data={
        "token": PUSHOVER_APP_TOKEN,
        "user": PUSHOVER_USER_KEY,
        "title": title,
        "message": message,
    })
    resp.raise_for_status()
    print(f"✅ Pushover-varsel sendt: {title}")


def login(session):
    school_cookie = "_" + base64.b64encode(WEBUNTIS_SCHOOL.encode()).decode()
    session.cookies.set("schoolname", school_cookie, domain=WEBUNTIS_SERVER, path="/WebUntis")

    url = f"https://{WEBUNTIS_SERVER}/WebUntis/j_spring_security_check"
    data = {
        "school": WEBUNTIS_SCHOOL,
        "j_username": WEBUNTIS_USERNAME,
        "j_password": WEBUNTIS_PASSWORD,
        "token": ""
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        "Referer": f"https://{WEBUNTIS_SERVER}/WebUntis/?school={WEBUNTIS_SCHOOL}"
    }
    resp = session.post(url, data=data, headers=headers, allow_redirects=True)
    if "invalidLogin" in resp.url:
        raise Exception("Innlogging feilet!")
    print("✅ Logget inn")

    # Hent Bearer-token slik nettleseren gjør
    token_resp = session.get(
        f"https://{WEBUNTIS_SERVER}/WebUntis/api/token/new",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
        }
    )
    print(f"Token status: {token_resp.status_code}")
    print(f"Token respons: {token_resp.text[:200]}")

    if token_resp.status_code == 200:
        token_data = token_resp.json()
        bearer = token_data.get("token") or token_data.get("access_token") or str(token_data)
        print(f"✅ Fikk token: {bearer[:30]}...")
        return bearer
    return None


def get_notes(session, bearer_token):
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())

    base_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    }
    if bearer_token:
        base_headers["Authorization"] = f"Bearer {bearer_token}"

    # Hent ukentlig timeplan
    url = f"https://{WEBUNTIS_SERVER}/WebUntis/api/public/timetable/weekly/data"
    params = {
        "elementType": 5,
        "elementId": WEBUNTIS_ELEMENT_ID,
        "date": monday.strftime("%Y-%m-%d"),
        "formatId": 1
    }
    resp = session.get(url, params=params, headers=base_headers)
    data = resp.json()
    result = data.get("data", {}).get("result", {}).get("data", {})
    periods = result.get("elementPeriods", {}).get(str(WEBUNTIS_ELEMENT_ID), [])

    # Grupper per lessonId, per dag
    from collections import defaultdict
    lesson_day = defaultdict(list)
    for period in periods:
        key = (period.get("lessonId"), str(period.get("date", "")))
        lesson_day[key].append(period)

    print(f"Fant {len(lesson_day)} unike leksjon-dag kombinasjoner")

    notes = []
    seen_texts = set()

    for (lesson_id, date_raw), day_periods in lesson_day.items():
        day_periods.sort(key=lambda p: p.get("startTime", 0))
        first = day_periods[0]
        last = day_periods[-1]

        start_time = str(first.get("startTime", "0000")).zfill(4)
        end_time = str(last.get("endTime", "0000")).zfill(4)

        try:
            d = datetime.strptime(date_raw, "%Y%m%d")
            start_dt = d.strftime(f"%Y-%m-%dT{start_time[:2]}:{start_time[2:]}:00")
            end_dt = d.strftime(f"%Y-%m-%dT{end_time[:2]}:{end_time[2:]}:00")
        except Exception:
            continue

        start_enc = start_dt.replace(":", "%3A")
        end_enc = end_dt.replace(":", "%3A")

        detail_url = (
            f"https://{WEBUNTIS_SERVER}/WebUntis/api/rest/view/v2/calendar-entry/detail"
            f"?elementId={WEBUNTIS_ELEMENT_ID}&elementType=5"
            f"&endDateTime={end_enc}"
            f"&homeworkOption=DUE"
            f"&startDateTime={start_enc}"
        )

        referer = (
            f"https://{WEBUNTIS_SERVER}/WebUntis/timetable/my-student"
            f"/lessonDetails/{lesson_id}/{WEBUNTIS_ELEMENT_ID}/5"
            f"/{start_dt}/{end_dt}/true?date={d.strftime('%Y-%m-%d')}&entityId={WEBUNTIS_ELEMENT_ID}"
        )

        req_headers = {**base_headers, "Referer": referer}
        detail_resp = session.get(detail_url, headers=req_headers)
        print(f"  {start_dt}: status {detail_resp.status_code}")

        if detail_resp.status_code != 200:
            continue

        entries = detail_resp.json().get("calendarEntries", [])
        for entry in entries:
            notes_text = (entry.get("notesAll") or "").strip()
            if not notes_text:
                continue

            subject = entry.get("subject", {}).get("longName", "Ukjent fag")
            date_str = entry.get("startDateTime", "")[:10]
            try:
                due_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y")
            except Exception:
                due_date = date_str

            nid = note_id(subject, notes_text, due_date)
            if nid not in seen_texts:
                seen_texts.add(nid)
                notes.append({"subject": subject, "text": notes_text,
                              "date": due_date, "raw_date": date_str})
                print(f"    📝 {subject}: {notes_text[:60]}")

    return notes


def main():
    print(f"🕐 Kjører sjekk: {datetime.now().strftime('%d.%m.%Y %H:%M')}")

    if not all([WEBUNTIS_SERVER, WEBUNTIS_SCHOOL, WEBUNTIS_USERNAME,
                WEBUNTIS_PASSWORD, PUSHOVER_USER_KEY, PUSHOVER_APP_TOKEN]):
        raise Exception("Mangler miljøvariabler!")

    seen = load_seen_homework()
    today = datetime.now()

    session = requests.Session()
    bearer = login(session)
    notes = get_notes(session, bearer)

    print(f"\n📋 Fant {len(notes)} notater denne uken")

    new_notes = []
    for n in notes:
        nid = note_id(n["subject"], n["text"], n["date"])
        if nid not in seen:
            new_notes.append(n)
            seen.add(nid)

    if not notes:
        if today.weekday() == 0:
            send_pushover("📚 Lekser denne uken", "Ingen lekser denne uken! 🎉")
        else:
            print("Ingen notater – ingen varsling.")
    elif new_notes:
        lines = []
        for n in sorted(new_notes, key=lambda x: x.get("raw_date", "")):
            lines.append(f"📚 {n['subject']} ({n['date']})")
            lines.append(f"   {n['text']}")
        send_pushover(f"📚 {len(new_notes)} ny(e) lekse(r)!", "\n".join(lines))
    else:
        print("Ingen nye notater siden siste sjekk.")

    save_seen_homework(seen)
    print("✅ Ferdig!")


if __name__ == "__main__":
    main()
