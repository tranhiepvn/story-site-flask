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

import os
import re
import uuid
from datetime import datetime
import json
import io
import smtplib
from email.message import EmailMessage

from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError

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
    first_candidates_1 = ["Truyện Chỉ Có 1 Chương", "Phim Chỉ Có 1 Tập"]
    first_candidates_2 = ["Truyện Có Nhiều Chương", "Phim Có Nhiều Tập"]
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


# Khi module được import (dù bởi flask CLI hay chạy trực tiếp),
# đảm bảo rằng các bảng trong SQLite được tạo. Thực hiện trong
# app context để tránh lỗi "no such table" khi truy cập lần đầu.
with app.app_context():
    db.create_all()
    # Chỉ thực hiện nâng cấp cột nếu đang sử dụng SQLite. Đối với PostgreSQL hoặc
    # các hệ quản trị khác, cần dùng migration (ví dụ Alembic) để thay đổi lược đồ.
    if db.engine.dialect.name == "sqlite":
        def upgrade_db():
            """
            Kiểm tra và thêm các cột mới vào bảng stories nếu chúng chưa tồn tại.

            Khi cập nhật phiên bản mới, cơ sở dữ liệu cũ sẽ thiếu các cột như
            `is_hidden`, `rating_sum`, `rating_count` và `is_completed`. Hàm này
            sử dụng PRAGMA để kiểm tra thông tin bảng và thực hiện ALTER TABLE
            nếu cần. Lưu ý: Chỉ áp dụng cho SQLite.
            """
            with db.engine.connect() as conn:
                result = conn.execute(text("PRAGMA table_info(stories)")).fetchall()
                columns = [row[1] for row in result]
                if "is_hidden" not in columns:
                    conn.execute(text("ALTER TABLE stories ADD COLUMN is_hidden BOOLEAN DEFAULT 0"))
                if "rating_sum" not in columns:
                    conn.execute(text("ALTER TABLE stories ADD COLUMN rating_sum INTEGER DEFAULT 0"))
                if "rating_count" not in columns:
                    conn.execute(text("ALTER TABLE stories ADD COLUMN rating_count INTEGER DEFAULT 0"))
                if "is_completed" not in columns:
                    conn.execute(text("ALTER TABLE stories ADD COLUMN is_completed BOOLEAN DEFAULT 0"))

        # gọi hàm nâng cấp sau khi tạo bảng
        upgrade_db()


def create_tables() -> None:
    """Tạo cơ sở dữ liệu và bảng nếu chưa tồn tại.

    Hàm này được gọi lúc khởi động để đảm bảo các bảng tồn tại.
    """
    with app.app_context():
        db.create_all()


# ------------------ Comment handling and notification ------------------

def send_comment_notification(recipients: list[str], story: Story, comment_url: str) -> bool:
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
    """Trang chủ hiển thị danh sách truyện nổi bật, truyện ngắn và truyện dài.

    - Truyện nổi bật: tối đa 20 truyện có lượt xem cao nhất.
    - Truyện ngắn: phân trang 10 truyện mỗi trang, sắp xếp theo ngày đăng mới nhất.
    - Truyện dài: phân trang 10 truyện mỗi trang, sắp xếp theo ngày đăng mới nhất.
    Người đọc có thể chuyển trang riêng biệt cho danh sách truyện ngắn và truyện dài bằng
    cách thay đổi tham số ``short_page`` hoặc ``long_page`` trên URL. Danh sách thể loại
    được lấy để hiển thị trong thanh bên.
    """
    # xác định số trang cho danh sách truyện ngắn và truyện dài
    short_page = request.args.get("short_page", 1, type=int)
    long_page = request.args.get("long_page", 1, type=int)
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
    return render_template(
        "index.html",
        best=best,
        trending=trending,
        short_stories=short_stories,
        long_stories=long_stories,
        short_pagination=short_pagination,
        long_pagination=long_pagination,
        categories_group1=categories_group1,
        categories_group2=categories_group2,
        categories_group3=categories_group3,
        recent_stories=recent_stories,
    )


