import sqlite3
import random
import hashlib
import secrets
import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import anthropic

# Claude API mijozi (ANTHROPIC_API_KEY muhit o'zgaruvchisidan avtomatik olinadi)
try:
    claude_client = anthropic.Anthropic()
except Exception:
    claude_client = None

# Har bir mentor uchun "shaxsiyat" (system prompt)
MENTOR_PROFILES = {
    0: {
        "name": "Alisher Navoiy",
        "system": "Sen Alisher Navoiysan — 15-asr o'zbek shoiri va mutafakkiri. O'quvchi bilan iliq, dono, she'riy uslubda, lekin tushunarli o'zbek tilida gaplash. Javoblaring qisqa (2-4 jumla), hikmatli, va imkon qadar o'quvchining savoliga aniq javob ber. O'zingni 21-asr haqida gapirma, lekin zamonaviy savollarga ham donolik bilan javob berishga harakat qil."
    },
    1: {
        "name": "Amir Temur",
        "system": "Sen Amir Temursan — buyuk sarkarda va davlat arbobi. O'quvchi bilan qat'iy, ilhomlantiruvchi, strategik fikrlovchi ohangda gaplash. Javoblaring qisqa (2-4 jumla), kuch va intizom haqida bo'lsin, lekin o'quvchining savoliga ham aniq javob ber."
    },
    2: {
        "name": "Mirzo Ulug'bek",
        "system": "Sen Mirzo Ulug'beksan — olim, astronom va hukmdor. O'quvchi bilan ilmga chanqoq, mehribon ustoz ohangida gaplash, ayniqsa matematika va astronomiyaga oid savollarga chuqur va aniq javob ber. Javoblaring qisqa (2-4 jumla) bo'lsin."
    },
    3: {
        "name": "Al-Xorazmiy",
        "system": "Sen Al-Xorazmiysan — algebra va algoritmlar otasi. O'quvchi bilan mantiqiy, bosqichma-bosqich tushuntiruvchi ohangda gaplash, ayniqsa matematika savollariga aniq va tushunarli javob ber. Javoblaring qisqa (2-4 jumla) bo'lsin."
    },
    4: {
        "name": "Ibn Sino",
        "system": "Sen Ibn Sinosan — buyuk tabib va faylasuf. O'quvchi bilan mehribon, donishmand ohangda gaplash, sog'liq, falsafa va hayot haqidagi savollarga chuqur javob ber. Javoblaring qisqa (2-4 jumla) bo'lsin."
    }
}

# Oddiy parol xeshlash (loyiha darajasida yetarli)
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

# Faol sessiyalarni xotirada saqlash: {token: student_id}
SESSIONS = {}

app = FastAPI(title="AI Maktabim Global API")

