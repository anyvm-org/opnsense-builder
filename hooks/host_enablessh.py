# host_enablessh.py -- make an OPNsense appliance reachable over ssh, using
# nothing but the serial console.
#
# WHY IT ALL HAPPENS HERE. OPNsense is the one guest in this fleet that has no
# offline preparation path at all:
#
#   * no cloud-init -- it is an appliance, its entire state lives in
#     /conf/config.xml and it has no datasource concept;
#   * no libguestfs -- the root is UFS, which libguestfs cannot write, so the
#     virt-customize trick every Linux builder here uses is unavailable.
#
# So the host pubkey, the network and sshd all have to be configured from
# inside a booted guest, over the console, before ssh exists.
#
# TWO THINGS MUST CHANGE, and neither is optional:
#
#   1. THE INTERFACE. With one NIC the shipped config makes it the LAN with a
#      static 192.168.1.1/24. slirp cannot route to that, so the hostfwd has
#      no target and ssh can never connect -- from the host it looks like
#      "Connection timed out during banner exchange", because slirp accepts
#      the TCP itself and only then fails to reach the guest. Renaming the
#      <lan> element to <wan> and setting <ipaddr>dhcp</ipaddr> puts the NIC
#      on the slirp network; verified, it comes up as 10.0.2.15/24.
#   2. SSHD. It is not running on a fresh appliance and is not even an rc
#      script -- `service sshd status` says it does not exist. It is enabled
#      through config.xml, and on the next boot OPNsense starts it and
#      generates host keys itself.
#
# THE EDITS ARE THREE SHORT SEDS, DELIBERATELY. The first attempt pushed a
# python heredoc through the console and it never executed -- a multi-line
# heredoc typed into a getty does not survive. Single-line commands do. Note
# `sed -i ''`: this is FreeBSD sed, not GNU.
#
# One more trap worth recording: while proving this by hand, the guest looked
# hung for 19 minutes after the reboot -- 80% CPU, not a byte on the console.
# It was not hung. The probe had attached to a `-serial unix:` socket, and a
# guest reset drops that chardev, leaving the reader on a dead pipe. build.py
# uses a socket with logfile=, which writes to a file and does not have the
# problem, but do not diagnose this guest through a bare socket.

log("opnsense enablessh: configuring the appliance over the console")

_idrsa = os.path.join(HOME, ".ssh", "id_rsa")
if not os.path.exists(_idrsa):
    run(["ssh-keygen", "-f", _idrsa, "-q", "-N", ""])
_PUB = open(_idrsa + ".pub").read().strip()
log("opnsense enablessh: host pubkey = %s..." % _PUB[:50])

# OPNsense stores a user's authorized keys base64-encoded inside config.xml.
# That is THE durable location -- see the note at step 3.
import base64 as _b64
_PUB_B64 = _b64.b64encode((_PUB + "\n").encode()).decode()


def _to_shell():
    """console login -> appliance menu -> option 8 -> a BOURNE shell.

    The root shell is csh, where `2>&1` is a syntax error ("Ambiguous output
    redirect."), so drop into /bin/sh immediately and keep every later command
    bourne.
    """
    waitForText("login:", "900")
    time.sleep(5)
    string("root")
    enter()
    time.sleep(3)
    string(env("VM_ROOT_PASSWORD") or "opnsense")
    enter()
    # The appliance prints its banner and menu; give it room on a slow TCG
    # runner before answering.
    waitForText("Enter an option", "600")
    time.sleep(3)
    string("8")
    enter()
    time.sleep(15)
    string("/bin/sh")
    enter()
    time.sleep(5)
    string("echo MARK_SH_READY")
    enter()
    waitForText("MARK_SH_READY", "180")
    time.sleep(2)


_to_shell()

# --- keep a copy of the pristine config, for anyone debugging an image ------
string("cp /conf/config.xml /conf/config.xml.anyvm-orig; echo MARK_BACKUP_$?")
enter()
waitForText("MARK_BACKUP_0", "120")
time.sleep(2)

# --- 1. the only NIC becomes WAN on DHCP ------------------------------------
string("sed -i '' -e 's|<lan>|<wan>|' -e 's|</lan>|</wan>|' /conf/config.xml; echo MARK_SED1_$?")
enter()
waitForText("MARK_SED1_0", "120")
time.sleep(2)

string("sed -i '' -e 's|<ipaddr>192.168.1.1</ipaddr>|<ipaddr>dhcp</ipaddr>|' /conf/config.xml; echo MARK_SED2_$?")
enter()
waitForText("MARK_SED2_0", "120")
time.sleep(2)

