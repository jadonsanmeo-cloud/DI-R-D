FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install --no-cache-dir \
    matplotlib==3.11.0 \
    openpyxl==3.1.5 \
    xlrd==2.0.2 \
    pandas==3.0.3 \
    pyarrow==25.0.0 \
    pypdf==6.14.2

WORKDIR /workspace

CMD ["python", "-c", "import time; time.sleep(86400)"]
