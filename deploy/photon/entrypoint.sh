#!/bin/sh
# Settings come from environment variables in the compose file. First start: fetch the index (about 10 GB), verify its md5, unpack beside the
# final place and move it into place only when complete, so a stopped download never leaves a half index. Later starts go straight to serving.
set -e
cd /data
if [ ! -d /data/photon_data ]; then
  # an index a person unpacked by hand into /data/incoming/photon_data (the documented head start) is used as it is
  if [ -d /data/incoming/photon_data/node_1/data ]; then
    mv /data/incoming/photon_data /data/photon_data
  else
    echo "photon: no index yet, downloading $PHOTON_INDEX_URL"
    mkdir -p /data/incoming && cd /data/incoming
    wget -c -q -O index.tar.bz2 "$PHOTON_INDEX_URL"
    wget -q -O index.md5 "$PHOTON_INDEX_URL.md5"
    want=$(cut -d' ' -f1 index.md5); have=$(md5sum index.tar.bz2 | cut -d' ' -f1)
    [ "$want" = "$have" ] || { echo "photon: md5 mismatch ($have, wanted $want), deleting the download"; rm -f index.tar.bz2; exit 1; }
    echo "photon: unpacking"
    mkdir -p building
    # tar -I reports a failure of the unpacker or of tar itself (a plain pipe would hide it)
    tar -I pbzip2 -xf index.tar.bz2 -C building || { echo "photon: unpacking failed, check the data disk and the kernel (tar must be able to create files)"; rm -rf building; exit 1; }
    [ -d building/photon_data/node_1/data ] || { echo "photon: the unpacked index looks empty, removing it"; rm -rf building; exit 1; }
    mv building/photon_data /data/photon_data
    rm -rf building index.tar.bz2 index.md5
  fi
  rmdir /data/incoming 2>/dev/null || true
fi
echo "photon: serving"
exec java -Xmx"$PHOTON_HEAP" -jar /photon/photon.jar serve -data-dir /data -listen-ip 0.0.0.0 -listen-port 2322 ${PHOTON_EXTRA_ARGS:-}
