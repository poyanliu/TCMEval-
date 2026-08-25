#!/bin/bash
# ── TCM Evaluation System — Cloud Server Setup ─────────────────────
# One-command deployment for Ubuntu 20.04+/Debian 11+.
# Run as root on a fresh cloud server with a public IP.
#
# Usage:
#   chmod +x setup.sh
#   ./setup.sh
#
# What this does:
#   1. Check system requirements (Docker, ports, .env)
#   2. Build Docker image (no GPU)
#   3. Request SSL certificate (interactive)
#   4. Start all services
#   5. Smoke-test the deployment

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── Colors ─────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

step()  { echo -e "${GREEN}[STEP]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Step 1: System check ──────────────────────────────────────────
step "1/5  Checking system requirements..."

# Docker
if ! command -v docker &>/dev/null; then
    error "Docker not found. Install: curl -fsSL https://get.docker.com | sh"
fi

if ! docker compose version &>/dev/null; then
    error "Docker Compose not available. Upgrade Docker to 20.10+."
fi

# Ports 80/443 must be free
for port in 80 443; do
    if ss -tlnp | grep -q ":${port} "; then
        warn "Port ${port} is in use. Stop the conflicting service first."
    fi
done

# .env
if [ ! -f .env ] || ! grep -q "ZHIPUAI_API_KEY=.\+" .env; then
    error ".env file missing or ZHIPUAI_API_KEY not set. Copy .env.example or create one."
fi

step "System check passed."

# ── Step 2: Build image ───────────────────────────────────────────
step "2/5  Building Docker image (this may take a few minutes)..."

docker compose build api streamlit

step "Build complete."

# ── Step 3: SSL certificate ───────────────────────────────────────
step "3/5  SSL certificate setup..."

echo ""
echo "  Make sure tcmeval.cn DNS A record points to this server's IP."
echo "  Your public IP: $(curl -s ifconfig.me 2>/dev/null || echo 'unknown')"
echo ""

read -p "  Proceed with SSL certificate request? [Y/n] " yn
case "$yn" in
    [Nn]*)
        warn "Skipping SSL. HTTPS will not work until cert is installed."
        ;;
    *)
        read -p "  Email address for Let's Encrypt: " EMAIL
        docker compose --profile production run --rm certbot \
            certonly --webroot --webroot-path=/var/www/certbot \
            -d tcmeval.cn -d www.tcmeval.cn \
            --email "$EMAIL" --agree-tos --no-eff-email
        step "SSL certificate obtained."
        ;;
esac

# ── Step 4: Start services ────────────────────────────────────────
step "4/5  Starting services..."

docker compose --profile production up -d

# Wait for health check
sleep 3
for i in $(seq 1 10); do
    if docker compose ps | grep -q "healthy"; then
        break
    fi
    sleep 2
done

step "Services started."

# ── Step 5: Verify ────────────────────────────────────────────────
step "5/5  Smoke test..."

HTTP_CODE=$(curl -sk -o /dev/null -w "%{http_code}" https://localhost/health 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    echo -e "${GREEN}  ✓ Health check passed (HTTPS 200)${NC}"
else
    warn "  Health check returned ${HTTP_CODE}. Check: docker compose logs api"
fi

# ── Done ──────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Deployment complete!${NC}"
echo ""
echo "  URL:    https://tcmeval.cn"
echo "  Streamlit: https://tcmeval.cn/literature"
echo "  API docs:  https://tcmeval.cn/docs"
echo ""
echo "  Useful commands:"
echo "    docker compose --profile production logs -f   # View all logs"
echo "    docker compose --profile production restart    # Restart all"
echo "    docker compose --profile production down       # Stop all"
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
