"""Regenerate `libraries.toml`: the C libraries a configure script links by `-l` name that the
Bazel Central Registry provides as modules, with each module's latest version and the library
target its overlay defines.

`CANDIDATES` maps the `-l` names autoconf projects use to the registry modules that may
provide them (the first that exists wins: `ssl` is openssl before boringssl).  The registry is
scanned for the module's latest version and its overlay `BUILD.bazel`; a module whose BUILD
files live inside the archive or a patch has no overlay to read, so it is skipped with a
message rather than guessed, unless `SHIPPED_TARGETS` names the target its archive defines.  Names the host provides (`m`, `dl`, `rt`, `pthread`) are not
here: they are link options, not modules.

A module keeps the upstream release the table already holds and takes its newest `.bcr.N`;
`--allow-upstream <module>` lets one move to a newer upstream release.

Usage: `bazel run //tools/convert:update_libraries`, which reads the public registry at
https://bcr.bazel.build; `--registry <bazel-central-registry clone>` reads a clone's
`origin/main` instead (a fork with entries not published yet).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from tools.convert import libraries, tomlout
from tools.convert.index import workspace_root
from tools.convert.libraries import upstream_version
from tools.convert.source import USER_AGENT

UPDATE_COMMAND = "bazel run //tools/convert:update_libraries"
PUBLIC_REGISTRY = "https://bcr.bazel.build"

CANDIDATES: dict[str, list[str]] = {
    # compression
    "z": ["zlib"],
    "bz2": ["bzip2"],
    "lzma": ["xz"],
    "zstd": ["zstd"],
    "lz4": ["lz4"],
    "brotlidec": ["brotli"],
    "brotlienc": ["brotli"],
    "zip": ["libzip"],
    "zzip": ["zziplib"],
    "archive": ["libarchive"],
    # arithmetic
    "gmp": ["gmp"],
    "mpfr": ["mpfr"],
    "mpc": ["mpc"],
    # text, parsing, data
    "ffi": ["libffi"],
    "pcre2-8": ["pcre2"],
    "pcre": ["pcre"],
    "re2": ["re2"],
    "expat": ["expat"],
    "xml2": ["libxml2"],
    "xslt": ["libxslt"],
    "yaml": ["libyaml", "yaml"],
    "json-c": ["json-c"],
    "jansson": ["jansson"],
    "cjson": ["cjson"],
    "icuuc": ["icu"],
    "icui18n": ["icu"],
    "iconv": ["libiconv"],
    "intl": ["gettext"],
    "unistring": ["libunistring"],
    "idn2": ["libidn2"],
    "psl": ["libpsl"],
    "popt": ["popt"],
    "argp": ["argp-standalone"],
    "magic": ["file"],
    "tre": ["tre"],
    "protobuf": ["protobuf"],
    "protobuf-c": ["protobuf-c"],
    # crypto, tls
    "ssl": ["openssl", "boringssl"],
    "crypto": ["openssl", "boringssl"],
    "gnutls": ["gnutls"],
    "nettle": ["nettle"],
    "hogweed": ["nettle"],
    "gcrypt": ["libgcrypt"],
    "gpg-error": ["libgpg-error"],
    "sodium": ["libsodium"],
    "mbedtls": ["mbedtls"],
    "mbedcrypto": ["mbedtls"],
    "crypt": ["libxcrypt"],
    # networking
    "curl": ["curl"],
    "nghttp2": ["nghttp2"],
    "ssh2": ["libssh2"],
    "ssh": ["libssh"],
    "pcap": ["libpcap"],
    "microhttpd": ["libmicrohttpd"],
    "websockets": ["libwebsockets"],
    "mosquitto": ["mosquitto"],
    "zmq": ["libzmq"],
    "czmq": ["czmq"],
    "hiredis": ["hiredis"],
    "pq": ["postgres", "libpq"],
    "mysqlclient": ["mysql"],
    # event loops
    "event": ["libevent"],
    "event_core": ["libevent"],
    "ev": ["libev"],
    "uv": ["libuv"],
    # storage
    "sqlite3": ["sqlite3", "sqlite"],
    "leveldb": ["leveldb"],
    "rocksdb": ["rocksdb"],
    "lmdb": ["lmdb"],
    "snappy": ["snappy"],
    # terminal
    "ncurses": ["ncurses"],
    "ncursesw": ["ncurses"],
    "tinfo": ["ncurses"],
    "termcap": ["ncurses"],
    "readline": ["readline"],
    "edit": ["libedit"],
    # system
    "cap": ["libcap"],
    "selinux": ["libselinux"],
    "acl": ["acl"],
    "attr": ["attr"],
    "uuid": ["libuuid", "util-linux"],
    "mount": ["util-linux"],
    "blkid": ["util-linux"],
    "udev": ["libudev", "eudev"],
    "systemd": ["systemd"],
    "dbus-1": ["dbus"],
    "seccomp": ["libseccomp"],
    "kmod": ["kmod"],
    "numa": ["numactl"],
    "hwloc": ["hwloc"],
    "fuse": ["libfuse"],
    "fuse3": ["libfuse"],
    "usb-1.0": ["libusb"],
    "gpiod": ["libgpiod"],
    "elf": ["elfutils", "libelf"],
    "dw": ["elfutils"],
    "unwind": ["libunwind"],
    "bfd": ["binutils"],
    "iberty": ["binutils"],
    "ltdl": ["libtool"],
    # images, fonts, graphics
    "png": ["libpng"],
    "jpeg": ["libjpeg-turbo", "libjpeg"],
    "turbojpeg": ["libjpeg-turbo"],
    "tiff": ["libtiff"],
    "gif": ["giflib"],
    "webp": ["libwebp"],
    "de265": ["libde265"],
    "heif": ["libheif"],
    "gd": ["libgd"],
    "freetype": ["freetype"],
    "fontconfig": ["fontconfig"],
    "harfbuzz": ["harfbuzz"],
    "pixman-1": ["pixman"],
    "cairo": ["cairo"],
    "X11": ["libx11"],
    "xcb": ["libxcb"],
    "xkbcommon": ["xkbcommon"],
    "GL": ["mesa"],
    "vulkan": ["vulkan"],
    "SDL2": ["sdl2"],
    "SDL3": ["sdl3"],
    # audio, video
    "ogg": ["libogg"],
    "vorbis": ["libvorbis"],
    "FLAC": ["flac"],
    "opus": ["opus"],
    "mp3lame": ["lame"],
    "sndfile": ["libsndfile"],
    "asound": ["alsa_lib"],
    "pulse": ["pulseaudio"],
    "x264": ["x264"],
    "x265": ["x265"],
    "avcodec": ["ffmpeg"],
    "avformat": ["ffmpeg"],
    "avutil": ["ffmpeg"],
    # glib
    "glib-2.0": ["glib"],
    "gobject-2.0": ["glib"],
    "gio-2.0": ["glib"],
}

# Modules whose archive ships its own BUILD files (no overlay in the registry to read), with
# the library target that archive defines, checked by hand against the release the registry
# points at.  Keyed by module, valued by target name.
SHIPPED_TARGETS: dict[str, str] = {
    "pcre2": "pcre2",  # pcre2-<version>/BUILD.bazel: cc_library(name = "pcre2")
}

# The header a probe includes to use a library, by the `-l` name of `CANDIDATES` it stands
# for: a configure check whose program includes one of these compiles and links against that
# library (`#include <gmp.h>` in gawk's MPFR probe), and a public header of a converted project
# including one keeps the library among its `deps` rather than its `implementation_deps`.  One
# owner per header: where several `-l` names share one (ncurses, ncursesw and tinfo all install
# curses.h), the name a probe including it most plausibly means.  Only the identifying public
# header of each library, not everything it installs, so that a project's own header of the same
# name does not pass for it.  Emitted as the `[headers]` table; a header whose library the
# registry scan did not resolve is left out with a message.
HEADERS: dict[str, str] = {
    "archive.h": "archive",
    "bzlib.h": "bz2",
    "curl/curl.h": "curl",
    "curses.h": "ncurses",
    "expat.h": "expat",
    "ffi.h": "ffi",
    "gmp.h": "gmp",
    "lz4.h": "lz4",
    "lzma.h": "lzma",
    "mpc.h": "mpc",
    "mpfr.h": "mpfr",
    "ncurses.h": "ncurses",
    "openssl/ssl.h": "ssl",
    "pcap.h": "pcap",
    "pcap/pcap.h": "pcap",
    "pcre.h": "pcre",
    "pcre2.h": "pcre2-8",
    "png.h": "png",
    "readline/readline.h": "readline",
    "sqlite3.h": "sqlite3",
    "uuid/uuid.h": "uuid",
    "zip.h": "zip",
    "zlib.h": "z",
    "zstd.h": "zstd",
}

# A library rule or alias and its name, in a BUILD file or inside a generated `autoconf.bzl`'s
# macro (indented, `native.alias(`): the converter's overlays declare their public names there.
_LIBRARY_RULE_RE = re.compile(
    r'^\s*(?:native\.)?(?:cc_library|alias|cc_shared_library|cc_import)\(\s*\n\s*name = "([^"]+)"', re.MULTILINE
)


# A registry file by its path under the registry root (`modules/zlib/metadata.json`); None when
# the registry has no such file.
Reader = Callable[[str], "str | None"]


def clone_reader(registry: Path) -> Reader:
    """Files of a clone's `origin/main` (the working tree may sit on another branch)."""

    def show(path: str) -> str | None:
        try:
            return subprocess.check_output(
                ["git", "-C", str(registry), "show", f"origin/main:{path}"], text=True, stderr=subprocess.DEVNULL
            )
        except subprocess.CalledProcessError:
            return None

    return show


