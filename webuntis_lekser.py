"""
WebUntis Lekse-varsler til iPhone via Pushover
"""

import requests
import json
import os
from datetime import datetime, timedelta
import hashlib
import base64
from collections import defaultdict

WEBUNTIS_SERVER     = os.environ.get("WEBUNTIS_SERVER", "")
WEBUNTIS_SCHOOL     = os.environ.get("WEBUNTIS_SCHOOL", "")
WEBUNTIS_USERNAME   = os.environ.get("WEBUNTIS_USERNAME", "")
WEBUNTIS_PASSWORD   = os.environ.get("WEBUNTIS_PASSWORD", "")
WEBUNTIS_ELEMENT_ID = os.environ.get("WEBUNTIS_ELEMENT_ID", "1859")
PUSHOVER_USER_KEY   = os.environ.get("PUSHOVER_USER_KEY", "")
PUSHOVER_APP_TOKEN  = os.environ.get("PUSHOVER_APP_TOKEN", "")

SEEN_HOMEWORK_FILE = "seen_homework.json"

# Timeplanen fra skjermbildet – dato, start, slutt
TIMETABLE = [
    ("20260309", "0830", "1000"),  # Man: Matematikk S1
    ("20260309", "1245", "1415"),  # Man: Økonomistyring
    ("20260310", "0830", "1000"),  # Tir: Norsk
    ("20260310", "1020", "1150"),  # Tir: Informasjonsteknologi
    ("20260310", "1245", "1415"),  # Tir: Tysk
    ("20260311", "0830", "1000"),  # Ons: Tysk
    ("20260311", "1020", "1150"),  # Ons: Økonomistyring
    ("20260311", "1230", "1400"),  # Ons: Norsk
    ("20260311", "1415", "1545"),  # Ons: Kristendomskunnskap
    ("20260312", "0830", "1000"),  # To: Historie
    ("20260312", "1020", "1150"),  # To: Informasjonsteknologi
    ("20260312", "1230", "1400"),  # To: Informasjonsteknologi
    ("20260313", "0830", "1000"),  # Fr: Matematikk S1
    ("20260313", "1020", "1150"),  # Fr: Kroppsøving
    ("20260313", "1230", "1400"),  # Fr: Informasjonsteknologi
]


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

    # Besøk siden først for å få eventuelle initielle cookies
    session.get(
        f"https://{WEBUNTIS_SERVER}/WebUntis/?school={WEBUNTIS_SCHOOL}",
        headers={"User-Agent": "Mozilla/5.0"}
    )

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
    print(f"✅ Logget inn, cookies: {list(session.cookies.keys())}")


def get_notes(session):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://{WEBUNTIS_SERVER}/WebUntis/index.do#/my-timetable"
    }

    notes = []
    seen_texts = set()

    for date_raw, start_time, end_time in TIMETABLE:
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
                notes.append({
                    "subject": subject,
                    "text": notes_text,
                    "date": due_date,
                    "raw_date": date_str,
                })
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
    login(session)
    notes = get_notes(session)

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
