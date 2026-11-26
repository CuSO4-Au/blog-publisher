import io
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from blog_publisher.cli import main
from blog_publisher.state import locked_state, read_state, state_path
from blog_publisher.wordpress import WordPress, WordPressError


def mock_wordpress(site="https://example.com"):
    wp = Mock(spec=WordPress)
    wp.site = site
    wp.account.return_value = {"capabilities": {"edit_posts": True, "upload_files": True}}
    post = {"id": 17, "status": "draft"}
    wp.request.return_value = post
    wp.draft.return_value = post
    wp.post_result.side_effect = lambda item: WordPress.post_result(wp, item)
    return wp


def run_cli(wp, *args):
    with (
        patch("blog_publisher.cli.connect", return_value=wp),
        redirect_stdout(io.StringIO()) as output,
        redirect_stderr(io.StringIO()) as errors,
    ):
        code = main(list(args))
    return code, output.getvalue(), errors.getvalue()


class UploadRecordTests(unittest.TestCase):
    def test_repeating_draft_command_updates_original_post(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "中文稿件.md"
            source.write_text("# Title\n\nBody\n", encoding="utf-8")
            wp = mock_wordpress()
            self.assertEqual(run_cli(wp, "draft", str(source))[0], 0)
            self.assertEqual(run_cli(wp, "draft", str(source))[0], 0)
            self.assertEqual([call.args[0] for call in wp.request.call_args_list], ["/posts", "/posts/17"])
            entry = read_state(source)["sites"][wp.site]
            self.assertEqual(entry["post_id"], 17)
            self.assertEqual(entry["last_result"], "success")
            self.assertFalse(entry["creation_pending"])

    def test_media_failure_preserves_id_for_retry(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "article.md"
            source.write_text("# Title\n\n![Image](image.png)\n", encoding="utf-8")
            (root / "image.png").write_bytes(b"image fixture")
            wp = mock_wordpress()
            wp.media.side_effect = WordPressError("Upload interrupted.")
            self.assertEqual(run_cli(wp, "draft", str(source))[0], 1)
            entry = read_state(source)["sites"][wp.site]
            self.assertEqual(entry["post_id"], 17)
            self.assertEqual(entry["last_result"], "failed")
            wp.media.side_effect = None
            wp.media.return_value = {"id": 33, "source_url": "https://example.com/image.png"}
            self.assertEqual(run_cli(wp, "draft", str(source))[0], 0)
            self.assertEqual([call.args[0] for call in wp.request.call_args_list], ["/posts", "/posts/17"])

    def test_lost_create_response_requires_manual_post_id(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "article.md"
            source.write_text("# Title\n\nBody\n", encoding="utf-8")
            wp = mock_wordpress()
            wp.request.side_effect = WordPressError("Connection interrupted.")
            self.assertEqual(run_cli(wp, "draft", str(source))[0], 1)
            code, _, message = run_cli(wp, "draft", str(source))
            self.assertEqual(code, 1)
            self.assertIn("--post-id", message)
            self.assertEqual(wp.request.call_count, 1)
            wp.request.side_effect = None
            self.assertEqual(run_cli(wp, "draft", str(source), "--post-id", "17")[0], 0)
            self.assertFalse(read_state(source)["sites"][wp.site]["creation_pending"])

    def test_same_article_has_separate_records_for_different_sites(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "article.md"
            source.write_text("# Title\n\nBody\n", encoding="utf-8")
            first = mock_wordpress("https://EXAMPLE.com:443/blog/")
            second = mock_wordpress("https://other.example/blog")
            self.assertEqual(run_cli(first, "draft", str(source))[0], 0)
            self.assertEqual(run_cli(second, "draft", str(source))[0], 0)
            self.assertEqual(set(read_state(source)["sites"]), {"https://example.com/blog", second.site})
            first.site = "https://example.com/blog"
            self.assertEqual(run_cli(first, "draft", str(source))[0], 0)
            self.assertEqual(first.request.call_args.args, ("/posts/17",))

    def test_explicit_post_id_can_relink_an_existing_record(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "article.md"
            source.write_text("# Title\n\nBody\n", encoding="utf-8")
            wp = mock_wordpress()
            self.assertEqual(run_cli(wp, "draft", str(source))[0], 0)
            wp.draft.return_value = {"id": 31, "status": "draft"}
            wp.request.return_value = {"id": 31, "status": "draft"}
            self.assertEqual(run_cli(wp, "draft", str(source), "--post-id", "31")[0], 0)
            self.assertEqual(read_state(source)["sites"][wp.site]["post_id"], 31)

    def test_corrupt_record_blocks_upload_before_remote_requests(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "article.md"
            source.write_text("# Title\n\nBody\n", encoding="utf-8")
            state_path(source).write_text("{broken", encoding="utf-8")
            wp = mock_wordpress()
            code, _, message = run_cli(wp, "draft", str(source))
            self.assertEqual(code, 1)
            self.assertIn("record is invalid", message)
            wp.account.assert_not_called()
            wp.request.assert_not_called()

    def test_existing_record_cannot_update_a_published_post(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "article.md"
            source.write_text("# Title\n\nBody\n", encoding="utf-8")
            wp = mock_wordpress()
            self.assertEqual(run_cli(wp, "draft", str(source))[0], 0)
            wp.draft.side_effect = WordPressError("This command requires a draft.")
            self.assertEqual(run_cli(wp, "draft", str(source))[0], 1)
            self.assertEqual(wp.request.call_count, 1)
            self.assertEqual(read_state(source)["sites"][wp.site]["post_id"], 17)


class RecordStorageTests(unittest.TestCase):
    def test_failed_atomic_replace_preserves_previous_record(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "article.md"
            with locked_state(source, "https://example.com") as record:
                record.begin(17)
                saved = state_path(source).read_bytes()
                with patch("blog_publisher.state.os.replace", side_effect=OSError("Write failed")):
                    with self.assertRaises(OSError):
                        record.finish("failed")
                self.assertEqual(state_path(source).read_bytes(), saved)
            self.assertEqual(list(Path(directory).glob("*.json.*")), [])

    def test_process_lock_releases_after_context_exit(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "article.md"
            child = (
                "from pathlib import Path\n"
                "import sys\n"
                "from blog_publisher.state import locked_state\n"
                "try:\n"
                "    with locked_state(Path(sys.argv[1]), 'https://example.com'):\n"
                "        pass\n"
                "except ValueError:\n"
                "    sys.exit(2)\n"
            )
            args = [sys.executable, "-c", child, str(source)]
            with locked_state(source, "https://example.com"):
                self.assertEqual(subprocess.run(args, capture_output=True, timeout=10).returncode, 2)
            self.assertEqual(subprocess.run(args, capture_output=True, timeout=10).returncode, 0)


if __name__ == "__main__":
    unittest.main()
