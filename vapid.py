# VAPID keys of the sites: generation, import of existing keys and the Vapid objects used to sign the sendings.
#
# The keys are stored in the table site in the format of the old private_key.txt / public_key.txt
# (README.md > VAPID keys): private key = base64url of the raw 32 bytes, public key = base64url of the
# uncompressed point (65 bytes), the applicationServerKey of the browsers.
#
#     python vapid.py import <site code> private_key.txt public_key.txt
import sys
import threading

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from py_vapid.utils import b64urlencode


class InvalidKeys(ValueError):
    """Keys not valid."""


def public_key_of(vapid):
    raw = vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return b64urlencode(raw)


def private_key_of(vapid):
    return b64urlencode(vapid.private_key.private_numbers().private_value.to_bytes(32, 'big'))


def generate():
    """A new key pair: (private key, public key)."""
    vapid = Vapid()
    vapid.generate_keys()
    return private_key_of(vapid), public_key_of(vapid)


def check(private_key, public_key=None):
    """The key pair normalized (public key derived from the private one); InvalidKeys if not valid or not matching."""
    # base64 wraps the lines at 76 characters (public_key.txt of the README): without spaces and newlines
    private_key = ''.join((private_key or '').split())
    public_key = ''.join((public_key or '').split()).rstrip('=')
    try:
        vapid = Vapid.from_string(private_key=private_key)
    except Exception:
        raise InvalidKeys("The private key is not valid: base64url of the raw 32 bytes (private_key.txt)")
    derived = public_key_of(vapid)
    if public_key and public_key != derived:
        raise InvalidKeys("The public key doesn't belong to the private key")
    return private_key_of(vapid), derived


_cache = {}
_cache_lock = threading.Lock()


def signer(private_key):
    """The Vapid object of a private key, created once (pywebpush would parse the key at every sending)."""
    with _cache_lock:
        if private_key not in _cache:
            _cache[private_key] = Vapid.from_string(private_key=private_key)
        return _cache[private_key]


def import_files(code, private_file, public_file):
    import db
    site = db.get_site(code=code)
    if site is None:
        sys.exit(f"No site with code '{code}'")
    with open(private_file) as f:
        private_key = f.read()
    with open(public_file) as f:
        public_key = f.read()
    try:
        private_key, public_key = check(private_key, public_key)
    except InvalidKeys as e:
        sys.exit(str(e))
    db.set_site_keys(site['id'], private_key, public_key)
    print(f"Keys imported for {site['name']}: public key {public_key}")


if __name__ == '__main__':
    if len(sys.argv) != 5 or sys.argv[1] != 'import':
        sys.exit("Usage: python vapid.py import <site code> <private_key.txt> <public_key.txt>")
    import_files(*sys.argv[2:])
