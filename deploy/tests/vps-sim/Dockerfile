# A simulated VPS for deploy/tests/vps-sim.sh: a plain Debian 12 server as a
# cloud provider hands it out (systemd, sshd, root login with a key, Python),
# nothing of the dashboard installed. Ansible then does everything else, like
# on a real VPS: firewall, SSH hardening, Docker (inside this container), Caddy.
#
# Runs privileged (systemd as PID 1 and its own Docker daemon need it); its
# firewall and Docker only affect this container's own network namespace.
FROM debian:12@sha256:268fb24c39bca92d326dd83ea3abebe4a3f7751c804ffef23bb6d857dd343ff8

ENV container=docker DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends systemd systemd-sysv dbus openssh-server python3 sudo \
      ca-certificates iproute2 iptables kmod procps \
 && rm -rf /var/lib/apt/lists/* \
 # Units that make no sense in a container.
 && systemctl mask getty.target console-getty.service systemd-firstboot.service \
      systemd-udevd.service systemd-modules-load.service sys-kernel-debug.mount \
 # Like a fresh cloud VPS: root logs in with a key (set by the test), passwords allowed in sshd's defaults.
 && mkdir -p /root/.ssh && chmod 700 /root/.ssh \
 && systemctl enable ssh

STOPSIGNAL SIGRTMIN+3
CMD ["/lib/systemd/systemd"]
