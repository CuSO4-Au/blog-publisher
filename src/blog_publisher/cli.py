import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from .markdown import read_document
from .state import locked_state, read_state, state_path
from .wordpress import WordPress, WordPressError


def positive_id(value):
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number <= 0:
        raise argparse.ArgumentTypeError("Enter a positive WordPress post ID.")
    return number


def parser():
    command = argparse.ArgumentParser(prog="blog-publisher")
    subcommands = command.add_subparsers(dest="command", required=True)
    subcommands.add_parser("check", help="Check the connection and account capabilities.")
    preview = subcommands.add_parser("preview", help="Validate and render Markdown locally.")
    preview.add_argument("file", type=Path)
    record = subcommands.add_parser("record", help="Show local upload records without connecting to WordPress.")
    record.add_argument("file", type=Path)
    draft = subcommands.add_parser("draft", help="Create or update a WordPress draft.")
    draft.add_argument("file", type=Path)
    draft.add_argument("--post-id", type=positive_id, help="Update an existing draft.")
    status = subcommands.add_parser("status", help="Show a post's current status and edit link.")
    status.add_argument("post_id", type=positive_id)
    publish = subcommands.add_parser("publish", help="Publish a specific draft after confirmation.")
    publish.add_argument("post_id", type=positive_id)
    publish.add_argument("--confirm", action="store_true", help="Confirm publication of this post.")
    return command


def connect():
    site = os.environ.get("WP_PUBLISHER_URL", "")
    user = os.environ.get("WP_PUBLISHER_USER", "")
    if not site or not user:
        raise ValueError("Set WP_PUBLISHER_URL and WP_PUBLISHER_USER.")
    password = os.environ.get("WP_PUBLISHER_APP_PASSWORD", "")
    if not password and sys.stdin.isatty():
        password = getpass.getpass("WordPress application password: ")
    if not password:
        raise ValueError("Set WP_PUBLISHER_APP_PASSWORD or run in a terminal to enter it securely.")
    return WordPress(site, user, password, os.environ.get("WP_PUBLISHER_REST_MODE", "query"))


def save_draft(wp, document, post_id, record=None):
    account = wp.account()
    capabilities = account["capabilities"]
    if not capabilities.get("edit_posts"):
        raise WordPressError("This account needs permission to edit posts.")
    files = document.files
    if files and not capabilities.get("upload_files"):
        raise WordPressError("This account needs permission to upload files.")

    metadata = document.metadata
    initial_post = wp.draft(post_id) if post_id is not None else None
    payload = {name: metadata[name] for name in ("title", "slug", "excerpt") if name in metadata}
    for taxonomy in ("categories", "tags"):
        if taxonomy in metadata:
            payload[taxonomy] = wp.term_ids(taxonomy, metadata[taxonomy])

    if record is not None:
        record.begin(post_id)

    if post_id is None and not files:
        payload["content"] = document.render()
        result = create_draft(wp, payload)
        if record is not None:
            record.remember(result)
        return result

    if post_id is None:
        initial_post = create_draft(wp, {"title": payload["title"]})
        post_id = initial_post["id"]
        if record is not None:
            record.remember(initial_post)

    try:
        if files:
            print(f"Draft {post_id}: {wp.post_result(initial_post)['edit_url']}", file=sys.stderr, flush=True)
        attachments = {path: wp.media(path, post_id) for path in files}
        payload["content"] = document.render({path: item["source_url"] for path, item in attachments.items()})
        if document.cover is not None:
            payload["featured_media"] = attachments[document.cover]["id"]
        current = wp.draft(post_id)
        if current != initial_post:
            raise WordPressError("The draft changed during this run. Review the latest version before retrying.")
        result = wp.request(f"/posts/{post_id}", method="POST", payload=payload)
        if result.get("status") != "draft":
            raise WordPressError("The post is no longer a draft. Review it in WordPress.")
        if record is not None:
            record.remember(result)
        return result
    except (OSError, ValueError, WordPressError) as error:
        raise WordPressError(f"{error} Review draft {post_id} in WordPress before retrying with --post-id {post_id}.") from None


def create_draft(wp, payload):
    try:
        result = wp.request("/posts", method="POST", payload={**payload, "status": "draft"})
    except WordPressError as error:
        raise WordPressError(f"{error} Check the dashboard for an existing draft before retrying.") from None
    if result.get("status") != "draft":
        raise WordPressError("WordPress did not return a draft. Check the dashboard before retrying.")
    return result


def upload_draft(wp, source, document, explicit_id):
    with locked_state(source, wp.site) as record:
        post_id = record.post_id(explicit_id)
        try:
            result = wp.post_result(save_draft(wp, document, post_id, record))
            record.finish("success")
            return {**result, "record_file": str(state_path(source))}
        except (OSError, ValueError, WordPressError):
            if record.started:
                record.finish("failed")
            raise


def main(argv=None):
    if sys.stdout is sys.__stdout__:
        sys.stdout.reconfigure(encoding="utf-8")
    command = parser()
    options = command.parse_args(argv)
    try:
        if options.command in {"preview", "draft"}:
            document = read_document(options.file)
        if options.command == "preview":
            files = [path.relative_to(options.file.resolve().parent).as_posix() for path in document.files]
            result = {"metadata": document.metadata, "images": files, "html": document.render()}
        elif options.command == "record":
            result = {"record_file": str(state_path(options.file)), **read_state(options.file)}
        else:
            if options.command == "publish" and not options.confirm:
                command.error("publish requires --confirm after you review the draft.")
            wp = connect()
            if options.command == "check":
                account = wp.account()
                result = {"user_id": account["id"]}
                for key in ("edit_posts", "upload_files", "publish_posts"):
                    result[key] = bool(account["capabilities"].get(key))
            elif options.command == "draft":
                result = upload_draft(wp, options.file, document, options.post_id)
            elif options.command == "status":
                result = wp.post_result(wp.post(options.post_id))
            else:
                post = wp.post(options.post_id)
                if post.get("status") == "draft":
                    post = wp.request(f"/posts/{options.post_id}", method="POST", payload={"status": "publish"})
                if post.get("status") != "publish":
                    raise WordPressError("The post was not published. Check its status in WordPress.")
                result = wp.post_result(post)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, WordPressError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
