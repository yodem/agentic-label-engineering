import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from ale.board import BoardServer


def fetch(url, method="GET"):
    return urlopen(Request(url, method=method), timeout=2)


def test_board_server_rejects_bad_token_and_mutating_methods(tmp_path):
    status = lambda: {"run": {"id": "run-1", "finished": False}, "tasks": {}}
    server = BoardServer(str(tmp_path), status_provider=status, token="N5q2zS7vJ9mP4kL8")
    server.start()
    try:
        assert fetch(server.url).status == 200
        with pytest.raises(HTTPError) as bad_token:
            fetch(server.url.replace("N5q2zS7vJ9mP4kL8", "wrong-token"))
        assert bad_token.value.code == 404
        with pytest.raises(HTTPError) as post:
            fetch(server.url, method="POST")
        assert post.value.code == 405
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.close()


def test_board_server_sse_starts_with_snapshot_and_has_no_cors(tmp_path):
    server = BoardServer(str(tmp_path), lambda: {"run": {"id": "run-1"}, "tasks": {}}, "token")
    server.start()
    try:
        with fetch(server.url + "events") as response:
            assert response.headers["Content-Type"] == "text/event-stream"
            assert "Access-Control-Allow-Origin" not in response.headers
            assert response.readline() == b"event: snapshot\n"
            payload = json.loads(response.readline().decode().split(": ", 1)[1])
            assert payload["run"]["id"] == "run-1"
    finally:
        server.close()


def test_board_metadata_is_private_and_removed_on_close(tmp_path):
    server = BoardServer(str(tmp_path), lambda: {"run": {}, "tasks": {}}, "token")
    server.start()
    metadata = json.loads((tmp_path / "board.json").read_text())
    assert metadata["pid"] > 0 and metadata["instance_id"]
    assert (tmp_path / "board.json").stat().st_mode & 0o777 == 0o600
    server.close()
    assert not (tmp_path / "board.json").exists()


def test_served_page_csp_header_allows_its_own_event_stream(tmp_path):
    # The browser enforces the header and the meta tag together; a header without
    # connect-src blocked EventSource while every HTML-only test passed.
    server = BoardServer(str(tmp_path), lambda: {"run": {"id": "run-1"}, "tasks": {}}, "token")
    server.start()
    try:
        policy = fetch(server.url).headers["Content-Security-Policy"]
        directives = {part.split()[0]: part.split()[1:] for part in policy.split(";") if part.strip()}
        assert "'self'" in directives.get("connect-src", [])
    finally:
        server.close()
