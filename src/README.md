# Trang web truyện đơn giản

Đây là một dự án nhỏ sử dụng **Python Flask** để xây dựng trang web đăng và đọc truyện chữ. Bạn có thể triển khai trên máy MacBook hoặc máy tính cá nhân để kiểm tra trước khi mua tên miền và publish cho mọi người.

## Tính năng

* **Danh sách truyện** – Trang chủ hiển thị danh sách các truyện đã đăng với đoạn trích của phần đầu tiên và danh sách **truyện nổi bật**. Bạn có thể lọc truyện theo thể loại ở thanh bên.
* **Trang chi tiết** – Hiển thị nội dung từng phần của truyện. Nếu truyện có nhiều phần, trang chi tiết cung cấp nút **Phần trước/Phần tiếp theo** và hộp chọn để chuyển thẳng tới một phần cụ thể.
* **Đăng truyện & chỉnh sửa** – Trang quản lý cho phép tạo truyện mới hoặc chỉnh sửa truyện đã có. Tính năng bao gồm:
  - Chọn **nhiều thể loại** cho mỗi truyện (ví dụ: vừa là “Phiêu lưu” vừa là “Hài hước”).
  - Chọn loại truyện **Ngắn** hoặc **Dài**.
  - Chỉnh sửa tiêu đề, tác giả, danh sách thể loại và loại truyện.
  - Thêm phần (chương) mới hoặc xoá phần cuối cùng của truyện.
  - Bảo vệ thao tác upload bằng mật khẩu do bạn đặt.
* **Thể loại** – Bạn có thể tạo thêm các thể loại và gán một truyện vào nhiều thể loại. Thanh bên hiển thị danh sách thể loại để lọc nhanh.
* **Tìm kiếm nâng cao** – Tìm kiếm theo tiêu đề, tác giả hoặc nội dung của bất kỳ phần truyện. Kết quả hiển thị đoạn trích của phần đầu để bạn dễ nhận biết.
* **Truyện nổi bật (trending)** – Ứng dụng tự động đếm số lượt xem và hiển thị top 5 truyện được đọc nhiều nhất.
* **Giao diện màu sắc** – Giao diện mới sử dụng tông xanh dương nhạt cho nền và thanh tiêu đề; thanh bên và khung nội dung được bo tròn, tạo cảm giác thân thiện hơn so với phiên bản trước.

## Cách chạy trên MacBook

1. **Cài Python 3** – macOS thường có sẵn Python 3. Bạn có thể kiểm tra bằng `python3 --version`. Nếu chưa có, cài đặt qua Homebrew.

2. **Tạo môi trường ảo** (khuyến khích) để cách ly thư viện:

   ```bash
   cd đường/dẫn/tới/thư/mục/story_site
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Cài đặt các thư viện cần thiết**:

   ```bash
   pip install Flask Flask-SQLAlchemy
   ```

4. **Khởi chạy ứng dụng**:

   ```bash
   # Cách 1: dùng Flask CLI (yêu cầu Flask >=2.2)
   flask --app app run --debug

   # Cách 2: chạy trực tiếp file app.py
   python app.py
   ```

   Mặc định ứng dụng sẽ chạy ở địa chỉ [http://127.0.0.1:5000](http://127.0.0.1:5000). Bạn có thể mở trình duyệt Safari hoặc Chrome để xem.

5. **Sử dụng**:

   * Truy cập trang chủ để xem danh sách truyện. Nếu cơ sở dữ liệu trống sẽ có thông báo chưa có truyện.
   * Chọn “Đăng truyện” để nhập nội dung mới. Tiêu đề và nội dung là bắt buộc; tác giả có thể để trống (mặc định sẽ hiển thị là “Ẩn danh”).
   * Sau khi đăng thành công, trang sẽ chuyển về danh sách.
   * Truy cập “Thêm thể loại” để tạo thể loại mới (genre). Khi đăng truyện, bạn có thể chọn thể loại trong danh sách thả xuống; việc phân loại giúp người đọc lọc truyện theo chuyên mục.
   * Gõ từ khóa vào ô “Tìm kiếm…” ở thanh trên cùng để tìm truyện theo tiêu đề hoặc nội dung. Các kết quả phù hợp sẽ được hiển thị ở trang tìm kiếm.
   * Hệ thống sẽ tự đếm số lượt xem và hiển thị 5 truyện được xem nhiều nhất ở phần “Truyện nổi bật”.

## Cấu trúc thư mục

```
story_site/
├── app.py           # Tập lệnh Flask chính
├── README.md        # Hướng dẫn sử dụng
├── stories.db       # Cơ sở dữ liệu SQLite (sẽ được tạo khi chạy lần đầu)
├── templates/       # Thư mục chứa các template HTML sử dụng Jinja2
│   ├── layout.html  # Template dùng chung
│   ├── index.html   # Trang danh sách truyện
│   ├── story.html   # Trang chi tiết truyện
│   ├── upload.html  # Form đăng truyện
│   └── 404.html     # Trang lỗi 404
└── static/
    └── style.css    # File CSS đơn giản cho giao diện