@app.route("/story/<int:story_id>")
def story_detail(story_id: int):
    """Trang chi tiết hiển thị nội dung truyện.

    - Tăng lượt xem mỗi lần truy cập.
    - Hỗ trợ hiển thị theo từng phần (chương). Nếu truyện có nhiều hơn một phần,
      người đọc có thể chuyển tới phần trước/tiếp theo hoặc chọn phần cụ thể.
    """
    story = Story.query.get_or_404(story_id)
    # tăng lượt xem
    story.views = (story.views or 0) + 1
    db.session.commit()
    # Lấy danh sách tất cả các phần của truyện (sắp xếp theo số thứ tự)
    parts = Part.query.filter_by(story_id=story.id).order_by(Part.part_number).all()
    total_parts = len(parts)
    # Xác định phần đang chọn từ query string (part=)
    part_param = request.args.get("part", default=None, type=int)
    # Nếu có tham số part và hợp lệ thì dùng, ngược lại mặc định phần 1
    if part_param is not None and 1 <= part_param <= total_parts:
        current_index = part_param
    else:
        current_index = 1
    # Phần hiện tại cần hiển thị
    current_part = None
    if parts:
        for p in parts:
            if p.part_number == current_index:
                current_part = p
                break
    # Lấy danh sách bình luận cho truyện (mới nhất lên đầu)
    comments = Comment.query.filter_by(story_id=story.id).order_by(Comment.created_at.desc()).all()
    # url hiện tại (bao gồm query string) để gắn vào form comment
    current_url = request.url
    return render_template(
        "story.html",
        story=story,
        current_part=current_part,
        current_index=current_index,
        total_parts=total_parts,
        parts=parts,
        comments=comments,
        current_url=current_url,
    )


