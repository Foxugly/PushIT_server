import multiprocessing

bind = "unix:/run/pushit/gunicorn.sock"
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"
timeout = 30
graceful_timeout = 10

accesslog = "/var/log/pushit/gunicorn-access.log"
errorlog = "/var/log/pushit/gunicorn-error.log"
loglevel = "info"

preload_app = True
