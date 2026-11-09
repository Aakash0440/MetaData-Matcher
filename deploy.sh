#!/usr/bin/env bash
# One-off deploy from your machine. Needs: npm i -g wrangler && wrangler login
set -e
cd "$(dirname "$0")"
wrangler pages project create metadata-matcher --production-branch main 2>/dev/null || true
wrangler pages deploy web --project-name=metadata-matcher --branch=main
