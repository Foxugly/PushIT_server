# Gunicorn config for PushIT API (prod).
# Bound to a local TCP port; nginx reverse-proxies to it (127.0.0.1:8001).
# Matches the convention of the other Django services on this server.

bind = "127.0.0.1:8001"
workers = 3
worker_class = "sync"
timeout = 60
graceful_timeout = 30
keepalive = 5

accesslog = "/var/log/pushit/gunicorn-access.log"
errorlog = "/var/log/pushit/gunicorn-error.log"
loglevel = "info"

preload_app = True
