import io
import unittest
from contextlib import redirect_stderr
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import Mock

from blog_publisher.cli import main, save_draft
from blog_publisher.markdown import read_document
from blog_publisher.wordpress import WordPress, WordPressError


class MarkdownTests(unittest.TestCase):
    def test_inline_and_cover_images_with_spaces_resolve_to_one_file(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "配图 1.png"
            image.write_bytes(b"test asset")
            source = root / "article.md"
            source.write_text(
                '---\ncover: "配图 1.png"\n---\n\n# 草稿\n\n'
                '![配图](<配图 1.png>)\n\n<script>bad()</script>\n\n'
                '```python\nprint("你好")\n```\n',
                encoding="utf-8",
            )
            document = read_document(source)
            self.assertEqual(document.metadata["title"], "草稿")
            self.assertEqual(document.files, [image.resolve()])
            published = document.render({image.resolve(): "https://example.com/cover.png"})
            self.assertIn('src="https://example.com/cover.png"', published)
            self.assertIn('class="language-python"', published)
            self.assertIn("&lt;script&gt;", published)
            self.assertNotIn("<h1>", published)
            self.assertNotIn("https://example.com/cover.png", document.render())

    def test_parent_image_is_rejected_before_any_upload(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "outside.png").write_bytes(b"private asset")
            article_folder = root / "article"
            article_folder.mkdir()
            source = article_folder / "article.md"
            source.write_text("# Draft\n\n![Image](%2e%2e/outside.png)\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "inside the article folder"):
                read_document(source)


class DraftTests(unittest.TestCase):
    def test_failed_upload_reports_draft_id_and_leaves_it_unpublished(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "cover.png").write_bytes(b"test asset")
            source = root / "article.md"
            source.write_text("# Draft\n\n![Cover](cover.png)\n", encoding="utf-8")
            document = read_document(source)
            wp = Mock(spec=WordPress)
            wp.account.return_value = {"capabilities": {"edit_posts": True, "upload_files": True}}
            draft = {"id": 17, "status": "draft"}
            wp.request.return_value = draft
            wp.post_result.return_value = {"edit_url": "https://example.com/draft"}
            wp.media.side_effect = WordPressError("Upload interrupted.")
            with redirect_stderr(io.StringIO()), self.assertRaisesRegex(WordPressError, "--post-id 17"):
                save_draft(wp, document, None)
            wp.request.assert_called_once_with(
                "/posts", method="POST", payload={"title": "Draft", "status": "draft"}
            )

            wp.request.reset_mock()
            wp.draft.return_value = draft
            wp.media.side_effect = None
            wp.media.return_value = {"id": 33, "source_url": "https://example.com/cover.png"}
            with redirect_stderr(io.StringIO()):
                self.assertEqual(save_draft(wp, document, 17), draft)
            self.assertEqual(wp.request.call_count, 1)
            self.assertEqual(wp.request.call_args.args, ("/posts/17",))
            self.assertIn("https://example.com/cover.png", wp.request.call_args.kwargs["payload"]["content"])

    def test_publish_requires_confirmation_before_connecting(self):
        with redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit) as exit_info:
            main(["publish", "17"])
        self.assertEqual(exit_info.exception.code, 2)
        self.assertIn("requires --confirm", errors.getvalue())


class ConnectionTests(unittest.TestCase):
    def test_redirect_is_rejected(self):
        requested = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requested.append(self.path)
                self.send_response(302)
                self.send_header("Location", "/different-endpoint")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        with HTTPServer(("127.0.0.1", 0), Handler) as server:
            worker = Thread(target=server.serve_forever)
            worker.start()
            try:
                wp = WordPress(f"http://127.0.0.1:{server.server_port}", "test", "test")
                with self.assertRaisesRegex(WordPressError, "redirected the request"):
                    wp.account()
            finally:
                server.shutdown()
                worker.join()
        self.assertEqual(len(requested), 1)


if __name__ == "__main__":
    unittest.main()