@app.route("/upload", methods=["GET", "POST"])
def upload():
    """Trang quản lý truyện.

    Cho phép:
    * Tạo truyện mới (với phần/chương đầu tiên).
    * Chỉnh sửa truyện đã có: cập nhật tiêu đề, tác giả, thể loại, loại truyện;
      thêm phần mới; xoá phần cuối.
    Yêu cầu nhập mật khẩu trong mỗi thao tác POST để bảo vệ quyền upload.
    """
    # Nếu người dùng chưa xác thực để vào trang upload, chuyển hướng tới trang đăng nhập
    # Tạo một trang đăng nhập riêng để yêu cầu mật khẩu trước khi truy cập trang upload.
    if not session.get("upload_authenticated"):
        return redirect(url_for("upload_login"))

    # Mật khẩu upload; bạn có thể thay đổi hằng số này theo nhu cầu
    UPLOAD_PASSWORD = os.environ.get("UPLOAD_PASSWORD", "secret")

    # Danh sách thể loại luôn cần cho các form
    categories = Category.query.order_by(Category.name).all()
    # Xử lý tham số tìm kiếm và phân trang cho danh sách truyện
    # (áp dụng khi hiển thị danh sách truyện để chỉnh sửa ở chế độ GET)
    page = request.args.get("page", 1, type=int)
    search_query = request.args.get("q", "").strip()
    search_type = request.args.get("stype", "title")

    stories_query = Story.query.order_by(Story.created_at.desc())
    # Bộ sưu tập snippet highlight khi tìm theo nội dung
    highlight_snippets: dict[int, str] = {}

    if search_query:
        pattern = f"%{search_query}%"
        if search_type == "content":
            # tìm theo nội dung chương: join tới bảng Part
            stories_query = (
                Story.query.join(Part)
                .filter(Part.content.ilike(pattern))
                .distinct()
                .order_by(Story.created_at.desc())
            )
        else:
            # mặc định: tìm theo tiêu đề hoặc tác giả
            stories_query = stories_query.filter(
                (Story.title.ilike(pattern)) | (Story.author.ilike(pattern))
            )
    # Phân trang 25 truyện một trang
    stories_pagination = stories_query.paginate(page=page, per_page=25, error_out=False)
    stories = stories_pagination.items

    # Nếu tìm theo nội dung, tạo đoạn trích có highlight cho từng truyện trong trang
    if search_query and search_type == "content":
        # Tách từ khoá để highlight riêng từng từ (có thể nhiều từ)
        keywords = [kw.lower() for kw in search_query.split() if kw.strip()]
        for st in stories:
            # tìm chương đầu tiên chứa từ khoá
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
                if idx < 0:
                    # nếu không tìm thấy nguyên chuỗi, thử tìm theo từ đầu tiên
                    idx = content_lower.find(keywords[0]) if keywords else 0
                start = max(0, idx - 50)
                end = min(len(part_match.content), idx + len(search_query) + 50)
                snippet = part_match.content[start:end]
                # highlight tất cả từ khoá
                def repl(m: re.Match) -> str:
                    return f'<span class="highlight">{m.group(0)}</span>'
                for kw in keywords:
                    snippet = re.sub(
                        rf"({re.escape(kw)})",
                        repl,
                        snippet,
                        flags=re.IGNORECASE,
                    )
                highlight_snippets[st.id] = snippet

    # Xử lý gửi form (POST)
    if request.method == "POST":
        # kiểm tra mật khẩu
        password = request.form.get("password", "")
        if password != UPLOAD_PASSWORD:
            # giữ nguyên giao diện, thông báo lỗi
            story_id = request.form.get("existing_story_id")
            if story_id:
                # nếu đang chỉnh sửa, tải lại trang edit
                story = Story.query.get(int(story_id))
                parts = Part.query.filter_by(story_id=story.id).order_by(Part.part_number).all()
                # nếu đang cập nhật một phần cụ thể, lấy lại thông tin phần đó để hiển thị
                edit_part_id_form = request.form.get("part_id")
                edit_part_obj = None
                if edit_part_id_form:
                    try:
                        pid_int = int(edit_part_id_form)
                        edit_part_obj = Part.query.get(pid_int)
                    except Exception:
                        edit_part_obj = None
                return render_template(
                    "upload_edit.html",
                    error="Mật khẩu sai.",
                    story=story,
                    parts=parts,
                    categories=categories,
                    edit_part=edit_part_obj,
                    error_update=None,
                )
            else:
                return render_template(
                    "upload_new.html",
                    error="Mật khẩu sai.",
                    categories=categories,
                    stories=stories,
                    pagination=stories_pagination,
                    q=search_query,
                    stype=search_type,
                    highlight_snippets=highlight_snippets,
                )

        # Nếu có existing_story_id thì là thao tác trên truyện đã có
        existing_story_id = request.form.get("existing_story_id")
        action = request.form.get("action")
        if existing_story_id:
            story = Story.query.get_or_404(int(existing_story_id))
            if action == "update_story":
                # cập nhật thông tin truyện
                story.title = request.form.get("title", "").strip()
                story.author = request.form.get("author", "").strip()
                story_type = request.form.get("story_type", "short")
                story.story_type = story_type
                # đánh dấu truyện hoàn thành hay chưa
                story.is_completed = True if request.form.get("is_completed") else False
                # danh sách thể loại được chọn (có thể nhiều)
                cat_ids = request.form.getlist("category_ids")
                # chuyển thành list int
                cat_ids_int = [int(cid) for cid in cat_ids if cid]
                # gán quan hệ nhiều‑nhiều
                selected_categories = (
                    Category.query.filter(Category.id.in_(cat_ids_int)).all()
                    if cat_ids_int
                    else []
                )
                story.categories = selected_categories
                # đặt category_id bằng thể loại đầu tiên (nếu có) để đảm bảo tương thích
                story.category_id = cat_ids_int[0] if cat_ids_int else None
                db.session.commit()
                return redirect(url_for("upload", story_id=story.id))
            elif action == "add_part":
                # thêm phần mới cho truyện
                content = request.form.get("content", "").rstrip()
                if not content:
                    parts = Part.query.filter_by(story_id=story.id).order_by(Part.part_number).all()
                    return render_template(
                        "upload_edit.html",
                        error="Nội dung phần mới không được trống.",
                        story=story,
                        parts=parts,
                        categories=categories,
                    )
                # Nếu dòng đầu tiên bắt đầu bằng '### Phần ' hoặc '## Phần ' thì thay bằng 'Chương '
                lines = content.split('\n', 1)
                first_line = lines[0]
                if first_line.startswith("### Phần "):
                    first_line = "Chương " + first_line[len("### Phần "):]
                elif first_line.startswith("## Phần "):
                    first_line = "Chương " + first_line[len("## Phần "):]
                if len(lines) > 1:
                    content = first_line + "\n" + lines[1]
                else:
                    content = first_line
                # xác định số thứ tự phần mới
                last_part = Part.query.filter_by(story_id=story.id).order_by(Part.part_number.desc()).first()
                next_number = last_part.part_number + 1 if last_part else 1
                new_part = Part(story_id=story.id, part_number=next_number, content=content)
                db.session.add(new_part)
                db.session.commit()
                # Lưu các liên kết video cho phần mới
                video_urls = request.form.getlist("video_urls")
                # Chỉ lấy tối đa 9 liên kết video để tránh quá nhiều mục
                for url in video_urls[:9]:
                    url = (url or "").strip()
                    if url:
                        db.session.add(PartVideo(part_id=new_part.id, url=url))
                db.session.commit()
                return redirect(url_for("upload", story_id=story.id))
            elif action == "delete_last":
                # xoá phần cuối cùng nếu có
                last_part = Part.query.filter_by(story_id=story.id).order_by(Part.part_number.desc()).first()
                if last_part:
                    db.session.delete(last_part)
                    db.session.commit()
                return redirect(url_for("upload", story_id=story.id))
            elif action == "toggle_hidden":
                # ẩn hoặc hiện lại truyện
                story.is_hidden = not (story.is_hidden or False)
                db.session.commit()
                return redirect(url_for("upload", story_id=story.id))
            elif action == "delete_story":
                # xoá hoàn toàn một truyện và các phần liên quan
                # gỡ mối quan hệ với thể loại
                story.categories = []
                # xoá tất cả các chương của truyện
                Part.query.filter_by(story_id=story.id).delete()
                # xoá truyện
                db.session.delete(story)
                db.session.commit()
                return redirect(url_for("upload"))
            elif action == "replace_text":
                # Thay thế cụm từ trong tất cả các chương của truyện
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
                    flash(f"Đã thay '{search_str}' bằng '{replacement}' trong {replaced_count} chương.")
                else:
                    flash("Không tìm thấy cụm từ trong các chương.")
                return redirect(url_for("upload", story_id=story.id))
            elif action == "update_part":
                # cập nhật nội dung của một chương cụ thể
                part_id = request.form.get("part_id")
                content = request.form.get("content", "").strip()
                # kiểm tra dữ liệu hợp lệ
                if not part_id or not content:
                    parts = Part.query.filter_by(story_id=story.id).order_by(Part.part_number).all()
                    edit_part_obj = None
                    try:
                        edit_part_obj = Part.query.get(int(part_id))
                    except Exception:
                        pass
                    return render_template(
                        "upload_edit.html",
                        story=story,
                        parts=parts,
                        categories=categories,
                        edit_part=edit_part_obj,
                        error_update="Nội dung không được để trống.",
                    )
                try:
                    part_obj = Part.query.get(int(part_id))
                except Exception:
                    part_obj = None
                if part_obj and part_obj.story_id == story.id:
                    part_obj.content = content
                    # Cập nhật các liên kết video: xoá cũ và thêm mới
                    # Xoá toàn bộ video cũ của phần
                    PartVideo.query.filter_by(part_id=part_obj.id).delete()
                    video_urls = request.form.getlist("video_urls")
                    for url in video_urls[:9]:
                        url = (url or "").strip()
                        if url:
                            db.session.add(PartVideo(part_id=part_obj.id, url=url))
                    db.session.commit()
                return redirect(url_for("upload", story_id=story.id))
            # không nhận ra action, trở lại trang edit
            return redirect(url_for("upload", story_id=story.id))
        else:
            # tạo truyện mới
            title = request.form.get("title", "").strip()
            author = request.form.get("author", "").strip()
            story_type = request.form.get("story_type", "short")
            # trạng thái hoàn thành
            is_completed = True if request.form.get("is_completed") else False
            # nhận danh sách thể loại (có thể nhiều) từ form
            cat_ids = request.form.getlist("category_ids")
            content = request.form.get("content", "").strip()
            if not title or not content:
                return render_template(
                    "upload_new.html",
                    error="Vui lòng nhập đầy đủ tiêu đề và nội dung.",
                    categories=categories,
                    stories=stories,
                    pagination=stories_pagination,
                    q=search_query,
                    stype=search_type,
                    highlight_snippets=highlight_snippets,
                )
            # tạo truyện mới
            story = Story(
                title=title,
                author=author,
                story_type=story_type,
                is_completed=is_completed,
            )
            # thiết lập thể loại many‑to‑many và category_id chính
            cat_ids_int = [int(cid) for cid in cat_ids if cid]
            if cat_ids_int:
                selected_categories = Category.query.filter(Category.id.in_(cat_ids_int)).all()
                story.categories = selected_categories
                story.category_id = cat_ids_int[0]
            else:
                story.category_id = None
            db.session.add(story)
            db.session.commit()
            # tạo phần đầu tiên
            first_part = Part(story_id=story.id, part_number=1, content=content)
            db.session.add(first_part)
            db.session.commit()
            # Lưu các liên kết video cho chương đầu
            video_urls = request.form.getlist("video_urls")
            for url in video_urls[:9]:
                url = (url or "").strip()
                if url:
                    db.session.add(PartVideo(part_id=first_part.id, url=url))
            db.session.commit()
            return redirect(url_for("upload", story_id=story.id))

    # Xử lý GET: hiển thị trang mới hoặc trang chỉnh sửa
    story_id = request.args.get("story_id")
    if story_id:
        story = Story.query.get_or_404(int(story_id))
        parts = Part.query.filter_by(story_id=story.id).order_by(Part.part_number).all()
        # Kiểm tra xem có tham số edit_part trên URL để hiển thị form cập nhật chương
        edit_part_id = request.args.get("edit_part", type=int)
        edit_part_obj = None
        if edit_part_id:
            edit_part_obj = Part.query.get(edit_part_id)
            # chỉ hiển thị nếu chương thuộc truyện đang chỉnh sửa
            if edit_part_obj and edit_part_obj.story_id != story.id:
                edit_part_obj = None
        return render_template(
            "upload_edit.html",
            story=story,
            parts=parts,
            categories=categories,
            edit_part=edit_part_obj,
            error_update=None,
        )
    # Mặc định: hiển thị form tạo truyện mới cùng danh sách truyện để chọn
    return render_template(
        "upload_new.html",
        categories=categories,
        stories=stories,
        pagination=stories_pagination,
        q=search_query,
        stype=search_type,
        highlight_snippets=highlight_snippets,
    )


