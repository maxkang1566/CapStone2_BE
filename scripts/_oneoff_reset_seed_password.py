"""[일회성] test@example.com 비번을 'test1234'로 강제 갱신."""
import os
import sys

from dotenv import load_dotenv
import psycopg2

# 프로젝트 루트 import 가능하도록
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.core.security import hash_password  # noqa: E402

load_dotenv()
conn = psycopg2.connect(os.environ["DATABASE_URL"])
conn.autocommit = False
cur = conn.cursor()

cur.execute(
    "SELECT id, email, nickname, created_at FROM users WHERE email=%s",
    ("test@example.com",),
)
u = cur.fetchone()
print("기존 user:", u)

if not u:
    print("계정 없음 — 종료")
    conn.close()
    sys.exit(1)

new_hash = hash_password("test1234")
cur.execute(
    "UPDATE users SET password=%s WHERE email=%s",
    (new_hash, "test@example.com"),
)
print(f"updated rows: {cur.rowcount}")

cur.execute(
    """
    SELECT s.id, s.title, sm.role
    FROM storages s
    JOIN storage_members sm ON sm.storage_id = s.id
    JOIN users u ON u.id = sm.user_id
    WHERE u.email = 'test@example.com'
    ORDER BY s.id
    """
)
storages = cur.fetchall()
print("storage 목록:", storages)

conn.commit()
print("COMMIT 완료")
conn.close()
