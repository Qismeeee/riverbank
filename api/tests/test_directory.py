import json

import pytest

import config
from services.directory import UserDirectory
from services.underwriting.models import WorkflowError
from services.underwriting.sources import SellerBook


def user(subject="minhchau.shop", role="seller", full_name="Lê Minh Châu"):
    return {"subject": subject, "role": role, "full_name": full_name, "display_name": "Shop"}


def write(tmp_path, users):
    path = tmp_path / "users.json"
    path.write_text(json.dumps({"users": users}), encoding="utf-8")
    return path


def test_every_seller_in_book_has_a_named_account():
    directory = UserDirectory.load(config.USER_DIRECTORY_PATH)
    for seller in SellerBook.load().sellers:
        record = directory.find(seller.seller_id, "seller")
        assert record is not None and record.full_name, seller.seller_id
    assert any(u.role == "underwriter" for u in directory.users.values())


@pytest.mark.parametrize(
    "users",
    [
        [user(), user()],
        [user(role="admin")],
        [user(full_name=" ")],
        [user(subject="has space")],
        [{"subject": "x", "role": "seller"}],
    ],
)
def test_invalid_directory_is_rejected(tmp_path, users):
    with pytest.raises(WorkflowError) as error:
        UserDirectory.load(write(tmp_path, users))
    assert error.value.code == "USER_DIRECTORY_INVALID"


def test_find_matches_subject_and_role(tmp_path):
    directory = UserDirectory.load(write(tmp_path, [user()]))
    assert directory.find("minhchau.shop", "seller").full_name == "Lê Minh Châu"
    assert directory.find("minhchau.shop", "underwriter") is None
    assert directory.find("other", "seller") is None
