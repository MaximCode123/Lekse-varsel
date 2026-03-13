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
        raise Exception("Innlogging feilet! Sjekk brukernavn/passord.")

    print("✅ Logget inn")


def get_user_info(session):
    """Hent info om innlogget bruker (person-ID, klasse-ID osv)"""
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }

    # Hent brukerinfo
    url = f"https://{WEBUNTIS_SERVER}/WebUntis/api/rest/view/v1/app/data"
    resp = session.get(url, headers=headers)
    print(f"App data status: {resp.status_code}")
    print(f"App data respons: {resp.text[:500]}")

    if resp.status_code == 200 and resp.text.strip().startswith("{"):
        return resp.json()
    return None


def get_homework(session):
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    start = monday.strftime("%Y-%m-%d")
    end = sunday.strftime("%Y-%m-%d")

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://{WEBUNTIS_SERVER}/WebUntis/?school={WEBUNTIS_SCHOOL}"
    }

    # Prøv REST v1 API
    endpoints = [
        f"/WebUntis/api/rest/view/v1/homeworks?startDate={start}&endDate={end}",
        f"/WebUntis/api/rest/view/v1/students/me/homeworks?startDate={start}&endDate={end}",
        f"/WebUntis/api/homeworks?startDate={start}&endDate={end}",
        f"/WebUntis/api/classreg/homework/lessons?startDate={start}&endDate={end}",
    ]

    for endpoint in endpoints:
        url = f"https://{WEBUNTIS_SERVER}{endpoint}"
        resp = session.get(url, headers=headers)
        print(f"Prøver {endpoint.split('?')[0]}: status {resp.status_code}")
        print(f"  Respons: {resp.text[:300]}")

        if resp.status_code == 200 and resp.text.strip().startswith("{"):
            data = resp.json()
            homeworks = []

            # Prøv ulike datastrukturer
            hw_list = (data.get("data", {}).get("homeworks")
                      or data.get("homeworks")
                      or data.get("data") if isinstance(data.get("data"), list) else None
                      or [])

            lessons = {l["id"]: l.get("subject", "Ukjent fag")
                      for l in (data.get("data", {}).get("lessons", [])
                                or data.get("lessons", []))}

            for hw in hw_list:
                lesson_id = hw.get("lessonId")
                subject = lessons.get(lesson_id, hw.get("subject", hw.get("subjectName", "Ukjent fag")))
                date_raw = str(hw.get("dueDate", hw.get("date", hw.get("endDate", ""))))
                try:
                    fmt = "%Y-%m-%d" if "-" in date_raw else "%Y%m%d"
                    due_date = datetime.strptime(date_raw, fmt).strftime("%d.%m.%Y")
                except Exception:
                    due_date = date_raw

                homeworks.append({
                    "subject": subject,
                    "text": hw.get("text", hw.get("remark", hw.get("description", ""))).strip(),
                    "date": due_date,
                    "raw_date": date_raw,
                })

            print(f"  Fant {len(homeworks)} lekser via {endpoint.split('?')[0]}")
            return homeworks

    return []


def main():
    print(f"🕐 Kjører sjekk: {datetime.now().strftime('%d.%m.%Y %H:%M')}")

    if not all([WEBUNTIS_SERVER, WEBUNTIS_SCHOOL, WEBUNTIS_USERNAME,
                WEBUNTIS_PASSWORD, PUSHOVER_USER_KEY, PUSHOVER_APP_TOKEN]):
        raise Exception("Mangler miljøvariabler! Sjekk GitHub Secrets.")

    seen_homework = load_seen_homework()
    today = datetime.now()

    session = requests.Session()
    login(session)
    
    # Hent brukerinfo for debugging
    get_user_info(session)
    
    homeworks = get_homework(session)

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
            print("Ingen lekser – ingen varsling.")
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
