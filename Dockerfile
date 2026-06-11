FROM python:3.11-slim

# Run as non-root (uid 1000 = host 'geebee') so CLI runs stop writing root-owned
# files into the bind-mounted ./data and ./outreach. The fix lives in the image —
# a compose user: override is insufficient (WORKDIR /app is root-owned). See
# tae-docs Slate "Custom TAE app containers run as root — convert to non-root".
RUN groupadd -g 1000 app && useradd -m -u 1000 -g 1000 -s /usr/sbin/nologin app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=1000:1000 outreach/ outreach/

USER 1000

CMD ["python", "-m", "outreach", "--help"]