# Hiển thị trang đăng nhập trước khi vào trang upload.
# Người dùng cần nhập mật khẩu hợp lệ để tiếp tục.
@app.route("/upload_login", methods=["GET", "POST"])
def upload_login():
    """Trang nhập mật khẩu trước khi vào trang quản trị đăng truyện.

    Trang này hiển thị một form đơn giản yêu cầu mật khẩu. Nếu mật khẩu
    hợp lệ, thiết lập session và chuyển hướng tới trang upload. Nếu sai,
    hiển thị thông báo lỗi. Danh sách thể loại được truyền vào để hiện
    trong sidebar, giống như các trang khác.
    """
    categories_group1, categories_group2, categories_group3 = get_category_groups()
    # Mật khẩu upload từ biến môi trường hoặc giá trị mặc định
    UPLOAD_PASSWORD = os.environ.get("UPLOAD_PASSWORD", "secret")
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == UPLOAD_PASSWORD:
            # Ghi nhớ rằng người dùng đã đăng nhập để tránh phải nhập lại trong phiên
            session["upload_authenticated"] = True
            return redirect(url_for("upload"))
        else:
            return render_template(
                "upload_login.html",
                error="Mật khẩu sai.",
                categories_group1=categories_group1,
                categories_group2=categories_group2,
                categories_group3=categories_group3,
            )
    # GET: hiển thị form nhập mật khẩu
    return render_template(
        "upload_login.html",
        categories_group1=categories_group1,
        categories_group2=categories_group2,
        categories_group3=categories_group3,
    )


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
    """Tìm kiếm truyện theo tiêu đề hoặc nội dung.

    Nhận tham số q trên URL, trả về danh sách truyện phù hợp.
    """
    query = request.args.get("q", "").strip()
    stories = []
    if query:
        # tìm theo tiêu đề, tác giả hoặc nội dung phần truyện và loại bỏ truyện ẩn.
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
    categories_group1, categories_group2, categories_group3 = get_category_groups()
    return render_template(
        "search.html",
        query=query,
        stories=stories,
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
    """Trang lỗi 404 tuỳ chỉnh."""
    return render_template("404.html"), 404


if __name__ == "__main__":
    # Tạo cơ sở dữ liệu khi khởi động để đảm bảo các bảng tồn tại
    create_tables()
    # Chạy ứng dụng khi chạy trực tiếp file này
    app.run(debug=True)