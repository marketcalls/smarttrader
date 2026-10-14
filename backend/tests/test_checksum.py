import hashlib

from app.broker.tradesmart import build_authorize_url, make_checksum, sha256_hex


def test_checksum_matches_docs_example():
    # Docs: App Key ABC, Secret 123, code x1y2z3 -> SHA-256("ABC123x1y2z3")
    expected = "7b482d7b380a3067eaba4c9c909b19253c4fa0edb5833e246401b8497c99a9c3"
    assert hashlib.sha256(b"ABC123x1y2z3").hexdigest() == expected
    assert make_checksum("ABC", "123", "x1y2z3") == expected


def test_checksum_order_is_key_secret_code():
    assert make_checksum("K", "S", "C") == sha256_hex("KSC")
    assert make_checksum("K", "S", "C") != sha256_hex("KCS")


def test_checksum_is_lowercase_hex():
    value = make_checksum("key", "secret", "code")
    assert len(value) == 64
    assert value == value.lower()
    int(value, 16)


def test_authorize_url_encodes_client_id():
    assert build_authorize_url("ABC123") == (
        "https://v2api.tradesmartonline.in/OAuthlogin/authorize/oauth?client_id=ABC123"
    )
    assert build_authorize_url("A B&C").endswith("client_id=A+B%26C")
