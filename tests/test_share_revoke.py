"""A share link you cannot withdraw is not a share feature, it is a leak.

The token is a self-contained JWT, which is why reading a shared report needs
no database. It is also why the link could not be taken back: the only recourse
was rotating ``WEB_SECRET_KEY``, which signs the operator out of the console and
invalidates every other link at the same time.
"""

from __future__ import annotations

import time

import pytest

from web import share as share_mod


@pytest.fixture(autouse=True)
def registry_path(tmp_path, monkeypatch):
    """Point the registry at a temp file; never the live one."""
    import web.settings as web_settings

    settings = web_settings.get_settings()
    monkeypatch.setattr(settings.paths, "state_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(share_mod, "_registry_path",
                        lambda: str(tmp_path / "share_links.json"))
    return tmp_path / "share_links.json"


def test_a_new_link_verifies_and_is_listed_as_live():
    token, exp, jti = share_mod.create_share_token("output/x.md", created_by="admin")
    assert share_mod.verify_share_token(token)["path"] == "output/x.md"
    assert exp > time.time()

    links = share_mod.list_links()
    assert len(links) == 1
    assert links[0]["jti"] == jti
    assert links[0]["state"] == "live"
    assert links[0]["by"] == "admin"


def test_revoking_stops_the_link_working():
    token, _exp, jti = share_mod.create_share_token("output/x.md", created_by="admin")
    assert share_mod.verify_share_token(token) is not None

    assert share_mod.revoke(jti) is True
    assert share_mod.verify_share_token(token) is None, \
        "a revoked link still reads the report"
    assert share_mod.list_links()[0]["state"] == "revoked"


def test_revoking_twice_is_not_an_error():
    _token, _exp, jti = share_mod.create_share_token("output/x.md")
    assert share_mod.revoke(jti) is True
    assert share_mod.revoke(jti) is False, "second revoke should report no change"


def test_revoking_one_link_leaves_the_others_alone():
    a, _e, ja = share_mod.create_share_token("output/a.md")
    b, _e, jb = share_mod.create_share_token("output/b.md")

    share_mod.revoke(ja)
    assert share_mod.verify_share_token(a) is None
    assert share_mod.verify_share_token(b) is not None


def test_expired_links_are_hidden_but_revocable_ones_are_kept(registry_path):
    _t, _e, jti = share_mod.create_share_token("output/x.md")
    # Age it past its expiry by rewriting the registry.
    import json
    entries = json.loads(registry_path.read_text(encoding="utf-8"))
    entries[0]["expires_at"] = int(time.time()) - 10
    registry_path.write_text(json.dumps(entries), encoding="utf-8")

    assert share_mod.list_links() == [], "an expired link should not be listed"
    assert share_mod.list_links(include_expired=True)[0]["state"] == "expired"
    # Still revocable, in case it is listed again.
    assert share_mod.revoke(jti) is True


def test_a_token_with_no_jti_still_verifies():
    """Links minted before jti existed must not all break at once.

    Refusing them would invalidate links that are currently working, which is
    the opposite of what a revocation feature is for.
    """
    import jwt
    from web.settings import get_settings

    payload = {"scope": share_mod.SCOPE, "path": "output/old.md",
               "by": "admin", "iat": int(time.time()),
               "exp": int(time.time()) + 3600}
    legacy = jwt.encode(payload, get_settings().auth.secret_key,
                        algorithm=share_mod._ALG)
    assert share_mod.verify_share_token(legacy) is not None


def test_another_scope_is_still_refused():
    import jwt
    from web.settings import get_settings

    now = int(time.time())
    other = jwt.encode({"scope": "something-else", "path": "x",
                        "iat": now, "exp": now + 60},
                       get_settings().auth.secret_key, algorithm=share_mod._ALG)
    assert share_mod.verify_share_token(other) is None


def test_ttl_is_clamped_at_both_ends():
    _t, exp_short, _j = share_mod.create_share_token("x", ttl_hours=0)
    assert exp_short > time.time() + 3500
    _t2, exp_long, _j2 = share_mod.create_share_token("x", ttl_hours=99_999)
    assert exp_long <= time.time() + share_mod.MAX_TTL_HOURS * 3600 + 5


def test_a_corrupt_registry_does_not_lock_every_share_out(registry_path):
    # The registry is a convenience for listing and for revocation; a damaged
    # file must not turn into an outage for links that are otherwise fine.
    registry_path.write_text("{not json", encoding="utf-8")
    assert share_mod.list_links() == []          # reads as empty, does not raise
    assert share_mod.verify_share_token("nonsense") is None

    # And a fresh link works, and repairs the file by being appended to it.
    token, _exp, jti = share_mod.create_share_token("output/x.md")
    assert share_mod.verify_share_token(token) is not None
    assert share_mod.revoke(jti) is True
