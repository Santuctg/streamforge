#!/usr/bin/env bash
set -e
nginx -t >/dev/null 2>&1 && systemctl reload nginx
