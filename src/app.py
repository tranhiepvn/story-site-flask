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
from datetime import datetime
import json
import io
import smtplib
from email.message import EmailMessage

from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash
from flask_sqlalchemy import SQLAlchemy

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

# Cung cấp đối tượng datetime cho tất cả template Jinja.
# Điều này cho phép dùng {{ datetime.utcnow().year }} trong layout.html
# mà không gặp lỗi UndefinedError.
@app.context_processor
def inject_datetime():
    return {"datetime": datetime}


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
            from sqlalchemy import text
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
    # danh sách thể loại để hiển thị trong thanh bên
    categories = Category.query.order_by(Category.name).all()
    return render_template(
        "index.html",
        best=best,
        trending=trending,
        short_stories=short_stories,
        long_stories=long_stories,
        short_pagination=short_pagination,
        long_pagination=long_pagination,
        categories=categories,
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
    categories = Category.query.order_by(Category.name).all()
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
                categories=categories,
            )
    # GET: hiển thị form nhập mật khẩu
    return render_template(
        "upload_login.html",
        categories=categories,
    )


# --------- Export/Import data utilities ---------

@app.route("/export_data", methods=["POST"])
def export_data():
    """Export tất cả dữ liệu về truyện, phần và thể loại ra một file JSON.

    Người dùng phải đăng nhập trang quản trị (upload_authenticated) mới được phép tải dữ liệu.
    Sau khi thu thập dữ liệu, hàm trả về file JSON để người dùng tải xuống.
    """
    # Kiểm tra quyền truy cập
    if not session.get("upload_authenticated"):
        return redirect(url_for("upload_login"))
    # Lấy dữ liệu từ cơ sở dữ liệu
    stories = Story.query.all()
    categories = Category.query.all()
    parts = Part.query.all()
    # Chuyển đổi dữ liệu sang dict
    comments = Comment.query.all()
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
                # danh sách id thể loại liên kết (nhiều‑nhiều)
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
    }
    # Chuyển đổi sang JSON và gửi file
    json_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    buf = io.BytesIO(json_bytes)
    filename = f"stories_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    return send_file(buf, as_attachment=True, download_name=filename, mimetype="application/json")


