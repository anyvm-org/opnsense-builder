

| Release | x86_64 |
|---------|---------|
| 26.7 | ✅ (rsync,scp,nfs,tar) |


amd64 only, and one release: OPNsense publishes a single current version at a
time and does not keep older ones on the mirror.

No `sshfs` cell: `fusefs-sshfs` is not in OPNsense's package repository
(`pkg search fusefs-sshfs` returns nothing), and that repository is the only
one an appliance should be pointed at. `rsync` IS there (3.4.4). `scp` comes
from the base system, and `nfs` and `tar` need nothing installed in the guest.

This is the only builder here that can use neither cloud-init nor libguestfs
to prepare its image -- OPNsense is an appliance whose entire state lives in
`/conf/config.xml`, and its root is UFS, which libguestfs cannot write. The
host pubkey, the network and sshd are therefore all configured over the
serial console by `hooks/host_enablessh.py`, which documents the two edits
that are mandatory: the single NIC has to become WAN with DHCP (the shipped
config makes it a static-IP LAN that slirp cannot reach) and sshd has to be
switched on in config.xml, since it is not even an rc script on a fresh
appliance.
