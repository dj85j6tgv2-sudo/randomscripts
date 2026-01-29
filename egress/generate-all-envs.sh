#!/bin/bash
#
# generate-all-envs.sh
#
# Generate Envoy egress configurations for all environments (dev, stg, prd)
#
# Usage:
#   ./generate-all-envs.sh
#   ./generate-all-envs.sh --validate
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ALLOWLIST="egress-allowlist.yaml"
TEMPLATE="envoy.yaml.j2"
VALIDATE_FLAG=""

# Parse arguments
if [[ "$1" == "--validate" ]]; then
    VALIDATE_FLAG="--validate"
    echo "Validation enabled"
fi

echo "========================================================================"
echo "Generating Envoy configs for all environments"
echo "========================================================================"
echo ""

# Check required files exist
if [[ ! -f "$ALLOWLIST" ]]; then
    echo "ERROR: Allowlist not found: $ALLOWLIST"
    exit 1
fi

if [[ ! -f "$TEMPLATE" ]]; then
    echo "ERROR: Template not found: $TEMPLATE"
    exit 1
fi

# Generate DEV config
echo "=========================================="
echo "1/3: Generating DEV environment config"
echo "=========================================="
python3 generate-envoy-config.py \
    --env dev \
    --allowlist "$ALLOWLIST" \
    --template "$TEMPLATE" \
    --output envoy-dev.yaml \
    $VALIDATE_FLAG

echo ""

# Generate STG config
echo "=========================================="
echo "2/3: Generating STG environment config"
echo "=========================================="
python3 generate-envoy-config.py \
    --env stg \
    --allowlist "$ALLOWLIST" \
    --template "$TEMPLATE" \
    --output envoy-stg.yaml \
    $VALIDATE_FLAG

echo ""

# Generate PRD config
echo "=========================================="
echo "3/3: Generating PRD environment config"
echo "=========================================="
python3 generate-envoy-config.py \
    --env prd \
    --allowlist "$ALLOWLIST" \
    --template "$TEMPLATE" \
    --output envoy-prd.yaml \
    $VALIDATE_FLAG

echo ""
echo "========================================================================"
echo "✓ All environment configs generated successfully!"
echo "========================================================================"
echo ""
echo "Generated files:"
echo "  - envoy-dev.yaml (DEV environment)"
echo "  - envoy-stg.yaml (STG environment)"
echo "  - envoy-prd.yaml (PRD environment)"
echo ""
echo "Next steps:"
echo "  1. Review the generated configs"
echo "  2. Deploy to respective environments:"
echo ""
echo "     # DEV"
echo "     kubectl apply -f envoy-dev.yaml -n dev"
echo ""
echo "     # STG"
echo "     kubectl apply -f envoy-stg.yaml -n stg"
echo ""
echo "     # PRD (requires approval)"
echo "     kubectl apply -f envoy-prd.yaml -n prd"
echo ""
echo "  3. Monitor logs:"
echo "     kubectl logs -f deployment/envoy-proxy -n <env>"
echo ""
