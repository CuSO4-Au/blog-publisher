import base64
import hashlib
import json
import os
import re
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .markdown import IMAGE_TYPES


class WordPressError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Credentials should only go to the explicitly configured endpoint.
        return None


class WordPress:
    def __init__(self, site: str, username: str, password: str, rest_mode: str = "query"):
        url = urlsplit(site)
        # Permit the isolated local test server as well as production HTTPS.
        loopback = url.scheme == "http" and url.hostname in {"127.0.0.1", "::1", "localhost"}
        if (
            (url.scheme != "https" and not loopback)
            or not url.hostname
            or url.username is not None
            or url.query
            or url.fragment
            or any(character.isspace() for character in site)
        ):
            raise ValueError("Set WP_PUBLISHER_URL to the WordPress installation URL using HTTPS.")
        if not username or ":" in username or not password:
            raise ValueError("Provide the WordPress username and an application password.")
        if rest_mode not in {"query", "pretty"}:
            raise ValueError("WP_PUBLISHER_REST_MODE must be query or pretty.")
        self.site = site.rstrip("/")
        self.rest_mode = rest_mode
        credentials = f"{username}:{password}".encode("utf-8")
        self.authorization = "Basic " + base64.b64encode(credentials).decode("ascii")
        self.opener = build_opener(NoRedirect(), ProxyHandler({})) if loopback else build_opener(NoRedirect())

    def request(self, path, *, method="GET", params=None, payload=None, data=None, headers=None):
        route = "/wp/v2" + path
        query = dict(params or {})
        if self.rest_mode == "query":
            base = self.site + "/"
            query["rest_route"] = route
        else:
            base = self.site + "/wp-json" + route
        url = base + ("?" + urlencode(query) if query else "")
        request_headers = {
            "Accept": "application/json",
            "Authorization": self.authorization,
            "User-Agent": "blog-publisher/0.1",
            **(headers or {}),
        }
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(url, data=data, headers=request_headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            with error:
                raise WordPressError(self.error_message(error)) from None
        except (URLError, TimeoutError, ConnectionError, HTTPException):
            raise WordPressError("Could not complete the connection to WordPress. Check the site URL, HTTPS and network.") from None
        except (UnicodeError, json.JSONDecodeError):
            raise WordPressError("WordPress returned an invalid JSON response. Check the REST endpoint.") from None

    @staticmethod
    def error_message(error):
        status = error.code
        code = ""
        try:
            body = json.loads(error.read(65536))
            value = body.get("code") if isinstance(body, dict) else None
            if isinstance(value, str) and re.fullmatch(r"(?:rest_[a-z_]+|incorrect_password|invalid_username)", value):
                code = f" ({value})"
        except (ValueError, OSError):
            pass
        advice = {
            400: "Check the article metadata and upload format.",
            401: "Check the username, application password and whether the host passes the Authorization header.",
            403: "Check the account permissions and the host's REST API settings.",
            404: "Check the installation URL, REST route mode and content ID.",
            413: "The upload exceeds the host's request size limit.",
        }
        if 300 <= status < 400:
            return "WordPress redirected the request. Set WP_PUBLISHER_URL to the final installation URL."
        return f"WordPress returned HTTP {status}{code}. {advice.get(status, 'Check the site and try again.')}"

    def account(self):
        return self.request("/users/me", params={"context": "edit", "_fields": "id,capabilities"})

    def post(self, post_id):
        return self.request(f"/posts/{post_id}", params={"context": "edit"})

    def draft(self, post_id):
        post = self.post(post_id)
        if post.get("status") != "draft":
            raise WordPressError(f"Post {post_id} has status {post.get('status')!r}. This command requires a draft.")
        return post

    def term_ids(self, taxonomy, slugs):
        ids = []
        for slug in slugs:
            terms = self.request(
                f"/{taxonomy}", params={"slug": slug, "hide_empty": "false", "_fields": "id"}
            )
            if len(terms) != 1:
                raise WordPressError(f"Expected one existing WordPress term in {taxonomy} for {slug!r}.")
            ids.append(terms[0]["id"])
        return ids

    def media(self, path: Path, draft_id: int):
        extension = path.suffix.lower()
        with path.open("rb") as image:
            size = os.fstat(image.fileno()).st_size
            if not size:
                raise ValueError(f"Image file is empty: {path.name}")
            digest = hashlib.file_digest(image, "sha256").hexdigest()
            slug = "blog-" + digest[:24]
            attachments = self.request(
                "/media", params={"parent": draft_id, "slug": slug, "_fields": "id,source_url"}
            )
            if attachments:
                return attachments[0]
            image.seek(0)
            return self.request(
                "/media",
                method="POST",
                params={"post": draft_id, "slug": slug, "_fields": "id,source_url"},
                data=image,
                headers={
                    "Content-Type": IMAGE_TYPES[extension],
                    "Content-Length": str(size),
                    "Content-Disposition": f'attachment; filename="{slug}{extension}"',
                },
            )

    def post_result(self, post):
        post_id = post["id"]
        result = {
            "id": post_id,
            "status": post["status"],
            "edit_url": f"{self.site}/wp-admin/post.php?post={post_id}&action=edit",
        }
        if post["status"] == "publish":
            result["url"] = post["link"]
        return result
