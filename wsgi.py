"""
Production WSGI entry point.

Used by Gunicorn:
    gunicorn wsgi:app --workers 1 --bind 0.0.0.0:$PORT

Note the single worker: the app keeps wizard state in an in-process dict, so
multiple workers would lose sessions between requests. If you ever need to
scale beyond one worker, swap the in-memory store in app.py for Redis.
"""

from app import app

if __name__ == "__main__":
    app.run()
