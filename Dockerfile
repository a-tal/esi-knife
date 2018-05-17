FROM python:3.6
MAINTAINER Adam Talsma <adam@talsma.ca>

# caching pre-reqs for faster rebuilds
COPY requirements.txt /src/
WORKDIR /src
RUN pip install -qUr requirements.txt

COPY . /src
RUN pip install -q . && rm -rf /src
WORKDIR /

RUN addgroup --system knife && \
    adduser --system --group knife

USER knife

CMD gunicorn \
    --threads ${THREADS-1000} \
    --workers ${WORKERS-1} \
    --worker-class ${WORKER_CLASS-gevent} \
    --timeout ${TIMEOUT-10} \
    --graceful-timeout ${GRACEFUL_TIMEOUT-2} \
    --limit-request-line ${LIMIT_REQUEST_LINE-8190} \
    --capture-output \
    --bind "0.0.0.0:${PORT-8080}" \
    "esi_knife.web:main()"
