#!/usr/bin/env bash
# Build what the emulator tests install, for both CPUs, into emu/build/:
#   pm/<label>/<arch>/Plugin-Manager-G2-<version>-mpc-<arch>.zip   the Manager, as the release workflow packages it, but reading its
#                                                                    catalog from the fixture server (http://10.0.2.2:8731/) instead of the live site
#   fx/<label>/                                                      what the fixture server serves: the zips, catalog.full.json and patches.json
# usage: emu/build.sh [--ref GIT_REF] [--label NAME]
#   --ref    build the Manager's C from that commit instead of the working tree (a test that must fail before a fix runs against it)
#   --label  the name of this build (default: "work", or the ref)
# The skin (vst/build/skin, all three theme folders) is built once by vst/build_themes.sh and reused: it takes about 15 minutes and does
# not depend on the C. Needs Docker with arm32v7 and arm64 emulation, and an mpc-vst-plugins checkout (MPC_VST).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"; pm="${PM:-$(cd "$here/../../plugin-manager" && pwd)}"
MV="${MPC_VST:-$(cd "$pm/../.." && pwd)/open-mpc}"
ref=""; label=""
while [ $# -gt 0 ]; do case "$1" in --ref) ref=$2; shift 2 ;; --label) label=$2; shift 2 ;; *) echo "usage: build.sh [--ref REF] [--label NAME]" >&2; exit 2 ;; esac; done
[ -n "$label" ] || label="${ref:-work}"; label="${label//\//-}"
out="$here/build"; fx="$out/fx/$label"; mkdir -p "$fx" "$out/pm/$label"
U="$(id -u):$(id -g)"
FIX=http://10.0.2.2:8731
VA=9.0.0; VB=9.0.1   # the Manager under test, and the "newer" one the fixture catalog offers (the same code)

skin="$pm/vst/build/skin/OpenMPC - VST - Plugin Manager"
if [ ! -d "$skin/Plugin Skins.light" ] || [ ! -f "$pm/vst/build/params.h" ]; then
    echo "== skin: vst/build_themes.sh (about 15 minutes, once)"
    MPC_VST="$MV" "$pm/vst/build_themes.sh" armv7 >/dev/null
fi

# the C and its parameter table (vst/build/params.h, from vst/params.json): the working tree's, or a commit's
src="$pm"
if [ -n "$ref" ]; then
    src="$out/pm/$label/src"; rm -rf "$src"; mkdir -p "$src"
    git -C "$pm" archive "$ref" src vst | tar -xf - -C "$src"
fi
python3 "$MV/tools/gen_vst.py" "$src/vst/vst.json" --params-h >/dev/null
eval "$(python3 "$MV/tools/gen_vst.py" "$pm/vst/vst.json" --shell | grep -E '^(CFLAGS|CFLAGS_ARM|CFLAGS_A64|LIBS)=')"
DEF="-DCATALOG_URL='\"$FIX/catalog.json\"' -DCATALOG_URL_FALLBACK='\"\"' -DPATCHES_URL='\"$FIX/patches.json\"' -DPATCHES_URL_FALLBACK='\"\"'"

compile() {   # compile <arch> <version>: the same command as mpc-vst-plugins' tools/build_port.sh
    local arch=$1 ver=$2 img plat xf o
    case $arch in armv7) img=arm32v7/gcc:11-bullseye; plat=linux/arm/v7; xf="$CFLAGS_ARM" ;; aarch64) img=arm64v8/gcc:12-bookworm; plat=linux/arm64; xf="$CFLAGS_A64" ;; esac
    o="$out/pm/$label/$arch/$ver"; mkdir -p "$o"
    docker run --rm --platform $plat -u "$U" -v "$src":/b:ro -v "$MV":/mv:ro -v "$o":/o -w /b $img bash -euc "
        gcc -O2 -w -fPIC -shared -fvisibility=hidden -std=gnu11 $CFLAGS $xf $DEF -DPM_VERSION='\"$ver\"' -Ivst/build -I/mv/wrapper \
            src/manager.c /mv/wrapper/vst2_wrap.c $LIBS -lpthread -ldl -Wl,--no-undefined -o /o/openmpc_pm_g2.so
        strip /o/openmpc_pm_g2.so"
}
package() {   # package <arch> <version>: release.py, as the release workflow does
    local arch=$1 ver=$2 o="$out/pm/$label/$1/$2"
    rm -rf "$o/skin" "$o/dist"; mkdir -p "$o/skin"
    cp -a --reflink=auto "$skin" "$o/skin/"
    # the built skin is reused between builds, so the source tree's own edits to it are applied to this copy (a commit from before an
    # edit existed gets the skin as it was then)
    if [ -f "$src/vst/skin_taps.py" ]; then python3 "$src/vst/skin_taps.py" "$o/skin" >/dev/null; fi
    python3 "$MV/tools/release.py" --so "$o/openmpc_pm_g2.so" --skin "$o/skin/OpenMPC - VST - Plugin Manager" --entry "$pm/vst/build/pluginlist-entry.xml" \
        --version "$ver" --id plugin-manager-g2 --repo open-mpc/plugin-manager --license MIT --asset-name Plugin-Manager-G2 \
        --retire "OpenMPC - VST - Plugin Manager G2" --about "The Manager, built for the emulator tests." -o "$o/dist" >/dev/null
    rm -rf "$o/skin"
    ls "$o/dist"/*.zip >/dev/null
}
for arch in armv7 aarch64; do
    for ver in $VA $VB; do
        z="$out/pm/$label/$arch/$ver/dist/Plugin-Manager-G2-$ver-mpc-$arch.zip"
        if [ -f "$z" ] && [ -z "$(find "$src/src" "$src/vst/params.json" "$src/vst/skin_taps.py" "$skin" "$here/build.sh" -newer "$z" -print -quit)" ]; then echo "== Manager $label $ver $arch: up to date"; continue; fi
        echo "== Manager $label $ver $arch"
        compile $arch $ver; package $arch $ver
    done
done

# the fixture plugins and addin, as real packages. Emu Probe (two versions) is a real VST2 instrument MPC can load on a plugin track
# (src/fxprobe.c built with PROBE_SYNTH; it logs to /tmp/emu-probe.log); Emu Probe Out (an effect) needs it (--depends), for the
# installers' dependency handling.
# skin_folder <work> <folder> <identifier>: the skin folder release.py packages (an empty skin and its version.xml)
skin_folder() {
    mkdir -p "$1/$2/Plugin Skins"; echo '{}' > "$1/$2/Plugin Skins/TUI.json"
    printf '<?xml version="1.0" encoding="utf-8"?>\n<plugincontent version="1.0">\n\t<identifier>%s</identifier>\n\t<version>1.0.0.0</version>\n</plugincontent>\n' "$3" > "$1/$2/version.xml"
}
echo "== fixture packages"
for arch in armv7 aarch64; do
    case $arch in armv7) img=arm32v7/gcc:11-bullseye; plat=linux/arm/v7 ;; aarch64) img=arm64v8/gcc:12-bookworm; plat=linux/arm64 ;; esac
    w="$out/fixture-work/$arch"; rm -rf "$w"; mkdir -p "$w/addin" "$w/out"
    docker run --rm --platform $plat -u "$U" -v "$here/src":/s:ro -v "$w":/o $img bash -euc "
        gcc -O2 -Wall -fPIC -shared -fvisibility=hidden -DPROBE_SYNTH -DPROBE_NAME='\"Emu Probe\"' -DPROBE_UID=0x456d5072 -DPROBE_LOG='\"/tmp/emu-probe.log\"' -o /o/emu_probe.so /s/fxprobe.c
        gcc -O2 -Wall -fPIC -shared -fvisibility=hidden -DPROBE_NAME='\"Emu Probe Out\"' -DPROBE_UID=0x456d504f -DPROBE_LOG='\"/tmp/emu-probe-out.log\"' -o /o/out/emu_probe_out.so /s/fxprobe.c
        gcc -O2 -fPIC -shared -o /o/addin/emu_addin.so /s/addin.c && strip /o/emu_probe.so /o/out/emu_probe_out.so /o/addin/emu_addin.so
        gcc -O2 -Wall -static -o /o/pm-fill /s/fill.c && strip /o/pm-fill"
    skin_folder "$w/skin" "acme - VST - Emu Probe" acme.vst.emuprobe
    echo '<PLUGIN name="Emu Probe" descriptiveName="Emu Probe" format="VST" category="Synth" manufacturer="acme" version="1.0" file="/sdcard/Synths/acme - VST - Emu Probe/emu_probe.so" uid="456d5072" isInstrument="1" fileTime="0" infoUpdateTime="0" numInputs="0" numOutputs="2" isShell="0"/>' > "$w/entry.xml"
    for ver in 1.0.0 1.0.1; do
        python3 "$MV/tools/release.py" --so "$w/emu_probe.so" --skin "$w/skin/acme - VST - Emu Probe" --entry "$w/entry.xml" --version $ver \
            --id emu-probe --repo acme/mpc-vst-emu-probe --license MIT --about "A stand-in plugin for the emulator tests." -o "$w/dist" >/dev/null
    done
    skin_folder "$w/out/skin" "acme - VST - Emu Probe Out" acme.vst.emuprobeout
    echo '<PLUGIN name="Emu Probe Out" descriptiveName="Emu Probe Out" format="VST" category="Effect" manufacturer="acme" version="1.0" file="/sdcard/Synths/acme - VST - Emu Probe Out/emu_probe_out.so" uid="456d504f" isInstrument="0" fileTime="0" infoUpdateTime="0" numInputs="2" numOutputs="2" isShell="0"/>' > "$w/out/entry.xml"
    python3 "$MV/tools/release.py" --so "$w/out/emu_probe_out.so" --skin "$w/out/skin/acme - VST - Emu Probe Out" --entry "$w/out/entry.xml" --version 1.0.0 \
        --id emu-probe-out --depends "emu-probe>=1.0.0" --repo acme/mpc-vst-emu-probe-out --license MIT --about "A stand-in plugin that needs Emu Probe." -o "$w/dist" >/dev/null
    printf 'ADDIN_ID=emu-addin\nADDIN_NAME="Emu addin"\nADDIN_SO=emu_addin.so\nADDIN_CONF=emu_addin.conf\nADDIN_FILES=""\n' > "$w/addin/addin.manifest"
    echo "# settings" > "$w/addin/emu_addin.conf"
    python3 "$MV/tools/release_addin.py" --dir "$w/addin" --version 1.0.0 --repo acme/mpc-addin-emu --license MIT --about "A stand-in addin for the emulator tests." -o "$w/dist" >/dev/null
done

# the MPC Sample's (aarch64 only; served as files, not in the catalog the Manager reads): a real VST2 effect MPC can load, and the RACK
# addin's published 1.0.1 release zip as it is, its own installer and all (1.0.0's installer did not know the Sample: FINDINGS.md).
# RACK_ZIP: a local RACK release zip instead (e.g. one to check before it is published), not checked against RACK_SHA.
echo "== MPC Sample packages"
w="$out/fixture-work/aarch64"; rm -rf "$w/fx" "$w/rack"; mkdir -p "$w/fx/skin/acme - VST - Emu FX/Plugin Skins" "$w/rack"
docker run --rm --platform linux/arm64 -u "$U" -v "$here/src":/s:ro -v "$w/fx":/o arm64v8/gcc:12-bookworm bash -euc "
    gcc -O2 -Wall -fPIC -shared -fvisibility=hidden -o /o/emu_fx.so /s/fxprobe.c && strip /o/emu_fx.so"
echo '{}' > "$w/fx/skin/acme - VST - Emu FX/Plugin Skins/TUI.json"
printf '<?xml version="1.0" encoding="utf-8"?>\n<plugincontent version="1.0">\n\t<identifier>acme.vst.emufx</identifier>\n\t<version>1.0.0.0</version>\n</plugincontent>\n' > "$w/fx/skin/acme - VST - Emu FX/version.xml"
echo '<PLUGIN name="Emu FX" descriptiveName="Emu FX" format="VST" category="Effect" manufacturer="acme" version="1.0" file="/sdcard/Synths/acme - VST - Emu FX/emu_fx.so" uid="456d4658" isInstrument="0" fileTime="0" infoUpdateTime="0" numInputs="2" numOutputs="2" isShell="0"/>' > "$w/fx/entry.xml"
for ver in 1.0.0 1.0.1; do
    python3 "$MV/tools/release.py" --so "$w/fx/emu_fx.so" --skin "$w/fx/skin/acme - VST - Emu FX" --entry "$w/fx/entry.xml" --version $ver \
        --id emu-fx --repo acme/mpc-vst-emu-fx --license MIT --about "A stand-in effect for the MPC Sample emulator tests." -o "$w/dist" >/dev/null
done
RACK_URL=https://github.com/jacob-sabella/mpc-addin-sample-rack/releases/download/sample-rack-addin-v1.0.1/MPC-Sample-rack-addin-1.0.1-mpc-aarch64.zip
RACK_SHA=6f7e14b9e387585eab1a318023f447a81197498c8386235d17c48e55fcd3f111
rm -f "$fx"/MPC-Sample-rack-addin-*-mpc-aarch64.zip
if [ -n "${RACK_ZIP:-}" ]; then
    case "$(basename "$RACK_ZIP")" in MPC-Sample-rack-addin-*-mpc-aarch64.zip) ;; *) echo "RACK_ZIP: not a RACK release zip name: $RACK_ZIP" >&2; exit 2 ;; esac
    cp "$RACK_ZIP" "$w/dist/"
else
    rz="$out/cache/$(basename "$RACK_URL")"; mkdir -p "$out/cache"
    [ -f "$rz" ] || curl -fsSL -o "$rz" "$RACK_URL"
    echo "$RACK_SHA  $rz" | sha256sum -c --quiet - || { echo "the RACK addin zip is not the published 1.0.1" >&2; rm -f "$rz"; exit 1; }
    cp "$rz" "$w/dist/"
fi
[ "$(ls "$w/dist"/MPC-Sample-rack-addin-*-mpc-aarch64.zip | wc -l)" = 1 ]
ls "$w/dist/Emu-FX-1.0.0-mpc-aarch64.zip" >/dev/null
# the desktop installer app (mpc-vst-plugins tools/desktop), built for this host from MPC_VST: the routes tests (routes.py) drive it over
# its local web API from the harness container, against the emulator's SSH port
echo "== desktop installer app"
mkdir -p "$out/cache/go"
docker run --rm -u "$U" -v "$MV/tools/desktop":/src:ro -v "$fx":/o -v "$out/cache/go":/go -e GOPATH=/go -e GOCACHE=/go/cache -e GOFLAGS=-mod=mod \
    -e CGO_ENABLED=0 -e GOOS=linux -e GOARCH=amd64 -w /src golang:1.26 go build -trimpath -ldflags "-X main.version=emu" -o /o/mpc-installer .
MPC_VST="$MV" python3 "$here/fixture.py" "$out" "$label" $VA $VB
# the published Plugin Manager app (modeslot.py), as released
APP=https://github.com/open-mpc/plugin-manager/releases/download/plugin-manager-app-v2.1.0
mkdir -p "$out/cache"
for p in armv7:3bcec66f1f136a34fc42982b905d262c0b722af6e592fe718a9bb5a4f35a443b aarch64:abe34734675f941f2fcbacbf292491d0477b569fb969af231d285dbfc645ff3c; do
    n="Plugin-Manager-App-2.1.0-mpc-${p%:*}.zip"; az="$out/cache/$n"
    [ -f "$az" ] || curl -fsSL -o "$az" "$APP/$n"
    echo "${p#*:}  $az" | sha256sum -c --quiet - || { echo "$n is not the published 2.1.0" >&2; rm -f "$az"; exit 1; }
    cp "$az" "$fx/"
done
# what full_disk fills a drive with (src/fill.c): served beside the zips, not in any catalog
for arch in armv7 aarch64; do cp "$out/fixture-work/$arch/pm-fill" "$fx/pm-fill-$arch"; done
git -C "$pm" rev-parse ${ref:-HEAD} > "$fx/commit" 2>/dev/null || true
