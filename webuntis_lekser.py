"""
WebUntis Lekse-varsler til iPhone via Pushover
Henter "Notater for elever" fra timeplanen via JSON-RPC
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


def rpc(session, method, params):
    """Kall WebUntis JSON-RPC API"""
    url = f"https://{WEBUNTIS_SERVER}/WebUntis/jsonrpc.do"
    payload = {
        "id": method,
        "method": method,
        "params": params,
        "jsonrpc": "2.0"
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Referer": f"https://{WEBUNTIS_SERVER}/WebUntis/?school={WEBUNTIS_SCHOOL}"
    }
    resp = session.post(url, json=payload, headers=headers,
                        params={"school": WEBUNTIS_SCHOOL})
    print(f"RPC {method}: {resp.status_code} - {resp.text[:200]}")
    if resp.status_code == 200:
        return resp.json().get("result")
    return None


def get_notes_from_timetable(session):
    """Hent timeplanen og finn alle timer med notater for elever"""
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)

    start = int(monday.strftime("%Y%m%d"))
    end = int(sunday.strftime("%Y%m%d"))

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://{WEBUNTIS_SERVER}/WebUntis/?school={WEBUNTIS_SCHOOL}"
    }

    # Hent min timeplan via REST
    url = f"https://{WEBUNTIS_SERVER}/WebUntis/api/public/timetable/weekly/data"
    params = {"elementType": 5, "date": monday.strftime("%Y-%m-%d")}
    resp = session.get(url, params=params, headers=headers)
    print(f"Timetable status: {resp.status_code}")
    print(f"Timetable respons: {resp.text[:500]}")

    notes = []
    if resp.status_code == 200 and resp.text.strip().startswith("{"):
        data = resp.json()
        elements = data.get("data", {}).get("result", {}).get("data", {}).get("elements", [])
        lesson_data = data.get("data", {}).get("result", {}).get("data", {}).get("elementPeriods", {})

        for key, periods in lesson_data.items():
            for period in periods:
                lstext = period.get("lstext", "").strip()
                if not lstext:
                    continue

                # Finn fagnavn
                subject = "Ukjent fag"
                for el in period.get("elements", []):
                    if el.get("type") == 3:  # type 3 = fag
                        for elem in elements:
                            if elem.get("type") == 3 and elem.get("id") == el.get("id"):
                                subject = elem.get("name", elem.get("longName", "Ukjent fag"))
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
                print(f"  Fant notat: {subject} - {lstext[:60]}")

    return notes


def main():
    print(f"🕐 Kjører sjekk: {datetime.now().strftime('%d.%m.%Y %H:%M')}")

    if not all([WEBUNTIS_SERVER, WEBUNTIS_SCHOOL, WEBUNTIS_USERNAME,
                WEBUNTIS_PASSWORD, PUSHOVER_USER_KEY, PUSHOVER_APP_TOKEN]):
        raise Exception("Mangler miljøvariabler! Sjekk GitHub Secrets.")

    seen = load_seen_homework()
    today = datetime.now()

    session = requests.Session()
    login(session)
    notes = get_notes_from_timetable(session)

    print(f"📋 Fant {len(notes)} notater denne uken")

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
