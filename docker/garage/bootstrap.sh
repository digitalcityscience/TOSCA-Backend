#!/bin/sh
set -eu

garage() {
  /garage -c "$GARAGE_CONFIG_FILE" "$@"
}

: "${GARAGE_RPC_SECRET:?GARAGE_RPC_SECRET is required}"
: "${GARAGE_ADMIN_TOKEN:?GARAGE_ADMIN_TOKEN is required}"
: "${GARAGE_PRIVATE_BUCKET:?GARAGE_PRIVATE_BUCKET is required}"
: "${GARAGE_PUBLIC_BUCKET:?GARAGE_PUBLIC_BUCKET is required}"
: "${GARAGE_ARCHIVE_BUCKET:?GARAGE_ARCHIVE_BUCKET is required}"
: "${GARAGE_ACCESS_KEY:?GARAGE_ACCESS_KEY is required}"

GARAGE_CONFIG_FILE=${GARAGE_CONFIG_FILE:-/tmp/garage.toml}
export GARAGE_CONFIG_FILE
umask 077
cat > "$GARAGE_CONFIG_FILE" <<EOF
metadata_dir = "/var/lib/garage/meta"
data_dir = "/var/lib/garage/data"
db_engine = "sqlite"
replication_factor = 1
compression_level = 2
rpc_bind_addr = "[::]:3901"
rpc_public_addr = "garage:3901"
rpc_secret = "${GARAGE_RPC_SECRET}"

[s3_api]
s3_region = "${S3_REGION_NAME:-garage}"
api_bind_addr = "[::]:3900"

[admin]
api_bind_addr = "[::]:3903"
admin_token = "${GARAGE_ADMIN_TOKEN}"
EOF

printf '%s\n' 'Waiting for the Garage single-node layout...'
i=0
while ! last_error=$(garage status 2>&1); do
  i=$((i + 1))
  if [ "$i" -ge 60 ]; then
    echo 'Garage did not become ready within 60 seconds. Last error:' >&2
    printf '%s\n' "$last_error" >&2
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

# No bucket gets anonymous website access: default, media_public and
# media_archive are all private S3 buckets. "Public" media is only reachable
# through a presigned URL Django issues after checking the asset/entity is
# actually public/published -- not through anonymous bucket access.
ensure_bucket "$GARAGE_PRIVATE_BUCKET"
ensure_bucket "$GARAGE_PUBLIC_BUCKET"
ensure_bucket "$GARAGE_ARCHIVE_BUCKET"

if [ -n "${GARAGE_TEST_PRIVATE_BUCKET:-}" ]; then
  ensure_bucket "$GARAGE_TEST_PRIVATE_BUCKET"
fi
if [ -n "${GARAGE_TEST_PUBLIC_BUCKET:-}" ]; then
  ensure_bucket "$GARAGE_TEST_PUBLIC_BUCKET"
fi
if [ -n "${GARAGE_TEST_ARCHIVE_BUCKET:-}" ]; then
  ensure_bucket "$GARAGE_TEST_ARCHIVE_BUCKET"
fi

printf 'Garage bootstrap complete: %s, %s, %s' "$GARAGE_PRIVATE_BUCKET" "$GARAGE_PUBLIC_BUCKET" "$GARAGE_ARCHIVE_BUCKET"
if [ -n "${GARAGE_TEST_PRIVATE_BUCKET:-}" ] || [ -n "${GARAGE_TEST_PUBLIC_BUCKET:-}" ] || [ -n "${GARAGE_TEST_ARCHIVE_BUCKET:-}" ]; then
  printf ', test buckets: %s, %s, %s' \
    "${GARAGE_TEST_PRIVATE_BUCKET:-}" \
    "${GARAGE_TEST_PUBLIC_BUCKET:-}" \
    "${GARAGE_TEST_ARCHIVE_BUCKET:-}"
fi
printf '\n'
