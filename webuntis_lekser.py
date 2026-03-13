"""
WebUntis Lekse-varsler til iPhone via Pushover
Bruker /api/rest/view/v2/calendar-entry/detail
"""

import requests
import json
import os
from datetime import datetime, timedelta
import hashlib
import base64

WEBUNTIS_SERVER   = os.environ.get("WEBUNTIS_SERVER", "")
WEBUNTIS_SCHOOL   = os.environ.get("WEBUNTIS_SCHOOL", "")
WEBUNTIS_USERNAME = os.environ.get("WEBUNTIS_USERNAME", "")
WEBUNTIS_PASSWORD = os.environ.get("WEBUNTIS_PASSWORD", "")
WEBUNTIS_ELEMENT_ID = os.environ.get("WEBUNTIS_ELEMENT_ID", "1859")  # Din student-ID
PUSHOVER_USER_KEY  = os.environ.get("PUSHOVER_USER_KEY", "")
PUSHOVER_APP_TOKEN = os.environ.get("PUSHOVER_APP_TOKEN", "")

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
    print(f"Login status: {resp.status_code}")
    if "invalidLogin" in resp.url:
        raise Exception("Innlogging feilet!")
    print("✅ Logget inn")


def get_notes(session):
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)

    # Format: 2026-03-09T00:00:00
    start = monday.strftime("%Y-%m-%dT00%%3A00%%3A00")
    end = sunday.strftime("%Y-%m-%dT23%%3A59%%3A59")

    url = (f"https://{WEBUNTIS_SERVER}/WebUntis/api/rest/view/v2/calendar-entry/detail"
           f"?elementId={WEBUNTIS_ELEMENT_ID}&elementType=5"
           f"&endDateTime={sunday.strftime('%Y-%m-%dT23:59:59')}"
           f"&homeworkOption=DUE"
           f"&startDateTime={monday.strftime('%Y-%m-%dT00:00:00')}")

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://{WEBUNTIS_SERVER}/WebUntis/?school={WEBUNTIS_SCHOOL}"
    }

    resp = session.get(url, headers=headers)
    print(f"Calendar API status: {resp.status_code}")
    print(f"Respons: {resp.text[:800]}")

    if resp.status_code != 200 or not resp.text.strip().startswith("{"):
        return []

    data = resp.json()
    notes = []

    # Parse responsen
    entries = data.get("data", data) if isinstance(data.get("data"), list) else []
    if not entries and isinstance(data, list):
        entries = data

    print(f"Antall entries: {len(entries)}")

    for entry in entries:
        # Finn notater/lstext
        lstext = (entry.get("lstext") or entry.get("lessonText") or
                  entry.get("studentGroup") or entry.get("text") or "").strip()
        info = (entry.get("studentGroup") or entry.get("info") or "").strip()

        # Kombiner tilgjengelig tekst
        text = lstext or info
        if not text:
            continue

        subject = (entry.get("subject") or entry.get("subjectName") or
                   entry.get("lessonSubject") or "Ukjent fag")

        date_raw = entry.get("date") or entry.get("startDateTime", "")[:10]
        try:
            due_date = datetime.strptime(date_raw, "%Y-%m-%d").strftime("%d.%m.%Y")
        except Exception:
            due_date = date_raw

        notes.append({
            "subject": subject,
            "text": text,
            "date": due_date,
            "raw_date": date_raw,
        })
        print(f"  📝 {subject} ({due_date}): {text[:80]}")

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
