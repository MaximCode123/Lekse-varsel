"""
WebUntis Lekse-varsler til iPhone via Pushover
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
    print(f"Login status: {resp.status_code}, URL: {resp.url}")
    if "invalidLogin" in resp.url:
        raise Exception("Innlogging feilet!")
    print("✅ Logget inn")


def get_student_id(session):
    """Hent innlogget brukers person-ID og elev-ID"""
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }

    # Prøv å hente brukerinfo
    for url in [
        f"https://{WEBUNTIS_SERVER}/WebUntis/api/rest/view/v1/profile",
        f"https://{WEBUNTIS_SERVER}/WebUntis/api/profile",
        f"https://{WEBUNTIS_SERVER}/WebUntis/api/rest/view/v1/auth/currentUser",
    ]:
        resp = session.get(url, headers=headers)
        print(f"Profile {url.split('/')[-1]}: {resp.status_code} - {resp.text[:300]}")
        if resp.status_code == 200 and resp.text.strip().startswith("{"):
            data = resp.json()
            # Finn ID i ulike felt
            person_id = (data.get("personId") or data.get("id") or
                        data.get("data", {}).get("personId") or
                        data.get("data", {}).get("id"))
            if person_id:
                print(f"✅ Fant person ID: {person_id}")
                return person_id

    # Prøv via JSON-RPC getCurrentSchoolyear for å bekrefte innlogging
    rpc_url = f"https://{WEBUNTIS_SERVER}/WebUntis/jsonrpc.do"
    for method in ["getLatestImportTime", "getCurrentSchoolyear"]:
        payload = {"id": method, "method": method, "params": {}, "jsonrpc": "2.0"}
        resp = session.post(rpc_url, json=payload, headers=headers,
                           params={"school": WEBUNTIS_SCHOOL})
        print(f"RPC {method}: {resp.status_code} - {resp.text[:200]}")

    return None


def get_timetable_with_notes(session, student_id=None):
    """Hent timeplan med notater"""
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://{WEBUNTIS_SERVER}/WebUntis/?school={WEBUNTIS_SCHOOL}"
    }

    # Prøv med elementType=5 (student/meg selv) og elementId fra student_id
    date_str = monday.strftime("%Y-%m-%d")

    urls_to_try = [
        f"/WebUntis/api/public/timetable/weekly/data?elementType=5&elementId={student_id}&date={date_str}" if student_id else None,
        f"/WebUntis/api/public/timetable/weekly/data?elementType=5&date={date_str}&formatId=1",
        f"/WebUntis/api/timetable/weekly/data?elementType=5&date={date_str}",
    ]

    notes = []
    for url_path in urls_to_try:
        if not url_path:
            continue
        url = f"https://{WEBUNTIS_SERVER}{url_path}"
        resp = session.get(url, headers=headers)
        print(f"\nTimeplan URL: {url_path[:80]}")
        print(f"Status: {resp.status_code}")
        print(f"Respons: {resp.text[:600]}")

        if resp.status_code != 200 or not resp.text.strip().startswith("{"):
            continue

        data = resp.json()
        result = (data.get("data", {}).get("result", {}).get("data", {})
                  or data.get("data", {}))

        elements = result.get("elements", [])
        element_periods = result.get("elementPeriods", {})

        for key, periods in element_periods.items():
            for period in periods:
                lstext = period.get("lstext", "").strip()
                if not lstext:
                    continue

                subject = "Ukjent fag"
                for el in period.get("elements", []):
                    if el.get("type") == 3:
                        for elem in elements:
                            if elem.get("type") == 3 and elem.get("id") == el.get("id"):
                                subject = elem.get("name", "Ukjent fag")
                                break

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
                print(f"  ✅ Notat: {subject} - {lstext[:60]}")

        if notes:
            return notes

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

    student_id = get_student_id(session)
    notes = get_timetable_with_notes(session, student_id)

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
