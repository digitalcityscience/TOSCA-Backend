#!/bin/sh
set -eu

garage() {
  /garage -c "${GARAGE_CONFIG_FILE:-/etc/garage/garage.toml}" "$@"
}

: "${GARAGE_PRIVATE_BUCKET:?GARAGE_PRIVATE_BUCKET is required}"
: "${GARAGE_PUBLIC_BUCKET:?GARAGE_PUBLIC_BUCKET is required}"
: "${GARAGE_ARCHIVE_BUCKET:?GARAGE_ARCHIVE_BUCKET is required}"
: "${GARAGE_ACCESS_KEY:?GARAGE_ACCESS_KEY is required}"

printf '%s\n' 'Waiting for the Garage single-node layout...'
i=0
while ! garage status >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 60 ]; then
    echo 'Garage did not become ready within 60 seconds.' >&2
    exit 1
  fi
  sleep 1
done

ensure_bucket() {
  bucket="$1"
  if ! garage bucket info "$bucket" >/dev/null 2>&1; then
    garage bucket create "$bucket"
  fi
  garage bucket allow --read --write --owner "$bucket" --key "$GARAGE_ACCESS_KEY"
}

ensure_bucket "$GARAGE_PRIVATE_BUCKET"
ensure_bucket "$GARAGE_PUBLIC_BUCKET"
ensure_bucket "$GARAGE_ARCHIVE_BUCKET"

for bucket in \
  "${GARAGE_TEST_PRIVATE_BUCKET:-}" \
  "${GARAGE_TEST_PUBLIC_BUCKET:-}" \
  "${GARAGE_TEST_ARCHIVE_BUCKET:-}" \
  "${GARAGE_STATIC_BUCKET:-}"
do
  if [ -n "$bucket" ]; then
    ensure_bucket "$bucket"
  fi
done

printf 'Garage bootstrap complete: %s, %s, %s' "$GARAGE_PRIVATE_BUCKET" "$GARAGE_PUBLIC_BUCKET" "$GARAGE_ARCHIVE_BUCKET"
if [ -n "${GARAGE_TEST_PRIVATE_BUCKET:-}" ] || [ -n "${GARAGE_TEST_PUBLIC_BUCKET:-}" ] || [ -n "${GARAGE_TEST_ARCHIVE_BUCKET:-}" ]; then
  printf ', test buckets: %s, %s, %s' \
    "${GARAGE_TEST_PRIVATE_BUCKET:-}" \
    "${GARAGE_TEST_PUBLIC_BUCKET:-}" \
    "${GARAGE_TEST_ARCHIVE_BUCKET:-}"
fi
if [ -n "${GARAGE_STATIC_BUCKET:-}" ]; then
  printf ', static bucket: %s' "$GARAGE_STATIC_BUCKET"
fi
printf '\n'
