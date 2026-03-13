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
        "User-Agent": "Mozilla/5.0",
        "Referer": f"https://{WEBUNTIS_SERVER}/WebUntis/?school={WEBUNTIS_SCHOOL}"
    }
    resp = session.post(url, data=data, headers=headers, allow_redirects=True)
    if "invalidLogin" in resp.url:
        raise Exception("Innlogging feilet!")
    session.cookies.set("Tenant-Id", '"7418800"', domain=WEBUNTIS_SERVER, path="/WebUntis")
    print("✅ Logget inn")


def get_notes(session):
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://{WEBUNTIS_SERVER}/WebUntis/?school={WEBUNTIS_SCHOOL}"
    }

    # Hent ukentlig timeplan
    url = f"https://{WEBUNTIS_SERVER}/WebUntis/api/public/timetable/weekly/data"
    params = {
        "elementType": 5,
        "elementId": WEBUNTIS_ELEMENT_ID,
        "date": monday.strftime("%Y-%m-%d"),
        "formatId": 1
    }
    resp = session.get(url, params=params, headers=headers)
    data = resp.json()
    result = data.get("data", {}).get("result", {}).get("data", {})
    periods = result.get("elementPeriods", {}).get(str(WEBUNTIS_ELEMENT_ID), [])

    # Grupper timer per lessonId – finn start på første og slutt på siste
    from collections import defaultdict
    lesson_groups = defaultdict(list)
    for period in periods:
        lesson_id = period.get("lessonId")
        if lesson_id:
            lesson_groups[lesson_id].append(period)

    print(f"Fant {len(lesson_groups)} unike leksjoner denne uken")

    notes = []

    for lesson_id, lesson_periods in lesson_groups.items():
        # Sorter etter dato og starttid
        lesson_periods.sort(key=lambda p: (p.get("date", 0), p.get("startTime", 0)))

        first = lesson_periods[0]
        last = lesson_periods[-1]

        date_raw = str(first.get("date", ""))
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

        detail_resp = session.get(detail_url, headers=headers)
        print(f"  {start_dt} → {end_dt}: status {detail_resp.status_code}")

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

            notes.append({
                "subject": subject,
                "text": notes_text,
                "date": due_date,
                "raw_date": date_str,
            })
            print(f"  📝 {subject} ({due_date}): {notes_text[:80]}")

    return notes


def main():
    print(f"🕐 Kjører sjekk: {datetime.now().strftime('%d.%m.%Y %H:%M')}")

    if not all([WEBUNTIS_SERVER, WEBUNTIS_SCHOOL, WEBUNTIS_USERNAME,
                WEBUNTIS_PASSWORD, PUSHOVER_USER_KEY, PUSHOVER_APP_TOKEN]):
        raise Exception("Mangler miljøvariabler!")

    seen = load_seen_homework()
    today = datetime.now()

    session = requests.Session()
    login(session)
    notes = get_notes(session)

    # Dedupliser
    seen_texts = set()
    unique_notes = []
    for n in notes:
        nid = note_id(n["subject"], n["text"], n["date"])
        if nid not in seen_texts:
            seen_texts.add(nid)
            unique_notes.append(n)
    notes = unique_notes

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
