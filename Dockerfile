FROM python:3.12-slim
ARG KUBECTL_VERSION=v1.31.2
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
 && curl -fsSLo /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
 && chmod +x /usr/local/bin/kubectl \
 && apt-get purge -y curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY capplan ./capplan
RUN pip install --no-cache-dir . && useradd -u 10001 -m planner
USER 10001
ENTRYPOINT ["capplan"]
CMD ["plan", "--live"]
