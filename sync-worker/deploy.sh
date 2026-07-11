#!/bin/bash
# Copies the debug map + configs into the worker's asset dir and deploys.
set -e
cd "$(dirname "$0")"
mkdir -p public
cp ../index.html public/
cp ../*.project.json public/ 2>/dev/null || true
wrangler deploy
