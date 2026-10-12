# The modules read the settings at import: values for the tests, no database is used (db is monkeypatched)
import os
import sys

os.environ.setdefault('DB_USER', 'test')
os.environ.setdefault('DB_PASSWORD', 'test')
os.environ.setdefault('DB_NAME', 'test')
os.environ.setdefault('JWT_SECRET_KEY', 'test-secret-key-test-secret-key-0123456789')
os.environ.setdefault('PUBLIC_URL', 'https://push.example.com')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
