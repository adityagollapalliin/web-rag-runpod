#!/bin/bash
# Container entrypoint: run the s6 supervisor directly over the service directories.
# s6-overlay /init is not used because it must be process 1, and Runpod starts containers
# under its own init, where /init exits with "can only run as pid 1".
export PATH="/command:$PATH"
exec s6-svscan /opt/docqa/services
