"""
WebUntis Lekse-varsler til iPhone via Pushover
Bruker JSON-RPC for å hente timeplan og notater
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


def rpc_call(session, method, params={}):
    url = f"https://{WEBUNTIS_SERVER}/WebUntis/jsonrpc.do"
    payload = {"id": method, "method": method, "params": params, "jsonrpc": "2.0"}
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Referer": f"https://{WEBUNTIS_SERVER}/WebUntis/?school={WEBUNTIS_SCHOOL}"
    }
    resp = session.post(url, json=payload, headers=headers,
                        params={"school": WEBUNTIS_SCHOOL})
    data = resp.json()
    if "error" in data:
        print(f"RPC feil for {method}: {data['error']}")
        return None
    return data.get("result")


def get_notes(session):
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    start = int(monday.strftime("%Y%m%d"))
    end = int(sunday.strftime("%Y%m%d"))

    # Hent brukerinfo via getStudents / getPersonId
    person_id = None
    person_type = 5  # 5 = student

    # Prøv å hente min egen ID
    students = rpc_call(session, "getStudents")
    print(f"getStudents: {str(students)[:300]}")

    if students:
        for s in students:
            if s.get("name", "").lower() == WEBUNTIS_USERNAME.lower() or \
               s.get("longName", "").lower() == WEBUNTIS_USERNAME.lower():
                person_id = s.get("id")
                print(f"✅ Fant student ID: {person_id} ({s.get('longName')})")
                break

        if not person_id and students:
            # Vis første 5 studenter for debugging
            print("Første studenter:", [(s.get("id"), s.get("name"), s.get("longName")) for s in students[:5]])

    # Hent timeplan
    params = {
        "id": person_id or 0,
        "type": person_type,
        "startDate": start,
        "endDate": end,
        "fields": ["id", "date", "startTime", "endTime", "subjects", "lstext", "activityType"]
    }

    timetable = rpc_call(session, "getTimetable", params)
    print(f"getTimetable: fant {len(timetable) if timetable else 0} timer")
    if timetable:
        print(f"Eksempel time: {timetable[0]}")

    notes = []
    if not timetable:
        return notes

    # Hent faginfo
    subjects_list = rpc_call(session, "getSubjects") or []
    subject_map = {s["id"]: s.get("name", s.get("longName", "Ukjent")) for s in subjects_list}

    for period in timetable:
        lstext = period.get("lstext", "").strip()
        if not lstext:
            continue

        # Finn fagnavn
        subject = "Ukjent fag"
        subj_ids = [s["id"] for s in period.get("su", period.get("subjects", []))]
        if subj_ids:
            subject = subject_map.get(subj_ids[0], "Ukjent fag")

        date_raw = str(period.get("date", ""))
        try:
            due_date = datetime.strptime(date_raw, "%Y%m%d").strftime("%d.%m.%Y")
        except Exception:
            due_date = date_raw

        notes.append({
            "subject": subject,
            "text": lstext,
            "date": due_date,
            "raw_date": date_raw,
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
