#!/usr/bin/env bash
# ==============================================================================
# RAHBAR: One-Command Cluster Runner + Live Port-Forward
# ==============================================================================
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${SCRIPT_DIR}"

BOLD="\033[1m"
GREEN="\033[32m"
BLUE="\033[34m"
YELLOW="\033[33m"
OFF="\033[0m"

# Help
if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    echo -e "\n${BOLD}${BLUE}RAHBAR: Grounded RAG on Kubernetes${OFF}"
    echo -e "Usage:"
    echo -e "  ${BOLD}./scripts/run.sh${OFF}       # Deploy full Kubernetes cluster + forward UI to localhost:8080"
    echo -e "  ${BOLD}make run${OFF}               # Deploy full cluster via Makefile"
    echo -e "  ${BOLD}./scripts/run.sh --help${OFF}# Show this help message\n"
    exit 0
fi

# 1. Run bootstrap and cluster deployment
bash scripts/up.sh

# 2. Automatically port-forward UI to localhost:8080
echo -e "\n${BOLD}${GREEN}======================================================================${OFF}"
echo -e "${BOLD}${GREEN}  RAHBAR Grounded RAG is LIVE!                                       ${OFF}"
echo -e "${BOLD}${GREEN}  Open in your browser:  ${BLUE}http://localhost:8080${OFF}                        ${OFF}"
echo -e "${BOLD}${GREEN}======================================================================${OFF}"
echo -e "${YELLOW}Port-forwarding UI service (Press Ctrl+C to exit)...${OFF}\n"

exec kubectl port-forward -n rahbar svc/ui 8080:8080
