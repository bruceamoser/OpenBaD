from __future__ import annotations

from pathlib import Path

from openbad.state.db import initialize_state_db
from openbad.toolbelt.access_control import (
    approve_access_request,
    create_access_request,
    effective_allowed_roots,
    list_access_grants,
    list_access_requests,
    revoke_access_grant,
)


def test_access_request_approve_and_revoke(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    initialize_state_db(db_path)
    target = tmp_path / "outside" / "specs"

    created = create_access_request(
        str(target),
        requester="test",
        reason="Need to inspect specs",
        db_path=db_path,
    )
    request = created["request"]
    assert created["status"] == "pending"
    assert request["normalized_root"] == str(target)
    assert list_access_requests(db_path=db_path, status="pending")

    approved = approve_access_request(request["request_id"], approved_by="user", db_path=db_path)
    assert approved["grant"]["normalized_root"] == str(target)
    roots = effective_allowed_roots([], db_path=db_path)
    assert str(target) in roots

    revoked = revoke_access_grant(approved["grant"]["grant_id"], revoked_by="user", db_path=db_path)
    assert revoked["revoked_by"] == "user"
    active_grants = list_access_grants(db_path=db_path)
    assert active_grants == []