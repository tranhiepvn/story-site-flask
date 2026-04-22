"""
Ứng dụng Flask đơn giản để đăng và đọc truyện.

Mục đích của ứng dụng này là cung cấp một nền tảng nhỏ cho phép bạn
đăng tải các truyện chữ và hiển thị chúng cho người đọc. Ứng dụng sử
dụng cơ sở dữ liệu SQLite để lưu trữ thông tin truyện, đồng thời
tận dụng Flask và Flask‑SQLAlchemy để quản lý dữ liệu và hiển thị
giao diện web.

Chức năng chính:
  * Danh sách truyện: hiển thị tiêu đề, tác giả và ngày tạo của
    từng truyện với đường dẫn chi tiết.
  * Trang chi tiết: hiển thị toàn bộ nội dung của một truyện.
  * Form đăng truyện: cho phép bạn (admin) nhập tiêu đề, tác giả
    và nội dung truyện rồi lưu vào cơ sở dữ liệu.

Để chạy ứng dụng:
  1. Cài đặt các gói phụ thuộc: Flask và Flask‑SQLAlchemy.
  2. Khởi động máy chủ với lệnh `flask --app app run --debug`.
  3. Mở trình duyệt tới http://127.0.0.1:5000 để xem trang.
"""
from pathlib import Path
import time
from threading import Thread
import asyncio
import edge_tts
import os
import threading
import re
import uuid
from datetime import datetime
from datetime import datetime, date, timedelta
import json
import io
import smtplib
from email.message import EmailMessage

from dotenv import load_dotenv   # thêm dòng này

load_dotenv()                     # thêm dòng này

from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError

# ====================== HÀM DỌN DẸP + TÁCH PHẦN (theo script anh đưa) ======================
def clean_line(line: str) -> str:
    line = line.rstrip('\n')
    line = re.sub(r'^#+\s*', '', line)
    
    if line.startswith('*') and line.endswith('*') and len(line) > 1 and line[1] != '*' and line[-2] != '*':
        content = line[1:-1].strip()
        line = f'"{content}"'
    else:
        line = line.strip('*')
    
    if line.startswith('- '):
        line = f'"{line[2:].strip()}"'
    if line.startswith('– '):
        line = f'"{line[2:].strip()}"'
    
    line = line.replace("’", "'").replace("‘", "'")
    line = line.replace("…", "...")
    line = line.replace("”", '"').replace("“", '"')
    line = line.replace("–", "-")
    
    line = line.replace("cái cặc", "con cặc").replace("Cái cặc", "Con cặc")
    line = line.replace("quần lót", "xì-líp").replace("Quần lót", "Xì-líp")
    line = line.replace("địt", "đụ").replace("Địt", "Đụ")
    
    line = line.replace('"*', '"')
    line = line.replace('""', '"')
    
    return line


def split_and_clean_content(content: str) -> list[tuple[int, str]]:
    lines = content.splitlines()
    sections = []
    current_content = []
    part_num = None

    for raw_line in lines:
        cleaned = clean_line(raw_line)
        match = re.match(r'^\s*Phần\s+(\d+)\s*:', cleaned, re.IGNORECASE)
        if match:
            if current_content and part_num is not None:
                sections.append((part_num, '\n'.join(current_content)))
            part_num = int(match.group(1))
            current_content = [cleaned]
        else:
            if part_num is not None:
                current_content.append(cleaned)

    if current_content and part_num is not None:
        sections.append((part_num, '\n'.join(current_content)))

    if not sections and content.strip():
        full_clean = '\n'.join(clean_line(line) for line in content.splitlines())
        sections = [(1, full_clean)]

    return sections
# ==========================================================================================