def public_reader(base_url: str = PUBLIC_REGISTRY) -> Reader:
    """Files of a registry served over HTTP, the public one by default.  A missing file is a
    404; anything else (no network, a server error) is raised rather than read as "no such
    module", which would silently drop libraries from the table."""

    def fetch(path: str) -> str | None:
        request = urllib.request.Request(f"{base_url}/{path}", headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request) as response:
                return str(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise RuntimeError(f"{base_url}/{path}: HTTP {error.code}") from error
        except OSError as error:
            raise RuntimeError(f"{base_url}/{path}: {error}") from error

    return fetch


def _target_for(lib: str, module: str, names: list[str]) -> str | None:
    """The overlay target that is the library: named after the `-l` name or the module."""
    short = module.removeprefix("lib")
    for candidate in (lib, lib.replace("-", "_"), module, short, "lib" + lib, "lib" + short):
        if candidate in names:
            return candidate
    return None


def _newest(versions: list[str], held: str | None, allow_upstream: bool) -> tuple[str, str | None]:
    """The version to record: the newest `.bcr.N` of the upstream release the table holds
    (`held`), or the newest of all for a module the table does not hold yet or that
    `allow_upstream` frees; with the newer upstream release left alone, if any, for the
    message.  Moving a library to another upstream release is a decision made per module."""
    newest = versions[-1]
    if held is None or allow_upstream or upstream_version(newest) == upstream_version(held):
        return newest, None
    same = [v for v in versions if upstream_version(v) == upstream_version(held)]
    return (same[-1] if same else held), newest


def scan(
    show: Reader, current: dict[str, str] | None = None, allow_upstream: set[str] | None = None
) -> tuple[dict[str, dict[str, str]], list[str]]:
    """The resolved table and the messages for what could not be resolved, from the registry
    `show` reads; `current` is module -> the version the table holds now, `allow_upstream` the
    modules that may change upstream release."""
    table: dict[str, dict[str, str]] = {}
    messages: list[str] = []
    for lib, modules in CANDIDATES.items():
        for module in modules:
            metadata = show(f"modules/{module}/metadata.json")
            if metadata is None:
                continue
            data = json.loads(metadata)
            yanked = set(data.get("yanked_versions", {}))
            versions = [v for v in data["versions"] if v not in yanked]
            if not versions:
                continue
            version, newer = _newest(versions, (current or {}).get(module), module in (allow_upstream or set()))
            if newer is not None:
                messages.append(
                    f"{lib}: the registry has {module} {newer}; the table stays on {version} "
                    f"(pass --allow-upstream {module} to move)"
                )
            overlay = show(f"modules/{module}/{version}/overlay/BUILD.bazel")
            if overlay is None and module in SHIPPED_TARGETS:
                table[lib] = {"module": module, "version": version, "label": f"@{module}//:{SHIPPED_TARGETS[module]}"}
                break
            if overlay is None:
                messages.append(
                    f"{lib}: {module} {version} keeps its BUILD files in the archive or a patch; "
                    "no overlay to read the library target from"
                )
                break
            # A generated overlay declares its targets in autoconf.bzl; BUILD.bazel keeps the
            # hand-written ones (and the names earlier versions exported, as aliases).
            autoconf_bzl = show(f"modules/{module}/{version}/overlay/autoconf.bzl") or ""
            names = _LIBRARY_RULE_RE.findall(autoconf_bzl) + _LIBRARY_RULE_RE.findall(overlay)
            target = _target_for(lib, module, names)
            if target is None:
                messages.append(f"{lib}: {module} {version}'s overlay has no target named after the library")
                break
            table[lib] = {"module": module, "version": version, "label": f"@{module}//:{target}"}
            break
        else:
            messages.append(f"{lib}: no registry module among {modules}")
    return table, messages


def main() -> int:
    """Regenerate tools/convert/libraries.toml from the public registry or a clone."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry",
        type=Path,
        default=None,
        help=f"a clone of bazelbuild/bazel-central-registry to read instead of {PUBLIC_REGISTRY}",
    )
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument(
        "--allow-upstream",
        action="append",
        default=[],
        metavar="MODULE",
        help="let this module move to a newer upstream release; by default a module keeps the "
        "upstream release the table holds and takes only its newest `.bcr.N`",
    )
    args = parser.parse_args()
    workspace = workspace_root(args.workspace)
    current = {entry.module: entry.version for entry in libraries.table().values()}
    show = clone_reader(args.registry.expanduser().resolve()) if args.registry else public_reader()
    table, messages = scan(show, current, set(args.allow_upstream))
    target = workspace / "tools/convert/libraries.toml"
    rows: dict[str, dict[str, object]] = {lib: dict(entry) for lib, entry in sorted(table.items())}
    headers = {header: lib for header, lib in sorted(HEADERS.items()) if lib in table}
    messages += [
        f"{header}: its library {lib} is not in the table; left out"
        for header, lib in HEADERS.items()
        if lib not in table
    ]
    text = (
        tomlout.header(
            UPDATE_COMMAND,
            "C libraries by `-l` name and the Bazel Central Registry modules that "
            "provide them, each module's latest version and verified library target, and the "
            "headers that identify them",
        )
        + "\n"
        + tomlout.tables("libraries", rows)
        + "\n"
        + tomlout.table("headers", headers)
    )
    target.write_text(text, encoding="utf-8", newline="\n")
    for message in messages:
        print(message, file=sys.stderr)
    print(f"{target}: {len(table)} libraries ({len(messages)} candidates unresolved)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
