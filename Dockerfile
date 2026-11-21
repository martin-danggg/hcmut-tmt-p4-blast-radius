# The scorer builds this on linux/amd64 and runs it with no network. The
# digest is the image every commit was labeled in.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

# The environment the commits were labeled in (needed to run sqlglot's tests).
COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements.lock

# sqlglot source at the pinned commit.
ARG SQLGLOT_SHA=eb11aec4be8cb2122afe713a4816662c3f0fb2fa
RUN python -c "import io, tarfile, time, urllib.request, os, shutil; \
url = 'https://github.com/tobymao/sqlglot/archive/${SQLGLOT_SHA}.tar.gz'; \
data = None; \
exec('for i in range(5):\n    try:\n        data = urllib.request.urlopen(url, timeout=60).read(); break\n    except Exception as e:\n        print(\"retry\", i, e); time.sleep(5 * (i + 1))'); \
tarfile.open(fileobj=io.BytesIO(data)).extractall('/opt', filter='data'); \
os.rename('/opt/sqlglot-${SQLGLOT_SHA}', '/opt/sqlglot')" \
 && test -f /opt/sqlglot/sqlglot/parser.py

# Warm bytecode and pytest's assertion-rewrite cache so runs under a read-only disk start fast.
RUN cd /opt/sqlglot && SKIP_INTEGRATION=0 PYTHONPATH=/opt/sqlglot \
    python -m pytest --collect-only -q -p no:cacheprovider tests > /dev/null \
 && python -m compileall -q sqlglot tests

COPY selector.py /app/selector.py
COPY blast /app/blast
COPY map /app/map
RUN python -m compileall -q /app && chmod -R a+rX /app /opt/sqlglot

ENV PYTHONDONTWRITEBYTECODE=1 PYTHON_CPU_COUNT=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 BR_HEAVY_SKIP=1
CMD ["python", "/app/selector.py"]