# --- 2. sshd on, root allowed ------------------------------------------------
string("sed -i '' -e 's|<ssh>|<ssh><enabled>enabled</enabled><permitrootlogin>1</permitrootlogin><passwordauth>1</passwordauth>|' /conf/config.xml; echo MARK_SED3_$?")
enter()
waitForText("MARK_SED3_0", "120")
time.sleep(2)

# Prove the edits landed rather than trusting three exit codes: sed exits 0
# when it matches nothing at all, so a schema change upstream would sail
# straight past the checks above and only surface as an unreachable guest.
string("echo MARK_CHECK_$(grep -c '<wan>' /conf/config.xml)$(grep -c '<ipaddr>dhcp</ipaddr>' /conf/config.xml)$(grep -c permitrootlogin /conf/config.xml)")
enter()
waitForText("MARK_CHECK_111", "120")
time.sleep(2)

# --- 3. the host pubkey, into config.xml where it actually survives ---------
# This is the one that had to be got right, and it took three tries to see
# why. configd rebuilds root's ~/.ssh/authorized_keys from config.xml on EVERY
# boot, so a key written to the filesystem only lasts until the next one:
#
#   try 1: written before the reboot -> wiped by that very reboot. Symptom: a
#          flat "Permission denied (publickey)" from a guest whose sshd was up
#          and whose console had just shown the key being written.
#   try 2: written after the reboot -> the build itself worked (ssh in, pkg
#          install rsync, all five artifacts produced), and then the EXPORTED
#          image failed its verification boot, because that boot regenerated
#          the file again. Symptom: an image that reaches a login prompt and
#          is never ssh-reachable. That verification boot earned its keep --
#          without it this would have shipped as a green build.
#   try 3: this. Base64, inside the root <user> element, in config.xml.
#
# Same lesson as the MaxAuthTries line that silently reverted: on this
# appliance anything outside config.xml is temporary.
# REPLACE the element the config already carries; do not add a second one.
# Inserting one after <name>root</name> looked right and broke the appliance:
# the shipped config already has an empty <authorizedkeys/> for root, PHP maps
# two same-named elements to an ARRAY, and configd died on the next boot with
#
#   Fatal error: Uncaught TypeError: base64_decode():
#     Argument #1 ($string) must be of type string, array given
#     in /usr/local/etc/inc/auth.inc:411
#
# which took the whole reconfiguration down with it. Handle both the
# self-closing and the empty-pair spelling.
string("sed -i '' -e 's|<authorizedkeys/>|<authorizedkeys>%s</authorizedkeys>|' "
       "-e 's|<authorizedkeys></authorizedkeys>|<authorizedkeys>%s</authorizedkeys>|' "
       "/conf/config.xml; echo MARK_SED4_$?" % (_PUB_B64, _PUB_B64))
enter()
waitForText("MARK_SED4_0", "180")
time.sleep(2)

# Assert BOTH that our key is in there and that there is exactly one such
# element. sed exits 0 having matched nothing, so the exit status alone proves
# nothing -- and the count is what caught the duplicate above.
string("echo MARK_AUTHKEYS_$(grep -c '<authorizedkeys>' /conf/config.xml)_$(grep -c '%s' /conf/config.xml)"
       % _PUB_B64[20:60])
enter()
waitForText("MARK_AUTHKEYS_1_1", "180")
time.sleep(2)

# --- 4. apply -----------------------------------------------------------------
# A reboot is what makes OPNsense re-read config.xml, reconfigure the
# interface and start sshd. Trying to poke configd for a partial reload is
# more fragile than just restarting the appliance.
log("opnsense enablessh: rebooting so the appliance applies the new config")
string("/sbin/reboot")
enter()

# Come back on the far side and confirm the two things that had to change.
# VM_LOGIN_TAG is "(ttyu0)", which the banner prints just above the prompt.
waitForText("(ttyu0)", "900")
time.sleep(5)
log("opnsense enablessh: appliance is back up after the reconfiguration")

# --- 5. finish inside the rebooted appliance ---------------------------------
# main()'s built-in _enable_ssh_console_branch() is NOT reused here even
# though this is a FreeBSD derivative: that branch logs in and expects a
# shell, and OPNsense drops you at a numbered menu instead. run_hook returning
# True already made main() skip its own dispatch, so everything it would have
# done has to be done here.
_to_shell()