@app.route("/import_data", methods=["POST"])
def import_data():
    """Nhận file JSON và nhập toàn bộ dữ liệu vào cơ sở dữ liệu hiện tại.

    Khi import, dữ liệu cũ sẽ bị xoá để tránh trùng lặp. Chỉ người dùng đã đăng nhập
    trang quản trị mới được phép thao tác. Sau khi hoàn thành, hàm chuyển hướng
    về trang upload với thông báo.
    """
    if not session.get("upload_authenticated"):
        return redirect(url_for("upload_login"))
    uploaded_file = request.files.get("import_file")
    if not uploaded_file:
        flash("Không tìm thấy file để import.")
        return redirect(url_for("upload"))
    try:
        data = json.load(uploaded_file)
    except Exception:
        flash("File import không hợp lệ.")
        return redirect(url_for("upload"))
    # Xoá toàn bộ dữ liệu cũ (categories, stories, parts, comments) và các quan hệ
    db.session.execute(story_categories.delete())
    Comment.query.delete()
    Part.query.delete()
    Story.query.delete()
    Category.query.delete()
    db.session.commit()
    # Import categories
    category_objs = {}
    for cat in data.get("categories", []):
        cobj = Category(id=cat.get("id"), name=cat.get("name"))
        db.session.add(cobj)
        category_objs[cobj.id] = cobj
    db.session.commit()
    # Import stories
    story_objs = {}
    for st in data.get("stories", []):
        ts = st.get("created_at")
        created_at = None
        if ts:
            try:
                created_at = datetime.fromisoformat(ts)
            except Exception:
                created_at = datetime.utcnow()
        sobj = Story(
            id=st.get("id"),
            title=st.get("title"),
            author=st.get("author"),
            story_type=st.get("story_type", "short"),
            created_at=created_at,
            views=st.get("views", 0),
            is_hidden=st.get("is_hidden", False),
            is_completed=st.get("is_completed", False),
            rating_sum=st.get("rating_sum", 0),
            rating_count=st.get("rating_count", 0),
            category_id=st.get("category_id"),
        )
        db.session.add(sobj)
        story_objs[sobj.id] = sobj
    db.session.commit()
    # Import parts
    for part in data.get("parts", []):
        ts = part.get("created_at")
        p_created_at = None
        if ts:
            try:
                p_created_at = datetime.fromisoformat(ts)
            except Exception:
                p_created_at = datetime.utcnow()
        pobj = Part(
            id=part.get("id"),
            story_id=part.get("story_id"),
            part_number=part.get("part_number"),
            content=part.get("content"),
            created_at=p_created_at,
        )
        db.session.add(pobj)
    db.session.commit()
    # Gán quan hệ nhiều‑nhiều story_categories
    for st in data.get("stories", []):
        s_id = st.get("id")
        sobj = story_objs.get(s_id)
        if not sobj:
            continue
        cat_ids = st.get("categories", [])
        sobj.categories = [category_objs[cid] for cid in cat_ids if cid in category_objs]
    db.session.commit()
    # Import comments
    for c in data.get("comments", []):
        ts = c.get("created_at")
        c_created = None
        if ts:
            try:
                c_created = datetime.fromisoformat(ts)
            except Exception:
                c_created = datetime.utcnow()
        cm = Comment(
            id=c.get("id"),
            story_id=c.get("story_id"),
            url=c.get("url"),
            name=c.get("name"),
            email=c.get("email"),
            content=c.get("content"),
            created_at=c_created,
        )
        db.session.add(cm)
    db.session.commit()
    # Khôi phục sequence tự tăng cho PostgreSQL
    if db.engine.dialect.name == "postgresql":
        from sqlalchemy import text
        with db.engine.connect() as conn:
            conn.execute(text("SELECT setval(pg_get_serial_sequence('categories','id'), COALESCE((SELECT MAX(id) FROM categories), 1), true)"))
            conn.execute(text("SELECT setval(pg_get_serial_sequence('stories','id'), COALESCE((SELECT MAX(id) FROM stories), 1), true)"))
            conn.execute(text("SELECT setval(pg_get_serial_sequence('parts','id'), COALESCE((SELECT MAX(id) FROM parts), 1), true)"))
            conn.execute(text("SELECT setval(pg_get_serial_sequence('comments','id'), COALESCE((SELECT MAX(id) FROM comments), 1), true)"))
    flash("Import dữ liệu thành công!")
    return redirect(url_for("upload"))


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
    # Xoá toàn bộ dữ liệu liên quan tới truyện
    try:
        db.session.execute(story_categories.delete())
        Part.query.delete()
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
    nhiều thể loại), sắp xếp theo ngày đăng mới nhất và phân trang 10 truyện mỗi trang.
    Tham số ``page`` trên URL dùng để chuyển trang. Trả về template list.html để
    hiển thị danh sách.
    """
    category = Category.query.get_or_404(category_id)
    page = request.args.get("page", 1, type=int)
    per_page = 10
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
    categories = Category.query.order_by(Category.name).all()
    prev_url = url_for("category_view", category_id=category.id, page=pagination.prev_num) if pagination.has_prev else None
    next_url = url_for("category_view", category_id=category.id, page=pagination.next_num) if pagination.has_next else None
    return render_template(
        "list.html",
        title=f"Thể loại: {category.name}",
        filter_type="category",
        filter_name=category.name,
        stories=stories,
        pagination=pagination,
        prev_url=prev_url,
        next_url=next_url,
        categories=categories,
    )


@app.route("/author/<author>")
def author_view(author: str):
    """Hiển thị danh sách truyện của một tác giả."""
    page = request.args.get("page", 1, type=int)
    per_page = 10
    query = (
        Story.query.filter(Story.author == author, Story.is_hidden == False)
        .order_by(Story.created_at.desc())
    )
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    stories = pagination.items
    categories = Category.query.order_by(Category.name).all()
    # chuẩn bị liên kết chuyển trang cho template
    prev_url = url_for("author_view", author=author, page=pagination.prev_num) if pagination.has_prev else None
    next_url = url_for("author_view", author=author, page=pagination.next_num) if pagination.has_next else None
    return render_template(
        "list.html",
        title=f"Tác giả: {author}",
        filter_type="author",
        filter_name=author,
        stories=stories,
        pagination=pagination,
        prev_url=prev_url,
        next_url=next_url,
        categories=categories,
    )


@app.route("/type/<story_type>")
def type_view(story_type: str):
    """Hiển thị danh sách truyện theo loại ngắn/dài."""
    if story_type not in ("short", "long"):
        return page_not_found(404)
    page = request.args.get("page", 1, type=int)
    per_page = 10
    query = (
        Story.query.filter_by(story_type=story_type, is_hidden=False)
        .order_by(Story.created_at.desc())
    )
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    stories = pagination.items
    categories = Category.query.order_by(Category.name).all()
    # xác định tiêu đề tiếng Việt
    title_vi = "Truyện Ngắn" if story_type == "short" else "Truyện Dài"
    prev_url = url_for("type_view", story_type=story_type, page=pagination.prev_num) if pagination.has_prev else None
    next_url = url_for("type_view", story_type=story_type, page=pagination.next_num) if pagination.has_next else None
    return render_template(
        "list.html",
        title=title_vi,
        filter_type="type",
        filter_name=story_type,
        stories=stories,
        pagination=pagination,
        prev_url=prev_url,
        next_url=next_url,
        categories=categories,
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
    categories = Category.query.order_by(Category.name).all()
    return render_template(
        "search.html",
        query=query,
        stories=stories,
        categories=categories,
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
    categories = Category.query.order_by(Category.name).all()
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


@app.errorhandler(404)
def page_not_found(e):
    """Trang lỗi 404 tuỳ chỉnh."""
    return render_template("404.html"), 404


if __name__ == "__main__":
    # Tạo cơ sở dữ liệu khi khởi động để đảm bảo các bảng tồn tại
    create_tables()
    # Chạy ứng dụng khi chạy trực tiếp file này
    app.run(debug=True)