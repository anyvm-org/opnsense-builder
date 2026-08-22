How the images are built:

Each image is built automatically in the
[anyvm-org/opnsense-builder](https://github.com/anyvm-org/opnsense-builder)
repo's GitHub Actions: it downloads the official OPNsense nano disk
image, boots it in QEMU, configures console and ssh access, and exports
the disk as a compressed qcow2 image. No interactive installer is run.

Upstream media: the official OPNsense images from
https://pkg.opnsense.org/releases/ (download page:
https://opnsense.org/download/).
