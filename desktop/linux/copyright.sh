#!/usr/bin/env bash
# Print /usr/share/doc/ostraka/copyright in Debian's machine-readable format (DEP-5), carrying
# LICENSE (ELv2) and COMMERCIAL-LICENSE.md whole: the packages ship the terms, so installing
# is accepting them (README, Licence). The .deb uses it.
#   copyright.sh <repo root>
set -euo pipefail

ROOT="$1"
# DEP-5 license text: every line indented one space, blank lines as " ."
indent() { sed 's/^$/./; s/^/ /' "$1"; }

cat <<HEAD
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: Ostraka
Upstream-Contact: cmdaltr <cmdaltr@users.noreply.github.com>
Source: https://github.com/cmdaltr/ostraka

Files: *
Copyright: 2026 Ben Smith (cmdaltr)
License: Elastic-2.0
Comment: The paid editions (Pro and Services) are used under the Ostraka Commercial
 Licence too, in the LicenseRef-Ostraka-Commercial paragraph below.

License: Elastic-2.0
HEAD
indent "$ROOT/LICENSE"
echo
echo "License: LicenseRef-Ostraka-Commercial"
indent "$ROOT/COMMERCIAL-LICENSE.md"
