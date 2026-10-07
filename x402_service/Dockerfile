# OMEGA Opportunity Intelligence — x402 sell-side service
# Deploy target: any free-tier PaaS that builds a Dockerfile
# (Render free web service, Hugging Face Docker Space, Railway, Fly.io).
# The app reads PORT from the environment (PaaS-injected) and binds 0.0.0.0.
FROM python:3.12-slim

WORKDIR /app
# Build context is income_assets/ (see render.yaml: dockerContext ".",
# dockerfilePath ./x402_service/Dockerfile), so requirements.txt lives
# under x402_service/ in the context — not at the context root.
COPY x402_service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# app + the screener package it wraps (paths mirror the repo layout)
COPY x402_service/ ./x402_service/
COPY opportunity_intelligence/ ./opportunity_intelligence/

WORKDIR /app/x402_service
ENV OMEGA_X402_FACILITATOR_URL=https://facilitator.payai.network
ENV OMEGA_X402_NETWORK=eip155:8453
# OMEGA_PAY_TO is injected at deploy time (burner wallet, not a secret)

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8766}"]