# Now write the host pubkey, on the far side of the reboot where configd
# cannot undo it before ssh is used.
string("mkdir -p /root/.ssh && chmod 700 /root/.ssh && echo MARK_SSHDIR_$?")
enter()
waitForText("MARK_SSHDIR_0", "120")
time.sleep(2)

string("printf '%%s\\n' '%s' > /root/.ssh/authorized_keys" % _PUB)
enter()
time.sleep(3)
string("chmod 600 /root/.ssh/authorized_keys")
enter()
time.sleep(2)

# Assert the file really holds OUR key, not merely that a file exists. The
# previous version matched the marker prefix alone and happily passed while
# the key had been wiped -- grep for a distinctive slice of the key body so a
# clobbered or truncated file cannot look like success.
string("echo MARK_KEYOK_$(grep -c '%s' /root/.ssh/authorized_keys)" % _PUB[30:70])
enter()
waitForText("MARK_KEYOK_1", "180")
time.sleep(2)

# The guest needs its OWN keypair: main() exports ~/.ssh/id_rsa.pub as the
# <output>-id_rsa.pub release asset (the key the VM uses to reach back to the
# host for rsync). Without it that asset is a 0-byte file and the GitHub
# release upload rejects it outright -- freebsd-builder hit exactly that.
string("rm -f /root/.ssh/id_rsa /root/.ssh/id_rsa.pub")
enter()
time.sleep(2)
string("ssh-keygen -t rsa -f /root/.ssh/id_rsa -q -N '' && echo MARK_GUESTKEY_OK")
enter()
waitForText("MARK_GUESTKEY_OK", "300")
time.sleep(2)

# Confirm the appliance really is reachable now, from inside: an address on
# the slirp network and something listening on 22. If either is missing the
# build should say so here, not fail minutes later in _wait_ssh with no clue.
# Do NOT pin the expected subnet. This first shipped matching 'inet 10.'
# because that is what slirp handed out during hand-testing, and then the real
# build came up on the libvirt bridge at 192.168.122.254 and the assertion
# failed on a perfectly healthy guest. Assert what actually matters: a v4
# address that is not loopback.
string("echo MARK_ADDR_$(ifconfig vtnet0 | grep 'inet ' | grep -vc '127\\.')")
enter()
waitForText("MARK_ADDR_1", "300")
time.sleep(2)

string("echo MARK_SSHD_$(sockstat -4 -l | grep -c ':22')")
enter()
waitForText("MARK_SSHD_1", "300")
time.sleep(2)

# NOT DONE HERE: raising sshd's MaxAuthTries. It was tried and it does not
# stick -- OPNsense regenerates sshd_config from config.xml, so an appended
# line is gone the moment anything restarts the service. config.xml really is
# the only durable state on this appliance, and it exposes no MaxAuthTries
# knob.
#
# It was tried because a local build kept dying with "Too many authentication
# failures" while every check above passed. That turned out to be the
# developer machine, not the guest: ~/.ssh/config.d there holds 110+ files
# that each open with `Host *` and add their own IdentityFile, so ssh offers a
# long accumulated list and the build's own key never gets a turn within
# sshd's default six. A CI runner has one key and never sees it. Fixing the
# guest would have been treating the wrong end -- if this bites locally,
# connect with `-o IdentitiesOnly=yes -i ~/.ssh/id_rsa` to confirm the image
# itself is fine.

# build.py's generated enablessh.local reads /etc/ssh/sshd_config, which is
# where sshd lives on a stock FreeBSD -- but OPNsense installs openssh from
# ports, so the real file is /usr/local/etc/ssh/sshd_config and /etc/ssh does
# not exist at all. The build got all the way to exporting the artifacts and
# then died on "cat: /etc/ssh/sshd_config: No such file or directory".
#
# A symlink rather than an empty placeholder: a reader then sees the config
# that is actually in force. Anything appended through it lands in the real
# file, which configd may regenerate later -- that is fine, because the
# settings that matter are already in config.xml.
string("if [ ! -e /etc/ssh/sshd_config ]; then mkdir -p /etc/ssh && "
       "ln -s /usr/local/etc/ssh/sshd_config /etc/ssh/sshd_config; fi; "
       "echo MARK_SSHDCFG_$(test -r /etc/ssh/sshd_config && echo 1 || echo 0)")
enter()
waitForText("MARK_SSHDCFG_1", "180")
time.sleep(2)

log("opnsense enablessh: WAN has an address and sshd is listening")
