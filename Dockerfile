FROM python:3.12.1-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY . /app
RUN useradd --uid 10001 --create-home runner && mkdir -p /results && chown runner:runner /results
USER 10001:10001
ENTRYPOINT ["python", "scripts/verify.py"]
CMD ["--output", "/results/evidence"]
