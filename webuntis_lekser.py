"""
WebUntis Lekse-varsler til iPhone via Pushover
Bruker webuntis-biblioteket for enkel tilkobling
"""

import requests
import json
import os
from datetime import datetime, timedelta
import hashlib

try:
    import webuntis
    import webuntis.session
except ImportError:
    print("Installer: pip install webuntis")
    raise

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


def main():
    print(f"🕐 Kjører sjekk: {datetime.now().strftime('%d.%m.%Y %H:%M')}")

    if not all([WEBUNTIS_SERVER, WEBUNTIS_SCHOOL, WEBUNTIS_USERNAME,
                WEBUNTIS_PASSWORD, PUSHOVER_USER_KEY, PUSHOVER_APP_TOKEN]):
        raise Exception("Mangler miljøvariabler! Sjekk GitHub Secrets.")

    seen_homework = load_seen_homework()

    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)

    with webuntis.session.Session(
        server=WEBUNTIS_SERVER,
        school=WEBUNTIS_SCHOOL,
        username=WEBUNTIS_USERNAME,
        password=WEBUNTIS_PASSWORD,
        useragent="LekseVarsler"
    ).login() as s:

        print("✅ Logget inn på WebUntis")

        timetable = s.timetable(start=monday, end=sunday)

        homeworks = []
        for lesson in timetable:
            if hasattr(lesson, 'lstext') and lesson.lstext:
                subject = lesson.subjects[0].name if lesson.subjects else "Ukjent fag"
                date_str = lesson.start.strftime("%d.%m.%Y")
                text = lesson.lstext

                homeworks.append({
                    "subject": subject,
                    "text": text,
                    "date": date_str,
                })

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
        elif new_homeworks:
            lines = []
            for hw in sorted(new_homeworks, key=lambda x: x["date"]):
                lines.append(f"📚 {hw['subject']} ({hw['date']})")
                if hw["text"]:
                    lines.append(f"   {hw['text']}")
            send_pushover(f"📚 {len(new_homeworks)} ny(e) lekse(r)!", "\n".join(lines))
        else:
            print("Ingen nye lekser siden siste sjekk.")

        save_seen_homework(seen_homework)


if __name__ == "__main__":
    main()