# Helper để chạy edge_tts một cách an toàn trong thread, tạo event loop riêng
def run_async_save(communicate, file_path: str) -> None:
    """Chạy communicate.save() với event loop riêng cho thread hiện tại."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(communicate.save(file_path))
    finally:
        loop.close()

# Tạo ứng dụng Flask
app = Flask(__name__)

# Thiết lập secret key để sử dụng session. Ứng dụng cần khóa bí mật cho
# cookie session. Bạn có thể đặt biến môi trường SECRET_KEY để thay đổi
# giá trị này khi triển khai. Nếu không đặt, khóa mặc định sẽ được sử dụng.
app.secret_key = os.environ.get("SECRET_KEY", "a-very-secret-key")

# Thiết lập chuỗi kết nối cơ sở dữ liệu.  
# Ứng dụng ưu tiên sử dụng biến môi trường DATABASE_URL để kết nối tới PostgreSQL
# (hoặc các hệ quản trị cơ sở dữ liệu khác). Nếu biến này không tồn tại, ứng dụng
# sẽ mặc định sử dụng SQLite trong thư mục ``data`` bên ngoài ``src`` để tiện
# phát triển và thử nghiệm trên máy local.
db_url = os.environ.get("DATABASE_URL")
if db_url:
    # Khi triển khai trên Render với PostgreSQL, bạn nên đặt DATABASE_URL trong phần
    # Environment Variables của dịch vụ. Render cung cấp cả Internal Database URL
    # và External Database URL. Sử dụng Internal URL cho kết nối trong cùng
    # Render để tối ưu hiệu suất và bảo mật.
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
else:
    # Cấu hình đường dẫn tới file cơ sở dữ liệu SQLite
    # Cơ sở dữ liệu được đặt trong thư mục ``data`` nằm cùng cấp với thư mục mã nguồn để tránh bị
    # ghi đè khi cập nhật mã. Nếu thư mục không tồn tại, tự động tạo. Khi triển khai, bạn chỉ
    # cần thay thế mã trong thư mục ``src`` mà không cần xoá thư mục ``data``.
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, os.pardir, "data"))
    os.makedirs(DATA_DIR, exist_ok=True)
    db_path = os.path.join(DATA_DIR, "stories.db")
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Khởi tạo đối tượng SQLAlchemy
db = SQLAlchemy(app)

# Helper to sort categories into special order
def get_category_groups() -> tuple[list["Category"], list["Category"], list["Category"]]:
    """
    Return categories divided into three groups with a specific ordering:

    - Group 1 (special): categories named "Truyện Chỉ Có 1 Chương" or "Phim Chỉ Có 1 Tập"
      and categories named "Truyện Có Nhiều Chương" or "Phim Có Nhiều Tập".  These appear
      first in the sidebar.
    - Group 2 (uppercase): categories whose name starts with an uppercase letter (A–Z)
      excluding those already placed in group 1.
    - Group 3 (lowercase/other): all remaining categories, sorted by name (case-insensitive).

    Each group is sorted alphabetically (case-insensitive) except for group 1 which preserves
    the order defined by ``first_candidates_1`` and ``first_candidates_2``.
    """
    cats = Category.query.all()
    # define names for special groups. preserve order within these lists.
    # first_candidates_1 = ["Truyện Chỉ Có 1 Chương", "Phim Chỉ Có 1 Tập"]
    # first_candidates_2 = ["Truyện Có Nhiều Chương", "Phim Có Nhiều Tập"]
    first_candidates_1 = ["Phim một tập", "Truyện một phần"]
    first_candidates_2 = ["Truyện nhiều tập", "Truyện nhiều phần"]
    # prepare containers
    group1: list[Category] = []
    group2: list[Category] = []
    group3: list[Category] = []
    # assign categories to groups
    for c in cats:
        if c.name in first_candidates_1:
            group1.append(c)
        elif c.name in first_candidates_2:
            group1.append(c)
        else:
            # categorize by first character
            first_char = c.name[0] if c.name else ''
            if first_char.isalpha() and first_char.isupper():
                group2.append(c)
            else:
                group3.append(c)
    # sort group2 and group3 by name case-insensitive
    group2 = sorted(group2, key=lambda c: c.name.lower())
    group3 = sorted(group3, key=lambda c: c.name.lower())
    return group1, group2, group3

def get_user_session_id():
    if 'reader_session_id' not in session:
        session['reader_session_id'] = str(uuid.uuid4())
    return session['reader_session_id']

def is_mobile():
    """Xác định request có đến từ mobile hay không dựa vào User-Agent."""
    user_agent = request.headers.get('User-Agent', '').lower()
    return 'mobile' in user_agent or 'android' in user_agent or 'iphone' in user_agent or 'ipad' in user_agent

# Hàm mới: Trả về danh sách tất cả categories đã sắp xếp theo tên (case-insensitive)
def get_sorted_categories() -> list["Category"]:
    """
    Trả về danh sách tất cả các thể loại (Category), sắp xếp theo tên (case-insensitive).
    """
    return sorted(Category.query.all(), key=lambda c: c.name.lower())

# Cung cấp đối tượng datetime cho tất cả template Jinja.
# Điều này cho phép dùng {{ datetime.utcnow().year }} trong layout.html
# mà không gặp lỗi UndefinedError.
# Define a helper to convert Google Drive sharing links into embeddable preview URLs.
def drive_embed(url: str) -> str:
    """
    Convert a Google Drive sharing link into an embeddable preview URL.

    If the provided URL matches the pattern of a Google Drive file link
    (either ``https://drive.google.com/file/d/<id>/...`` or contains ``id=<id>``),
    this function returns the corresponding preview URL (``.../preview``).
    If the URL does not match, it is returned unchanged.

    Args:
        url: The original Google Drive sharing URL.
    Returns:
        A URL pointing to the embeddable preview of the file, or an
        empty string if no pattern is recognised.
    """
    if not url:
        return ""
    # Match /file/d/<id>/ path
    m = re.search(r"/file/d/([A-Za-z0-9_-]+)", url)
    if not m:
        # Fallback: match id=... parameter
        m = re.search(r"[?&]id=([A-Za-z0-9_-]+)", url)
    if m:
        fid = m.group(1)
        return f"https://drive.google.com/file/d/{fid}/preview"
    return ""


# Provide utilities (datetime, range, drive_embed) to all Jinja templates.
@app.context_processor
def inject_utilities():
    """Inject common utilities into Jinja templates.

    Returns a dictionary mapping names to functions/objects that should be available
    in the Jinja environment, including:

      * ``datetime``: allows access to current time, e.g., ``datetime.utcnow()``.
      * ``range``: built-in function for iterating a fixed number of times.
      * ``drive_embed``: convert a Google Drive link to an embeddable preview URL.
    """
    # Additionally inject category groups so templates can access them without passing explicitly.
    cat1, cat2, cat3 = get_category_groups()
    # Provide a combined list of all categories as 'categories' for templates that still reference it
    return {
        "datetime": datetime,
        "range": range,
        "drive_embed": drive_embed,
        "categories": cat1 + cat2 + cat3,
        "categories_group1": cat1,
        "categories_group2": cat2,
        "categories_group3": cat3,
    }

class Follow(db.Model):
    """Mô hình lưu email theo dõi truyện."""
    __tablename__ = "follows"
    id = db.Column(db.Integer, primary_key=True)
    story_id = db.Column(db.Integer, db.ForeignKey("stories.id"), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    story = db.relationship("Story", backref=db.backref("followers", lazy=True))
    
    __table_args__ = (db.UniqueConstraint('story_id', 'email', name='uq_follow_story_email'),)

class NewStorySubscription(db.Model):
    """Lưu email muốn nhận thông báo khi có truyện mới được đăng."""
    __tablename__ = "new_story_subscriptions"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Story(db.Model):
    """Mô hình dữ liệu cho truyện.

    Lưu thông tin cơ bản của truyện: tiêu đề, tác giả, loại truyện (ngắn/dài),
    thời điểm tạo, lượt xem và thể loại. Nội dung cụ thể từng phần được lưu
    riêng trong bảng `Part` để hỗ trợ truyện nhiều chương.
    """

    __tablename__ = "stories"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    author = db.Column(db.String(100), nullable=True)
    # loại truyện: 'short' (truyện ngắn) hoặc 'long' (truyện dài)
    story_type = db.Column(db.String(10), nullable=False, default="short")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # số lượt xem, dùng để hiển thị top truyện
    views = db.Column(db.Integer, default=0)

    # cờ ẩn truyện: nếu True thì truyện không hiển thị trên trang cho người đọc
    is_hidden = db.Column(db.Boolean, default=False)

    # cờ đánh dấu truyện đã hoàn thành hay chưa. Nếu True thì truyện đã hoàn thành
    # và không cần thêm chương mới. Khi truyện hoàn thành, giao diện sẽ hiển thị
    # nút "Chương cuối" thay cho "Chương sau" trên trang chi tiết và phần cuối
    # trong danh sách chương sẽ được gắn nhãn "Chương cuối".
    is_completed = db.Column(db.Boolean, default=False)

    # lưu tổng điểm đánh giá và số lượt đánh giá để tính trung bình
    rating_sum = db.Column(db.Integer, default=0)
    rating_count = db.Column(db.Integer, default=0)

    # khóa ngoại tới bảng thể loại (category). Đây là thể loại chính (có thể không
    # dùng nếu truyện thuộc nhiều thể loại). Khi sử dụng nhiều thể loại, cột này
    # có thể được đặt là None hoặc bằng ID của thể loại đầu tiên trong danh sách.
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True)

    # Quan hệ tới bảng Part để lấy danh sách các phần/chương
    parts = db.relationship(
        "Part", backref="story", lazy=True, order_by="Part.part_number"
    )

    # Quan hệ nhiều‑nhiều với Category thông qua bảng phụ story_categories.
    categories = db.relationship(
        "Category",
        secondary="story_categories",
        # Sử dụng backref khác tên để tránh xung đột với quan hệ một‑nhiều 'stories' trên Category
        backref=db.backref("stories_multi", lazy=True),
        lazy="subquery",
    )

    def __repr__(self) -> str:
        return f"<Story {self.id} {self.title}>"


def send_new_chapter_notification(story: Story, part_number: int, part_title: str, recipients: list[str]) -> bool:
    """Gửi email thông báo có chương mới cho danh sách email."""
    if not recipients:
        return False
    smtp_server = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_username = os.environ.get("SMTP_USERNAME")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    if not smtp_username or not smtp_password:
        return False
    from_name = os.environ.get("EMAIL_FROM_NAME", "Webdoctruyen Admin")
    from_addr = os.environ.get("EMAIL_FROM_ADDR", "admin@webdoctruyen.org")
    
    subject = f"[{story.title}] Có phần mới: {part_title}"
    story_url = url_for("story_detail", story_id=story.id, part=part_number, _external=True)
    body = f"""Xin chào,

Truyện "{story.title}" vừa được cập nhật phần mới.

{part_title}
Xem tại: {story_url}

Cảm ơn bạn đã theo dõi.

Trân trọng,
Webdoctruyen
"""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_addr}>"
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)
    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Lỗi gửi email: {e}")
        return False

def send_follow_confirmation(story: Story, email: str) -> bool:
    """Gửi email xác nhận khi người dùng đăng ký theo dõi truyện."""
    smtp_server = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_username = os.environ.get("SMTP_USERNAME")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    if not smtp_username or not smtp_password:
        return False
    from_name = os.environ.get("EMAIL_FROM_NAME", "Webdoctruyen Admin")
    from_addr = os.environ.get("EMAIL_FROM_ADDR", "admin@webdoctruyen.org")
    
    subject = f"Xác nhận theo dõi truyện: {story.title}"
    body = f"""Xin chào,

Bạn vừa đăng ký theo dõi truyện "{story.title}" trên Webdoctruyen.

Bạn sẽ nhận được email thông báo mỗi khi truyện có phần mới.

Để hủy theo dõi, truy cập trang truyện và sử dụng form "Hủy theo dõi".

Trân trọng,
Webdoctruyen
"""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_addr}>"
    msg["To"] = email
    msg.set_content(body)
    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Lỗi gửi email xác nhận follow: {e}")
        return False

def send_unfollow_confirmation(story: Story, email: str) -> bool:
    """Gửi email xác nhận khi người dùng hủy theo dõi truyện."""
    smtp_server = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_username = os.environ.get("SMTP_USERNAME")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    if not smtp_username or not smtp_password:
        return False
    from_name = os.environ.get("EMAIL_FROM_NAME", "Webdoctruyen Admin")
    from_addr = os.environ.get("EMAIL_FROM_ADDR", "admin@webdoctruyen.org")
    
    subject = f"Xác nhận hủy theo dõi truyện: {story.title}"
    body = f"""Xin chào,

Bạn đã hủy theo dõi truyện "{story.title}" trên Webdoctruyen.

Bạn sẽ không còn nhận được email thông báo khi truyện có phần mới.

Nếu muốn theo dõi lại, hãy truy cập trang truyện và đăng ký lại.

Trân trọng,
Webdoctruyen
"""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_addr}>"
    msg["To"] = email
    msg.set_content(body)
    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Lỗi gửi email xác nhận hủy follow: {e}")
        return False

def send_new_story_notification(story: Story, recipients: list[str]) -> bool:
    """Gửi email thông báo có truyện mới cho danh sách email."""
    if not recipients:
        return False
    smtp_server = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_username = os.environ.get("SMTP_USERNAME")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    if not smtp_username or not smtp_password:
        return False
    from_name = os.environ.get("EMAIL_FROM_NAME", "Webdoctruyen Admin")
    from_addr = os.environ.get("EMAIL_FROM_ADDR", "admin@webdoctruyen.org")
    
    subject = f"[Webdoctruyen] Truyện mới: {story.title}"
    story_url = url_for("story_detail", story_id=story.id, _external=True)
    body = f"""Xin chào,

Truyện mới "{story.title}" vừa được đăng tải trên Webdoctruyen.

Tác giả: {story.author or 'Ẩn danh'}
Thể loại: {', '.join([c.name for c in story.categories]) if story.categories else 'Chưa phân loại'}

Đọc ngay: {story_url}

Cảm ơn bạn đã quan tâm.

Trân trọng,
Webdoctruyen
"""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_addr}>"
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)
    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Lỗi gửi email thông báo truyện mới: {e}")
        return False

def send_new_story_subscription_confirmation(email: str) -> bool:
    """Xác nhận đăng ký nhận thông báo truyện mới."""
    smtp_server = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_username = os.environ.get("SMTP_USERNAME")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    if not smtp_username or not smtp_password:
        return False
    from_name = os.environ.get("EMAIL_FROM_NAME", "Webdoctruyen Admin")
    from_addr = os.environ.get("EMAIL_FROM_ADDR", "admin@webdoctruyen.org")
    
    subject = "Xác nhận đăng ký nhận thông báo truyện mới"
    body = f"""Xin chào,

Bạn vừa đăng ký nhận email thông báo khi có truyện mới được đăng trên Webdoctruyen.

Bạn sẽ nhận được thông báo mỗi khi chúng tôi đăng tải một truyện mới.

Để hủy đăng ký, truy cập trang "Theo dõi của tôi" và bỏ chọn ô tương ứng.

Trân trọng,
Webdoctruyen
"""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_addr}>"
    msg["To"] = email
    msg.set_content(body)
    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Lỗi gửi email xác nhận đăng ký truyện mới: {e}")
        return False

def send_new_story_unsubscription_confirmation(email: str) -> bool:
    """Xác nhận hủy đăng ký nhận thông báo truyện mới."""
    smtp_server = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_username = os.environ.get("SMTP_USERNAME")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    if not smtp_username or not smtp_password:
        return False
    from_name = os.environ.get("EMAIL_FROM_NAME", "Webdoctruyen Admin")
    from_addr = os.environ.get("EMAIL_FROM_ADDR", "admin@webdoctruyen.org")
    
    subject = "Xác nhận hủy nhận thông báo truyện mới"
    body = f"""Xin chào,

Bạn đã hủy đăng ký nhận email thông báo khi có truyện mới trên Webdoctruyen.

Bạn sẽ không còn nhận được thông báo về truyện mới nữa.

Nếu muốn đăng ký lại, hãy truy cập trang "Theo dõi của tôi" và chọn lại.

Trân trọng,
Webdoctruyen
"""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_addr}>"
    msg["To"] = email
    msg.set_content(body)
    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Lỗi gửi email xác nhận hủy đăng ký truyện mới: {e}")
        return False
        
class Category(db.Model):
    """Mô hình thể loại truyện."""

    __tablename__ = "categories"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    stories = db.relationship("Story", backref="category", lazy=True)

    def __repr__(self) -> str:
        return f"<Category {self.id} {self.name}>"


# Bảng phụ để thiết lập quan hệ nhiều‑nhiều giữa Story và Category.
story_categories = db.Table(
    "story_categories",
    db.Column("story_id", db.Integer, db.ForeignKey("stories.id"), primary_key=True),
    db.Column("category_id", db.Integer, db.ForeignKey("categories.id"), primary_key=True),
)


# Bảng lưu các phần (chương) của truyện dài. Mỗi phần thuộc một truyện.
class Part(db.Model):
    __tablename__ = "parts"
    id = db.Column(db.Integer, primary_key=True)
    story_id = db.Column(db.Integer, db.ForeignKey("stories.id"), nullable=False)
    part_number = db.Column(db.Integer, nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<Part {self.part_number} of Story {self.story_id}>"


# Bảng lưu bình luận cho truyện.
class Comment(db.Model):
    """Mô hình lưu trữ bình luận của người đọc.

    Mỗi bình luận gắn với một truyện (story_id) và lưu đường dẫn (url) của trang
    chương mà người dùng đăng bình luận. Ngoài ra còn lưu tên, email của
    người bình luận để hiển thị và gửi thông báo khi có bình luận mới.
    """
    __tablename__ = "comments"
    id = db.Column(db.Integer, primary_key=True)
    story_id = db.Column(db.Integer, db.ForeignKey("stories.id"), nullable=False)
    url = db.Column(db.String(1024), nullable=False)
    name = db.Column(db.String(100), nullable=True)
    email = db.Column(db.String(255), nullable=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_hidden = db.Column(db.Boolean, default=False)

    story = db.relationship("Story", backref=db.backref("comments", lazy=True))

    def __repr__(self) -> str:
        return f"<Comment {self.id} on Story {self.story_id}>"



# Bảng lưu video liên kết cho từng chương (part) của truyện.
# Mỗi bản ghi lưu URL của một video kèm theo khóa ngoại tới phần chứa video.
class PartVideo(db.Model):
    """Mô hình lưu trữ các liên kết video cho từng phần (chương) của truyện.

    Sử dụng để đính kèm tối đa 10 video cho mỗi phần. Các video được lưu
    riêng biệt khỏi nội dung để dễ dàng thêm, sửa và xoá mà không ảnh
    hưởng tới nội dung chữ của phần truyện.
    """

    __tablename__ = "part_videos"
    id = db.Column(db.Integer, primary_key=True)
    # Khoá ngoại trỏ tới bảng parts. Một phần có thể có nhiều video liên kết.
    part_id = db.Column(db.Integer, db.ForeignKey("parts.id"), nullable=False)
    # URL tới video. Các URL này nên là liên kết nhúng (embed) của Google Drive.
    url = db.Column(db.String(1024), nullable=False)

    # Thiết lập quan hệ ngược để có thể truy cập các video từ đối tượng Part.
    # Sử dụng cascade="all, delete-orphan" để xoá các video khi phần bị xoá.
    part = db.relationship(
        "Part",
        backref=db.backref("videos", cascade="all, delete-orphan", lazy=True),
    )

    def __repr__(self) -> str:
        return f"<PartVideo {self.id} for Part {self.part_id}>"

class ReadingHistory(db.Model):
    __tablename__ = "reading_history"
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(255), nullable=True)          # <-- THÊM CỘT EMAIL
    story_id = db.Column(db.Integer, db.ForeignKey("stories.id"), nullable=False)
    part_number = db.Column(db.Integer, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    story = db.relationship("Story", backref=db.backref("reading_history", lazy=True))

# ====================== MODEL MỚI: VIEWS THEO NGÀY ======================
class DailyView(db.Model):
    __tablename__ = "daily_view"
    id = db.Column(db.Integer, primary_key=True)
    story_id = db.Column(db.Integer, db.ForeignKey("stories.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    views = db.Column(db.Integer, default=0, nullable=False)
    views_desktop = db.Column(db.Integer, default=0, nullable=False)
    views_mobile = db.Column(db.Integer, default=0, nullable=False)
    __table_args__ = (db.UniqueConstraint('story_id', 'date', name='uq_daily_story_date'),)
    story = db.relationship('Story', backref=db.backref('daily_views', lazy=True))

class DailyListen(db.Model):
    __tablename__ = "daily_listen"
    id = db.Column(db.Integer, primary_key=True)
    story_id = db.Column(db.Integer, db.ForeignKey("stories.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    listens = db.Column(db.Integer, default=0, nullable=False)
    listens_desktop = db.Column(db.Integer, default=0, nullable=False)
    listens_mobile = db.Column(db.Integer, default=0, nullable=False)
    __table_args__ = (db.UniqueConstraint('story_id', 'date', name='uq_listen_story_date'),)
    story = db.relationship('Story', backref=db.backref('daily_listens', lazy=True))

class Announcement(db.Model):
    __tablename__ = "announcements"
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    device_type = db.Column(db.String(20), default='both')  # Thêm dòng này
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Announcement {self.id}>"

# Khi module được import (dù bởi flask CLI hay chạy trực tiếp),
# đảm bảo rằng các bảng trong SQLite được tạo. Thực hiện trong
# app context để tránh lỗi "no such table" khi truy cập lần đầu.
with app.app_context():
    db.create_all()
    
    def column_exists(table_name, column_name):
        """Check if a column exists in a table, compatible with SQLite and PostgreSQL."""
        if db.engine.dialect.name == 'postgresql':
            # Use information_schema for PostgreSQL
            result = db.session.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = :table AND column_name = :col"),
                {"table": table_name, "col": column_name}
            )
            return result.fetchone() is not None
        else:  # SQLite
            result = db.session.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
            columns = [row[1] for row in result]
            return column_name in columns

    # --- Upgrade Stories Table (works for both) ---
    # For PostgreSQL, use BOOLEAN DEFAULT FALSE; for SQLite, BOOLEAN DEFAULT 0 is fine.
    # SQLite will accept either.
    default_false = "FALSE" if db.engine.dialect.name == 'postgresql' else "0"
    
    for col, dtype in [('is_hidden', f'BOOLEAN DEFAULT {default_false}'),
                       ('rating_sum', 'INTEGER DEFAULT 0'),
                       ('rating_count', 'INTEGER DEFAULT 0'),
                       ('is_completed', f'BOOLEAN DEFAULT {default_false}')]:
        if not column_exists('stories', col):
            db.session.execute(text(f"ALTER TABLE stories ADD COLUMN {col} {dtype}"))
    
    # --- Upgrade Comments Table ---
    if not column_exists('comments', 'is_hidden'):
        db.session.execute(text(f"ALTER TABLE comments ADD COLUMN is_hidden BOOLEAN DEFAULT {default_false}"))

    # --- Upgrade Announcements Table ---
    if not column_exists('announcements', 'device_type'):
        db.session.execute(text("ALTER TABLE announcements ADD COLUMN device_type VARCHAR(20) DEFAULT 'both'"))

    # --- Thêm cột email vào bảng reading_history nếu chưa có ---
    if not column_exists('reading_history', 'email'):
        db.session.execute(text("ALTER TABLE reading_history ADD COLUMN email VARCHAR(255)"))
        print("Đã thêm cột email vào bảng reading_history")

    # --- Thêm cột views_desktop, views_mobile cho daily_view ---
    for col in ['views_desktop', 'views_mobile']:
        if not column_exists('daily_view', col):
            db.session.execute(text(f"ALTER TABLE daily_view ADD COLUMN {col} INTEGER DEFAULT 0"))
            print(f"Đã thêm cột {col} vào bảng daily_view")

    # --- Thêm cột listens_desktop, listens_mobile cho daily_listen ---
    for col in ['listens_desktop', 'listens_mobile']:
        if not column_exists('daily_listen', col):
            db.session.execute(text(f"ALTER TABLE daily_listen ADD COLUMN {col} INTEGER DEFAULT 0"))
            print(f"Đã thêm cột {col} vào bảng daily_listen")

    db.session.commit()

def create_tables() -> None:
    """Tạo cơ sở dữ liệu và bảng nếu chưa tồn tại.

    Hàm này được gọi lúc khởi động để đảm bảo các bảng tồn tại.
    """
    with app.app_context():
        db.create_all()


# ------------------ Comment handling and notification ------------------

def send_comment_notification(recipient: list[str], story: Story, comment_url: str) -> bool:
    """Gửi email thông báo tới danh sách người nhận khi có bình luận mới.

    Trả về True nếu gửi thành công, False nếu không gửi được. Hàm sẽ đọc các
    cấu hình SMTP từ biến môi trường:
      * SMTP_SERVER (mặc định smtp.gmail.com)
      * SMTP_PORT (mặc định 587)
      * SMTP_USERNAME
      * SMTP_PASSWORD
      * EMAIL_FROM_NAME (tên hiển thị, mặc định "Webdoctruyen Admin")
      * EMAIL_FROM_ADDR (địa chỉ email hiển thị, mặc định "admin@webdoctruyen.org")

    Mặc định, nếu không đặt SMTP_USERNAME hoặc SMTP_PASSWORD thì hàm trả về
    False và không gửi email. Nếu gửi thất bại (ngoại lệ), hàm cũng trả về
    False. Người gọi có thể dựa vào kết quả này để hiển thị thông báo cho
    người dùng.
    """
    # Không có người nhận thì không cần gửi
    if not recipients:
        return False
    smtp_server = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_username = os.environ.get("SMTP_USERNAME")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    if not smtp_username or not smtp_password:
        return False
    from_name = os.environ.get("EMAIL_FROM_NAME", "Webdoctruyen Admin")
    from_addr = os.environ.get("EMAIL_FROM_ADDR", "admin@webdoctruyen.org")
    # Tạo nội dung email
    subject = f"Có bình luận mới cho truyện '{story.title}'"
    body = (
        "Xin chào,\n\n"
        "Có người vừa bình luận một truyện mà bạn đã theo dõi. "
        f"Bạn có thể xem bình luận và trả lời tại: {comment_url}\n\n"
        f"Truyện: {story.title}\n"
        "Cảm ơn bạn đã quan tâm tới webdoctruyen.org."
    )
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_addr}>"
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)
    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(msg)
        return True
    except Exception:
        return False


@app.route("/comment/<int:story_id>", methods=["POST"])
def post_comment(story_id: int):
    """Xử lý việc đăng bình luận cho một truyện.

    Nhận các trường ``name``, ``email``, ``content`` và ``url`` từ form. Lưu
    bình luận vào cơ sở dữ liệu và gửi email thông báo tới những người đã
    bình luận trước đó trên cùng truyện (trừ địa chỉ email của người vừa bình
    luận). Sau khi xử lý xong, chuyển hướng về lại trang mà người dùng
    bình luận. Thông báo lỗi sẽ được flash nếu nội dung rỗng.
    """
    story = Story.query.get_or_404(story_id)
    name = request.form.get("name", "").strip()
    # không sử dụng email trong phiên bản này
    content = request.form.get("content", "").strip()
    url = request.form.get("url", request.url)
    if not content:
        flash("Nội dung bình luận không được để trống.")
        return redirect(request.referrer or url_for("story_detail", story_id=story_id))
    comment = Comment(
        story_id=story.id,
        url=url,
        name=name if name else None,
        # Không lưu email vì tính năng thông báo đã bỏ
        email=None,
        content=content,
    )
    db.session.add(comment)
    db.session.commit()
    # Gửi email cho những người đã bình luận trước đó (có email và khác người hiện tại)
    # Bỏ tính năng gửi thông báo qua email
    flash("Bình luận đã được đăng.")
    return redirect(url)


@app.route("/")
def index():
    """ Trang chủ hiển thị danh sách truyện nổi bật, truyện ngắn và truyện dài.
    
    - Truyện nổi bật: tối đa 20 truyện có lượt xem cao nhất.
    - Truyện ngắn: phân trang 10 truyện mỗi trang, sắp xếp theo ngày đăng mới nhất.
    - Truyện dài: phân trang 10 truyện mỗi trang, sắp xếp theo ngày đăng mới nhất.
    - Lịch sử đọc: 10 truyện gần nhất (mỗi truyện chỉ giữ phần đọc sau cùng).
    Người đọc có thể chuyển trang riêng biệt cho danh sách truyện ngắn và truyện dài bằng
    cách thay đổi tham số ``short_page`` hoặc ``long_page`` trên URL. Danh sách thể loại
    được lấy để hiển thị trong thanh bên.
    """
    # xác định số trang cho danh sách truyện ngắn và truyện dài
    short_page = request.args.get("short_page", 1, type=int)
    long_page = request.args.get("long_page", 1, type=int)
    active_tab = request.args.get("tab", "recent")  # mặc định là recent
    per_page = 10
    # truyện ngắn (không bao gồm truyện ẩn)
    short_query = (
        Story.query.filter_by(story_type="short", is_hidden=False)
        .order_by(Story.created_at.desc())
    )
    short_pagination = short_query.paginate(page=short_page, per_page=per_page, error_out=False)
    short_stories = short_pagination.items
    # truyện dài (không bao gồm truyện ẩn)
    long_query = (
        Story.query.filter_by(story_type="long", is_hidden=False)
        .order_by(Story.created_at.desc())
    )
    long_pagination = long_query.paginate(page=long_page, per_page=per_page, error_out=False)
    long_stories = long_pagination.items
    # truyện nhiều người đọc nhất: giới hạn 20 theo lượt xem, không bao gồm truyện ẩn
    trending = (
        Story.query.filter_by(is_hidden=False).order_by(Story.views.desc()).limit(20).all()
    )

    # truyện hay nhất: sắp xếp theo trung bình đánh giá (rating_sum / rating_count)
    # chỉ lấy những truyện đã có ít nhất 1 lượt đánh giá
    best = (
        Story.query.filter(Story.rating_count > 0, Story.is_hidden == False)
        .order_by((Story.rating_sum / Story.rating_count).desc())
        .limit(10)
        .all()
    )
    # Lấy danh sách truyện có chương được thêm mới nhất (truyện mới cập nhật)
    # Sử dụng subquery để lấy thời gian tạo phần mới nhất cho mỗi truyện
    recent_parts = (
        db.session.query(Part.story_id, db.func.max(Part.created_at).label("latest_part"))
        .group_by(Part.story_id)
        .subquery()
    )
    recent_stories = (
        Story.query.join(recent_parts, Story.id == recent_parts.c.story_id)
        .filter(Story.is_hidden == False)
        .order_by(recent_parts.c.latest_part.desc())
        .limit(10)
        .all()
    )
    # danh sách thể loại để hiển thị trong thanh bên
    categories_group1, categories_group2, categories_group3 = get_category_groups()

    # --- Lấy lịch sử đọc (10 truyện gần nhất, mỗi truyện chỉ một bản ghi mới nhất) ---
    session_id = get_user_session_id()
    # Lấy tất cả bản ghi của session, sắp xếp theo thời gian cập nhật giảm dần
    # Mỗi story chỉ xuất hiện một lần do unique constraint (session_id, story_id) trong bảng?
    # Nếu chưa có unique constraint, ta vẫn lấy tất cả nhưng có thể trùng story.
    # Để an toàn, ta sẽ lấy 10 bản ghi gần nhất nhưng nếu trùng story thì chỉ lấy bản mới nhất.
    # Cách đơn giản: dùng DISTINCT ON (PostgreSQL) hoặc subquery.
    # Dưới đây dùng subquery để lấy max updated_at cho mỗi story, sau đó join lại.
    from sqlalchemy import func, and_
    
    subq = db.session.query(
        ReadingHistory.story_id,
        func.max(ReadingHistory.updated_at).label('max_updated')
    ).filter(ReadingHistory.session_id == session_id).group_by(ReadingHistory.story_id).subquery()
    
    history_list = db.session.query(ReadingHistory).join(
        subq,
        and_(
            ReadingHistory.story_id == subq.c.story_id,
            ReadingHistory.updated_at == subq.c.max_updated
        )
    ).order_by(ReadingHistory.updated_at.desc()).limit(10).all()
    
    # Nếu bạn muốn đơn giản hơn và tin rằng mỗi story chỉ có một bản ghi (do unique constraint),
    # thì chỉ cần:
    # history_list = ReadingHistory.query.filter_by(session_id=session_id)\
    #     .order_by(ReadingHistory.updated_at.desc()).limit(10).all()
    # Tuy nhiên, đoạn trên vẫn an toàn hơn.

    return render_template(
        "index.html",
        best=best,
        trending=trending,
        short_stories=short_stories,
        long_stories=long_stories,
        short_pagination=short_pagination,
        long_pagination=long_pagination,
        recent_stories=recent_stories,
        active_tab=active_tab,
        history_list=history_list,          # <- biến mới thay cho continue_story, continue_part
        categories_group1=categories_group1,
        categories_group2=categories_group2,
        categories_group3=categories_group3,
    )

@app.route("/story/<int:story_id>")
def story_detail(story_id: int):
    """Trang chi tiết truyện - ĐÃ FIX TIÊU ĐỀ LẶP + giữ nguyên xuống dòng"""
    story = Story.query.get_or_404(story_id)
    
    # Tăng lượt xem
    story.views = (story.views or 0) + 1
    today = date.today()
    daily = DailyView.query.filter_by(story_id=story.id, date=today).first()
    mobile = is_mobile()
    if daily:
        daily.views += 1
        if mobile:
            daily.views_mobile = (daily.views_mobile or 0) + 1
        else:
            daily.views_desktop = (daily.views_desktop or 0) + 1
    else:
        daily = DailyView(story_id=story.id, date=today, views=1,
                          views_desktop=0 if mobile else 1,
                          views_mobile=1 if mobile else 0)
        db.session.add(daily)
    db.session.commit()

    parts = Part.query.filter_by(story_id=story.id).order_by(Part.part_number).all()
    total_parts = len(parts)

    part_param = request.args.get("part", default=None, type=int)
    current_index = part_param if part_param and 1 <= part_param <= total_parts else 1

    current_part = None
    for p in parts:
        if p.part_number == current_index:
            current_part = p
            break

    if current_part is None:
        return render_template("story.html", story=story, current_part=None,
                               chapter_title="Chưa có nội dung phần này",
                               content_processed="<p><em>Truyện này chưa có phần nào.</em></p>",
                               current_index=current_index, total_parts=total_parts,
                               parts=parts, comments=[], current_url=request.url)

    # Tách tiêu đề và nội dung
    raw = current_part.content
    if '\n' in raw:
        chapter_title, chapter_body = raw.split('\n', 1)
    else:
        chapter_title = raw
        chapter_body = ""

    chapter_title = chapter_title.strip()

    # Highlight chỉ áp dụng cho nội dung
    chapter_body = re.sub(r'("(.*?)")', r'<span class="highlight-green">\1</span>', chapter_body, flags=re.DOTALL)
    chapter_body = re.sub(r"('(.*?)')", r'<span class="highlight-red">\1</span>', chapter_body, flags=re.DOTALL)

    # Giữ nguyên tất cả xuống dòng
    content_processed = chapter_body.replace('\n', '<br>')

    comments = Comment.query.filter_by(story_id=story.id, is_hidden=False).order_by(Comment.created_at.desc()).all()

    # Lưu lịch sử đọc
    if current_part:
        session_id = get_user_session_id()
        email_in_session = session.get('reader_email')
        
        # Nếu có email trong session, ưu tiên dùng email để tìm bản ghi
        if email_in_session:
            history = ReadingHistory.query.filter_by(email=email_in_session, story_id=story.id).first()
            if history:
                history.part_number = current_index
                history.updated_at = datetime.utcnow()
                history.session_id = session_id
            else:
                history = ReadingHistory(session_id=session_id, email=email_in_session, story_id=story.id, part_number=current_index)
                db.session.add(history)
        else:
            # Không có email, dùng session_id
            history = ReadingHistory.query.filter_by(session_id=session_id, story_id=story.id).first()
            if history:
                history.part_number = current_index
                history.updated_at = datetime.utcnow()
            else:
                history = ReadingHistory(session_id=session_id, story_id=story.id, part_number=current_index)
                db.session.add(history)
        db.session.commit()

    return render_template(
        "story.html",
        story=story,
        current_part=current_part,
        chapter_title=chapter_title,
        content_processed=content_processed,
        current_index=current_index,
        total_parts=total_parts,
        parts=parts,
        comments=comments,
        current_url=request.url,
    )

def split_to_chunks(text: str, max_chars: int = 800) -> list:
    """Split text thành chunk <= 1000 ký tự, không cắt giữa câu."""
    if not text.strip():
        return [" "]

    import re
    sentence_split = re.compile(r'(?<=[\.!?…])\s+')
    sentences = [s.strip() for s in sentence_split.split(text.strip()) if s.strip()]

    chunks = []
    current = ""
    for sentence in sentences:
        if not current:
            current = sentence
        elif len(current) + 1 + len(sentence) <= max_chars:
            current += " " + sentence
        else:
            chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return chunks


# Thêm dòng này ở đầu file app.py (gần phần import)
background_tasks = {}   # key: (story_id, part_number) → thread đang chạy

@app.route("/api/start_audio/<int:part_id>")
def start_audio(part_id: int):
    """Tạo chunk 1 ngay + background tạo chunk còn lại (đã fix race condition)"""
    part = Part.query.get_or_404(part_id)
    story_id = part.story_id
    part_number = part.part_number
    task_key = (story_id, part_number)

    print(f"[AUDIO] 🚀 Yêu cầu nghe phần {part_number} - truyện {story_id}")

    audio_dir = Path("static/audio") / str(story_id)
    audio_dir.mkdir(parents=True, exist_ok=True)

    text = part.content.strip()
    if not text:
        return jsonify({"status": "error", "message": "Nội dung trống"}), 400

    # === GHI NHẬN LƯỢT NGHE MỚI ===
    today = date.today()
    listen_record = DailyListen.query.filter_by(story_id=story_id, date=today).first()
    mobile = is_mobile()
    if listen_record:
        listen_record.listens += 1
        if mobile:
            listen_record.listens_mobile = (listen_record.listens_mobile or 0) + 1
        else:
            listen_record.listens_desktop = (listen_record.listens_desktop or 0) + 1
    else:
        listen_record = DailyListen(story_id=story_id, date=today, listens=1,
                                    listens_desktop=0 if mobile else 1,
                                    listens_mobile=1 if mobile else 0)
        db.session.add(listen_record)
    db.session.commit()
    # ================================

    chunks = split_to_chunks(text, max_chars=800)
    total_chunks = len(chunks)

    # === CHUNK 1 ===
    chunk1_path = audio_dir / f"{part_number}_chunk_0001.mp3"
    tmp1 = chunk1_path.with_name(chunk1_path.stem + "__tmp" + chunk1_path.suffix)

    if not chunk1_path.exists():
        print(f"[AUDIO] 🔨 Đang tạo CHUNK 1/{total_chunks}...")
        try:
            communicate = edge_tts.Communicate(text=chunks[0], voice="vi-VN-HoaiMyNeural")
            run_async_save(communicate, str(tmp1))
            if tmp1.exists():
                os.replace(tmp1, chunk1_path)
                print(f"[AUDIO] ✅ CHUNK 1 HOÀN TẤT")
        except Exception as e:
            print(f"[AUDIO] ❌ Lỗi chunk 1: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500

    url_chunk1 = f"/static/audio/{story_id}/{part_number}_chunk_0001.mp3"

    # === BACKGROUND: Chỉ khởi động nếu chưa có thread nào đang chạy ===
    if task_key not in background_tasks or not background_tasks[task_key].is_alive():
        def background_create():
            print(f"[BACKGROUND] 🔄 Bắt đầu tạo {total_chunks-1} chunk còn lại cho phần {part_number}...")
            for i in range(1, total_chunks):
                chunk_path = audio_dir / f"{part_number}_chunk_{i+1:04d}.mp3"
                tmp_path = chunk_path.with_name(chunk_path.stem + "__tmp" + chunk_path.suffix)

                if chunk_path.exists():
                    continue

                print(f"[BACKGROUND] 🔨 Đang tạo chunk {i+1}/{total_chunks}...")
                try:
                    communicate = edge_tts.Communicate(text=chunks[i], voice="vi-VN-HoaiMyNeural")
                    run_async_save(communicate, str(tmp_path))
                    if tmp_path.exists():
                        os.replace(tmp_path, chunk_path)
                        print(f"[BACKGROUND] ✅ Chunk {i+1}/{total_chunks} HOÀN TẤT")
                except Exception as e:
                    print(f"[BACKGROUND] ❌ Lỗi chunk {i+1}: {e}")

            print(f"[BACKGROUND] 🎉 Hoàn thành tất cả chunk của phần {part_number}")
            # Xóa thread khỏi dict khi xong
            if task_key in background_tasks:
                del background_tasks[task_key]

        thread = threading.Thread(target=background_create, daemon=True)
        background_tasks[task_key] = thread
        thread.start()
    else:
        print(f"[BACKGROUND] ♻️ Đã có background thread đang chạy cho phần này, bỏ qua khởi động mới")

    return jsonify({
        "status": "success",
        "url": url_chunk1,
        "current_chunk": 1,
        "total_chunks": total_chunks,
        "part_id": part_id,
        "story_id": story_id,
        "part_number": part_number
    })

@app.route("/api/get_chunk/<int:story_id>/<int:part_number>/<int:chunk_index>")
def get_chunk(story_id: int, part_number: int, chunk_index: int):
    """Trả về chunk - Nếu chunk bị xóa thì tạo lại ngay"""
    print(f"[GET_CHUNK] 📥 Yêu cầu chunk {chunk_index} của phần {part_number} (truyện {story_id})")

    audio_dir = Path("static/audio") / str(story_id)
    chunk_path = audio_dir / f"{part_number}_chunk_{chunk_index:04d}.mp3"

    # Nếu chunk còn tồn tại → trả về ngay
    if chunk_path.exists() and chunk_path.stat().st_size > 500:
        url = f"/static/audio/{story_id}/{chunk_path.name}"
        print(f"[GET_CHUNK] ✅ Chunk {chunk_index} SẴN SÀNG")
        return jsonify({"status": "success", "url": url})

    # Chunk bị xóa hoặc chưa có → tạo lại ngay
    print(f"[GET_CHUNK] 🔄 Chunk {chunk_index} bị xóa → tạo lại ngay")

    # Lấy Part theo story_id + part_number
    part = Part.query.filter_by(story_id=story_id, part_number=part_number).first_or_404()

    text = part.content.strip()
    chunks = split_to_chunks(text, max_chars=800)

    if chunk_index < 1 or chunk_index > len(chunks):
        return jsonify({"status": "error", "message": "Chunk index không hợp lệ"}), 400

    tmp_path = chunk_path.with_name(chunk_path.stem + "__tmp" + chunk_path.suffix)

    try:
        communicate = edge_tts.Communicate(text=chunks[chunk_index-1], voice="vi-VN-HoaiMyNeural")
        run_async_save(communicate, str(tmp_path))

        if tmp_path.exists():
            os.replace(tmp_path, chunk_path)
            print(f"[GET_CHUNK] ✅ ĐÃ TẠO LẠI chunk {chunk_index}")

            url = f"/static/audio/{story_id}/{chunk_path.name}"
            return jsonify({"status": "success", "url": url})
        else:
            return jsonify({"status": "error", "message": "Tạo chunk thất bại"}), 500

    except Exception as e:
        print(f"[GET_CHUNK] ❌ Lỗi tạo lại chunk {chunk_index}: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/upload", methods=["GET", "POST"])
def upload():
    """ Trang quản lý truyện - ĐÃ TÍCH HỢP LOGIC DỌN DẸP + TÁCH PHẦN TỰ ĐỘNG + VIDEO_URLS """
    if not session.get("upload_authenticated"):
        return redirect(url_for("upload_login"))

    UPLOAD_PASSWORD = os.environ.get("UPLOAD_PASSWORD", "secret")
    categories = Category.query.order_by(Category.name).all()

    # === PHẦN GET ===
    page = request.args.get("page", 1, type=int)
    search_query = request.args.get("q", "").strip()
    search_type = request.args.get("stype", "title")
    filter_category = request.args.get("category", type=int)
    filter_completed = request.args.get("completed", "").strip()
    filter_hidden = request.args.get("hidden", "").strip()
    filter_date_from = request.args.get("date_from", "").strip()
    filter_date_to = request.args.get("date_to", "").strip()

    stories_query = Story.query.order_by(Story.created_at.desc())
    highlight_snippets: dict[int, str] = {}

    # Tìm kiếm cơ bản
    if search_query:
        pattern = f"%{search_query}%"
        if search_type == "content":
            stories_query = (
                Story.query.join(Part)
                .filter(Part.content.ilike(pattern))
                .distinct()
                .order_by(Story.created_at.desc())
            )
        else:
            stories_query = stories_query.filter(
                (Story.title.ilike(pattern)) | (Story.author.ilike(pattern))
            )
    
    # Lọc theo thể loại (nhiều thể loại)
    if filter_category:
        stories_query = stories_query.join(story_categories).filter(story_categories.c.category_id == filter_category)
    
    # Lọc theo trạng thái hoàn thành
    if filter_completed == "completed":
        stories_query = stories_query.filter(Story.is_completed == True)
    elif filter_completed == "uncompleted":
        stories_query = stories_query.filter(Story.is_completed == False)
    
    # Lọc theo trạng thái ẩn
    if filter_hidden == "hidden":
        stories_query = stories_query.filter(Story.is_hidden == True)
    elif filter_hidden == "visible":
        stories_query = stories_query.filter(Story.is_hidden == False)
    
    # Lọc theo khoảng ngày
    if filter_date_from:
        try:
            date_from = datetime.strptime(filter_date_from, "%Y-%m-%d")
            stories_query = stories_query.filter(Story.created_at >= date_from)
        except:
            pass
    if filter_date_to:
        try:
            date_to = datetime.strptime(filter_date_to, "%Y-%m-%d")
            stories_query = stories_query.filter(Story.created_at <= date_to)
        except:
            pass

    stories_pagination = stories_query.paginate(page=page, per_page=25, error_out=False)
    stories = stories_pagination.items

    if search_query and search_type == "content":
        pattern = f"%{search_query}%"
        keywords = [kw.lower() for kw in search_query.split() if kw.strip()]
        for st in stories:
            part_match = (
                Part.query.filter(
                    Part.story_id == st.id,
                    Part.content.ilike(pattern),
                )
                .order_by(Part.part_number)
                .first()
            )
            if part_match:
                content_lower = part_match.content.lower()
                idx = content_lower.find(search_query.lower())
                if idx < 0 and keywords:
                    idx = content_lower.find(keywords[0])
                start = max(0, idx - 50)
                end = min(len(part_match.content), idx + len(search_query) + 50)
                snippet = part_match.content[start:end]
                for kw in keywords:
                    snippet = re.sub(
                        rf"({re.escape(kw)})",
                        lambda m: f'<span class="highlight">{m.group(0)}</span>',
                        snippet,
                        flags=re.IGNORECASE,
                    )
                highlight_snippets[st.id] = snippet

    # Lấy danh sách thể loại cho dropdown lọc
    all_categories = Category.query.order_by(Category.name).all()

    # === PHẦN POST (giữ nguyên logic cũ) ===
    if request.method == "POST":
        password = request.form.get("password", "")
        if password != UPLOAD_PASSWORD:
            flash("Mật khẩu sai.", "error")
            return redirect(url_for("upload"))

        existing_story_id = request.form.get("existing_story_id")
        action = request.form.get("action")
        video_urls = request.form.getlist("video_urls")

        if existing_story_id:
            story = Story.query.get_or_404(int(existing_story_id))

            if action == "add_part":
                raw_content = request.form.get("content", "").strip()
                if not raw_content:
                    flash("Nội dung phần mới không được trống.", "error")
                    return redirect(url_for("upload", story_id=story.id))

                parts_data = split_and_clean_content(raw_content)

                for part_num, cleaned_content in parts_data:
                    last_part = Part.query.filter_by(story_id=story.id).order_by(Part.part_number.desc()).first()
                    next_number = (last_part.part_number + 1) if last_part else 1
                    new_part = Part(story_id=story.id, part_number=next_number, content=cleaned_content)
                    db.session.add(new_part)
                    db.session.flush()

                    for url in video_urls[:9]:
                        url = (url or "").strip()
                        if url:
                            db.session.add(PartVideo(part_id=new_part.id, url=url))

                db.session.commit()

                flash(f"Đã thêm {len(parts_data)} phần mới (đã dọn dẹp, tách tự động và gán video).", "success")

                # Gửi thông báo cho người theo dõi
                if story.followers:
                    recipient_emails = [f.email for f in story.followers]
                    # Lấy tiêu đề phần mới (dòng đầu tiên của nội dung)
                    part_title = cleaned_content.split('\n', 1)[0].strip()
                    send_new_chapter_notification(story, next_number, part_title, recipient_emails)
                    flash(f"Đã gửi thông báo đến {len(recipient_emails)} người theo dõi.")

                return redirect(url_for("upload", story_id=story.id))

            elif action == "update_part":
                part_id = request.form.get("part_id")
                raw_content = request.form.get("content", "").strip()
                if not part_id or not raw_content:
                    flash("Nội dung không được trống.", "error")
                    return redirect(url_for("upload", story_id=story.id))

                part_obj = Part.query.get(int(part_id))
                if part_obj and part_obj.story_id == story.id:
                    cleaned_content = '\n'.join(clean_line(line) for line in raw_content.splitlines())
                    part_obj.content = cleaned_content

                    PartVideo.query.filter_by(part_id=part_obj.id).delete()
                    for url in video_urls[:9]:
                        url = (url or "").strip()
                        if url:
                            db.session.add(PartVideo(part_id=part_obj.id, url=url))

                    db.session.commit()
                    flash("Đã cập nhật phần (đã dọn dẹp và cập nhật video).", "success")
                return redirect(url_for("upload", story_id=story.id))

            elif action == "update_story":
                story.title = request.form.get("title", "").strip()
                story.author = request.form.get("author", "").strip()
                story.story_type = request.form.get("story_type", "short")
                story.is_completed = bool(request.form.get("is_completed"))
                cat_ids = [int(x) for x in request.form.getlist("category_ids") if x]
                story.categories = Category.query.filter(Category.id.in_(cat_ids)).all() if cat_ids else []
                story.category_id = cat_ids[0] if cat_ids else None
                db.session.commit()
                return redirect(url_for("upload", story_id=story.id))

            elif action == "delete_last":
                last_part = Part.query.filter_by(story_id=story.id).order_by(Part.part_number.desc()).first()
                if last_part:
                    db.session.delete(last_part)
                    db.session.commit()
                return redirect(url_for("upload", story_id=story.id))

            elif action == "toggle_hidden":
                story.is_hidden = not bool(story.is_hidden)
                db.session.commit()
                return redirect(url_for("upload", story_id=story.id))

            elif action == "delete_story":
                story.categories = []
                Part.query.filter_by(story_id=story.id).delete()
                db.session.delete(story)
                db.session.commit()
                return redirect(url_for("upload"))

            elif action == "replace_text":
                search_str = request.form.get("search_string", "").strip()
                replacement = request.form.get("replacement_string", "")
                if not search_str:
                    flash("Bạn phải nhập cụm từ cần tìm.")
                    return redirect(url_for("upload", story_id=story.id))
                parts = Part.query.filter_by(story_id=story.id).all()
                replaced_count = 0
                for part in parts:
                    if search_str in part.content:
                        part.content = part.content.replace(search_str, replacement)
                        replaced_count += 1
                if replaced_count > 0:
                    db.session.commit()
                    flash(f"Đã thay '{search_str}' bằng '{replacement}' trong {replaced_count} phần.")
                else:
                    flash("Không tìm thấy cụm từ trong các phần.")
                return redirect(url_for("upload", story_id=story.id))

        else:
            # Tạo truyện mới
            title = request.form.get("title", "").strip()
            raw_content = request.form.get("content", "").strip()
            if not title or not raw_content:
                flash("Vui lòng nhập tiêu đề và nội dung.", "error")
                return redirect(url_for("upload"))

            story = Story(
                title=title,
                author=request.form.get("author", "").strip(),
                story_type=request.form.get("story_type", "short"),
                is_completed=bool(request.form.get("is_completed"))
            )
            db.session.add(story)
            db.session.commit()

            parts_data = split_and_clean_content(raw_content)

            for part_num, cleaned_content in parts_data:
                part = Part(story_id=story.id, part_number=part_num, content=cleaned_content)
                db.session.add(part)
                db.session.flush()

                for url in video_urls[:9]:
                    url = (url or "").strip()
                    if url:
                        db.session.add(PartVideo(part_id=part.id, url=url))

            db.session.commit()
            flash(f"Đã tạo truyện '{title}' với {len(parts_data)} phần (đã dọn dẹp, tách tự động và gán video).", "success")

            # Gửi thông báo truyện mới cho những email đăng ký
            subscribers = NewStorySubscription.query.all()
            if subscribers:
                recipient_emails = [sub.email for sub in subscribers]
                send_new_story_notification(story, recipient_emails)
                flash(f"Đã gửi thông báo truyện mới đến {len(recipient_emails)} độc giả.")

            return redirect(url_for("upload", story_id=story.id))

    # === PHẦN GET - hiển thị form ===
    story_id = request.args.get("story_id")
    if story_id:
        story = Story.query.get_or_404(int(story_id))
        parts = Part.query.filter_by(story_id=story.id).order_by(Part.part_number).all()
        edit_part_id = request.args.get("edit_part", type=int)
        edit_part_obj = Part.query.get(edit_part_id) if edit_part_id else None
        return render_template(
            "upload_edit.html",
            story=story,
            parts=parts,
            categories=categories,
            edit_part=edit_part_obj,
            error_update=None,
        )

    return render_template(
        "upload_new.html",
        categories=categories,
        stories=stories,
        pagination=stories_pagination,
        q=search_query,
        stype=search_type,
        highlight_snippets=highlight_snippets,
        filter_category=filter_category,
        filter_completed=filter_completed,
        filter_hidden=filter_hidden,
        filter_date_from=filter_date_from,
        filter_date_to=filter_date_to,
        all_categories=all_categories,
    )

@app.route("/admin/dashboard")
def admin_dashboard():
    if 'upload_authenticated' not in session:
        flash("Vui lòng đăng nhập admin.", "danger")
        return redirect(url_for('upload_login'))

    end_date = date.today()
    start_date = end_date - timedelta(days=6)
    dates = [start_date + timedelta(days=i) for i in range(7)]
    date_strs = [d.strftime('%d/%m') for d in dates]

    # Lấy top 10 truyện có tổng views cao nhất trong 7 ngày, kèm theo story object
    top_views_data = db.session.query(
        DailyView.story_id,
        func.sum(DailyView.views).label('total_views')
    ).filter(DailyView.date.between(start_date, end_date)).group_by(DailyView.story_id).order_by(func.sum(DailyView.views).desc()).limit(10).all()
    
    top_listens_data = db.session.query(
        DailyListen.story_id,
        func.sum(DailyListen.listens).label('total_listens')
    ).filter(DailyListen.date.between(start_date, end_date)).group_by(DailyListen.story_id).order_by(func.sum(DailyListen.listens).desc()).limit(10).all()

    # Tạo list các tuple (story, total_views) và (story, total_listens)
    top_views = []
    for item in top_views_data:
        story = Story.query.get(item.story_id)
        if story:
            top_views.append((story, item.total_views))
    
    top_listens = []
    for item in top_listens_data:
        story = Story.query.get(item.story_id)
        if story:
            top_listens.append((story, item.total_listens))

    # Lấy dữ liệu cho biểu đồ (top 5 truyện theo views)
    chart_labels = []
    chart_views_data = []
    for story, total in top_views[:5]:
        chart_labels.append(story.title[:20])
        chart_views_data.append(total)

    return render_template('admin_dashboard.html',
                           dates=date_strs,
                           top_views=top_views,
                           top_listens=top_listens,
                           chart_labels=chart_labels,
                           chart_views_data=chart_views_data)


# Hiển thị trang đăng nhập trước khi vào trang upload.
# Người dùng cần nhập mật khẩu hợp lệ để tiếp tục.
@app.route("/upload_login", methods=["GET", "POST"])
def upload_login():
    categories_group1, categories_group2, categories_group3 = get_category_groups()
    UPLOAD_PASSWORD = os.environ.get("UPLOAD_PASSWORD", "secret")
    
    next_url = request.args.get("next") or url_for("upload")  # mặc định quay về upload nếu không có next

    if request.method == "POST":
        if request.form.get("password") == UPLOAD_PASSWORD:
            session["upload_authenticated"] = True
            return redirect(next_url)          # ← Quan trọng: redirect theo next
        return render_template("upload_login.html", 
                               error="Mật khẩu sai.",
                               categories_group1=categories_group1,
                               categories_group2=categories_group2,
                               categories_group3=categories_group3)

    return render_template("upload_login.html",
                           categories_group1=categories_group1,
                           categories_group2=categories_group2,
                           categories_group3=categories_group3)


# --------- Export/Import data utilities ---------

@app.route("/export_data", methods=["POST"])
def export_data():
    """Export tất cả dữ liệu về truyện, phần, video, bình luận và thể loại ra một file JSON.

    Người dùng phải đăng nhập trang quản trị và cung cấp mật khẩu hợp lệ để tải dữ liệu.
    Sau khi thu thập dữ liệu, hàm trả về file JSON để người dùng tải xuống.
    """
    # Chỉ cho phép khi đã đăng nhập trang upload
    if not session.get("upload_authenticated"):
        return redirect(url_for("upload_login"))
    # Kiểm tra mật khẩu được gửi kèm trong form
    UPLOAD_PASSWORD = os.environ.get("UPLOAD_PASSWORD", "secret")
    pw = request.form.get("password", "")
    if pw != UPLOAD_PASSWORD:
        flash("Mật khẩu không hợp lệ.")
        return redirect(url_for("upload"))
    # Lấy dữ liệu từ cơ sở dữ liệu
    stories = Story.query.all()
    categories = Category.query.all()
    parts = Part.query.all()
    videos = PartVideo.query.all()
    comments = Comment.query.all()
    # Chuyển đổi dữ liệu sang dict
    data = {
        "categories": [
            {"id": c.id, "name": c.name} for c in categories
        ],
        "stories": [
            {
                "id": s.id,
                "title": s.title,
                "author": s.author,
                "story_type": s.story_type,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "views": s.views,
                "is_hidden": s.is_hidden,
                "is_completed": s.is_completed,
                "rating_sum": s.rating_sum,
                "rating_count": s.rating_count,
                "category_id": s.category_id,
                "categories": [cat.id for cat in s.categories],
            }
            for s in stories
        ],
        "parts": [
            {
                "id": p.id,
                "story_id": p.story_id,
                "part_number": p.part_number,
                "content": p.content,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in parts
        ],
        "comments": [
            {
                "id": c.id,
                "story_id": c.story_id,
                "url": c.url,
                "name": c.name,
                "email": c.email,
                "content": c.content,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in comments
        ],
        "videos": [
            {
                "id": v.id,
                "part_id": v.part_id,
                "url": v.url,
            }
            for v in videos
        ],
    }
    # Chuyển đổi sang JSON và gửi file
    json_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    buf = io.BytesIO(json_bytes)
    filename = f"stories_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    return send_file(buf, as_attachment=True, download_name=filename, mimetype="application/json")


@app.route("/import_data", methods=["POST"])
def import_data():
    """Xử lý yêu cầu import dữ liệu từ file JSON.

    Bổ sung xác thực mật khẩu trước khi import. Hàm sẽ kiểm tra trùng tên truyện
    dựa trên tiêu đề (không phân biệt chữ hoa/thường) và nếu phát hiện, hiển
    thị một trang xem xét để người dùng quyết định ghi đè hoặc bỏ qua từng
    truyện trùng tên. Nếu không có trùng, dữ liệu sẽ được import ngay lập tức.
    """
    # Chỉ cho phép người dùng đã đăng nhập vào trang upload
    if not session.get("upload_authenticated"):
        return redirect(url_for("upload_login"))

    # Kiểm tra mật khẩu gửi kèm
    UPLOAD_PASSWORD = os.environ.get("UPLOAD_PASSWORD", "secret")
    pw = request.form.get("password", "")
    if pw != UPLOAD_PASSWORD:
        flash("Mật khẩu không hợp lệ.")
        return redirect(url_for("upload"))

    uploaded_file = request.files.get("import_file")
    if not uploaded_file:
        flash("Không tìm thấy file để import.")
        return redirect(url_for("upload"))
    try:
        data = json.load(uploaded_file)
    except Exception:
        flash("File import không hợp lệ.")
        return redirect(url_for("upload"))

    # Đảm bảo tồn tại các khoá cơ bản trong file JSON
    for key in ("categories", "stories", "parts", "comments", "videos"):
        if key not in data:
            data[key] = []

    # Xác định tiêu đề truyện đã tồn tại trong cơ sở dữ liệu (không phân biệt chữ hoa/thường)
    existing_titles = {s.title.lower() for s in Story.query.all()}
    duplicates = []
    non_duplicates = []
    for st in data.get("stories", []):
        title = (st.get("title") or "").strip()
        if title.lower() in existing_titles:
            duplicates.append(st)
        else:
            non_duplicates.append(st)

    # Nếu có trùng tên, chuẩn bị danh sách chi tiết để hỏi người dùng
    if duplicates:
        duplicate_info_list = []
        for st in duplicates:
            json_id = st.get("id")
            title = st.get("title", "")
            # Tìm truyện hiện có trong DB
            existing_story = Story.query.filter(func.lower(Story.title) == title.lower()).first()
            # Lấy phần đầu tiên của truyện trên web
            db_snippet = ""
            if existing_story:
                db_first_part = (
                    Part.query.filter_by(story_id=existing_story.id)
                    .order_by(Part.part_number)
                    .first()
                )
                if db_first_part and db_first_part.content:
                    # Ghép nội dung vào một dòng và lấy tối đa 400 ký tự, cắt tới từ gần nhất
                    db_text = db_first_part.content.replace("\n", " ")
                    snippet = db_text[:400]
                    if len(db_text) > 400:
                        snippet = snippet.rsplit(" ", 1)[0] + "..."
                    db_snippet = snippet
            # Lấy phần đầu tiên của truyện trong file JSON
            json_snippet = ""
            for p in data.get('parts', []):
                if p.get('story_id') == json_id and p.get('part_number') == 1:
                    content = (p.get('content') or "").replace("\n", " ")
                    snippet = content[:400]
                    if len(content) > 400:
                        snippet = snippet.rsplit(" ", 1)[0] + "..."
                    json_snippet = snippet
                    break
            duplicate_info_list.append({
                'json_id': json_id,
                'db_id': existing_story.id if existing_story else None,
                'title': title,
                'db_snippet': db_snippet,
                'json_snippet': json_snippet,
            })
        # Lưu dữ liệu import vào file tạm để sử dụng ở bước xác nhận
        BASE_DIR = os.path.abspath(os.path.dirname(__file__))
        DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, os.pardir, "data"))
        os.makedirs(DATA_DIR, exist_ok=True)
        temp_filename = f"import_{uuid.uuid4().hex}.json"
        temp_path = os.path.join(DATA_DIR, temp_filename)
        try:
            with open(temp_path, 'w', encoding='utf8') as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            flash("Không thể lưu tệp tạm thời để import.")
            return redirect(url_for("upload"))
        # Chuyển sang trang xác nhận import, truyền danh sách trùng và tên file tạm
        return render_template(
            "import_review.html",
            duplicates=duplicate_info_list,
            temp_file=temp_filename,
            success_count=len(non_duplicates),
        )
    # Không có trùng tên, thực hiện import trực tiếp
    imported_count, overwritten_count, skipped_count = perform_import(data, decisions=None)
    flash(f"Import thành công {imported_count} truyện.")
    return redirect(url_for("upload"))


# Route xử lý bước xác nhận import sau khi người dùng lựa chọn cách xử lý các truyện trùng tên.
@app.route("/import_confirm", methods=["POST"])
def import_confirm():
    """Nhận quyết định import cuối cùng từ trang xác nhận và thực hiện import dữ liệu.

    Người dùng cần đã đăng nhập vào trang upload. Hàm đọc lại tệp tạm đã lưu chứa
    dữ liệu JSON, kiểm tra mật khẩu một lần nữa và áp dụng quyết định skip/overwrite
    cho từng truyện trùng tên (được truyền qua các trường form ``decision_<json_id>``).
    """
    # Yêu cầu đã đăng nhập
    if not session.get("upload_authenticated"):
        return redirect(url_for("upload_login"))
    # Kiểm tra mật khẩu gửi kèm để xác nhận import
    UPLOAD_PASSWORD = os.environ.get("UPLOAD_PASSWORD", "secret")
    pw = request.form.get("password", "")
    if pw != UPLOAD_PASSWORD:
        flash("Mật khẩu không hợp lệ.")
        return redirect(url_for("upload"))
    temp_file = request.form.get("temp_file")
    if not temp_file:
        flash("Thiếu file tạm để import.")
        return redirect(url_for("upload"))
    # Thu thập quyết định cho các truyện trùng tên
    decisions: dict[str, str] = {}
    for key, value in request.form.items():
        if key.startswith("decision_"):
            json_id = key.split("decision_", 1)[1]
            decisions[json_id] = value
    # Xử lý tuỳ chọn áp dụng chung: skip_all hoặc overwrite_all
    apply_all = request.form.get("apply_all", "none")
    if apply_all == "skip_all":
        for k in list(decisions.keys()):
            decisions[k] = "skip"
    elif apply_all == "overwrite_all":
        for k in list(decisions.keys()):
            decisions[k] = "overwrite"
    # Đọc lại dữ liệu từ file tạm
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, os.pardir, "data"))
    temp_path = os.path.join(DATA_DIR, temp_file)
    try:
        with open(temp_path, 'r', encoding='utf8') as f:
            data = json.load(f)
    except Exception:
        flash("Không thể đọc dữ liệu import.")
        return redirect(url_for("upload"))
    # Xoá file tạm sau khi đọc
    try:
        os.remove(temp_path)
    except Exception:
        pass
    # Đảm bảo khoá mặc định tồn tại
    for key in ("categories", "stories", "parts", "comments", "videos"):
        if key not in data:
            data[key] = []
    imported_count, overwritten_count, skipped_count = perform_import(data, decisions)
    flash(
        f"Import hoàn tất. Đã import {imported_count} truyện, ghi đè {overwritten_count} và bỏ qua {skipped_count}."
    )
    return redirect(url_for("upload"))


def perform_import(data: dict, decisions: dict[str, str] | None = None) -> tuple[int, int, int]:
    """Nhập dữ liệu từ dict JSON vào cơ sở dữ liệu.

    Hàm này xây dựng lại toàn bộ cấu trúc dữ liệu (thể loại, truyện, chương,
    bình luận và video) dựa trên nội dung của ``data``. Các truyện bị đánh dấu
    ``skip`` trong ``decisions`` sẽ bị bỏ qua. Các truyện có quyết định ``overwrite``
    sẽ xoá truyện hiện có (cùng tiêu đề, không phân biệt chữ hoa/thường) trước khi
    import lại. Các truyện còn lại được import như bình thường.

    Trả về bộ ba ``(imported_count, overwritten_count, skipped_count)`` để hiển thị
    thống kê số lượng truyện được tạo mới, số bị ghi đè và số bị bỏ qua.
    """
    if decisions is None:
        decisions = {}
    imported_count = 0
    overwritten_count = 0
    skipped_count = 0

    # Tạo hoặc lấy các thể loại dựa trên tên (không phân biệt chữ hoa/thường).
    # Thay vì flush từng bản ghi và bắt lỗi, chúng ta thu thập trước các tên thể loại
    # và chỉ tạo mới những tên chưa tồn tại, sau đó flush một lần. Điều này tránh
    # lỗi UNIQUE constraint khi gặp tên trùng.
    category_objs: dict[int, Category] = {}
    existing_categories: dict[str, Category] = {c.name.lower(): c for c in Category.query.all()}
    # Duyệt qua danh sách thể loại trong file JSON
    for cat in data.get("categories", []):
        name = (cat.get("name") or "").strip()
        if not name:
            continue
        lower_name = name.lower()
        # Nếu thể loại đã tồn tại, dùng lại
        if lower_name in existing_categories:
            cobj = existing_categories[lower_name]
        else:
            # Tạo mới thể loại nhưng chưa flush ngay
            cobj = Category(name=name)
            db.session.add(cobj)
            existing_categories[lower_name] = cobj
        category_objs[cat.get("id")] = cobj
    # Flush một lần để tạo id cho các thể loại mới tạo
    try:
        db.session.flush()
    except IntegrityError:
        # Nếu vẫn bị lỗi trùng (trong trường hợp chạy đồng thời), rollback và ánh xạ lại
        db.session.rollback()
        existing_categories = {c.name.lower(): c for c in Category.query.all()}
        for cat in data.get("categories", []):
            name = (cat.get("name") or "").strip()
            if not name:
                continue
            lower_name = name.lower()
            cobj = existing_categories.get(lower_name)
            if not cobj:
                cobj = Category(name=name)
                db.session.add(cobj)
                existing_categories[lower_name] = cobj
            category_objs[cat.get("id")] = cobj
        db.session.flush()
    # Commit thay đổi thể loại trước khi xử lý truyện
    db.session.commit()

    # mapping từ id cũ sang id mới
    story_map: dict[int, int] = {}
    part_map: dict[int, int] = {}

    # Import truyện
    for st in data.get("stories", []):
        old_id = st.get("id")
        title = (st.get("title") or "").strip()
        # Lấy quyết định: có thể là skip, overwrite hoặc None (mặc định là import)
        decision = decisions.get(str(old_id)) or decisions.get(old_id)
        # Bỏ qua truyện nếu được đánh dấu skip
        if decision == "skip":
            skipped_count += 1
            continue
        # Nếu quyết định overwrite, xoá truyện hiện có cùng tên (case-insensitive)
        if decision == "overwrite":
            existing_story = Story.query.filter(func.lower(Story.title) == title.lower()).first()
            if existing_story:
                # Gỡ liên kết thể loại
                existing_story.categories = []
                # Xoá video của các phần
                for part in existing_story.parts:
                    PartVideo.query.filter_by(part_id=part.id).delete()
                # Xoá các phần
                Part.query.filter_by(story_id=existing_story.id).delete()
                # Xoá bình luận
                Comment.query.filter_by(story_id=existing_story.id).delete()
                # Xoá truyện
                db.session.delete(existing_story)
                db.session.commit()
                overwritten_count += 1
        # Tạo truyện mới (luôn tạo mới để tránh xung đột id)
        created_at_str = st.get("created_at")
        if created_at_str:
            try:
                created_at_dt = datetime.fromisoformat(created_at_str)
            except Exception:
                created_at_dt = datetime.utcnow()
        else:
            created_at_dt = datetime.utcnow()
        new_story = Story(
            title=st.get("title"),
            author=st.get("author"),
            story_type=st.get("story_type", "short"),
            created_at=created_at_dt,
            views=st.get("views", 0),
            is_hidden=st.get("is_hidden", False),
            is_completed=st.get("is_completed", False),
            rating_sum=st.get("rating_sum", 0),
            rating_count=st.get("rating_count", 0),
        )
        db.session.add(new_story)
        db.session.flush()
        story_map[old_id] = new_story.id
        imported_count += 1
        # Thiết lập danh sách thể loại
        cat_ids = st.get("categories", [])
        selected_cats = [category_objs[cid] for cid in cat_ids if cid in category_objs]
        new_story.categories = selected_cats
        # category_id gốc chỉ dùng để tham chiếu, đặt theo thể loại đầu tiên nếu có
        if selected_cats:
            new_story.category_id = selected_cats[0].id
        else:
            new_story.category_id = None
        db.session.flush()
    db.session.commit()

    # Import các phần cho mỗi truyện
    for part in data.get("parts", []):
        old_story_id = part.get("story_id")
        # Nếu truyện cũ không được import (do skip) thì bỏ qua phần
        if old_story_id not in story_map:
            continue
        created_at_str = part.get("created_at")
        if created_at_str:
            try:
                part_created = datetime.fromisoformat(created_at_str)
            except Exception:
                part_created = datetime.utcnow()
        else:
            part_created = datetime.utcnow()
        new_part = Part(
            story_id=story_map[old_story_id],
            part_number=part.get("part_number"),
            content=part.get("content", ""),
            created_at=part_created,
        )
        db.session.add(new_part)
        db.session.flush()
        part_map[part.get("id")] = new_part.id
    db.session.commit()

    # Import bình luận (sử dụng id mới của truyện); cập nhật lại url nếu có chứa /story/<id>
    for c in data.get("comments", []):
        old_story_id = c.get("story_id")
        new_story_id = story_map.get(old_story_id)
        if not new_story_id:
            continue  # bỏ qua bình luận của truyện đã skip
        created_at_str = c.get("created_at")
        if created_at_str:
            try:
                c_created = datetime.fromisoformat(created_at_str)
            except Exception:
                c_created = datetime.utcnow()
        else:
            c_created = datetime.utcnow()
        url = c.get("url", "")
        try:
            import re
            url = re.sub(r"/story/(\d+)", lambda m: f"/story/{new_story_id}", url)
        except Exception:
            pass
        new_comment = Comment(
            story_id=new_story_id,
            url=url,
            name=c.get("name"),
            email=c.get("email"),
            content=c.get("content"),
            created_at=c_created,
        )
        db.session.add(new_comment)
    db.session.commit()

    # Import video liên kết cho các phần
    for vid in data.get("videos", []):
        old_part_id = vid.get("part_id")
        new_part_id = part_map.get(old_part_id)
        if not new_part_id:
            continue
        url = vid.get("url")
        if url:
            db.session.add(PartVideo(part_id=new_part_id, url=url))
    db.session.commit()

    # Cập nhật sequence tự tăng khi sử dụng PostgreSQL
    if db.engine.dialect.name == "postgresql":
        with db.engine.connect() as conn:
            conn.execute(text("SELECT setval(pg_get_serial_sequence('categories','id'), COALESCE((SELECT MAX(id) FROM categories), 1), true)"))
            conn.execute(text("SELECT setval(pg_get_serial_sequence('stories','id'), COALESCE((SELECT MAX(id) FROM stories), 1), true)"))
            conn.execute(text("SELECT setval(pg_get_serial_sequence('parts','id'), COALESCE((SELECT MAX(id) FROM parts), 1), true)"))
            conn.execute(text("SELECT setval(pg_get_serial_sequence('comments','id'), COALESCE((SELECT MAX(id) FROM comments), 1), true)"))
            conn.execute(text("SELECT setval(pg_get_serial_sequence('part_videos','id'), COALESCE((SELECT MAX(id) FROM part_videos), 1), true)"))
    return imported_count, overwritten_count, skipped_count



# ------ Delete all stories utility ------
@app.route("/delete_all_stories", methods=["POST"])
def delete_all_stories():
    """Xoá toàn bộ truyện hiện có trong hệ thống.

    Yêu cầu người dùng đã đăng nhập trang quản trị (upload_authenticated). Khi nhận
    yêu cầu, hàm xác nhận hai mật khẩu gửi kèm giống nhau và khớp với
    UPLOAD_PASSWORD. Nếu hợp lệ, hàm xoá tất cả các liên kết
    story_categories, xoá các chương (Part) và xoá các truyện (Story). Thể loại
    (Category) được giữ nguyên. Sau khi hoàn thành sẽ hiển thị thông báo và
    chuyển về trang upload.
    """
    # Kiểm tra quyền truy cập
    if not session.get("upload_authenticated"):
        return redirect(url_for("upload_login"))
    # Mật khẩu upload để xác thực hành động xoá
    UPLOAD_PASSWORD = os.environ.get("UPLOAD_PASSWORD", "secret")
    pw1 = request.form.get("password1", "")
    pw2 = request.form.get("password2", "")
    if not pw1 or not pw2 or pw1 != pw2 or pw1 != UPLOAD_PASSWORD:
        flash("Mật khẩu không hợp lệ hoặc hai mật khẩu không khớp.")
        return redirect(url_for("upload"))
    # Xoá toàn bộ dữ liệu liên quan tới truyện, bao gồm cả video và bình luận
    try:
        # Gỡ quan hệ nhiều-nhiều giữa truyện và thể loại
        db.session.execute(story_categories.delete())
        # Xoá bình luận trước để tránh khoá ngoại tới story
        Comment.query.delete()
        # Xoá liên kết video của các chương
        PartVideo.query.delete()
        # Xoá tất cả các chương
        Part.query.delete()
        # Xoá truyện
        Story.query.delete()
        db.session.commit()
        flash("Đã xoá toàn bộ truyện thành công!")
    except Exception:
        db.session.rollback()
        flash("Đã xảy ra lỗi khi xoá truyện. Vui lòng thử lại.")
    return redirect(url_for("upload"))


@app.route("/replace_prefix_all", methods=["POST"])
def replace_prefix_all():
    """
    Thay thế cụm từ ở đầu dòng đầu tiên của tất cả các chương của tất cả truyện.

    Yêu cầu người dùng đã đăng nhập trang upload và cung cấp mật khẩu hợp lệ.
    Form gửi cần các trường:
      - find_prefix: cụm từ cần tìm ở đầu dòng.
      - replace_prefix: cụm từ dùng để thay thế.
      - password: mật khẩu xác thực.
    Hàm sẽ kiểm tra password, duyệt qua tất cả các chương (Part) và nếu dòng
    đầu tiên của nội dung chương bắt đầu bằng ``find_prefix`` thì thay thế
    bằng ``replace_prefix``. Sau khi hoàn tất, hiển thị thông báo số chương đã
    được cập nhật và chuyển hướng về trang upload.
    """
    # Chỉ cho phép sau khi đã đăng nhập trang upload
    if not session.get("upload_authenticated"):
        return redirect(url_for("upload_login"))
    # Lấy mật khẩu cấu hình
    UPLOAD_PASSWORD = os.environ.get("UPLOAD_PASSWORD", "secret")
    search_prefix = request.form.get("find_prefix", "").strip()
    replacement = request.form.get("replace_prefix", "")
    pw = request.form.get("password", "")
    # Xác thực mật khẩu
    if pw != UPLOAD_PASSWORD:
        flash("Mật khẩu không hợp lệ.")
        return redirect(url_for("upload"))
    if not search_prefix:
        flash("Bạn phải nhập cụm từ cần tìm.")
        return redirect(url_for("upload"))
    replaced_count = 0
    # Duyệt qua tất cả các chương và thay thế nếu phù hợp
    parts = Part.query.all()
    for part in parts:
        lines = part.content.split('\n', 1)
        if lines and lines[0].startswith(search_prefix):
            new_first = replacement + lines[0][len(search_prefix):]
            if len(lines) > 1:
                part.content = new_first + '\n' + lines[1]
            else:
                part.content = new_first
            replaced_count += 1
    if replaced_count > 0:
        db.session.commit()
        flash(f"Đã thay '{search_prefix}' bằng '{replacement}' ở dòng đầu của {replaced_count} phần.")
    else:
        flash("Không tìm thấy cụm từ ở đầu dòng trong bất kỳ phần nào.")
    return redirect(url_for("upload"))



@app.route("/category/<int:category_id>")
def category_view(category_id: int):
    """Hiển thị truyện theo thể loại với phân trang.

    Lấy tất cả truyện thuộc thể loại có id ``category_id`` (kể cả truyện thuộc
    nhiều thể loại), sắp xếp theo ngày đăng mới nhất và phân trang 25 truyện mỗi trang.
    Tham số ``page`` trên URL dùng để chuyển trang. Trả về template list.html để
    hiển thị danh sách.
    """
    category = Category.query.get_or_404(category_id)
    page = request.args.get("page", 1, type=int)
    per_page = 25
    query = (
        Story.query.join(story_categories)
        .filter(
            story_categories.c.category_id == category.id,
            Story.is_hidden == False,
        )
        .order_by(Story.created_at.desc())
    )
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    stories = pagination.items
    categories_group1, categories_group2, categories_group3 = get_category_groups()
    # Chuẩn bị các URL chuyển trang: đầu tiên, cuối cùng, trước và sau
    first_url = url_for("category_view", category_id=category.id, page=1) if pagination.page > 1 else None
    prev_url = url_for("category_view", category_id=category.id, page=pagination.prev_num) if pagination.has_prev else None
    next_url = url_for("category_view", category_id=category.id, page=pagination.next_num) if pagination.has_next else None
    last_url = url_for("category_view", category_id=category.id, page=pagination.pages) if pagination.page < pagination.pages else None
    return render_template(
        "list.html",
        title=f"Thể loại: {category.name}",
        filter_type="category",
        filter_name=category.name,
        stories=stories,
        pagination=pagination,
        first_url=first_url,
        prev_url=prev_url,
        next_url=next_url,
        last_url=last_url,
        categories_group1=categories_group1,
        categories_group2=categories_group2,
        categories_group3=categories_group3,
    )


@app.route("/author/<author>")
def author_view(author: str):
    """Hiển thị danh sách truyện của một tác giả."""
    page = request.args.get("page", 1, type=int)
    per_page = 25
    query = (
        Story.query.filter(Story.author == author, Story.is_hidden == False)
        .order_by(Story.created_at.desc())
    )
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    stories = pagination.items
    categories_group1, categories_group2, categories_group3 = get_category_groups()
    # chuẩn bị liên kết chuyển trang cho template: đầu, cuối, trước, sau
    first_url = url_for("author_view", author=author, page=1) if pagination.page > 1 else None
    prev_url = url_for("author_view", author=author, page=pagination.prev_num) if pagination.has_prev else None
    next_url = url_for("author_view", author=author, page=pagination.next_num) if pagination.has_next else None
    last_url = url_for("author_view", author=author, page=pagination.pages) if pagination.page < pagination.pages else None
    return render_template(
        "list.html",
        title=f"Tác giả: {author}",
        filter_type="author",
        filter_name=author,
        stories=stories,
        pagination=pagination,
        first_url=first_url,
        prev_url=prev_url,
        next_url=next_url,
        last_url=last_url,
        categories_group1=categories_group1,
        categories_group2=categories_group2,
        categories_group3=categories_group3,
    )


@app.route("/type/<story_type>")
def type_view(story_type: str):
    """Hiển thị danh sách truyện theo loại ngắn/dài."""
    if story_type not in ("short", "long"):
        return page_not_found(404)
    page = request.args.get("page", 1, type=int)
    per_page = 25
    query = (
        Story.query.filter_by(story_type=story_type, is_hidden=False)
        .order_by(Story.created_at.desc())
    )
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    stories = pagination.items
    categories_group1, categories_group2, categories_group3 = get_category_groups()
    # xác định tiêu đề tiếng Việt
    title_vi = "Truyện Ngắn" if story_type == "short" else "Truyện Dài"
    first_url = url_for("type_view", story_type=story_type, page=1) if pagination.page > 1 else None
    prev_url = url_for("type_view", story_type=story_type, page=pagination.prev_num) if pagination.has_prev else None
    next_url = url_for("type_view", story_type=story_type, page=pagination.next_num) if pagination.has_next else None
    last_url = url_for("type_view", story_type=story_type, page=pagination.pages) if pagination.page < pagination.pages else None
    return render_template(
        "list.html",
        title=title_vi,
        filter_type="type",
        filter_name=story_type,
        stories=stories,
        pagination=pagination,
        first_url=first_url,
        prev_url=prev_url,
        next_url=next_url,
        last_url=last_url,
        categories_group1=categories_group1,
        categories_group2=categories_group2,
        categories_group3=categories_group3,
    )


@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    limit_param = request.args.get("limit", "3")
    
    # Xử lý limit
    if limit_param == 'all':
        max_snippets = None
    else:
        try:
            max_snippets = int(limit_param)
            if max_snippets <= 0:
                max_snippets = 3
        except ValueError:
            max_snippets = 3

    stories = []
    all_snippets = {}
    if query:
        like_pattern = f"%{query}%"
        stories = (
            Story.query.outerjoin(Part)
            .filter(
                (Story.title.ilike(like_pattern))
                | (Story.author.ilike(like_pattern))
                | (Part.content.ilike(like_pattern))
            )
            .filter(Story.is_hidden == False)
            .distinct()
            .order_by(Story.created_at.desc())
            .all()
        )
        keywords = [kw.lower() for kw in query.split() if kw.strip()]
        for story in stories:
            parts = Part.query.filter_by(story_id=story.id).order_by(Part.part_number).all()
            snippets = []
            for part in parts:
                content_lower = part.content.lower()
                idx = content_lower.find(query.lower())
                if idx == -1 and keywords:
                    for kw in keywords:
                        idx = content_lower.find(kw)
                        if idx != -1:
                            break
                if idx != -1:
                    start = max(0, idx - 100)
                    end = min(len(part.content), idx + len(query) + 150)
                    raw_snippet = part.content[start:end]
                    # Highlight keywords
                    highlighted = raw_snippet
                    for kw in keywords:
                        highlighted = re.sub(rf'({re.escape(kw)})', r'<span class="highlight">\1</span>', highlighted, flags=re.IGNORECASE)
                    snippets.append((part.part_number, highlighted))
                    if max_snippets is not None and len(snippets) >= max_snippets:
                        break
            if snippets:
                all_snippets[story.id] = snippets
            else:
                # fallback: lấy 200 ký tự đầu của phần đầu tiên
                first_part = parts[0] if parts else None
                if first_part:
                    raw = first_part.content[:200]
                    if len(first_part.content) > 200:
                        raw = raw.rsplit(' ', 1)[0] + "..."
                    all_snippets[story.id] = [(1, raw.replace('\n', ' '))]
    
    categories_group1, categories_group2, categories_group3 = get_category_groups()
    return render_template(
        "search.html",
        query=query,
        stories=stories,
        all_snippets=all_snippets,
        limit=limit_param,
        categories_group1=categories_group1,
        categories_group2=categories_group2,
        categories_group3=categories_group3,
    )

# Đánh giá truyện: nhận giá trị rating 1-5 qua POST và cập nhật tổng/số lượng
@app.route("/rate/<int:story_id>", methods=["POST"])
def rate_story(story_id: int):
    """Xử lý đánh giá truyện. Người đọc gửi rating từ 1 tới 5."""
    story = Story.query.get_or_404(story_id)
    try:
        rating_value = int(request.form.get("rating", 0))
    except ValueError:
        rating_value = 0
    # chỉ chấp nhận giá trị từ 1 đến 5
    if 1 <= rating_value <= 5:
        story.rating_sum = (story.rating_sum or 0) + rating_value
        story.rating_count = (story.rating_count or 0) + 1
        db.session.commit()
    return redirect(url_for("story_detail", story_id=story_id))


@app.route("/add-category", methods=["GET", "POST"])
def add_category():
    """
    Trang quản lý thể loại.
    Cho phép tạo mới, cập nhật và xoá thể loại.
    Tất cả hành động đều yêu cầu mật khẩu upload giống như trang upload truyện.
    """
    categories = get_sorted_categories()
    if request.method == "POST":
        UPLOAD_PASSWORD = os.environ.get("UPLOAD_PASSWORD", "secret")
        password = request.form.get("password", "")
        action = request.form.get("action", "create")
        category_id = request.form.get("category_id")
        name = request.form.get("name", "").strip()
        # hỗ trợ nhập nhiều tên thể loại cùng lúc (danh sách names)
        names = request.form.getlist("names")
        # kiểm tra mật khẩu
        if password != UPLOAD_PASSWORD:
            return render_template(
                "add_category.html",
                error="Password sai.",
                categories=categories,
            )
        # xử lý xoá
        if action == "delete":
            if category_id:
                cat = Category.query.get(int(category_id))
                if cat:
                    # nếu thể loại đang được dùng, không cho xoá
                    # nếu thể loại liên kết với truyện qua quan hệ một‑nhiều hoặc nhiều‑nhiều thì không xoá
                    if cat.stories or getattr(cat, "stories_multi", []):
                        return render_template(
                            "add_category.html",
                            error="Không thể xoá thể loại đang được sử dụng.",
                            categories=categories,
                        )
                    db.session.delete(cat)
                    db.session.commit()
                    return redirect(url_for("add_category"))
        # xử lý cập nhật
        elif action == "update":
            if category_id and name:
                cat = Category.query.get(int(category_id))
                if cat:
                    existing = Category.query.filter_by(name=name).first()
                    if existing and existing.id != cat.id:
                        return render_template(
                            "add_category.html",
                            error="Tên thể loại đã tồn tại.",
                            categories=categories,
                        )
                    cat.name = name
                    db.session.commit()
                    return redirect(url_for("add_category"))
        # xử lý tạo mới
        else:
            # ưu tiên danh sách nhiều tên thể loại nếu được gửi từ form
            # nếu có ít nhất một tên trong danh sách, xử lý từng tên
            if names and any(n.strip() for n in names):
                added_any = False
                for nm in names:
                    nm_strip = nm.strip()
                    if not nm_strip:
                        continue
                    existing = Category.query.filter_by(name=nm_strip).first()
                    if existing is None:
                        db.session.add(Category(name=nm_strip))
                        added_any = True
                if added_any:
                    db.session.commit()
                    return redirect(url_for("add_category"))
                else:
                    # tất cả các thể loại đã tồn tại
                    return render_template(
                        "add_category.html",
                        error="Tất cả các thể loại này đã tồn tại.",
                        categories=categories,
                    )
            # nếu không có danh sách, fallback dùng một tên
            elif name:
                existing = Category.query.filter_by(name=name).first()
                if existing is None:
                    db.session.add(Category(name=name))
                    db.session.commit()
                    return redirect(url_for("add_category"))
                else:
                    return render_template(
                        "add_category.html",
                        error="Thể loại đã tồn tại.",
                        categories=categories,
                    )
            else:
                return render_template(
                    "add_category.html",
                    error="Vui lòng nhập tên thể loại.",
                    categories=categories,
                )
        # nếu không đáp ứng điều kiện nào, reload danh sách
        return render_template(
            "add_category.html",
            categories=categories,
        )
    # phương thức GET
    return render_template(
        "add_category.html",
        categories=categories,
    )


# -----------------------------------------------------------------------------
# API endpoints
#
# Trả về danh sách truyện thuộc một thể loại dưới dạng JSON. Sử dụng khi người
# dùng chọn nhiều thể loại trên trang chủ để hiển thị thêm truyện theo từng
# thể loại. Kết quả trả về là danh sách JSON gồm các trường id, title,
# author, categories (tên), rating trung bình, số lượt đánh giá, trạng thái
# hoàn thành, số chương và đoạn trích của chương đầu tiên.
@app.route("/api/category_stories/<int:category_id>")
def api_category_stories(category_id: int):
    """Trả về danh sách truyện của thể loại ở dạng JSON, hỗ trợ phân trang.

    Endpoint này phục vụ tính năng chọn nhiều thể loại ở trang chủ. Nó nhận
    tham số ``page`` (số trang bắt đầu từ 1) và ``limit`` (số mục mỗi trang,
    mặc định 25) trong query string. Kết quả trả về bao gồm danh sách
    truyện theo trang, cùng thông tin phân trang (số trang, có trang trước/sau).

    Mỗi mục trong ``stories`` chứa id, title, author, categories (danh sách
    tên), rating trung bình, số lượt đánh giá, cờ hoàn thành, số chương
    và trích đoạn của chương đầu tiên.
    """
    page = request.args.get("page", 1, type=int)
    limit = request.args.get("limit", 25, type=int)
    if limit <= 0:
        limit = 25
    # Lấy thể loại, trả 404 nếu không tồn tại
    category = Category.query.get_or_404(category_id)
    # Truy vấn truyện theo thể loại, sắp xếp mới nhất và không ẩn
    query = (
        Story.query.join(story_categories)
        .filter(
            story_categories.c.category_id == category.id,
            Story.is_hidden == False,
        )
        .order_by(Story.created_at.desc())
    )
    pagination = query.paginate(page=page, per_page=limit, error_out=False)
    stories_items = pagination.items
    result = []
    for st in stories_items:
        snippet = ""
        if st.parts:
            first_part = st.parts[0]
            snippet = first_part.content[:200]
            if len(first_part.content) > 200:
                snippet = snippet.rsplit(" ", 1)[0] + "..."
            snippet = snippet.replace("\n", " ")
        avg = (st.rating_sum / st.rating_count) if st.rating_count else 0
        result.append(
            {
                "id": st.id,
                "title": st.title,
                "author": st.author,
                "categories": [c.name for c in st.categories],
                "rating": avg,
                "rating_count": st.rating_count,
                "is_completed": st.is_completed,
                "part_count": len(st.parts),
                "snippet": snippet,
            }
        )
    return jsonify({
        "page": pagination.page,
        "total_pages": pagination.pages,
        "has_next": pagination.has_next,
        "has_prev": pagination.has_prev,
        "stories": result,
    })


@app.errorhandler(404)
def page_not_found(e):
    """ Trang lỗi 404 tuỳ chỉnh."""
    return render_template("404.html"), 404


@app.route("/admin/views")
def views_analytics():
    """Trang Xem Views 7 ngày gần nhất"""
    if 'upload_authenticated' not in session:
        flash("Vui lòng đăng nhập admin.", "danger")
        return redirect(url_for('upload_login'))

    end_date = date.today()
    start_date = end_date - timedelta(days=6)
    dates = [start_date + timedelta(days=i) for i in range(7)]

    stories = Story.query.all()
    story_data = []

    for story in stories:
        daily_views = DailyView.query.filter(
            DailyView.story_id == story.id,
            DailyView.date.between(start_date, end_date)
        ).all()

        daily_map = {d: 0 for d in dates}
        for dv in daily_views:
            daily_map[dv.date] = dv.views

        total_week = sum(daily_map.values())
        total_desktop = sum(dv.views_desktop or 0 for dv in daily_views)
        total_mobile = sum(dv.views_mobile or 0 for dv in daily_views)

        prev_start = start_date - timedelta(days=7)
        prev_views = DailyView.query.filter(
            DailyView.story_id == story.id,
            DailyView.date.between(prev_start, prev_start + timedelta(days=6))
        ).all()
        prev_total = sum(dv.views for dv in prev_views)
        change_pct = round(((total_week - prev_total) / prev_total * 100), 1) if prev_total > 0 else None

        story_data.append({
            'story': story,
            'daily': daily_map,
            'total_week': total_week,
            'total_desktop': total_desktop,
            'total_mobile': total_mobile,
            'change_pct': change_pct,
            'created_at': story.created_at
        })

    sort = request.args.get('sort', 'total')
    if sort == 'name':
        story_data.sort(key=lambda x: x['story'].title.lower())
    elif sort == 'created':
        story_data.sort(key=lambda x: x['created_at'], reverse=True)
    else:
        story_data.sort(key=lambda x: x['total_week'], reverse=True)

    return render_template('views_analytics.html',
                           story_data=story_data,
                           dates=dates,
                           start_date=start_date,
                           end_date=end_date)


@app.route("/admin/hears")
def hears_analytics():
    """Trang Xem Lượt nghe 7 ngày gần nhất"""
    if 'upload_authenticated' not in session:
        flash("Vui lòng đăng nhập admin.", "danger")
        return redirect(url_for('upload_login'))

    end_date = date.today()
    start_date = end_date - timedelta(days=6)
    dates = [start_date + timedelta(days=i) for i in range(7)]

    stories = Story.query.all()
    story_data = []

    for story in stories:
        daily_listens = DailyListen.query.filter(
            DailyListen.story_id == story.id,
            DailyListen.date.between(start_date, end_date)
        ).all()

        daily_map = {d: 0 for d in dates}
        for dl in daily_listens:
            daily_map[dl.date] = dl.listens

        total_week = sum(daily_map.values())
        total_desktop = sum(dl.listens_desktop or 0 for dl in daily_listens)
        total_mobile = sum(dl.listens_mobile or 0 for dl in daily_listens)

        prev_start = start_date - timedelta(days=7)
        prev_listens = DailyListen.query.filter(
            DailyListen.story_id == story.id,
            DailyListen.date.between(prev_start, prev_start + timedelta(days=6))
        ).all()
        prev_total = sum(dl.listens for dl in prev_listens)
        change_pct = round(((total_week - prev_total) / prev_total * 100), 1) if prev_total > 0 else None

        story_data.append({
            'story': story,
            'daily': daily_map,
            'total_week': total_week,
            'total_desktop': total_desktop,
            'total_mobile': total_mobile,
            'change_pct': change_pct,
            'created_at': story.created_at
        })

    sort = request.args.get('sort', 'total')
    if sort == 'name':
        story_data.sort(key=lambda x: x['story'].title.lower())
    elif sort == 'created':
        story_data.sort(key=lambda x: x['created_at'], reverse=True)
    else:
        story_data.sort(key=lambda x: x['total_week'], reverse=True)

    return render_template('hears_analytics.html',
                           story_data=story_data,
                           dates=dates,
                           start_date=start_date,
                           end_date=end_date)

@app.route("/api/delete_chunk/<int:story_id>/<int:part_number>/<int:chunk_index>")
def delete_chunk(story_id: int, part_number: int, chunk_index: int):
    """Xóa file chunk mp3 sau khi browser play xong"""
    try:
        audio_dir = Path("static/audio") / str(story_id)
        chunk_path = audio_dir / f"{part_number}_chunk_{chunk_index:04d}.mp3"
        
        if chunk_path.exists():
            os.remove(chunk_path)
            print(f"[DELETE] 🗑️ Đã xóa chunk {chunk_index} của phần {part_number} (truyện {story_id})")
            return jsonify({"status": "deleted"})
        else:
            return jsonify({"status": "not_found"})
    except Exception as e:
        print(f"[DELETE] ❌ Lỗi khi xóa chunk: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

def cleanup_old_audio():
    """Xóa các file chunk mp3 cũ hơn 1 ngày (24 giờ)"""
    while True:
        try:
            audio_root = Path("static/audio")
            if not audio_root.exists():
                time.sleep(300)
                continue

            now = time.time()
            deleted = 0
            for story_dir in audio_root.iterdir():
                if not story_dir.is_dir():
                    continue
                for mp3_file in story_dir.glob("*.mp3"):
                    # 86400 giây = 1 ngày
                    if mp3_file.stat().st_mtime < now - 86400:
                        try:
                            mp3_file.unlink()
                            deleted += 1
                            print(f"[CLEANUP] 🗑️ Đã xóa file cũ hơn 1 ngày: {mp3_file.name}")
                        except Exception as e:
                            print(f"[CLEANUP] Lỗi xóa {mp3_file.name}: {e}")
            if deleted > 0:
                print(f"[CLEANUP] Đã xóa {deleted} file mp3 cũ hơn 1 ngày")
        except Exception as e:
            print(f"[CLEANUP] Lỗi: {e}")
        time.sleep(300)   # quét mỗi 5 phút

# Khởi động background cleaner
Thread(target=cleanup_old_audio, daemon=True).start()
print("[CLEANUP] ✅ Background cleaner đã khởi động (xóa file mp3 cũ hơn 1 ngày)")

@app.route("/api/category/<int:category_id>")
def api_category(category_id: int):
    """API cho checkbox filter thể loại - Trả về HẾT tất cả truyện, sắp xếp theo tên A-Z"""
    category = Category.query.get_or_404(category_id)

    # Lấy hết truyện thuộc thể loại này, sắp xếp theo tên truyện (A → Z)
    stories = Story.query.filter(Story.categories.any(id=category_id))\
        .order_by(Story.title.asc())\
        .all()

    return jsonify({
        "stories": [{"id": s.id, "title": s.title} for s in stories]
    })

@app.route("/api/type/<string:story_type>")
def api_type(story_type: str):
    """API cho Truyện Dài / Truyện Ngắn - trả về hết, sắp xếp theo tên A-Z"""
    if story_type not in ['long', 'short']:
        return jsonify({"stories": []})

    stories = Story.query.filter_by(story_type=story_type)\
        .order_by(Story.title.asc())\
        .all()

    return jsonify({
        "stories": [{"id": s.id, "title": s.title} for s in stories]
    })

@app.route("/delete_all_audio", methods=["POST"])
def delete_all_audio():
    """Xóa tất cả file .mp3 trong thư mục static/audio ngay lập tức."""
    # Kiểm tra đăng nhập admin
    if not session.get("upload_authenticated"):
        return redirect(url_for("upload_login"))

    # Xác thực mật khẩu
    UPLOAD_PASSWORD = os.environ.get("UPLOAD_PASSWORD", "secret")
    pw = request.form.get("password", "")
    if pw != UPLOAD_PASSWORD:
        flash("Mật khẩu không hợp lệ.")
        return redirect(url_for("upload"))

    audio_root = Path("static/audio")
    if not audio_root.exists():
        flash("Thư mục audio không tồn tại.")
        return redirect(url_for("upload"))

    deleted_count = 0
    # Duyệt đệ quy tìm tất cả file .mp3
    for mp3_file in audio_root.rglob("*.mp3"):
        try:
            mp3_file.unlink()
            deleted_count += 1
        except Exception as e:
            print(f"Lỗi xóa {mp3_file}: {e}")

    flash(f"Đã xóa {deleted_count} file MP3.")
    return redirect(url_for("upload"))

@app.route("/api/suggest")
def suggest():
    """Trả về danh sách gợi ý: truyện và tác giả."""
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])
    pattern = f"%{query}%"
    # Tìm truyện theo tên
    stories = Story.query.filter(
        Story.is_hidden == False,
        Story.title.ilike(pattern)
    ).limit(5).all()
    # Tìm tác giả (duy nhất)
    authors = db.session.query(Story.author).filter(
        Story.is_hidden == False,
        Story.author.isnot(None),
        Story.author.ilike(pattern)
    ).distinct().limit(5).all()
    suggestions = []
    for s in stories:
        suggestions.append({
            "type": "story",
            "id": s.id,
            "title": s.title,
            "author": s.author
        })
    for a in authors:
        if a[0]:
            suggestions.append({
                "type": "author",
                "name": a[0]
            })
    return jsonify(suggestions)

@app.route("/admin/announcements", methods=["GET", "POST"])
def admin_announcements():
    if not session.get("upload_authenticated"):
        return redirect(url_for("upload_login"))
    UPLOAD_PASSWORD = os.environ.get("UPLOAD_PASSWORD", "secret")
    
    if request.method == "POST":
        password = request.form.get("password", "")
        if password != UPLOAD_PASSWORD:
            flash("Mật khẩu không hợp lệ.")
            return redirect(url_for("admin_announcements"))
        action = request.form.get("action")
        if action == "create":
            content = request.form.get("content", "").strip()
            device_type = request.form.get("device_type", "both")
            if content:
                # Tắt các thông báo có cùng device_type hoặc both nếu cần
                if device_type == 'both':
                    # Tắt tất cả thông báo both, desktop, mobile (vì both ảnh hưởng cả hai)
                    Announcement.query.update({Announcement.is_active: False})
                elif device_type == 'desktop':
                    # Tắt các thông báo desktop và both
                    Announcement.query.filter(
                        (Announcement.device_type == 'desktop') | (Announcement.device_type == 'both')
                    ).update({Announcement.is_active: False}, synchronize_session=False)
                elif device_type == 'mobile':
                    Announcement.query.filter(
                        (Announcement.device_type == 'mobile') | (Announcement.device_type == 'both')
                    ).update({Announcement.is_active: False}, synchronize_session=False)
                ann = Announcement(content=content, is_active=True, device_type=device_type)
                db.session.add(ann)
                db.session.commit()
                flash("Đã tạo thông báo mới và kích hoạt.")
            else:
                flash("Nội dung không được để trống.")
        elif action == "toggle":
            ann_id = request.form.get("ann_id")
            ann = Announcement.query.get(ann_id)
            if ann:
                if not ann.is_active:
                    # Bật thông báo này, tắt các thông báo xung đột
                    if ann.device_type == 'both':
                        Announcement.query.update({Announcement.is_active: False})
                    elif ann.device_type == 'desktop':
                        Announcement.query.filter(
                            (Announcement.device_type == 'desktop') | (Announcement.device_type == 'both')
                        ).update({Announcement.is_active: False}, synchronize_session=False)
                    elif ann.device_type == 'mobile':
                        Announcement.query.filter(
                            (Announcement.device_type == 'mobile') | (Announcement.device_type == 'both')
                        ).update({Announcement.is_active: False}, synchronize_session=False)
                    ann.is_active = True
                else:
                    ann.is_active = False
                db.session.commit()
                flash(f"Đã {'bật' if ann.is_active else 'tắt'} thông báo.")
        elif action == "delete":
            ann_id = request.form.get("ann_id")
            ann = Announcement.query.get(ann_id)
            if ann:
                db.session.delete(ann)
                db.session.commit()
                flash("Đã xóa thông báo.")
        return redirect(url_for("admin_announcements"))
    
    announcements = Announcement.query.order_by(Announcement.created_at.desc()).all()
    return render_template("admin_announcements.html", announcements=announcements)

@app.route("/admin/comments/manage", methods=["POST"])
def manage_comments():
    if not session.get("upload_authenticated"):
        return redirect(url_for("upload_login"))
    UPLOAD_PASSWORD = os.environ.get("UPLOAD_PASSWORD", "secret")
    password = request.form.get("password", "")
    if password != UPLOAD_PASSWORD:
        flash("Mật khẩu không hợp lệ.")
        return redirect(url_for("view_all_comments"))
    
    action = request.form.get("action")
    comment_ids = request.form.getlist("comment_ids")
    if not comment_ids:
        flash("Chưa chọn bình luận nào.")
        return redirect(url_for("view_all_comments"))
    
    if action == "hide":
        Comment.query.filter(Comment.id.in_(comment_ids)).update({Comment.is_hidden: True}, synchronize_session=False)
        flash(f"Đã ẩn {len(comment_ids)} bình luận.")
    elif action == "show":
        Comment.query.filter(Comment.id.in_(comment_ids)).update({Comment.is_hidden: False}, synchronize_session=False)
        flash(f"Đã hiện {len(comment_ids)} bình luận.")
    elif action == "delete":
        Comment.query.filter(Comment.id.in_(comment_ids)).delete(synchronize_session=False)
        flash(f"Đã xóa {len(comment_ids)} bình luận.")
    else:
        flash("Hành động không hợp lệ.")
    db.session.commit()
    return redirect(url_for("view_all_comments"))

@app.route("/admin/announcements/update_device", methods=["POST"])
def update_announcement_device():
    if not session.get("upload_authenticated"):
        return redirect(url_for("upload_login"))
    UPLOAD_PASSWORD = os.environ.get("UPLOAD_PASSWORD", "secret")
    pw = request.form.get("password", "")
    if pw != UPLOAD_PASSWORD:
        flash("Mật khẩu không hợp lệ.")
        return redirect(url_for("admin_announcements"))
    ann_id = request.form.get("ann_id")
    device_type = request.form.get("device_type")
    if device_type not in ['both', 'desktop', 'mobile']:
        device_type = 'both'
    ann = Announcement.query.get(ann_id)
    if ann:
        ann.device_type = device_type
        db.session.commit()
        flash("Đã cập nhật thiết bị hiển thị.")
    return redirect(url_for("admin_announcements"))

@app.route("/clear_history")
def clear_history():
    session_id = get_user_session_id()
    ReadingHistory.query.filter_by(session_id=session_id).delete()
    db.session.commit()
    flash("Đã xóa lịch sử đọc.")
    return redirect(url_for('index'))

@app.route("/remove_history/<int:history_id>")
def remove_history_item(history_id: int):
    session_id = get_user_session_id()
    history = ReadingHistory.query.filter_by(id=history_id, session_id=session_id).first_or_404()
    db.session.delete(history)
    db.session.commit()
    flash("Đã xóa một mục khỏi lịch sử.")
    return redirect(url_for('index'))

@app.route("/follow/<int:story_id>", methods=["POST"])
def follow_story(story_id: int):
    story = Story.query.get_or_404(story_id)
    email = request.form.get("email", "").strip()
    part_number = request.form.get("part_number", type=int)
    if not email:
        flash("Vui lòng nhập email.")
        return redirect(url_for("story_detail", story_id=story_id))
    
    existing = Follow.query.filter_by(story_id=story_id, email=email).first()
    if existing:
        flash("Bạn đã theo dõi truyện này rồi.")
    else:
        follow = Follow(story_id=story_id, email=email)
        db.session.add(follow)
        db.session.commit()
        send_follow_confirmation(story, email)
        flash("Đã đăng ký theo dõi thành công. Bạn sẽ nhận được email khi có phần mới.")
        
        # Tạo hoặc cập nhật lịch sử đọc cho email này
        session['reader_email'] = email
        session_id = get_user_session_id()
        history = ReadingHistory.query.filter_by(email=email, story_id=story.id).first()
        if history:
            if part_number:
                history.part_number = part_number
                history.updated_at = datetime.utcnow()
            history.session_id = session_id
        else:
            if part_number:
                history = ReadingHistory(session_id=session_id, email=email, story_id=story.id, part_number=part_number)
                db.session.add(history)
        db.session.commit()
    
    return redirect(url_for("story_detail", story_id=story_id, part=part_number) if part_number else url_for("story_detail", story_id=story_id))

@app.route("/unfollow/<int:story_id>", methods=["POST"])
def unfollow_story(story_id: int):
    email = request.form.get("email", "").strip()
    if not email:
        flash("Email không hợp lệ.")
        return redirect(url_for("story_detail", story_id=story_id))
    
    follow = Follow.query.filter_by(story_id=story_id, email=email).first()
    if follow:
        story = follow.story
        db.session.delete(follow)
        db.session.commit()
        send_unfollow_confirmation(story, email)   # Gửi email xác nhận hủy
        flash("Đã hủy theo dõi truyện.")
    else:
        flash("Bạn chưa theo dõi truyện này.")
    return redirect(url_for("story_detail", story_id=story_id))

@app.route("/admin/follows")
def admin_follows():
    if not session.get("upload_authenticated"):
        flash("Vui lòng đăng nhập admin.", "danger")
        return redirect(url_for('upload_login'))
    
    # Subquery lấy max updated_at cho mỗi (email, story_id)
    from sqlalchemy import and_
    subq = db.session.query(
        ReadingHistory.email,
        ReadingHistory.story_id,
        func.max(ReadingHistory.updated_at).label('max_updated')
    ).group_by(ReadingHistory.email, ReadingHistory.story_id).subquery()
    
    # Lấy phần đọc gần nhất
    latest_read = db.session.query(
        ReadingHistory.email,
        ReadingHistory.story_id,
        ReadingHistory.part_number
    ).join(
        subq,
        and_(
            ReadingHistory.email == subq.c.email,
            ReadingHistory.story_id == subq.c.story_id,
            ReadingHistory.updated_at == subq.c.max_updated
        )
    ).all()
    
    read_map = {(r.email, r.story_id): r.part_number for r in latest_read}
    
    # Lấy danh sách follow
    follows = db.session.query(Follow, Story).join(Story, Follow.story_id == Story.id).order_by(Follow.created_at.desc()).all()
    follow_data = []
    for follow, story in follows:
        follow_data.append({
            'follow': follow,
            'story': story,
            'last_part': read_map.get((follow.email, story.id), 'Chưa đọc')
        })
    
    return render_template("admin_follows.html", follows=follow_data)

@app.route("/my_follows", methods=["GET", "POST"])
def my_follows():
    stories = []
    email = None
    history_map = {}
    receive_new_story = False

    if request.method == "POST":
        action = request.form.get("action", "view")
        email = request.form.get("email", "").strip()
        
        if action == "update_notification" and email:
            # Cập nhật đăng ký nhận thông báo truyện mới
            notify = request.form.get("notify_new_story") == "on"
            if notify:
                sub = NewStorySubscription.query.filter_by(email=email).first()
                if not sub:
                    sub = NewStorySubscription(email=email)
                    db.session.add(sub)
                    db.session.commit()
                    send_new_story_subscription_confirmation(email)
                    flash("Đã đăng ký nhận thông báo truyện mới. Kiểm tra email để xác nhận.")
                else:
                    flash("Bạn đã đăng ký nhận thông báo truyện mới trước đó.")
            else:
                # Hủy đăng ký
                deleted = NewStorySubscription.query.filter_by(email=email).delete()
                db.session.commit()
                if deleted:
                    send_new_story_unsubscription_confirmation(email)
                    flash("Đã hủy đăng ký nhận thông báo truyện mới.")
                else:
                    flash("Bạn chưa đăng ký nhận thông báo truyện mới.")
            # Sau khi cập nhật, hiển thị lại danh sách theo dõi (nếu có)
            # Lấy lại stories như bên dưới
        elif action == "view" and email:
            # Lưu email vào session để dùng khi đọc truyện
            session['reader_email'] = email
            # Lấy tất cả follow của email này
            follows = Follow.query.filter_by(email=email).all()
            story_ids = [f.story_id for f in follows]
            stories = Story.query.filter(Story.id.in_(story_ids), Story.is_hidden == False).all()
            
            # Lấy lịch sử đọc theo email
            history_records = ReadingHistory.query.filter_by(email=email).all()
            for rec in history_records:
                if rec.story_id not in history_map or rec.updated_at > history_map[rec.story_id][1]:
                    history_map[rec.story_id] = (rec.part_number, rec.updated_at)
            
            # Kiểm tra email có đăng ký nhận thông báo truyện mới không
            sub = NewStorySubscription.query.filter_by(email=email).first()
            receive_new_story = sub is not None
        else:
            flash("Vui lòng nhập email.")

    # Gắn last_read_part
    for story in stories:
        last_part = history_map.get(story.id, (1, None))[0]
        story.last_read_part = last_part

    return render_template("my_follows.html", stories=stories, email=email, receive_new_story=receive_new_story)

@app.route("/skip_comments", methods=["POST"])
def skip_comments():
    # Mark comments as seen for stories
    latest_comment_time = db.session.query(func.max(Comment.created_at)).scalar()
    if latest_comment_time:
        session['last_comment_seen_at_stories'] = latest_comment_time.isoformat()
    return redirect(url_for("upload"))

@app.context_processor
def inject_comment_notifications():
    def get_comment_notifications():
        latest_comment_time = db.session.query(func.max(Comment.created_at)).scalar()
        show = False
        commented = []
        if latest_comment_time:
            last_seen_str = session.get('last_comment_seen_at_stories')
            try:
                last_seen = datetime.fromisoformat(last_seen_str) if last_seen_str else None
            except Exception:
                last_seen = None
            if last_seen:
                # Lấy story có bình luận mới hơn last_seen, kèm theo bình luận mới nhất
                subq = db.session.query(
                    Comment.story_id,
                    func.max(Comment.created_at).label('last_comment_time')
                ).filter(Comment.created_at > last_seen).group_by(Comment.story_id).subquery()
                stories = db.session.query(Story).join(subq, Story.id == subq.c.story_id).filter(Story.is_hidden == False).all()
                # Với mỗi story, lấy bình luận mới nhất
                for story in stories:
                    latest_comment = Comment.query.filter_by(story_id=story.id).order_by(Comment.created_at.desc()).first()
                    if latest_comment:
                        commented.append((story, latest_comment))
            else:
                # Lần đầu, lấy tất cả story có bình luận, kèm bình luận mới nhất
                stories = db.session.query(Story).join(Comment).filter(Story.is_hidden == False).distinct().all()
                for story in stories:
                    latest_comment = Comment.query.filter_by(story_id=story.id).order_by(Comment.created_at.desc()).first()
                    if latest_comment:
                        commented.append((story, latest_comment))
            if commented:
                show = True
        return show, commented
    return {"get_comment_notifications": get_comment_notifications}

@app.context_processor
def inject_announcement():
    from flask import request
    user_agent = request.headers.get('User-Agent', '').lower()
    is_mobile = 'mobile' in user_agent or 'android' in user_agent or 'iphone' in user_agent or 'ipad' in user_agent
    
    if is_mobile:
        # Ưu tiên thông báo có device_type = 'mobile', nếu không có thì lấy 'both'
        announcement = Announcement.query.filter(
            Announcement.is_active == True,
            (Announcement.device_type == 'mobile') | (Announcement.device_type == 'both')
        ).order_by(
            db.case({'mobile': 1, 'both': 2}, value=Announcement.device_type)
        ).first()
    else:
        # Desktop: ưu tiên 'desktop', sau đó 'both'
        announcement = Announcement.query.filter(
            Announcement.is_active == True,
            (Announcement.device_type == 'desktop') | (Announcement.device_type == 'both')
        ).order_by(
            db.case({'desktop': 1, 'both': 2}, value=Announcement.device_type)
        ).first()
    return dict(active_announcement=announcement)

@app.route("/view_all_comments")
def view_all_comments():
    if 'upload_authenticated' not in session:
        flash("Vui lòng đăng nhập admin.", "danger")
        return redirect(url_for('upload_login'))
    comments = db.session.query(Comment, Story).join(Story, Comment.story_id == Story.id).order_by(Comment.created_at.desc()).all()
    return render_template("comments_list.html", comments=comments)

# Xóa một follow cụ thể
@app.route("/admin/follows/delete/<int:follow_id>", methods=["POST"])
def delete_follow(follow_id: int):
    if not session.get("upload_authenticated"):
        return redirect(url_for("upload_login"))
    UPLOAD_PASSWORD = os.environ.get("UPLOAD_PASSWORD", "secret")
    pw = request.form.get("password", "")
    if pw != UPLOAD_PASSWORD:
        flash("Mật khẩu không hợp lệ.")
        return redirect(url_for("admin_follows"))
    follow = Follow.query.get_or_404(follow_id)
    story_title = follow.story.title
    email = follow.email
    db.session.delete(follow)
    db.session.commit()
    flash(f"Đã xóa theo dõi của {email} đối với truyện '{story_title}'.")
    return redirect(url_for("admin_follows"))

# Export danh sách follow ra CSV
@app.route("/admin/follows/export")
def export_follows_csv():
    if not session.get("upload_authenticated"):
        return redirect(url_for("upload_login"))
    import csv
    from io import StringIO
    follows = db.session.query(Follow, Story).join(Story, Follow.story_id == Story.id).order_by(Follow.created_at.desc()).all()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Email", "Truyện ID", "Truyện", "Ngày đăng ký"])
    for follow, story in follows:
        writer.writerow([follow.id, follow.email, story.id, story.title, follow.created_at.strftime("%Y-%m-%d %H:%M:%S")])
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name="follows.csv"
    )

# Trang quản lý đăng ký nhận truyện mới
@app.route("/admin/subscribers")
def admin_subscribers():
    if not session.get("upload_authenticated"):
        flash("Vui lòng đăng nhập admin.", "danger")
        return redirect(url_for('upload_login'))
    subscribers = NewStorySubscription.query.order_by(NewStorySubscription.created_at.desc()).all()
    return render_template("admin_subscribers.html", subscribers=subscribers)

# Xóa một đăng ký nhận truyện mới
@app.route("/admin/subscribers/delete/<int:sub_id>", methods=["POST"])
def delete_subscriber(sub_id: int):
    if not session.get("upload_authenticated"):
        return redirect(url_for("upload_login"))
    UPLOAD_PASSWORD = os.environ.get("UPLOAD_PASSWORD", "secret")
    pw = request.form.get("password", "")
    if pw != UPLOAD_PASSWORD:
        flash("Mật khẩu không hợp lệ.")
        return redirect(url_for("admin_subscribers"))
    sub = NewStorySubscription.query.get_or_404(sub_id)
    email = sub.email
    db.session.delete(sub)
    db.session.commit()
    flash(f"Đã xóa {email} khỏi danh sách nhận thông báo truyện mới.")
    return redirect(url_for("admin_subscribers"))

# (Tuỳ chọn) Export danh sách subscribers ra CSV
@app.route("/admin/subscribers/export")
def export_subscribers_csv():
    if not session.get("upload_authenticated"):
        return redirect(url_for("upload_login"))
    import csv
    from io import StringIO
    subscribers = NewStorySubscription.query.order_by(NewStorySubscription.created_at.desc()).all()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Email", "Ngày đăng ký"])
    for sub in subscribers:
        writer.writerow([sub.id, sub.email, sub.created_at.strftime("%Y-%m-%d %H:%M:%S")])
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name="subscribers.csv"
    )
    
if __name__ == "__main__":
    # Tạo cơ sở dữ liệu khi khởi động để đảm bảo các bảng tồn tại
    create_tables()
    # Chạy ứng dụng khi chạy trực tiếp file này
    app.run(debug=True)