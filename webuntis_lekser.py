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
    print(f"Login status: {resp.status_code}")
    if "invalidLogin" in resp.url:
        raise Exception("Innlogging feilet!")
    print("✅ Logget inn")


def get_timetable_periods(session):
    """Hent alle timer for uken via weekly timetable"""
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://{WEBUNTIS_SERVER}/WebUntis/?school={WEBUNTIS_SCHOOL}"
    }

    url = f"https://{WEBUNTIS_SERVER}/WebUntis/api/public/timetable/weekly/data"
    params = {
        "elementType": 5,
        "elementId": WEBUNTIS_ELEMENT_ID,
        "date": monday.strftime("%Y-%m-%d"),
        "formatId": 1
    }

    resp = session.get(url, params=params, headers=headers)
    print(f"Weekly timetable status: {resp.status_code}")
    print(f"Respons: {resp.text[:600]}")
    return resp


def get_notes(session):
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://{WEBUNTIS_SERVER}/WebUntis/?school={WEBUNTIS_SCHOOL}"
    }

    notes = []

    # Hent ukentlig timeplan for å få alle timer med tider
    weekly_resp = get_timetable_periods(session)
    if weekly_resp.status_code == 200 and weekly_resp.text.strip().startswith("{"):
        data = weekly_resp.json()
        result = data.get("data", {}).get("result", {}).get("data", {})
        elements = result.get("elements", [])
        element_periods = result.get("elementPeriods", {})

        subject_map = {e["id"]: e.get("name", "Ukjent") for e in elements if e.get("type") == 3}

        # Gå gjennom hver time og hent detaljer
        for key, periods in element_periods.items():
            for period in periods:
                start_dt = period.get("startDateTime") or ""
                end_dt = period.get("endDateTime") or ""

                # Bygg start/end fra date+startTime+endTime hvis datetime mangler
                if not start_dt:
                    date_raw = str(period.get("date", ""))
                    start_time = str(period.get("startTime", "0000")).zfill(4)
                    end_time = str(period.get("endTime", "0000")).zfill(4)
                    try:
                        d = datetime.strptime(date_raw, "%Y%m%d")
                        start_dt = d.strftime(f"%Y-%m-%dT{start_time[:2]}:{start_time[2:]}:00")
                        end_dt = d.strftime(f"%Y-%m-%dT{end_time[:2]}:{end_time[2:]}:00")
                    except Exception:
                        continue

                # Kall detail-API for denne timen
                url = (f"https://{WEBUNTIS_SERVER}/WebUntis/api/rest/view/v2/calendar-entry/detail"
                       f"?elementId={WEBUNTIS_ELEMENT_ID}&elementType=5"
                       f"&endDateTime={end_dt}"
                       f"&homeworkOption=DUE"
                       f"&startDateTime={start_dt}")

                resp = session.get(url, headers=headers)
                if resp.status_code != 200:
                    continue

                detail = resp.json()
                entries = detail if isinstance(detail, list) else detail.get("data", [])
                if not isinstance(entries, list):
                    entries = [entries]

                for entry in entries:
                    lstext = (entry.get("lstext") or entry.get("lessonText") or
                              entry.get("text") or "").strip()
                    if not lstext:
                        continue

                    # Finn fagnavn
                    subject = "Ukjent fag"
                    for el in period.get("elements", []):
                        if el.get("type") == 3:
                            subject = subject_map.get(el.get("id"), "Ukjent fag")
                            break

                    date_str = start_dt[:10]
                    try:
                        due_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y")
                    except Exception:
                        due_date = date_str

                    notes.append({
                        "subject": subject,
                        "text": lstext,
                        "date": due_date,
                        "raw_date": date_str,
                    })
                    print(f"  📝 {subject} ({due_date}): {lstext[:80]}")

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
