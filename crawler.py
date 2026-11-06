import re
import time
from datetime import datetime
import io
from PIL import Image
from pathlib import Path
import threading
import json
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

class ThreadsCrawler:

    ALLOWED_HOSTS = ("fbcdn.net", "cdninstagram.com")

    def __init__(self, log, progress, finished):
        self.log = log
        self.progress = progress
        self.finished = finished
        self.cancel_event = threading.Event()

    # ==========================================
    # NORMALIZE PROFILE URL
    # ==========================================

    @staticmethod
    def normalize_profile_url(value):

        value = value.strip()

        if not value.lower().startswith(("http://", "https://")):
            value = f"https://www.threads.com/@{value.lstrip('@')}"

        parsed = urlparse(value)

        if parsed.hostname not in (
            "threads.com", "www.threads.com",
            "threads.net", "www.threads.net"
        ):
            raise ValueError("URL không thuộc Threads.")

        match = re.match(r"^/@([A-Za-z0-9._]+)", parsed.path)

        if not match:
            raise ValueError("Không xác định được username.")

        username = match.group(1)

        return f"https://www.threads.com/@{username}", username

    # ==========================================
    # IMAGE VALIDATION (dùng cho DOM fallback)
    # ==========================================

    @classmethod
    def is_valid_image(cls, url, width, height):

        if not url or not url.startswith("http"):
            return False

        if width < 240 or height < 240:
            return False

        host = urlparse(url).hostname or ""

        return any(
            host == d or host.endswith("." + d)
            for d in cls.ALLOWED_HOSTS
        )

    # ==========================================
    # PARSE JSON -> POSTS
    # ==========================================

    @staticmethod
    def best_candidate(image_versions):
        """Chọn URL có độ phân giải lớn nhất."""

        candidates = (image_versions or {}).get("candidates") or []
        candidates = [
            c for c in candidates
            if isinstance(c, dict) and c.get("url")
        ]

        if not candidates:
            return None

        best = max(
            candidates,
            key=lambda c: (c.get("width") or 0) * (c.get("height") or 0)
        )

        return best["url"]

    def extract_posts(self, node, username, posts):
        """Duyệt đệ quy JSON, tìm các object bài post."""

        if isinstance(node, list):
            for item in node:
                self.extract_posts(item, username, posts)
            return

        if not isinstance(node, dict):
            return

        code = node.get("code")

        if (
            isinstance(code, str)
            and ("image_versions2" in node or "carousel_media" in node)
        ):
            owner = (
                (node.get("user") or {}).get("username") or ""
            ).lower()

            # Chỉ lấy bài của chủ profile (bỏ bài của người khác trong thread)
            if not owner or owner == username.lower():
                urls = []
                carousel = node.get("carousel_media")

                if carousel:
                    for media in carousel:
                        if not isinstance(media, dict):
                            continue
                        if media.get("video_versions"):
                            continue
                        url = self.best_candidate(
                            media.get("image_versions2")
                        )
                        if url:
                            urls.append(url)

                elif not node.get("video_versions"):
                    url = self.best_candidate(
                        node.get("image_versions2")
                    )
                    if url:
                        urls.append(url)

                if urls:
                    post = posts.setdefault(
                        code,
                        {"urls": [], "taken_at": node.get("taken_at")}
                    )

                    # Giữ phiên bản có nhiều ảnh nhất
                    if len(urls) > len(post["urls"]):
                        post["urls"] = urls

                    if not post["taken_at"]:
                        post["taken_at"] = node.get("taken_at")

        for key, value in node.items():
            # Không đệ quy vào ảnh con của album, tránh tách 1 bài thành nhiều bài
            if key in ("carousel_media", "image_versions2", "video_versions"):
                continue
            if isinstance(value, (dict, list)):
                self.extract_posts(value, username, posts)

    def parse_json_text(self, text, username, posts):

        start = text.find("{")

        if start < 0:
            return

        text = text[start:]

        try:
            self.extract_posts(json.loads(text), username, posts)
            return
        except ValueError:
            pass

        # Response dạng nhiều dòng JSON
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                self.extract_posts(json.loads(line), username, posts)
            except ValueError:
                continue

    # ==========================================
    # FOLDER NAME / DOWNLOAD
    # ==========================================

    @staticmethod
    def build_folder_names(posts):
        """Tên folder = ngày đăng (DD-MM-YYYY).
        Nếu một ngày có nhiều bài thì thêm mã bài để không bị trộn ảnh."""

        by_date = {}
        names = {}

        for code, data in posts.items():
            ts = data.get("taken_at")
            date = None

            try:
                if ts:
                    date = datetime.fromtimestamp(int(ts)).strftime("%d-%m-%Y")
            except (ValueError, OSError, OverflowError):
                pass

            if date:
                by_date.setdefault(date, []).append(code)
            else:
                names[code] = code  # không có ngày thì dùng mã bài

        for date, codes in by_date.items():
            if len(codes) == 1:
                names[codes[0]] = date
            else:
                for code in codes:
                    names[code] = f"{date}_{code}"

        return names

    def download_image(self, context, image_url, post_dir, number):
        try:
            if any(post_dir.glob(f"{number:02d}.*")):
                return "duplicate"

            response = context.request.get(image_url, timeout=30000)

            if not response.ok:
                raise RuntimeError(f"HTTP {response.status}")

            content_type = response.headers.get(
                "content-type", ""
            ).split(";")[0].lower()

            if not content_type.startswith("image/"):
                raise RuntimeError("URL không trả về dữ liệu hình ảnh.")

            image_data = response.body()

            if not image_data:
                raise RuntimeError("Ảnh rỗng.")

            post_dir.mkdir(parents=True, exist_ok=True)

            image = Image.open(io.BytesIO(image_data))

            if image.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                image = image.convert("RGB")

            target = post_dir / f"{number:02d}.png"
            temp = post_dir / f"{number:02d}.tmp"

            image.save(temp, format="PNG")
            temp.replace(target)

            return "success"

        except Exception as error:

            self.log(f"Lỗi tải ảnh {post_dir.name}/{number:02d}: {error}")

            return "failed"
    # ==========================================
    # MAIN CRAWLER
    # ==========================================

    def crawl(self, profile_url, output_dir, max_scrolls=100):

        downloaded = 0
        failed = 0
        save_dir = ""

        try:

            profile_url, username = self.normalize_profile_url(profile_url)

            save_dir = Path(output_dir) / username
            save_dir.mkdir(parents=True, exist_ok=True)

            # code bài post -> {"urls": [...], "taken_at": ...}
            posts = {}
            # Dự phòng khi không đọc được JSON
            dom_posts = {}
            seen_scripts = set()

            self.log("Đang kết nối Chrome...")

            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(
                    "chrome",
                    timeout=15000
                )

                if not browser.contexts:
                    raise RuntimeError("Không tìm thấy Chrome context.")

                context = browser.contexts[0]
                page = context.new_page()

                try:

                    def handle_response(response):
                        try:
                            host = urlparse(response.url).hostname or ""

                            if "threads." not in host:
                                return

                            content_type = response.headers.get(
                                "content-type", ""
                            ).lower()

                            if not any(
                                t in content_type
                                for t in ("json", "javascript", "text/plain")
                            ):
                                return

                            text = response.text()

                            if "image_versions2" not in text:
                                return

                            self.parse_json_text(text, username, posts)
                        except Exception:
                            return

                    page.on("response", handle_response)

                    def collect_script_data():
                        texts = page.evaluate("""
                            () => Array.from(
                                document.querySelectorAll(
                                    'script[type="application/json"]'
                                )
                            )
                            .map(s => s.textContent)
                            .filter(t => t && t.includes('image_versions2'))
                        """)

                        for text in texts:
                            key = hash(text)
                            if key in seen_scripts:
                                continue
                            seen_scripts.add(key)
                            self.parse_json_text(text, username, posts)

                    def collect_dom_fallback():
                        items = page.evaluate(r"""
                            () => {
                                const out = [];
                                document.querySelectorAll('img').forEach(img => {
                                    if (img.naturalWidth < 240 || img.naturalHeight < 240) return;

                                    let node = img, code = null, time = null;
                                    while (node && node !== document.body && !code) {
                                        const a = node.querySelector &&
                                            node.querySelector('a[href*="/post/"]');
                                        if (a) {
                                            const m = a.getAttribute('href')
                                                .match(/\/post\/([^/?#]+)/);
                                            if (m) {
                                                code = m[1];
                                                const t = node.querySelector('time[datetime]');
                                                if (t) time = t.getAttribute('datetime');
                                            }
                                        }
                                        node = node.parentElement;
                                    }
                                    if (!code) return;

                                    let url = img.currentSrc || img.src;
                                    let width = img.naturalWidth;

                                    (img.srcset || '').split(',').forEach(part => {
                                        const [u, w] = part.trim().split(/\s+/);
                                        const n = parseInt(w) || 0;
                                        if (u && n > width) { url = u; width = n; }
                                    });

                                    out.push({code, url, width, height: img.naturalHeight, time});
                                });
                                return out;
                            }
                        """)

                        for item in items:
                            if not self.is_valid_image(
                                item["url"], item["width"], item["height"]
                            ):
                                continue

                            post = dom_posts.setdefault(
                                item["code"], {"by_path": {}, "taken_at": None}
                            )

                            if item.get("time") and not post["taken_at"]:
                                try:
                                    post["taken_at"] = datetime.fromisoformat(
                                        item["time"].replace("Z", "+00:00")
                                    ).timestamp()
                                except ValueError:
                                    pass

                            path = urlparse(item["url"]).path
                            old = post["by_path"].get(path)

                            if not old or item["width"] > old[0]:
                                post["by_path"][path] = (
                                    item["width"], item["url"]
                                )

                    def get_scroll_state():
                        return page.evaluate("""
                            () => {
                                const root = document.scrollingElement || document.documentElement;
                                return {
                                    top: root.scrollTop,
                                    height: root.scrollHeight,
                                    viewport: root.clientHeight
                                };
                            }
                        """)

                    self.log("Đã kết nối Chrome.")
                    self.log("Đang kiểm tra phiên đăng nhập...")

                    page.goto(
                        profile_url,
                        wait_until="domcontentloaded",
                        timeout=60000
                    )
                    page.wait_for_timeout(4000)

                    if "/login" in page.url.lower():
                        raise RuntimeError("Threads yêu cầu đăng nhập.")

                    if self.cancel_event.is_set():
                        raise InterruptedError("Người dùng đã dừng tác vụ.")

                    self.log(f"Đang crawl profile: @{username}")

                    unchanged = 0
                    collect_script_data()
                    collect_dom_fallback()

                    for index in range(max_scrolls):
                        if self.cancel_event.is_set():
                            break

                        old_count = len(posts) + len(dom_posts)
                        old_state = get_scroll_state()

                        page.mouse.move(
                            max(1, old_state["viewport"] // 2),
                            max(1, int(old_state["viewport"] * 0.75))
                        )
                        page.mouse.wheel(
                            0,
                            max(500, int(old_state["viewport"] * 0.75))
                        )
                        page.wait_for_timeout(2500)

                        collect_script_data()
                        collect_dom_fallback()

                        new_state = get_scroll_state()
                        new_count = len(posts) + len(dom_posts)

                        self.log(
                            f"Scroll {index + 1}/{max_scrolls} "
                            f"| Posts: {len(posts) or len(dom_posts)} "
                            f"| Position: {new_state['top']}/{new_state['height']}"
                        )

                        if (
                            new_count == old_count
                            and new_state["top"] == old_state["top"]
                            and new_state["height"] == old_state["height"]
                        ):
                            unchanged += 1
                        else:
                            unchanged = 0

                        if unchanged >= 10:
                            self.log(
                                "Không phát hiện nội dung mới sau 10 lần cuộn ổn định."
                            )
                            break

                    # ---- Chuẩn bị danh sách tải ----
                    if posts:
                        self.log("Dùng dữ liệu JSON (ảnh độ phân giải cao nhất).")
                        final_posts = posts
                    else:
                        self.log(
                            "Không đọc được JSON, dùng DOM fallback "
                            "(chất lượng có thể thấp hơn)."
                        )
                        final_posts = {
                            code: {
                                "urls": [
                                    u for _, u in sorted(
                                        data["by_path"].values(),
                                        key=lambda x: -x[0]
                                    )
                                ],
                                "taken_at": data["taken_at"]
                            }
                            for code, data in dom_posts.items()
                        }

                    total = sum(len(v["urls"]) for v in final_posts.values())
                    folder_names = self.build_folder_names(final_posts)

                    self.log(
                        f"Tìm thấy {len(final_posts)} bài post, {total} ảnh."
                    )

                    if not total:
                        self.log("Không tìm thấy ảnh phù hợp.")
                    else:
                        self.log("Bắt đầu tải ảnh...")

                        done = 0

                        for code, data in final_posts.items():
                            if self.cancel_event.is_set():
                                break

                            post_dir = save_dir / folder_names[code]

                            for number, image_url in enumerate(
                                data["urls"], start=1
                            ):
                                if self.cancel_event.is_set():
                                    break

                                result = self.download_image(
                                    context, image_url, post_dir, number
                                )

                                if result == "success":
                                    downloaded += 1
                                elif result == "failed":
                                    failed += 1

                                done += 1
                                self.progress(done, total)
                                self.log(
                                    f"[{done}/{total}] "
                                    f"{post_dir.name}/{number:02d} "
                                    f"{result.upper()}"
                                )
                                time.sleep(0.2)

                    self.log("Đã hoàn thành quá trình crawl.")

                finally:
                    try:
                        if not page.is_closed():
                            page.close()
                    except Exception as close_error:
                        self.log(f"Không thể đóng tab crawler: {close_error}")

            self.finished(
                downloaded, failed, str(save_dir),
                self.cancel_event.is_set()
            )

        except Exception as error:

            self.log(f"LỖI: {error}")

            self.finished(
                downloaded, failed,
                str(save_dir) if save_dir else "",
                self.cancel_event.is_set()
            )
