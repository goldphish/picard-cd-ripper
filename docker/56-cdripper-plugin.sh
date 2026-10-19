#!/bin/sh

set -u # Treat unset variables as an error.

# Register the CD Ripper plugin with Picard. /config is a volume, so this has
# to run at container start rather than at image build time. The plugin is
# loaded in place from /opt/picard-cd-ripper, so rebuilding the image is
# enough to update it; re-running the install is a no-op.
#
# The image's /usr/bin/picard-cli wrapper launches the GUI, so call the CLI
# module directly. This runs as root; 85-take-config-ownership.sh hands the
# files it creates under /config to the app user afterwards.
HOME=/config python3 -m picard.cli --no-color --yes \
    -c /config/Picard.ini plugins install /opt/picard-cd-ripper ||
    echo "WARNING: failed to install the CD Ripper plugin."

# vim:ts=4:sw=4:et:sts=4
