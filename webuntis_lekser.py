"""
WebUntis Lekse-varsler til iPhone via Pushover
Bruker WebUntis API direkte med requests
"""

import requests
import json
import os
from datetime import datetime, timedelta
import hashlib

WEBUNTIS_SERVER   = os.environ.get("WEBUNTIS_SERVER", "")
WEBUNTIS_SCHOOL   = os.environ.get("WEBUNTIS_SCHOOL", "")
WEBUNTIS_USERNAME = os.environ.get("WEBUNTIS_USERNAME", "")
WEBUNTIS_PASSWORD = os.environ.get("WEBUNTIS_PASSWORD", "")
PUSHOVER_USER_KEY  = os.environ.get("PUSHOVER_USER_KEY", "")
PUSHOVER_APP_TOKEN = os.environ.get("PUSHOVER_APP_TOKEN", "")

SEEN_HOMEWORK_FILE = "seen_homework.json"
BASE_URL = f"https://{{server}}/WebUntis"


def load_seen_homework():
    if os.path.exists(SEEN_HOMEWORK_FILE):
        with open(SEEN_HOMEWORK_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_seen_homework(seen: set):
    with open(SEEN_HOMEWORK_FILE, "w") as f:
        json.dump(list(seen), f)


def homework_id(subject, text, date) -> str:
    key = f"{date}-{subject}-{str(text)[:50]}"
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


def login(session, server, school, username, password):
    """Logg inn via cookies (nyere WebUntis-metode)"""
    url = f"https://{server}/WebUntis/j_spring_security_check"
    data = {
        "school": school,
        "j_username": username,
        "j_password": password,
        "token": ""
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0"
    }
    resp = session.post(url, data=data, headers=headers, allow_redirects=True)
    
    # Sjekk om vi er logget inn
    if "invalidLogin" in resp.url or "error" in resp.url.lower():
        raise Exception(f"Innlogging feilet! Sjekk brukernavn/passord. URL: {resp.url}")
    
    print(f"✅ Logget inn (status: {resp.status_code})")
    return session


def get_homework(session, server, school):
    """Hent lekser for denne uken"""
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)

    start = monday.strftime("%Y-%m-%d")
    end = sunday.strftime("%Y-%m-%d")

    url = f"https://{server}/WebUntis/api/homeworks/lessons"
    params = {"startDate": start, "endDate": end}
    headers = {"User-Agent": "Mozilla/5.0"}

    resp = session.get(url, params=params, headers=headers)
    print(f"Homework API status: {resp.status_code}")
    
    if resp.status_code != 200:
        print(f"Respons: {resp.text[:300]}")
        return []

    data = resp.json()
    homeworks = []

    hw_data = data.get("data", {})
    lessons = {l["id"]: l.get("subject", "Ukjent fag") for l in hw_data.get("lessons", [])}

    for hw in hw_data.get("homeworks", []):
        lesson_id = hw.get("lessonId")
        subject = lessons.get(lesson_id, "Ukjent fag")
        date_raw = str(hw.get("dueDate", ""))
        try:
            due_date = datetime.strptime(date_raw, "%Y-%m-%d").strftime("%d.%m.%Y")
        except Exception:
            due_date = date_raw

        homeworks.append({
            "subject": subject,
            "text": hw.get("text", "").strip(),
            "date": due_date,
            "raw_date": date_raw,
        })

    return homeworks


def main():
    print(f"🕐 Kjører sjekk: {datetime.now().strftime('%d.%m.%Y %H:%M')}")

    if not all([WEBUNTIS_SERVER, WEBUNTIS_SCHOOL, WEBUNTIS_USERNAME,
                WEBUNTIS_PASSWORD, PUSHOVER_USER_KEY, PUSHOVER_APP_TOKEN]):
        raise Exception("Mangler miljøvariabler! Sjekk GitHub Secrets.")

    seen_homework = load_seen_homework()
    today = datetime.now()

    session = requests.Session()

    login(session, WEBUNTIS_SERVER, WEBUNTIS_SCHOOL, WEBUNTIS_USERNAME, WEBUNTIS_PASSWORD)
    homeworks = get_homework(session, WEBUNTIS_SERVER, WEBUNTIS_SCHOOL)

    print(f"📋 Fant {len(homeworks)} lekser denne uken")

    new_homeworks = []
    for hw in homeworks:
        hid = homework_id(hw["subject"], hw["text"], hw["date"])
        if hid not in seen_homework:
            new_homeworks.append(hw)
            seen_homework.add(hid)

    if not homeworks:
        if today.weekday() == 0:
            send_pushover("📚 Lekser denne uken", "Ingen lekser denne uken! 🎉")
        else:
            print("Ingen lekser, men ikke mandag – ingen varsling.")
    elif new_homeworks:
        lines = []
        for hw in sorted(new_homeworks, key=lambda x: x.get("raw_date", "")):
            lines.append(f"📚 {hw['subject']} ({hw['date']})")
            if hw["text"]:
                lines.append(f"   {hw['text']}")
        send_pushover(f"📚 {len(new_homeworks)} ny(e) lekse(r)!", "\n".join(lines))
    else:
        print("Ingen nye lekser siden siste sjekk.")

    save_seen_homework(seen_homework)
    print("✅ Ferdig!")


if __name__ == "__main__":
    main()
