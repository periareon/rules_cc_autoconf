"""Regenerate `libraries.toml`: the C libraries a configure script links by `-l` name that the
Bazel Central Registry provides as modules, with each module's latest version and the library
target its overlay defines.

`CANDIDATES` maps the `-l` names autoconf projects use to the registry modules that may
provide them (the first that exists wins: `ssl` is openssl before boringssl).  The registry is
scanned for the module's latest version and its overlay `BUILD.bazel`; a module whose BUILD
files live inside the archive or a patch has no overlay to read, so it is skipped with a
message rather than guessed.  Names the host provides (`m`, `dl`, `rt`, `pthread`) are not
here: they are link options, not modules.

Usage: `bazel run //tools/convert:update_libraries -- --registry <bazel-central-registry clone>`
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from tools.convert import tomlout

UPDATE_COMMAND = "bazel run //tools/convert:update_libraries -- --registry <bazel-central-registry clone>"

CANDIDATES: dict[str, list[str]] = {
    # compression
    "z": ["zlib"], "bz2": ["bzip2"], "lzma": ["xz"], "zstd": ["zstd"], "lz4": ["lz4"],
    "brotlidec": ["brotli"], "brotlienc": ["brotli"], "zip": ["libzip"], "zzip": ["zziplib"], "archive": ["libarchive"],
    # arithmetic
    "gmp": ["gmp"], "mpfr": ["mpfr"], "mpc": ["mpc"],
    # text, parsing, data
    "ffi": ["libffi"], "pcre2-8": ["pcre2"], "pcre": ["pcre"], "re2": ["re2"], "expat": ["expat"],
    "xml2": ["libxml2"], "xslt": ["libxslt"], "yaml": ["libyaml", "yaml"], "json-c": ["json-c"], "jansson": ["jansson"],
    "cjson": ["cjson"], "icuuc": ["icu"], "icui18n": ["icu"], "iconv": ["libiconv"], "intl": ["gettext"],
    "unistring": ["libunistring"], "idn2": ["libidn2"], "psl": ["libpsl"], "popt": ["popt"], "argp": ["argp-standalone"],
    "magic": ["file"], "tre": ["tre"], "protobuf": ["protobuf"], "protobuf-c": ["protobuf-c"],
    # crypto, tls
    "ssl": ["openssl", "boringssl"], "crypto": ["openssl", "boringssl"], "gnutls": ["gnutls"], "nettle": ["nettle"],
    "hogweed": ["nettle"], "gcrypt": ["libgcrypt"], "gpg-error": ["libgpg-error"], "sodium": ["libsodium"],
    "mbedtls": ["mbedtls"], "mbedcrypto": ["mbedtls"], "crypt": ["libxcrypt"],
    # networking
    "curl": ["curl"], "nghttp2": ["nghttp2"], "ssh2": ["libssh2"], "ssh": ["libssh"], "pcap": ["libpcap"],
    "microhttpd": ["libmicrohttpd"], "websockets": ["libwebsockets"], "mosquitto": ["mosquitto"], "zmq": ["libzmq"],
    "czmq": ["czmq"], "hiredis": ["hiredis"], "pq": ["postgres", "libpq"], "mysqlclient": ["mysql"],
    # event loops
    "event": ["libevent"], "event_core": ["libevent"], "ev": ["libev"], "uv": ["libuv"],
    # storage
    "sqlite3": ["sqlite3", "sqlite"], "leveldb": ["leveldb"], "rocksdb": ["rocksdb"], "lmdb": ["lmdb"], "snappy": ["snappy"],
    # terminal
    "ncurses": ["ncurses"], "ncursesw": ["ncurses"], "tinfo": ["ncurses"], "termcap": ["ncurses"],
    "readline": ["readline"], "edit": ["libedit"],
    # system
    "cap": ["libcap"], "selinux": ["libselinux"], "acl": ["acl"], "attr": ["attr"], "uuid": ["libuuid", "util-linux"],
    "mount": ["util-linux"], "blkid": ["util-linux"], "udev": ["libudev", "eudev"], "systemd": ["systemd"],
    "dbus-1": ["dbus"], "seccomp": ["libseccomp"], "kmod": ["kmod"], "numa": ["numactl"], "hwloc": ["hwloc"],
    "fuse": ["libfuse"], "fuse3": ["libfuse"], "usb-1.0": ["libusb"], "gpiod": ["libgpiod"],
    "elf": ["elfutils", "libelf"], "dw": ["elfutils"], "unwind": ["libunwind"], "bfd": ["binutils"], "iberty": ["binutils"],
    "ltdl": ["libtool"],
    # images, fonts, graphics
    "png": ["libpng"], "jpeg": ["libjpeg-turbo", "libjpeg"], "turbojpeg": ["libjpeg-turbo"], "tiff": ["libtiff"],
    "gif": ["giflib"], "webp": ["libwebp"], "de265": ["libde265"], "heif": ["libheif"], "gd": ["libgd"],
    "freetype": ["freetype"], "fontconfig": ["fontconfig"], "harfbuzz": ["harfbuzz"], "pixman-1": ["pixman"],
    "cairo": ["cairo"], "X11": ["libx11"], "xcb": ["libxcb"], "xkbcommon": ["xkbcommon"], "GL": ["mesa"], "vulkan": ["vulkan"],
    "SDL2": ["sdl2"], "SDL3": ["sdl3"],
    # audio, video
    "ogg": ["libogg"], "vorbis": ["libvorbis"], "FLAC": ["flac"], "opus": ["opus"], "mp3lame": ["lame"],
    "sndfile": ["libsndfile"], "asound": ["alsa_lib"], "pulse": ["pulseaudio"], "x264": ["x264"], "x265": ["x265"],
    "avcodec": ["ffmpeg"], "avformat": ["ffmpeg"], "avutil": ["ffmpeg"],
    # glib
    "glib-2.0": ["glib"], "gobject-2.0": ["glib"], "gio-2.0": ["glib"],
}

_LIBRARY_RULE_RE = re.compile(r'^(?:cc_library|alias|cc_shared_library|cc_import)\(\s*\n\s*name = "([^"]+)"', re.M)


def _show(registry: Path, path: str) -> str | None:
    """A file of the registry's `origin/main` (the working tree may sit on another branch)."""
    try:
        return subprocess.check_output(["git", "-C", str(registry), "show", f"origin/main:{path}"],
                                       text=True, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        return None


def _target_for(lib: str, module: str, names: list[str]) -> str | None:
    """The overlay target that is the library: named after the `-l` name or the module."""
    short = module[3:] if module.startswith("lib") else module
    for candidate in (lib, lib.replace("-", "_"), module, short, "lib" + lib, "lib" + short):
        if candidate in names:
            return candidate
    return None


def scan(registry: Path) -> tuple[dict[str, dict[str, str]], list[str]]:
    """The resolved table and the messages for what could not be resolved."""
    table: dict[str, dict[str, str]] = {}
    messages: list[str] = []
    for lib, modules in CANDIDATES.items():
        for module in modules:
            metadata = _show(registry, f"modules/{module}/metadata.json")
            if metadata is None:
                continue
            version = json.loads(metadata)["versions"][-1]
            overlay = _show(registry, f"modules/{module}/{version}/overlay/BUILD.bazel")
            if overlay is None:
                messages.append(f"{lib}: {module} {version} keeps its BUILD files in the archive or a patch; "
                                "no overlay to read the library target from")
                break
            target = _target_for(lib, module, _LIBRARY_RULE_RE.findall(overlay))
            if target is None:
                messages.append(f"{lib}: {module} {version}'s overlay has no target named after the library")
                break
            table[lib] = {"module": module, "version": version, "label": f"@{module}//:{target}"}
            break
        else:
            messages.append(f"{lib}: no registry module among {modules}")
    return table, messages


def main() -> int:
    """Regenerate tools/convert/libraries.toml from a registry clone."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True, help="a clone of bazelbuild/bazel-central-registry")
    parser.add_argument("--workspace", type=Path, default=None)
    args = parser.parse_args()
    workspace = (
        args.workspace
        or (Path(os.environ["BUILD_WORKSPACE_DIRECTORY"]) if "BUILD_WORKSPACE_DIRECTORY" in os.environ else None)
        or Path(__file__).resolve().parents[2]
    )
    table, messages = scan(args.registry.expanduser().resolve())
    target = workspace / "tools" / "convert" / "libraries.toml"
    text = (tomlout.header(UPDATE_COMMAND, "C libraries by `-l` name and the Bazel Central Registry modules that "
                           "provide them, each module's latest version and verified library target") + "\n"
            + tomlout.tables("libraries", dict(sorted(table.items()))))
    target.write_text(text, encoding="utf-8", newline="\n")
    for message in messages:
        print(message, file=sys.stderr)
    print(f"{target}: {len(table)} libraries ({len(messages)} candidates unresolved)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
