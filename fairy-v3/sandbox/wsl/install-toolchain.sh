#!/bin/sh
set -eu

NODE_VERSION="24.18.0"
PNPM_VERSION="10.34.4"
YARN_VERSION="1.22.22"
UV_VERSION="0.11.28"

case "$(dpkg --print-architecture)" in
  amd64) node_arch="x64" ;;
  arm64) node_arch="arm64" ;;
  *) echo "unsupported FairySandbox architecture" >&2; exit 2 ;;
esac

work="$(mktemp -d)"
gnupg="$(mktemp -d)"
cleanup() {
  rm -rf "$work" "$gnupg"
}
trap cleanup EXIT INT TERM

export GNUPGHOME="$gnupg"
for key in \
  5BE8A3F6C8A5C01D106C0AD820B1A390B168D356 \
  DD792F5973C6DE52C432CBDAC77ABFA00DDBF2B7 \
  CC68F5A3106FF448322E48ED27F5E38D5B0A215F \
  8FCCA13FEF1D0C2E91008E09770F7A9A5AE15600 \
  890C08DB8579162FEE0DF9DB8BEAB4DFCF555EF4 \
  C82FA3AE1CBEDC6BE46B9360C43CEC45C17AB93C \
  108F52B48DB57BB0CC439B2997B01419BD92F80A \
  A363A499291CBBC940DD62E41F10027AF002F8B0
do
  gpg --batch --keyserver hkps://keys.openpgp.org --recv-keys "$key" \
    || gpg --batch --keyserver keyserver.ubuntu.com --recv-keys "$key"
done

cd "$work"
base="https://nodejs.org/dist/v${NODE_VERSION}"
archive="node-v${NODE_VERSION}-linux-${node_arch}.tar.xz"
curl --fail --location --silent --show-error --remote-name "${base}/${archive}"
curl --fail --location --silent --show-error --remote-name "${base}/SHASUMS256.txt"
curl --fail --location --silent --show-error --remote-name "${base}/SHASUMS256.txt.sig"
gpg --batch --verify SHASUMS256.txt.sig SHASUMS256.txt
grep " ${archive}$" SHASUMS256.txt | sha256sum --check --strict
tar --extract --xz --file "$archive" --directory /usr/local --strip-components=1 --no-same-owner
ln --symbolic --force /usr/local/bin/node /usr/local/bin/nodejs

/usr/bin/python3 -m venv /opt/fairy-python-toolchain
/opt/fairy-python-toolchain/bin/python -m pip install \
  --disable-pip-version-check --no-cache-dir "uv==${UV_VERSION}"
ln --symbolic --force /opt/fairy-python-toolchain/bin/uv /usr/local/bin/uv

/usr/local/bin/npm install --global --ignore-scripts \
  "pnpm@${PNPM_VERSION}" "yarn@${YARN_VERSION}"

test "$(/usr/local/bin/node --version)" = "v${NODE_VERSION}"
test "$(/usr/local/bin/pnpm --version)" = "${PNPM_VERSION}"
test "$(/usr/local/bin/yarn --version)" = "${YARN_VERSION}"
test "$(/usr/local/bin/uv --version | cut -d ' ' -f 1-2)" = "uv ${UV_VERSION}"