```

## Triển khai lên Internet

Sau khi chạy thử thành công trên máy địa phương, bạn có thể triển khai ứng dụng lên máy chủ và gắn tên miền. Một số điểm cần lưu ý:

* **Dùng WSGI server và reverse proxy** – Theo khuyến nghị của cộng đồng, khi triển khai Flask lên môi trường sản xuất cần sử dụng máy chủ WSGI như **Gunicorn** hoặc **uWSGI** phối hợp với reverse proxy như **Nginx** để tăng hiệu năng và bảo mật. Bài viết hướng dẫn triển khai Flask đã nêu rõ rằng cần có Python, Flask, WSGI server (Gunicorn), web server (Nginx) và tách biệt cấu hình theo môi trường【868250200358756†L174-L200】.
* **Cấu hình môi trường và bảo mật** – Cần lưu các biến quan trọng (ví dụ secret key, thông tin cơ sở dữ liệu) trong biến môi trường hoặc file cấu hình ngoài mã nguồn, đồng thời đảm bảo cập nhật các gói phần mềm và sử dụng HTTPS【868250200358756†L174-L200】. Khi triển khai sản xuất bạn cũng nên sử dụng cơ sở dữ liệu ổn định như PostgreSQL hoặc MySQL thay cho SQLite.【868250200358756†L188-L200】.
* **Chọn nền tảng phù hợp** – Bạn có thể triển khai trên các dịch vụ PaaS như Render, Fly.io, Railway, Heroku, hoặc tự thuê một VPS và cấu hình Nginx + Gunicorn. Việc lựa chọn phụ thuộc vào nhu cầu sử dụng, lưu lượng truy cập và ngân sách.

## Tham khảo tài liệu

* **Flask Quickstart** – Tài liệu chính thức mô tả cách tạo ứng dụng tối giản và chạy bằng lệnh `flask run`【356091916022901†L16-L47】. Bạn có thể xem thêm về các khái niệm như route, template tại đó.
* **Jinja và render_template** – Flask sử dụng Jinja để render template HTML. Bạn có thể truyền dữ liệu từ Python vào HTML và sử dụng vòng lặp, điều kiện giống ví dụ trong bài viết blog về blog viewer【265883278307334†L46-L50】.
* **Ứng dụng CRUD** – Một ứng dụng blog là ví dụ điển hình của ứng dụng CRUD. Trong hướng dẫn tạo CRUD với Flask, các chức năng Create, Retrieve, Update, Delete được mô tả rõ và minh hoạ code cho trang tạo dữ liệu【575919168767797†L41-L59】. Trong dự án này chúng ta chỉ cần hai thao tác: Create (đăng truyện) và Retrieve (xem truyện).

Chúc bạn xây dựng trang web vui vẻ và sáng tạo nhiều truyện hay!