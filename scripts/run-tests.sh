#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
python3 -m compileall -q sas_hba_console
python3 -m unittest discover -s tests -v