# Global xavfsizlik sozlamalari (Ilova har qanday joydan ma'lumot olishi uchun)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- DATABASE ARXITEKTURASI ---
def init_db():
    conn = sqlite3.connect("global_school.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password_hash TEXT,
            name TEXT,
            student_class TEXT,
            school_name TEXT DEFAULT 'Nomsiz maktab',
            xp_points INTEGER,
            streak INTEGER,
            today_change TEXT
        )
    """)
    # Eski bazada username/password ustunlari bo'lmasa, qo'shib qo'yamiz (migratsiya)
    cursor.execute("PRAGMA table_info(students)")
    cols = [c[1] for c in cursor.fetchall()]
    if "username" not in cols:
        cursor.execute("ALTER TABLE students ADD COLUMN username TEXT")
    if "password_hash" not in cols:
        cursor.execute("ALTER TABLE students ADD COLUMN password_hash TEXT")
    if "school_name" not in cols:
        cursor.execute("ALTER TABLE students ADD COLUMN school_name TEXT DEFAULT 'Nomsiz maktab'")
    if "attendance" not in cols:
        cursor.execute("ALTER TABLE students ADD COLUMN attendance INTEGER DEFAULT 90")
    if "avg_score" not in cols:
        cursor.execute("ALTER TABLE students ADD COLUMN avg_score REAL DEFAULT 4.5")

    # Agar baza bo'sh bo'lsa, test uchun professional va dinamik o'quvchilarni qo'shamiz
    cursor.execute("SELECT COUNT(*) FROM students")
    if cursor.fetchone()[0] == 0:
        test_students = [
            ("asilbek", hash_password("12345"), "Asilbek Yusupov", "10-A", "1-maktab", 1284, 12, "+45 bugun"),
            ("amirkhan", hash_password("12345"), "Amirkhan Saparbaev", "8-A", "1-maktab", 1150, 9, "+60 bugun"),
            ("malika", hash_password("12345"), "Malika Karimova", "10-A", "1-maktab", 980, 5, "+15 bugun"),
            ("dilshodbek", hash_password("12345"), "Dilshodbek Olimov", "9-B", "2-maktab", 890, 8, "+30 bugun"),
            ("jasur", hash_password("12345"), "Jasur Axmedov", "11-A", "2-maktab", 750, 4, "+10 bugun")
        ]
        cursor.executemany("INSERT INTO students (username, password_hash, name, student_class, school_name, xp_points, streak, today_change) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", test_students)
        conn.commit()
    conn.close()

init_db()

# --- AUTENTIFIKATSIYA MODELLARI ---
class SignupRequest(BaseModel):
    username: str
    full_name: str
    password: str
    student_class: str = "10-A"
    school_name: str = "Nomsiz maktab"

class LoginRequest(BaseModel):
    username: str
    password: str

# --- MODEL (Ma'lumotlar formati) ---
class MessageRequest(BaseModel):
    student_id: int
    mentor_id: int
    message: str

# --- AUTENTIFIKATSIYA ENDPOINTLARI ---

@app.post("/api/signup")
def signup(req: SignupRequest):
    conn = sqlite3.connect("global_school.db")
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM students WHERE username = ?", (req.username,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Bu username band, boshqasini tanlang")

    cursor.execute(
        "INSERT INTO students (username, password_hash, name, student_class, school_name, xp_points, streak, today_change) VALUES (?, ?, ?, ?, ?, 0, 0, '+0 bugun')",
        (req.username, hash_password(req.password), req.full_name, req.student_class, req.school_name)
    )
    conn.commit()
    student_id = cursor.lastrowid
    conn.close()

    token = secrets.token_hex(16)
    SESSIONS[token] = student_id
    return {"token": token, "student_id": student_id, "name": req.full_name}

@app.post("/api/login")
def login(req: LoginRequest):
    conn = sqlite3.connect("global_school.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, password_hash FROM students WHERE username = ?", (req.username,))
    row = cursor.fetchone()
    conn.close()

    if not row or row[2] != hash_password(req.password):
        raise HTTPException(status_code=401, detail="Username yoki parol noto'g'ri")

    token = secrets.token_hex(16)
    SESSIONS[token] = row[0]
    return {"token": token, "student_id": row[0], "name": row[1]}

@app.get("/api/me/{token}")
def get_me(token: str):
    student_id = SESSIONS.get(token)
    if not student_id:
        raise HTTPException(status_code=401, detail="Sessiya tugagan, qayta kiring")
    return {"student_id": student_id}

# --- API ENDPOINTS (Bo'limlarni tiriltiruvchi ko'priklar) ---

@app.get("/api/dashboard/{student_id}")
def get_dashboard(student_id: int):
    conn = sqlite3.connect("global_school.db")
    cursor = conn.cursor()

    # 1. Shaxsiy profil statistikasi (Har bir odamda har xil)
    cursor.execute("SELECT name, student_class, xp_points, streak, today_change, school_name, attendance, avg_score FROM students WHERE id = ?", (student_id,))
    student = cursor.fetchone()

    if not student:
        conn.close()
        raise HTTPException(status_code=404, detail="O'quvchi topilmadi")

    school_name = student[5]
    xp = student[2]
    level = xp // 250 + 1          # har 250 XP = 1 daraja
    xp_into_level = xp % 250
    xp_to_next = 250 - xp_into_level

    # 2. Haftalik Global Reyting (faqat shu o'quvchining maktabi bo'yicha, TOP-5)
    cursor.execute(
        "SELECT id, name, student_class, xp_points, streak, today_change FROM students WHERE school_name = ? ORDER BY xp_points DESC",
        (school_name,)
    )
    all_students = cursor.fetchall()

    leaderboard = []
    for rank, row in enumerate(all_students, 1):
        leaderboard.append({
            "rank": rank, "id": row[0], "name": row[1], "class": row[2], "xp": row[3], "streak": row[4], "change": row[5]
        })

    class_leaderboard = [s for s in leaderboard if s["class"] == student[1]]
    rank_in_class = next((s["rank"] for s in class_leaderboard), None)
    # haqiqiy sinf-ichi tartib (umumiy ro'yxatdan emas, faqat shu sinf orasida)
    class_only = sorted(class_leaderboard, key=lambda s: -s["xp"])
    for i, s in enumerate(class_only, 1):
        if s["id"] == student_id:
            rank_in_class = i
            break

    # 3. Sinflararo reytingni hisoblash (Dinamik, real bazadan, shu maktab bo'yicha)
    cursor.execute(
        "SELECT student_class, SUM(xp_points) FROM students WHERE school_name = ? GROUP BY student_class ORDER BY SUM(xp_points) DESC",
        (school_name,)
    )
    class_scores = {row[0]: row[1] for row in cursor.fetchall()}

    # 4. Global statistika (faqat shu maktab bo'yicha real son)
    cursor.execute("SELECT COUNT(*), SUM(xp_points) FROM students WHERE school_name = ?", (school_name,))
    active_users, total_xp = cursor.fetchone()

    conn.close()

    return {
        "personal": {
            "name": student[0], "class": student[1], "xp": student[2], "streak": student[3], "change": student[4], "school": school_name,
            "attendance": student[6], "avg_score": student[7], "level": level, "xp_into_level": xp_into_level, "xp_to_next": xp_to_next, "rank_in_class": rank_in_class
        },
        "leaderboard": leaderboard,
        "classes": class_scores,
        "global_stats": {
            "active_users": active_users or 0, "today_xp": total_xp or 0, "tasks_done": (active_users or 0) * 5, "new_badges": (active_users or 0) * 2
        }
    }

@app.post("/api/mentor/chat")
def mentor_chat(req: MessageRequest):
    profile = MENTOR_PROFILES.get(req.mentor_id, MENTOR_PROFILES[0])
    mentor_name = profile["name"]

    reply = None
    error_note = None

    if claude_client is not None and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            response = claude_client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=300,
                system=profile["system"],
                messages=[{"role": "user", "content": req.message}]
            )
            reply = response.content[0].text
        except Exception as e:
            error_note = str(e)

    if reply is None:
        # API ishlamasa (kalit yo'q, balans yo'q va h.k.) — zaxira javob
        reply = "Hozircha sizga to'liq javob bera olmayapman (AI ulanishida muammo bor). Iltimos ANTHROPIC_API_KEY to'g'ri sozlanganini va balans borligini tekshiring."
        if error_note:
            print("Claude API xatosi:", error_note)

    # AI Mentor bilan gaplashgani uchun bazada o'quvchiga +20 XP qo'shish (Jonli o'zgarish!)
    conn = sqlite3.connect("global_school.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE students SET xp_points = xp_points + 20 WHERE id = ?", (req.student_id,))
    conn.commit()
    conn.close()

    return {"mentor_name": mentor_name, "reply": reply, "xp_earned": 20}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)