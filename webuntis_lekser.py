"""
WebUntis Lekse-varsler til iPhone via Pushover
Kjøres automatisk via GitHub Actions hver time
"""

import requests
import json
import os
from datetime import datetime, timedelta
import hashlib

# ─────────────────────────────────────────────
# INNSTILLINGER – fyll inn dine egne verdier
# (bruk GitHub Secrets, ikke skriv dem direkte her)
# ─────────────────────────────────────────────
WEBUNTIS_SERVER   = os.environ.get("WEBUNTIS_SERVER", "hepta.webuntis.com")  # f.eks. "moja.webuntis.com"
WEBUNTIS_SCHOOL   = os.environ.get("WEBUNTIS_SCHOOL", "")    # Skolenavn i WebUntis
WEBUNTIS_USERNAME = os.environ.get("WEBUNTIS_USERNAME", "")
WEBUNTIS_PASSWORD = os.environ.get("WEBUNTIS_PASSWORD", "")

PUSHOVER_USER_KEY = os.environ.get("PUSHOVER_USER_KEY", "")
PUSHOVER_APP_TOKEN = os.environ.get("PUSHOVER_APP_TOKEN", "")

# Fil for å huske hvilke lekser vi allerede har varslet om
SEEN_HOMEWORK_FILE = "seen_homework.json"


def load_seen_homework():
    """Last inn tidligere varslete lekser"""
    if os.path.exists(SEEN_HOMEWORK_FILE):
        with open(SEEN_HOMEWORK_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_seen_homework(seen: set):
    """Lagre varslete lekser"""
    with open(SEEN_HOMEWORK_FILE, "w") as f:
        json.dump(list(seen), f)


def homework_id(hw: dict) -> str:
    """Lag en unik ID for en lekse basert på innhold"""
    key = f"{hw.get('date')}-{hw.get('subject')}-{hw.get('text', '')[:50]}"
    return hashlib.md5(key.encode()).hexdigest()


def login_webuntis(session: requests.Session) -> int | None:
    """Logg inn i WebUntis og returner student-ID"""
    url = f"https://{WEBUNTIS_SERVER}/WebUntis/jsonrpc.do?school={WEBUNTIS_SCHOOL}"
    
    payload = {
        "id": "login",
        "method": "authenticate",
        "params": {
            "user": WEBUNTIS_USERNAME,
            "password": WEBUNTIS_PASSWORD,
            "client": "lekse-varsler"
        },
        "jsonrpc": "2.0"
    }
    
    resp = session.post(url, json=payload)
    resp.raise_for_status()
    data = resp.json()
    
    if "error" in data:
        raise Exception(f"WebUntis login feilet: {data['error']}")
    
    return data["result"]["personId"]


def logout_webuntis(session: requests.Session):
    """Logg ut fra WebUntis"""
    url = f"https://{WEBUNTIS_SERVER}/WebUntis/jsonrpc.do?school={WEBUNTIS_SCHOOL}"
    payload = {"id": "logout", "method": "logout", "params": {}, "jsonrpc": "2.0"}
    session.post(url, json=payload)


def get_homework_this_week(session: requests.Session, student_id: int) -> list:
    """Hent lekser for denne uken"""
    today = datetime.now()
    
    # Start på mandag denne uken
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    
    start_date = int(monday.strftime("%Y%m%d"))
    end_date = int(sunday.strftime("%Y%m%d"))
    
    url = f"https://{WEBUNTIS_SERVER}/WebUntis/api/homeworks/lessons"
    params = {
        "startDate": start_date,
        "endDate": end_date,
    }
    
    resp = session.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()
    
    homeworks = []
    
    # Parser WebUntis sitt format
    lessons = data.get("data", {}).get("lessons", [])
    hw_list = data.get("data", {}).get("homeworks", [])
    
    # Lag en mapping fra lessonId til fagnavn
    lesson_map = {l["id"]: l.get("subject", "Ukjent fag") for l in lessons}
    
    for hw in hw_list:
        lesson_id = hw.get("lessonId")
        subject = lesson_map.get(lesson_id, "Ukjent fag")
        date_raw = str(hw.get("dueDate", ""))
        
        try:
            due_date = datetime.strptime(date_raw, "%Y%m%d").strftime("%d.%m.%Y")
        except Exception:
            due_date = date_raw
        
        homeworks.append({
            "subject": subject,
            "text": hw.get("text", "").strip(),
            "date": due_date,
            "raw_date": date_raw,
        })
    
    return homeworks


def send_pushover(title: str, message: str, priority: int = 0):
    """Send varsling til iPhone via Pushover"""
    resp = requests.post("https://api.pushover.net/1/messages.json", data={
        "token": PUSHOVER_APP_TOKEN,
        "user": PUSHOVER_USER_KEY,
        "title": title,
        "message": message,
        "priority": priority,
    })
    resp.raise_for_status()
    print(f"✅ Pushover-varsel sendt: {title}")


def format_homework_message(homeworks: list) -> str:
    """Formater leksene til en lesbar melding"""
    lines = []
    for hw in sorted(homeworks, key=lambda x: x.get("raw_date", "")):
        lines.append(f"📚 {hw['subject']} (innlevering {hw['date']})")
        if hw["text"]:
            lines.append(f"   {hw['text']}")
    return "\n".join(lines)


def main():
    print(f"🕐 Kjører sjekk: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    
    if not all([WEBUNTIS_SCHOOL, WEBUNTIS_USERNAME, WEBUNTIS_PASSWORD,
                PUSHOVER_USER_KEY, PUSHOVER_APP_TOKEN]):
        raise Exception("Mangler miljøvariabler! Sjekk GitHub Secrets.")
    
    seen_homework = load_seen_homework()
    
    session = requests.Session()
    
    try:
        # Logg inn
        student_id = login_webuntis(session)
        print(f"✅ Logget inn (student ID: {student_id})")
        
        # Hent lekser
        homeworks = get_homework_this_week(session, student_id)
        print(f"📋 Fant {len(homeworks)} lekser denne uken")
        
        # Finn nye lekser vi ikke har varslet om
        new_homeworks = []
        for hw in homeworks:
            hid = homework_id(hw)
            if hid not in seen_homework:
                new_homeworks.append(hw)
                seen_homework.add(hid)
        
        if not homeworks:
            # Ingen lekser i det hele tatt – send varsel én gang per uke (mandag)
            if datetime.now().weekday() == 0:
                send_pushover(
                    "📚 Lekser denne uken",
                    "Du har ingen lekser denne uken! 🎉"
                )
        elif new_homeworks:
            # Nye lekser funnet!
            msg = format_homework_message(new_homeworks)
            title = f"📚 {len(new_homeworks)} ny(e) lekse(r)!"
            send_pushover(title, msg)
        else:
            print("Ingen nye lekser siden siste sjekk.")
        
        # Lagre hva vi har varslet om
        save_seen_homework(seen_homework)
        
    finally:
        logout_webuntis(session)
        print("✅ Logget ut fra WebUntis")


if __name__ == "__main__":
    main()
