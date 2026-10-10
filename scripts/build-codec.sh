#!/usr/bin/env bash
# Reproducible local build; no system installation or radio firmware required.
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="$project_root/.cache/mbelib"
build_dir="$project_root/native/build"
mbelib_repository="https://github.com/szechyjs/mbelib.git"
mbelib_revision="9a04ed5c78176a9965f3d43f7aa1b1f5330e771f"
compiler="${CC:-cc}"

command -v git >/dev/null
command -v "$compiler" >/dev/null
mkdir -p "$project_root/.cache" "$build_dir"
if [[ ! -d "$source_dir" ]]; then
    git clone "$mbelib_repository" "$source_dir"
fi
if [[ ! -d "$source_dir/.git" ]]; then
    printf 'Expected mbelib git checkout at %s\n' "$source_dir" >&2
    exit 1
fi
if [[ -n "$(git -C "$source_dir" status --porcelain)" ]]; then
    printf 'Refusing to overwrite modified mbelib sources at %s\n' "$source_dir" >&2
    exit 1
fi
if ! git -C "$source_dir" cat-file -e "$mbelib_revision^{commit}" 2>/dev/null; then
    git -C "$source_dir" fetch origin "$mbelib_revision"
fi
git -C "$source_dir" checkout --quiet --detach "$mbelib_revision"

compile_flags=(-O2 -std=c11 -D_DEFAULT_SOURCE -fPIC -fvisibility=hidden)
"$compiler" "${compile_flags[@]}" -Wall -Wextra -Werror \
    -I"$source_dir" -c "$project_root/native/dmrdecode.c" \
    -o "$build_dir/dmrdecode.o"
# This substitution affects only mbelib's synthesis unit. The replacement RNG
# lives in the adapter and keeps separate state for every decoder instance.
"$compiler" "${compile_flags[@]}" -Drand=dmr_codec_rand \
    -I"$source_dir" -c "$source_dir/mbelib.c" -o "$build_dir/mbelib.o"
for unit in ecc ambe3600x2450; do
    "$compiler" "${compile_flags[@]}" -I"$source_dir" \
        -c "$source_dir/$unit.c" -o "$build_dir/$unit.o"
done
"$compiler" -shared -Wl,-z,defs -Wl,-z,relro,-z,now \
    "$build_dir/dmrdecode.o" "$build_dir/mbelib.o" \
    "$build_dir/ecc.o" "$build_dir/ambe3600x2450.o" \
    -lm -o "$build_dir/libdmrdecode.so"
cp "$source_dir/COPYRIGHT" "$build_dir/MBELIB_COPYRIGHT"
cp "$project_root/native/THIRD_PARTY_LICENSES.txt" "$build_dir/THIRD_PARTY_LICENSES.txt"
printf 'Built %s (mbelib %s)\n' "$build_dir/libdmrdecode.so" "$mbelib_revision"
